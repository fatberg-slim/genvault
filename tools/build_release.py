r"""Build the user-facing release zip: genvault-<version>.zip with a genvault\ folder.

    .venv\Scripts\python.exe tools\build_release.py [output_dir]

Only program files go in: no config.json, environment, logs, tests or dev
tools. The version comes from backend/config.py (APP_VERSION). The zip uses
forward-slash paths, so it unpacks cleanly with Windows, 7-Zip and update.bat.
"""
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXCLUDE_DIRS = {".venv", "venv", "logs", "__pycache__", ".git", "tests", "tools", ".vscode", ".idea"}
EXCLUDE_FILES = {"config.json", ".gitignore", ".gitattributes"}


def main() -> None:
    version = re.search(r'APP_VERSION = "([^"]+)"', (ROOT / "backend" / "config.py").read_text(encoding="utf-8")).group(1)
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT.parent
    out = out_dir / f"genvault-{version}.zip"
    out.unlink(missing_ok=True)
    count = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if not p.is_file() or any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            if rel.name in EXCLUDE_FILES or p.suffix in (".pyc", ".zip"):
                continue
            z.write(p, "genvault/" + rel.as_posix())
            count += 1
    print(f"{out}  ({count} files, {out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
