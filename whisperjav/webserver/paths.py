"""
Path helpers for the web server.

Deliberately free of any pywebview import so the server never pulls in the
desktop GUI stack. The default-output logic mirrors
whisperjav/webview_gui/api.py:46-62 and the extension sets mirror the instance
attributes in whisperjav/modules/media_discovery.py:21-23 (they live on
MediaDiscovery.__init__, not as module constants, and importing that class
shells out to ffprobe).
"""

import os
from pathlib import Path
from typing import Optional

# Mirrors MediaDiscovery.video_extensions / .audio_extensions.
VIDEO_EXTS = {
    ".mp4", ".avi", ".mkv", ".mov", ".wmv",
    ".flv", ".webm", ".m4v", ".mpg", ".mpeg",
}
AUDIO_EXTS = {
    ".mp3", ".wav", ".flac", ".aac", ".ogg",
    ".wma", ".m4a", ".m4b", ".opus",
}
MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS
SRT_EXTS = {".srt"}

# whisperjav.main:286 uses the literal string "source" to mean
# "write next to the input file". It must never be treated as a real path.
OUTPUT_DIR_SOURCE_SENTINEL = "source"

REPO_ROOT = Path(__file__).resolve().parents[2]


def documents_dir() -> Path:
    home = Path.home()
    docs = home / "Documents"
    return docs if docs.exists() else home


def default_output_dir(create: bool = True) -> Path:
    """~/Documents/WhisperJAV/output, falling back to ~/WhisperJAV/output."""
    base = documents_dir()
    if base.name.lower() != "documents" or not base.exists():
        base = Path.home()
    p = base / "WhisperJAV" / "output"
    if create:
        try:
            p.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return p


def normalize_path(raw: str) -> Path:
    """
    Normalize a user-supplied path without resolving symlinks.

    Path.resolve() is avoided on purpose: on Windows it rewrites subst/UNC
    paths and can raise on a disconnected network drive.
    """
    expanded = os.path.expanduser(str(raw).strip())
    return Path(os.path.normpath(os.path.abspath(expanded)))


def is_loopback_host(host: str) -> bool:
    return str(host).strip().lower() in {
        "127.0.0.1", "localhost", "::1", "[::1]",
    }


def exts_for_kind(kind: str) -> Optional[set]:
    """None means 'no filtering'."""
    kind = (kind or "media").lower()
    if kind == "media":
        return MEDIA_EXTS
    if kind == "srt":
        return SRT_EXTS
    if kind == "media_srt":
        return MEDIA_EXTS | SRT_EXTS
    return None
