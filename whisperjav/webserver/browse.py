"""
Server-side directory browsing.

The page browses the *server's* own disk, so there is no upload step and a
40 GB video costs nothing to select. This is safe only on loopback — see the
--allow-remote guard in app.py.
"""

import os
import string
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from whisperjav.webserver.paths import (
    REPO_ROOT,
    default_output_dir,
    exts_for_kind,
    normalize_path,
)

MAX_ENTRIES = 2000
_ROOTS_CACHE: Dict[str, Any] = {"at": 0.0, "value": None}
_ROOTS_TTL = 1.0

IS_WINDOWS = sys.platform == "win32"
# FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
_WIN_HIDDEN_MASK = 0x2 | 0x4


class BrowseError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def list_roots() -> List[Dict[str, str]]:
    """Drive letters on Windows, '/' elsewhere. Cached to avoid spinning up
    optical/removable drives on every keystroke."""
    now = time.time()
    if _ROOTS_CACHE["value"] is not None and now - _ROOTS_CACHE["at"] < _ROOTS_TTL:
        return _ROOTS_CACHE["value"]

    if IS_WINDOWS:
        roots = []
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            try:
                if os.path.exists(drive):
                    roots.append({"path": drive, "label": f"{letter}:"})
            except OSError:
                continue
    else:
        roots = [{"path": "/", "label": "/"}]

    _ROOTS_CACHE["at"] = now
    _ROOTS_CACHE["value"] = roots
    return roots


def shortcuts() -> List[Dict[str, str]]:
    items = [
        {"label": "Home", "path": str(Path.home())},
        {"label": "Output", "path": str(default_output_dir())},
        {"label": "Repo", "path": str(REPO_ROOT)},
    ]
    seen, out = set(), []
    for item in items:
        if item["path"] not in seen and os.path.isdir(item["path"]):
            seen.add(item["path"])
            out.append(item)
    return out


def _is_hidden(entry: os.DirEntry) -> bool:
    if entry.name.startswith("."):
        return True
    if IS_WINDOWS:
        try:
            return bool(entry.stat().st_file_attributes & _WIN_HIDDEN_MASK)
        except (OSError, AttributeError):
            return False
    return False


def _parent_of(p: Path) -> str:
    """
    Empty string means "go back to the drive list".

    Path("D:\\").parent == Path("D:\\"), so without this the Up button would
    loop forever at a drive root.
    """
    parent = p.parent
    return "" if parent == p else str(parent)


def list_dir(raw_path: Optional[str], kind: str = "media",
             show_hidden: bool = False) -> Dict[str, Any]:
    """Browse one directory. An empty path yields the 'This PC' view."""
    roots = list_roots()

    if not raw_path or not str(raw_path).strip():
        return {
            "path": None, "display": "", "parent": None, "is_root": True,
            "roots": roots, "shortcuts": shortcuts(),
            "dirs": [], "files": [], "truncated": False,
        }

    p = normalize_path(raw_path)

    if not p.exists():
        raise BrowseError("not_found", f"Path does not exist: {p}", 400)
    if not p.is_dir():
        raise BrowseError("not_a_directory", f"Not a directory: {p}", 400)

    wanted = exts_for_kind(kind)
    dirs: List[Dict[str, Any]] = []
    files: List[Dict[str, Any]] = []
    truncated = False

    try:
        with os.scandir(p) as it:
            for entry in it:
                if len(dirs) + len(files) >= MAX_ENTRIES:
                    truncated = True
                    break
                try:
                    if not show_hidden and _is_hidden(entry):
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append({"name": entry.name, "path": entry.path})
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    ext = os.path.splitext(entry.name)[1].lower()
                    if wanted is not None and ext not in wanted:
                        continue
                    stat = entry.stat()
                    files.append({
                        "name": entry.name, "path": entry.path,
                        "size": stat.st_size, "mtime": stat.st_mtime,
                    })
                except OSError:
                    continue
    except PermissionError as exc:
        raise BrowseError("permission_denied", f"Permission denied: {p}", 403) from exc
    except OSError as exc:
        raise BrowseError("io_error", f"Cannot read {p}: {exc}", 400) from exc

    dirs.sort(key=lambda e: e["name"].lower())
    files.sort(key=lambda e: e["name"].lower())

    return {
        "path": str(p), "display": str(p), "parent": _parent_of(p),
        "is_root": _parent_of(p) == "", "roots": roots,
        "shortcuts": shortcuts(), "dirs": dirs, "files": files,
        "truncated": truncated,
    }


def expand_dir_files(raw_path: str, kind: str = "media",
                     recursive: bool = False) -> List[Dict[str, Any]]:
    """Every matching file inside a directory, for 'add whole folder'."""
    p = normalize_path(raw_path)
    if not p.is_dir():
        raise BrowseError("not_a_directory", f"Not a directory: {p}", 400)

    wanted = exts_for_kind(kind)
    out: List[Dict[str, Any]] = []

    def collect(directory: Path) -> None:
        if len(out) >= MAX_ENTRIES:
            return
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name.lower())
        except OSError:
            return
        for entry in entries:
            if len(out) >= MAX_ENTRIES:
                return
            try:
                if entry.is_dir(follow_symlinks=False):
                    if recursive and not _is_hidden(entry):
                        collect(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                # Match what the browser listed, which hides these by default.
                if _is_hidden(entry):
                    continue
                ext = os.path.splitext(entry.name)[1].lower()
                if wanted is not None and ext not in wanted:
                    continue
                stat = entry.stat()
                out.append({
                    "name": entry.name, "path": entry.path,
                    "size": stat.st_size, "mtime": stat.st_mtime,
                })
            except OSError:
                continue

    collect(p)
    return out
