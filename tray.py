"""System-tray launcher for genvault v2.

Runs the server as a child process (output -> logs/server.log, no console
window) and shows a tray icon that reflects real health: green when the
server answers, gray while it's down/starting. Launched without a console
via genvault-tray.bat (pythonw).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent
PY = ROOT / ".venv" / "Scripts" / "python.exe"
SERVE = ROOT / "serve.py"
LOG_DIR = ROOT / "logs"
CREATE_NO_WINDOW = 0x08000000


def _read_config() -> dict:
    try:
        return json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


_CFG = _read_config()
PORT = int(_CFG.get("port", 8077))
URL = f"http://127.0.0.1:{PORT}"
HEALTH = URL + "/api/health"

_proc: subprocess.Popen | None = None
_up = False


def _icon(up: bool) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=(34, 197, 94, 255) if up else (120, 120, 130, 255))
    d.ellipse((24, 24, 40, 40), fill=(255, 255, 255, 235))
    return img


def _health() -> bool:
    try:
        with urllib.request.urlopen(HEALTH, timeout=1.5) as r:
            return r.status == 200
    except Exception:
        return False


def _start_server():
    global _proc
    if _health():            # something already serving — don't double-start
        return
    if _proc and _proc.poll() is None:
        return
    LOG_DIR.mkdir(exist_ok=True)
    log = open(LOG_DIR / "server.log", "a", encoding="utf-8", buffering=1)
    _proc = subprocess.Popen(
        [str(PY), str(SERVE)],
        stdout=log, stderr=subprocess.STDOUT, cwd=str(ROOT),
        creationflags=CREATE_NO_WINDOW,
    )


def _stop_server():
    global _proc
    if _proc and _proc.poll() is None:
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
    _proc = None


def _open(icon, item):
    webbrowser.open(URL)


def _open_library(icon, item):
    root = _read_config().get("library_root")
    if root and Path(os.path.expandvars(os.path.expanduser(root))).exists():
        os.startfile(os.path.expandvars(os.path.expanduser(root)))


def _restart(icon, item):
    _stop_server()
    _start_server()


def _quit(icon, item):
    _stop_server()
    icon.stop()


def _monitor(icon):
    global _up
    while True:
        up = _health()
        if up != _up:
            _up = up
            icon.icon = _icon(up)
            icon.title = f"genvault — running ({URL})" if up else "genvault — starting…"
        time.sleep(2)


def main():
    _start_server()
    icon = pystray.Icon(
        "genvault", _icon(False), "genvault — starting…",
        menu=pystray.Menu(
            pystray.MenuItem("Open genvault", _open, default=True),
            pystray.MenuItem("Restart server", _restart),
            pystray.MenuItem("Open library folder", _open_library),
            pystray.MenuItem("Quit", _quit),
        ),
    )
    threading.Thread(target=_monitor, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
