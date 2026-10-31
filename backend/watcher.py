r"""Drop-folder watcher.

Files placed into drop\ (also whole folders, recursively) are moved into the
inbox once their size is stable. The subfolder path inside drop\ is recorded
as a non-binding display hint only — it is NEVER used to derive project/kind.

watchdog events just wake the worker; the actual intake decision is a
size-stability check across two scans, so half-copied videos are left alone.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from . import config, library

SCAN_INTERVAL = 4.0          # fallback scan cadence in seconds
IGNORE_SUFFIXES = (".part", ".crdownload", ".tmp", ".partial")

_wake = threading.Event()
_started = False
_last_sizes: dict[str, int] = {}


class _Handler(FileSystemEventHandler):
    def on_any_event(self, event):
        _wake.set()


def _candidates() -> list[Path]:
    files = []
    for p in config.DROP_DIR.rglob("*"):
        if not p.is_file():
            continue
        name = p.name.lower()
        if name.startswith((".", "~")) or name.endswith(IGNORE_SUFFIXES):
            continue
        files.append(p)
    return files


def _hint_for(p: Path) -> str | None:
    rel = p.parent.relative_to(config.DROP_DIR)
    return str(rel).replace("\\", "/") if str(rel) != "." else None


def _cleanup_empty_dirs() -> None:
    for d in sorted(config.DROP_DIR.rglob("*"), key=lambda x: len(x.parts), reverse=True):
        if d.is_dir():
            try:
                d.rmdir()  # only succeeds when empty
            except OSError:
                pass


def _scan_once() -> None:
    global _last_sizes
    sizes: dict[str, int] = {}
    for p in _candidates():
        try:
            sizes[str(p)] = p.stat().st_size
        except OSError:
            continue
    stable = [Path(k) for k, v in sizes.items() if _last_sizes.get(k) == v]
    _last_sizes = sizes
    imported = 0
    for p in stable:
        try:
            asset = library.intake(p, hint=_hint_for(p), move=True)
            if asset:
                imported += 1
            else:
                print(f"[watcher] unsupported file type, leaving in drop: {p.name}")
                _last_sizes.pop(str(p), None)  # don't retry every scan
        except Exception as e:
            print(f"[watcher] intake failed for {p.name}: {e}")
    if imported:
        print(f"[watcher] imported {imported} file(s) into the inbox")
        _cleanup_empty_dirs()


def _worker() -> None:
    while True:
        _wake.wait(timeout=SCAN_INTERVAL)
        _wake.clear()
        try:
            _scan_once()
        except Exception as e:
            print(f"[watcher] scan error: {e}")


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    config.ensure_dirs()
    observer = Observer(timeout=2)
    observer.schedule(_Handler(), str(config.DROP_DIR), recursive=True)
    observer.daemon = True
    observer.start()
    threading.Thread(target=_worker, daemon=True, name="gv-drop-worker").start()
    print(f"[watcher] watching {config.DROP_DIR}")
