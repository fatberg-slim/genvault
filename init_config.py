"""Called by setup.bat: writes config.json with the chosen library path/port.

Merges into an existing config.json instead of overwriting it, so re-running
setup never loses platform lists or other customizations.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / "config.json"

DEFAULTS = {
    "library_root": str(Path.home() / "genvault"),
    "port": 8077,
    "platforms": ["X", "Civitai", "Reddit"],
    "kinds": ["clip", "image", "sound", "release", "pre-release", "recipe"],
    "ffmpeg": "",
    "ffprobe": "",
}


def main() -> None:
    cfg = dict(DEFAULTS)
    if CONFIG.exists():
        try:
            cfg.update(json.loads(CONFIG.read_text(encoding="utf-8")))
        except Exception:
            pass
    if len(sys.argv) > 1 and sys.argv[1].strip():
        cfg["library_root"] = str(Path(sys.argv[1].strip().strip('"')))
    if len(sys.argv) > 2 and sys.argv[2].strip():
        try:
            cfg["port"] = int(sys.argv[2])
        except ValueError:
            pass
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"config.json written — library: {cfg['library_root']}  port: {cfg['port']}")


if __name__ == "__main__":
    main()
