"""Identify external originals by their imported contents, including mapped copies."""
from __future__ import annotations

import hashlib
from pathlib import Path

from ..app.i18n import tr


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pdf_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        header = stream.read(4)
        if header != b"%PDF":
            raise ValueError(tr("不是 PDF 文件"))
        digest.update(header)
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verified_pdf(path: Path, digest: str | None, *, read: bool = False) -> Path | bytes | None:
    """Return the matching path or the exact bytes checked for an HTTP response."""
    try:
        if path.is_absolute() and path.is_file():
            if read:
                data = path.read_bytes()
                if data.startswith(b"%PDF") and sha256_bytes(data) == digest:
                    return data
            elif pdf_digest(path) == digest:
                return path
    except (OSError, ValueError):
        pass  # A stale or unavailable occurrence must not hide another good copy.
    return None


def _candidates(ws, original):
    yield original
    # Workers and CLI readers can also resolve a copy, without running the watcher.
    from ..bulk.folder_state import indexed_copies
    yield from indexed_copies(ws.root.parent, ws.id, original)


def original_pdf(ws, *, read: bool = False) -> Path | bytes:
    meta = (ws.load("paper") or {}).get("meta", {})
    stored = ws.root / "source.pdf"
    original = Path(meta["source_pdf_path"]) if meta.get("source_pdf_path") else None
    digest = meta.get("source_sha256")
    # Some older workspaces name their own stored copy as the link and have no digest.
    if original is None or (not digest and original == stored and not stored.is_symlink()):
        return stored.read_bytes() if read else stored
    if digest:
        for path in _candidates(ws, original):
            result = verified_pdf(path, digest, read=read)
            if result is not None:
                return result
    raise ValueError(tr("找不到内容匹配的原 PDF：{path}。请恢复原文件，或把相同的副本放入映射文件夹。", path=str(original)))
