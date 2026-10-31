"""Derivative generation: poster-frame thumbnails for videos and images.

Hover previews in Phase 1 just play the original (muted) via the range-enabled
file endpoint, so we only need a still thumbnail here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config


def thumb_path(asset_id: int) -> Path:
    return config.DERIV_DIR / str(asset_id) / "thumb.jpg"


def _run(cmd: list[str]) -> bool:
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def _pillow_thumb(src: Path, out: Path) -> bool:
    try:
        from PIL import Image
        with Image.open(src) as img:
            img = img.convert("RGB")
            img.thumbnail((480, 480))
            img.save(out, "JPEG", quality=85)
        return True
    except Exception:
        return False


def make_thumbnail(src: Path, asset_id: int, media_type: str,
                   duration: float | None = None) -> str | None:
    out = thumb_path(asset_id)
    out.parent.mkdir(parents=True, exist_ok=True)
    scale = "scale='min(480,iw)':-2"
    if media_type == "audio":
        _run([config.FFMPEG, "-y", "-i", str(src),
              "-filter_complex", "showwavespic=s=480x270:colors=#818cf8",
              "-frames:v", "1", str(out)])
        return str(out) if out.exists() else None
    if media_type == "video":
        ts = 1.0
        if duration and duration > 0:
            ts = max(0.0, min(duration / 3.0, duration - 0.1))
        ok = _run([config.FFMPEG, "-y", "-ss", f"{ts:.2f}", "-i", str(src),
                   "-frames:v", "1", "-vf", scale, "-q:v", "3", str(out)])
        if not ok or not out.exists():
            ok = _run([config.FFMPEG, "-y", "-i", str(src), "-frames:v", "1",
                       "-vf", scale, "-q:v", "3", str(out)])
    else:
        ok = _run([config.FFMPEG, "-y", "-i", str(src), "-frames:v", "1",
                   "-vf", scale, "-q:v", "3", str(out)])
        if not ok or not out.exists():
            ok = _pillow_thumb(src, out)
    return str(out) if out.exists() else None
