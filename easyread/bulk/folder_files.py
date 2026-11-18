"""Filesystem operations for watched PDF folders."""
from __future__ import annotations

import os
import re
import stat
import tempfile
from pathlib import Path

from ..app.i18n import tr
from .folder import MAX_FILES


class IncompletePDF(ValueError):
    pass


def directory_name(value: str) -> str:
    name = value.strip()
    if (not name or name.startswith(".") or name.endswith(".") or len(name) > 255
            or re.search(r'[\x00-\x1f<>:"/\\|?*]', name)
            or re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".")[0], re.I)):
        raise ValueError(tr("请输入有效的文件夹名称"))
    return name


def create_directory(path: Path) -> None:
    try:
        path.mkdir()
    except FileExistsError:
        raise ValueError(tr("目标位置已有同名文件或文件夹：{name}", name=path.name)) from None


def move_directory(source: Path, target: Path) -> None:
    if os.name != "nt" or source.stat().st_dev != target.parent.stat().st_dev:
        raise ValueError(tr("请在 Windows 的同一磁盘内改名或移动文件夹"))
    if target.parent.is_relative_to(source):
        raise ValueError(tr("不能把文件夹移到它自己的子文件夹"))
    try:
        source.rename(target)
    except FileExistsError:
        raise ValueError(tr("目标位置已有同名文件或文件夹：{name}", name=target.name)) from None


def _redirected(info: os.stat_result) -> bool:
    junction = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
    return stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", None) == junction


def checked_path(root: Path, path: Path) -> Path:
    if not path.is_relative_to(root) or path.resolve(strict=True) != path or _redirected(path.lstat()):
        raise ValueError(tr("文件夹同步只操作所选目录中的普通文件和文件夹"))
    return path


def _stamp(info: os.stat_result) -> list[int]:
    return [info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_ino]


def stamp(path: Path) -> list[int]:
    info = path.lstat()
    if _redirected(info) or not stat.S_ISREG(info.st_mode):
        raise ValueError(tr("文件夹同步只操作所选目录中的普通文件和文件夹"))
    return _stamp(info)


def walk(root: Path) -> tuple[list[str], dict[str, list[int]]]:
    checked_path(root, root)
    folders, files = [], {}

    def unavailable(error):
        raise error

    def entry_stat(path):
        try:
            info = path.lstat()
        except FileNotFoundError:  # The desktop may be moving it during this scan.
            return None
        return None if _redirected(info) else info

    for directory, children, names in os.walk(root, onerror=unavailable):
        here = Path(directory)
        relative = here.relative_to(root).as_posix()
        folders.append("" if relative == "." else relative)
        children[:] = sorted(name for name in children
                             if not name.startswith(".") and entry_stat(here / name) is not None)
        for name in sorted(names):
            if name.startswith(".") or not name.lower().endswith(".pdf"):
                continue
            path = here / name
            info = entry_stat(path)
            if info is not None and stat.S_ISREG(info.st_mode):
                if len(files) >= MAX_FILES:
                    raise ValueError(tr("这个文件夹里的 PDF 超过 {n} 个，请分几次导入，每次选一个子文件夹", n=MAX_FILES))
                files[path.relative_to(root).as_posix()] = _stamp(info)
    return folders, files


def read_stable(path: Path, expected: list[int]) -> bytes:
    if stamp(path) != expected:
        raise ValueError(tr("PDF 已变化，请等待文件夹重新同步"))
    data = path.read_bytes()
    if stamp(path) != expected:
        raise ValueError(tr("PDF 已变化，请等待文件夹重新同步"))
    if not data.startswith(b"%PDF") or b"%%EOF" not in data[-65536:]:
        raise IncompletePDF(tr("PDF 尚未下载完整或文件已损坏"))
    return data


def publish_pdf(path: Path, data: bytes) -> None:
    """Stage in the destination directory, then publish without replacing anything."""
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".easyread-", delete=False) as stream:
        temp = Path(stream.name)
    try:
        with temp.open("wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            temp.rename(path)  # Windows rename fails if the destination exists.
        else:
            os.link(temp, path)  # POSIX rename would overwrite the destination.
    except FileExistsError:
        raise ValueError(tr("目标文件夹已有同名文件：{name}", name=path.name)) from None
    finally:
        temp.unlink(missing_ok=True)
