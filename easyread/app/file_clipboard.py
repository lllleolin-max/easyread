"""Read and write Windows Explorer's file clipboard on explicit requests."""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .i18n import tr

_PREFIX = """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
"""


def _run(script: str, source: Path | None = None) -> str:
    if os.name != "nt":
        raise ValueError(tr("系统 PDF 剪贴板需要 Windows"))
    env = os.environ.copy()
    if source is not None:
        env["EASYREAD_CLIPBOARD_PDF"] = str(source)
    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-STA", "-Command", _PREFIX + script]
    try:
        result = subprocess.run(command, env=env, capture_output=True, timeout=8, check=True,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.stdout.decode("utf-8-sig")
    except (OSError, subprocess.SubprocessError, UnicodeError) as err:
        raise ValueError(tr("无法访问 Windows 剪贴板，请稍后重试")) from err


def paths() -> list[Path]:
    result = _run("""
$files = @([System.Windows.Forms.Clipboard]::GetFileDropList() | Where-Object { $_ })
ConvertTo-Json -InputObject $files -Compress
""")
    return [Path(path) for path in json.loads(result)]


def copy(source: Path) -> None:
    _run("""
$files = New-Object System.Collections.Specialized.StringCollection
[void]$files.Add($env:EASYREAD_CLIPBOARD_PDF)
[System.Windows.Forms.Clipboard]::SetFileDropList($files)
""", source)
