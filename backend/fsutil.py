r"""Windows-safe file primitives.

Background (2.1.2): genvault streams media to the browser (hover previews,
drawer player). Python's open() on Windows does not grant FILE_SHARE_DELETE,
so while a video was being streamed, every rename/move of that file failed
with WinError 32 ("used by another process") — the other process being
genvault itself. shutil.move then fell back to copy + delete, and when the
delete failed too, the file existed twice.

- open_shared(): read handle that still allows the file to be renamed/deleted
- move():        atomic same-volume rename with a short retry, never leaves a
                 copy behind
- unlink():      delete with the same retry
"""
from __future__ import annotations

import errno
import os
import shutil
import sys
import time
from pathlib import Path

RETRIES = 8          # 8 x 0.3 s: rides out short locks (AV scan, Explorer thumbnailer)
RETRY_DELAY = 0.3


class FileBusyError(PermissionError):
    """The file stayed locked by another program for the whole retry window."""

    def __init__(self, path: Path):
        super().__init__(
            f"file is in use by another program, please close it and try again: {Path(path).name}")


# ---- shared-read open ------------------------------------------------------------

if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                 wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
    _INVALID_HANDLE = wintypes.HANDLE(-1).value
    _GENERIC_READ = 0x80000000
    _SHARE_ALL = 0x1 | 0x2 | 0x4            # FILE_SHARE_READ | WRITE | DELETE
    _OPEN_EXISTING = 3
    _FLAGS = 0x80 | 0x08000000              # FILE_ATTRIBUTE_NORMAL | FILE_FLAG_SEQUENTIAL_SCAN

    def open_shared(path: Path):
        """Binary read handle that does not block rename/delete of the file."""
        h = _k32.CreateFileW(str(path), _GENERIC_READ, _SHARE_ALL, None,
                             _OPEN_EXISTING, _FLAGS, None)
        if h is None or h == _INVALID_HANDLE:
            err = ctypes.get_last_error()
            if err in (2, 3):               # file / path not found
                raise FileNotFoundError(errno.ENOENT, "file not found", str(path))
            raise ctypes.WinError(err)
        try:
            fd = msvcrt.open_osfhandle(h, os.O_RDONLY | os.O_BINARY)
        except Exception:
            _k32.CloseHandle(h)
            raise
        return os.fdopen(fd, "rb")
else:
    def open_shared(path: Path):
        return open(path, "rb")


# ---- move / delete -------------------------------------------------------------


def _is_cross_device(e: OSError) -> bool:
    return e.errno == errno.EXDEV or getattr(e, "winerror", None) == 17


def _retry(fn, path: Path):
    for attempt in range(RETRIES):
        try:
            return fn()
        except FileNotFoundError:
            raise
        except PermissionError:
            if attempt == RETRIES - 1:
                raise FileBusyError(path) from None
            time.sleep(RETRY_DELAY)


def unlink(path: Path) -> None:
    path = Path(path)
    if path.exists():
        _retry(lambda: os.unlink(path), path)


def move(src: Path, dest: Path) -> None:
    """Move src to dest. Same volume: atomic rename (an existing dest — e.g.
    a stale copy from an earlier failed move — is replaced). Across volumes:
    copy, then delete the source; if that delete fails the copy is removed
    again, so a file never ends up in two places."""
    src, dest = Path(src), Path(dest)
    if src == dest:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        _retry(lambda: os.replace(src, dest), src)
        return
    except OSError as e:
        if not _is_cross_device(e):
            raise
    shutil.copy2(src, dest)
    try:
        unlink(src)
    except OSError:
        dest.unlink(missing_ok=True)
        raise
