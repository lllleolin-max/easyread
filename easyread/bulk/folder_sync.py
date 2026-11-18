"""Mirror local PDF folders onto the existing content-addressed library."""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path, PurePosixPath

from ..app import file_clipboard
from ..app.i18n import tr
from ..app.log import log
from ..library.linked_pdf import verified_pdf
from ..library.library import sha256_bytes
from ..library.store import DataReadError, write_json_atomic
from . import folder_files as fs
from .folder_state import import_only_paths, read_mappings

INTERVAL = 2
IMPORTS_PER_SCAN = 24
MAX_AUTO_TRANSLATE_ARRIVAL = 5
OPTIONS = {"enabled": True, "translate": False, "network_metadata": False}
# Ordinary mappings have a nonempty name. The built-in All view uses the unnamed root.
DEFAULT_ROOT = ""


def _category(root: str, relative: str) -> str:
    return f"{root}/{relative}" if relative else root


def _parent(relative: str) -> str:
    return relative.rpartition("/")[0]


def _filename(meta: dict) -> str:
    name = re.split(r"[/\\]", str(meta.get("source") or ""))[-1]
    name = re.sub(r'[\x00-\x1f<>:"|?*]', "_", name).strip(" .")
    if not name.lower().endswith(".pdf"):
        name = name + ".pdf" if name else "paper.pdf"
    # Windows device names are reserved even with an extension.
    if re.fullmatch(r"CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³]", name.split(".")[0], re.I):
        name = "_" + name
    return name


class FolderSync:
    def __init__(self, app):
        self.app = app
        self.path = app.lib.root / ".folder-sync.json"
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._observed = {}
        self._cursor = {}
        self._invalid = {}
        self._errors = {}
        self._load_error = ""
        try:
            self._set_state(read_mappings(self.path))
        except DataReadError:
            self._load_error = tr("无法读取数据文件：{path}；原文件已保留，请从备份恢复或联系维护者", path=str(self.path))
            self._set_state({})

    def _set_state(self, roots):
        folders, by_id = [], {}
        default_enabled = roots.get(DEFAULT_ROOT, {}).get("enabled", False)
        for name, watch in roots.items():
            if name != DEFAULT_ROOT:
                folders.extend(_category(name, path) for path in watch["folders"])
            elif not default_enabled:
                continue
            for path, record in watch["files"].items():
                by_id.setdefault(record["id"], []).append({"folder": _category(name, _parent(path)),
                                                           "name": PurePosixPath(path).name})
        # The HTTP list reads a single published snapshot without blocking disk scans.
        self._state = (roots, {"folders": folders, "default_enabled": default_enabled,
                               "active": bool(folders) or default_enabled, "windows": os.name == "nt"}, by_id)

    def _commit(self, roots):
        write_json_atomic(self.path, roots)
        self._set_state(roots)

    def library(self):
        _, summary, by_id = self._state
        return summary, by_id

    def settings(self):
        watches = {name: {"name": name, "path": w["path"], **{key: w.get(key, default) for key, default in OPTIONS.items()},
                          "files": len(w["files"]), "error": self._errors.get(name, "")}
                   for name, w in self._state[0].items()}
        default = watches.pop(DEFAULT_ROOT, None)
        return {"roots": list(watches.values()), "default": default, "error": self._load_error}

    def _watch_path(self, value, roots, name):
        path = Path(str(value or "").strip().strip('"')).expanduser()
        if not path.is_absolute() or not path.is_dir():
            raise ValueError(tr("找不到这个文件夹：{path}", path=str(path)))
        try:
            path = path.resolve(strict=True)
        except OSError as err:
            raise ValueError(tr("找不到这个文件夹：{path}", path=str(path))) from err
        excluded = [self.app.lib.root, *(Path(w["path"]) for key, w in roots.items() if key != name)]
        if any(path.is_relative_to(other) or other.is_relative_to(path) for other in excluded):
            raise ValueError(tr("这个文件夹与文献库或已有的监控文件夹重叠"))
        return path

    @staticmethod
    def _watch_name(value, roots, old_name):
        if not value or len(value) > 80 or re.search(r"[/\\\x00-\x1f]", value):
            raise ValueError(tr("请输入有效的文献库显示名称"))
        if any(value.casefold() == other.casefold() for other in roots if other != old_name):
            raise ValueError(tr("已有同名的本地文件夹，请换一个显示名称"))
        return value

    def configure(self, body):
        if self._load_error:
            raise ValueError(self._load_error)
        action, name = body.get("action"), str(body.get("name") or "").strip()
        if action == "set_default":
            name = DEFAULT_ROOT
        elif action != "add" and not name:
            raise ValueError(tr("找不到要同步的文件夹"))
        with self._lock:
            roots = dict(self._state[0])
            new_name = name
            reset = False
            options = {key: body[key] for key in OPTIONS if type(body.get(key)) is bool}
            if action == "add" or (action == "set_default" and name not in roots):
                path = self._watch_path(body.get("path"), roots, None)
                if action == "add":
                    new_name = self._watch_name(name or path.name, roots, None)
                roots[new_name] = {**OPTIONS, **options, "path": str(path), "folders": [""], "files": {}, "backlog": None}
            elif action in ("update", "set_default") and name in roots:
                old = roots[name]
                watch = {**OPTIONS, **old, **options}
                if "path" in body and str(body["path"]).strip() != old["path"]:
                    path = self._watch_path(body["path"], roots, name)
                    reset = path != Path(old["path"])
                    if reset:
                        watch.update(path=str(path), folders=[""], files={}, backlog=None)
                if action == "update" and "new_name" in body:
                    new_name = self._watch_name(str(body.get("new_name") or "").strip() or Path(watch["path"]).name, roots, name)
                if any(watch[key] and not old.get(key, OPTIONS[key]) for key in ("translate", "enabled")):
                    watch["backlog"] = None
                if name == DEFAULT_ROOT and not watch["enabled"]:
                    reset = True
                roots = {new_name if key == name else key: watch if key == name else value for key, value in roots.items()}
            elif action == "remove" and name in roots:
                del roots[name]
                reset = True
            else:
                raise ValueError(tr("找不到要同步的文件夹"))
            self._commit(roots)
            if reset or new_name != name and action == "update":
                self._observed.pop(name, None)
                self._cursor.pop(name, None)
                self._invalid.pop(name, None)
                self._errors.pop(name, None)
            return self.settings()

    def start(self):
        self._thread = threading.Thread(target=self._loop, name="folder-sync", daemon=True)
        self._thread.start()

    def close(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def _loop(self):
        while not self._stop.is_set():
            self.scan()
            self._stop.wait(INTERVAL)

    def scan(self):
        for name in self._state[0]:
            if self._stop.is_set():
                return
            try:
                self.app.location.begin()
            except ValueError:
                return  # The library is moving or awaiting restart.
            try:
                with self._lock:
                    current = self._state[0].get(name)
                    if current and (name != DEFAULT_ROOT or current["enabled"]):
                        self._scan_root(name, current)
            except (OSError, ValueError) as err:
                self._errors[name] = str(err)
            except Exception as err:  # Keep other watches running after a library error.
                log.exception("Folder sync failed for %s", name)
                self._errors[name] = str(err)
            finally:
                self.app.location.end()

    def _scan_root(self, name, watch):
        root = Path(watch["path"])
        folders, observed = fs.walk(root)
        previous = self._observed.get(name, {})
        backlog = import_only_paths(watch, observed, previous, MAX_AUTO_TRANSLATE_ARRIVAL)
        invalid = {p: stamp for p, stamp in self._invalid.get(name, {}).items() if observed.get(p) == stamp}
        files = {p: record for p, record in watch["files"].items() if p in observed}
        errors, processed = [], 0
        enabled = watch.get("enabled", True)
        paths = list(observed)
        cursor = self._cursor.get(name)
        start = paths.index(cursor) + 1 if cursor in observed else 0
        for relative in paths[start:] + paths[:start]:
            if self._stop.is_set():
                break
            stamp = observed[relative]
            old = files.get(relative, {})
            unchanged = old.get("stamp") == stamp and not (old.get("pending") and enabled)
            if unchanged:
                backlog.discard(relative)
                continue
            if relative in invalid:
                continue
            if previous.get(relative) != stamp or processed >= IMPORTS_PER_SCAN:
                continue
            processed += 1
            # Retry locked files, but do not let them consume every scan's budget.
            self._cursor[name] = relative
            path = root / relative
            try:
                data = fs.read_stable(fs.checked_path(root, path), stamp)
                pid = self._import(path, data, watch.get("translate", False) and relative not in backlog,
                                   network=watch.get("network_metadata", False), allow_create=enabled)
                files[relative] = {"id": pid, "stamp": stamp, "pending": not enabled and not self.app.lib.ws(pid)}
                backlog.discard(relative)  # a later edition at this path gets its own decision
            except fs.IncompletePDF:
                invalid[relative] = stamp
                files.pop(relative, None)
            except (OSError, ValueError) as err:
                errors.append(f"{path.name} · {err}")
        if invalid:
            errors.append(tr("有 {n} 个 PDF 尚未下载完整或文件已损坏", n=len(invalid)))
        self._errors[name] = "\n".join(errors[:3])
        backlog = sorted(backlog)
        if files != watch["files"] or folders != watch["folders"] or backlog != watch.get("backlog"):
            self._commit({**self._state[0], name: {**watch, "files": files, "folders": folders, "backlog": backlog}})
            if files != watch["files"]:
                self._repair_displaced_links(root, watch["files"], files)
        # A failed save must not make a large arrival look like a small new batch.
        self._observed[name], self._invalid[name] = observed, invalid

    def _repair_displaced_links(self, root, previous, files):
        roots, _, indexed = self._state
        for relative, old in previous.items():
            pid = old["id"]
            if pid == files.get(relative, {}).get("id") or pid not in indexed:
                continue
            source = root / relative
            ws = self.app.lib.ws(pid)
            linked = ws.load("paper")["meta"].get("source_pdf_path") if ws else None
            if not linked or Path(linked) != source:
                continue
            for location in indexed[pid]:
                try:
                    _, _, candidate, _ = self._source(roots, location, pid)
                except (OSError, ValueError):
                    continue
                try:
                    self.app.lib.convert_pdf_to_link(pid, candidate, expected_link=source)
                    break
                except DataReadError:
                    raise
                except ValueError:
                    continue  # An indexed copy can itself be missing or have different bytes.

    def _import(self, path, data, translate, *, network, allow_create=True):
        lib = self.app.lib
        digest = sha256_bytes(data)
        pid = digest[:12]
        existing = lib.ws(pid)
        if not existing and (not allow_create or any((lib.root / ".trash").glob(f"{pid}-[0-9]*"))):
            return pid  # Leave deletion and restoration under the library's control.
        ws, fresh = (existing, False) if existing else lib.create_from_pdf(data, path.name, network_metadata=network)
        meta = ws.load("paper")["meta"]
        if meta.get("source_sha256") != digest:
            raise ValueError(tr("PDF 内容与文献库中的原文件不一致"))
        linked = Path(meta["source_pdf_path"]) if meta.get("source_pdf_path") else None
        if linked and linked != path and any(linked.is_relative_to(w["path"]) for w in self._state[0].values()):
            if verified_pdf(linked, digest) is None:
                lib.convert_pdf_to_link(pid, path, expected_link=linked)
        job = ws.load("job") or {}
        if allow_create and not job and (fresh or not meta.get("pages")):
            try:
                self.app.jobs.enqueue(ws, translate_after=translate)
            except ValueError:
                if (ws.load("job") or {}).get("state") not in ("queued", "running", "confirm"):
                    raise
        return pid

    @staticmethod
    def _folder(roots, category):
        name, _, relative = str(category or "").partition("/")
        watch = roots.get(name)
        if (not watch or (name == DEFAULT_ROOT and not watch["enabled"]) or
                relative not in watch["folders"] or category != _category(name, relative)):
            raise ValueError(tr("找不到要同步的文件夹"))
        return name, relative, watch

    def _source(self, roots, source, pid):
        if not isinstance(source, dict):
            raise ValueError(tr("找不到要操作的 PDF"))
        top, directory, watch = self._folder(roots, source.get("folder"))
        name = str(source.get("name") or "")
        relative = f"{directory}/{name}" if directory else name
        record = watch["files"].get(relative)
        if not record or record["id"] != pid or name != PurePosixPath(relative).name:
            raise ValueError(tr("找不到要操作的 PDF"))
        root = Path(watch["path"])
        return top, relative, fs.checked_path(root, root / relative), record["stamp"]

    def _relink(self, ws, source, dest):
        linked = ws.load("paper")["meta"].get("source_pdf_path") if ws else None
        if linked and Path(linked) == source:
            self.app.lib.convert_pdf_to_link(ws.id, dest, expected_link=source)

    def directory(self, body):
        action = body.get("action")
        if action not in ("create", "move"):
            raise ValueError(tr("找不到要同步的文件夹"))
        with self._lock:
            try:
                return self._directory(body, action)
            except OSError as err:
                raise ValueError(tr("文件夹操作失败：{msg}", msg=str(err))) from err

    def _directory(self, body, action):
        roots = dict(self._state[0])
        top, parent, watch = self._folder(roots, body.get("target"))
        root = Path(watch["path"])
        directory = fs.checked_path(root, root / parent)
        name = fs.directory_name(str(body.get("name") or ""))
        target = directory / name
        relative = target.relative_to(root).as_posix()
        warning = ""
        if action == "create":
            fs.create_directory(target)
            roots[top] = {**watch, "folders": sorted({*watch["folders"], relative})}
        else:
            src_top, src_rel, source_watch = self._folder(roots, body.get("source"))
            if not src_rel:
                raise ValueError(tr("请在设置中编辑映射根目录"))
            src_root = Path(source_watch["path"])
            source = fs.checked_path(src_root, src_root / src_rel)
            fs.move_directory(source, target)

            def under(path):
                return path == src_rel or path.startswith(src_rel + "/")

            def rebase(path):
                return relative + path[len(src_rel):]

            moved = {p: v for p, v in source_watch["files"].items() if under(p)}
            moved_folders = [rebase(p) for p in source_watch["folders"] if under(p)]
            import_only = set(moved).union(p for p in source_watch.get("backlog") or () if under(p))
            roots[src_top] = {**source_watch,
                              "folders": [p for p in source_watch["folders"] if not under(p)],
                              "files": {p: v for p, v in source_watch["files"].items() if not under(p)},
                              "backlog": None if source_watch.get("backlog") is None else [p for p in source_watch["backlog"] if not under(p)]}
            dest_watch = roots[top]
            backlog = dest_watch.get("backlog")
            roots[top] = {**dest_watch, "folders": sorted(set(dest_watch["folders"] + moved_folders)),
                          "files": {**dest_watch["files"], **{rebase(p): v for p, v in moved.items()}},
                          "backlog": None if backlog is None else sorted(set(backlog).union(map(rebase, import_only)))}
            for path, record in moved.items():
                try:
                    self._relink(self.app.lib.ws(record["id"]), src_root / path, root / rebase(path))
                except (OSError, ValueError) as err:
                    warning = tr("文件夹已移动，有链接的 PDF 需要检查：{msg}", msg=str(err))
        self._commit(roots)
        return {"folder": _category(top, relative), "warning": warning}

    def clipboard(self, body):
        action = body.get("action")
        if action == "copy":
            with self._lock:
                try:
                    _, _, source, stamp = self._source(self._state[0], body.get("source"), body.get("id"))
                    if fs.stamp(source) != stamp:
                        raise ValueError(tr("PDF 已变化，请等待文件夹重新同步"))
                    file_clipboard.copy(source)
                except OSError as err:
                    raise ValueError(tr("文件夹操作失败：{msg}", msg=str(err))) from err
            return {"ok": True}
        if action != "paste":
            raise ValueError(tr("找不到要操作的 PDF"))
        paths = file_clipboard.paths()
        pdfs = list(dict.fromkeys(p for p in paths if p.is_absolute() and p.suffix.lower() == ".pdf"))
        if not pdfs:
            raise ValueError(tr("Windows 剪贴板中没有 PDF 文件"))
        with self._lock:
            try:
                return self._paste_files(body.get("target"), pdfs)
            except OSError as err:
                raise ValueError(tr("文件夹操作失败：{msg}", msg=str(err))) from err

    def _paste_files(self, destination, paths):
        roots = dict(self._state[0])
        top, parent, watch = self._folder(roots, destination)
        root = Path(watch["path"])
        folder = fs.checked_path(root, root / parent)
        files = dict(watch["files"])
        translate = watch.get("translate", False) and len(paths) <= MAX_AUTO_TRANSLATE_ARRIVAL
        copied, failed = 0, []
        for path in paths:
            try:
                data = fs.read_stable(path, fs.stamp(path))
                target = folder / path.name
                fs.publish_pdf(target, data)
                copied += 1
                pid = self._import(target, data, translate, network=watch.get("network_metadata", False))
                files[target.relative_to(root).as_posix()] = {"id": pid, "stamp": fs.stamp(target)}
            except (OSError, ValueError) as err:
                failed.append(f"{path.name} · {err}")
        if files != watch["files"]:
            self._commit({**roots, top: {**watch, "files": files}})
        return {"copied": copied, "failed": failed}

    def transfer(self, body):
        """Transfer one registered occurrence, or copy the library's original PDF."""
        ws = self.app.lib.ws(str(body.get("id") or ""))
        move = body.get("mode") == "move"
        source = body.get("source")
        if not ws or body.get("mode") not in ("move", "copy") or (source is not None and not isinstance(source, dict)):
            raise ValueError(tr("找不到要操作的 PDF"))
        if move and not source:
            raise ValueError(tr("请从本地文件夹中选择要移动的 PDF"))
        with self._lock:
            try:
                return self._transfer(ws, source, body.get("target"), move)
            except OSError as err:
                raise ValueError(tr("文件夹操作失败：{msg}", msg=str(err))) from err

    def _transfer(self, ws, source, target, move):
        roots = dict(self._state[0])
        top, relative, watch = self._folder(roots, target)
        root = Path(watch["path"])
        dest_dir = fs.checked_path(root, root / relative)
        meta = ws.load("paper")["meta"]
        if meta.get("source_type") == "web":
            raise ValueError(tr("找不到要操作的 PDF"))
        if source:
            src_top, src_rel, src, stamp = self._source(roots, source, ws.id)
            src_root = Path(roots[src_top]["path"])
            name = src.name
        else:
            name, src = _filename(meta), ws.source_pdf
            stamp = fs.stamp(src)
        dest = dest_dir / name
        data = fs.read_stable(src, stamp)
        if sha256_bytes(data) != meta.get("source_sha256"):
            raise ValueError(tr("PDF 内容与文献库中的原文件不一致"))
        fs.publish_pdf(dest, data)
        dest_rel = dest.relative_to(root).as_posix()
        roots[top] = {**watch, "files": {**watch["files"], dest_rel: {"id": ws.id, "stamp": fs.stamp(dest)}}}
        warning = ""
        if move:
            try:
                self._relink(ws, src, dest)
                if fs.stamp(fs.checked_path(src_root, src)) != stamp:
                    raise ValueError(tr("PDF 已变化，请等待文件夹重新同步"))
                src.unlink()
                files = dict(roots[src_top]["files"])
                del files[src_rel]
                roots[src_top] = {**roots[src_top], "files": files}
            except (OSError, ValueError) as err:
                warning = tr("已复制 PDF，源文件未移走：{msg}", msg=str(err))
        self._commit(roots)
        return {"moved": move and not warning, "warning": warning}
