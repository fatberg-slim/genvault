"""Configuration: app folder vs. library folder are separate concepts.

config.json lives next to the app (program folder) and points at the library
root (data folder). Both can live on different drives. The library root is
fully owned by the app; the user never has to maintain its structure.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent      # .../backend
PROJECT_DIR = APP_DIR.parent                   # app root (program folder)
CONFIG_FILE = Path(os.environ.get("GENVAULT_CONFIG", str(PROJECT_DIR / "config.json")))

# App version. Bump on every release and add a CHANGELOG.md entry.
APP_VERSION = "2.4.1"

DEFAULTS = {
    "library_root": str(Path.home() / "genvault"),
    "port": 8077,
    "platforms": ["X", "Civitai", "Reddit"],
    "kinds": ["clip", "image", "sound", "release", "pre-release", "recipe"],
    "ffmpeg": "",   # autodetected when empty
    "ffprobe": "",
}


def _load() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_FILE.exists():
        try:
            cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    if os.environ.get("GENVAULT_ROOT"):
        cfg["library_root"] = os.environ["GENVAULT_ROOT"]
    if os.environ.get("GENVAULT_PORT"):
        cfg["port"] = int(os.environ["GENVAULT_PORT"])
    return cfg


_cfg = _load()

LIBRARY_ROOT = Path(os.path.expandvars(os.path.expanduser(str(_cfg["library_root"]))))
DROP_DIR = LIBRARY_ROOT / "drop"
INBOX_DIR = LIBRARY_ROOT / "_inbox"
LIBRARY_DIR = LIBRARY_ROOT / "library"
TRASH_DIR = LIBRARY_ROOT / ".trash"
DERIV_DIR = LIBRARY_ROOT / ".derivatives"
SIDECAR_DIR = LIBRARY_ROOT / ".sidecars"
BACKUP_DIR = LIBRARY_ROOT / "backups"
DB_PATH = LIBRARY_ROOT / "genvault.db"

PORT = int(_cfg["port"])
PLATFORMS = [str(p) for p in _cfg["platforms"]]
KINDS = [str(k) for k in _cfg["kinds"]]

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".gif", ".avi", ".m4v"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}


def _find_tool(name: str, override: str) -> str:
    if override and Path(override).exists():
        return override
    found = shutil.which(name)
    if found:
        return found
    fallback = Path("C:/ffmpeg/bin") / (name + ".exe")
    if fallback.exists():
        return str(fallback)
    return name  # last resort: assume it is on PATH


FFMPEG = _find_tool("ffmpeg", str(_cfg.get("ffmpeg", "")))
FFPROBE = _find_tool("ffprobe", str(_cfg.get("ffprobe", "")))


def media_type_for(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in VIDEO_EXTS:
        return "video"
    if ext in IMAGE_EXTS:
        return "image"
    if ext in AUDIO_EXTS:
        return "audio"
    return None


def ensure_dirs() -> None:
    for d in (LIBRARY_ROOT, DROP_DIR, INBOX_DIR, LIBRARY_DIR, TRASH_DIR,
              DERIV_DIR, SIDECAR_DIR, BACKUP_DIR):
        d.mkdir(parents=True, exist_ok=True)
