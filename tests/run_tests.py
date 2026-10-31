r"""Run all genvault integration checks against a throwaway server.

    .venv\Scripts\python.exe tests\run_tests.py

For every suite a fresh temporary library is created, a genvault server is
started on GV_TEST_PORT (default 8099) with GENVAULT_ROOT pointing at it, the
generated test media is dropped in and the suite runs. Your config.json and
your real library are never touched. Needs ffmpeg/ffprobe (PATH or C:\ffmpeg\bin).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
PORT = int(os.environ.get("GV_TEST_PORT", "8099"))
# each suite imports core_checks first, so core runs on every fresh library
SUITES = ["library_checks.py", "release_checks.py"]


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1) as r:
            return r.status == 200
    except Exception:
        return False


def _stop(proc: subprocess.Popen) -> None:
    # the venv python.exe is a launcher with a child process: kill the tree
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True)
    else:
        proc.terminate()
    proc.wait(timeout=15)


def run_suite(name: str, media: Path, work: Path, env_base: dict) -> bool:
    lib = Path(tempfile.mkdtemp(prefix="lib-", dir=work))
    env = dict(env_base, GENVAULT_ROOT=str(lib), GENVAULT_PORT=str(PORT), GV_TEST_LIB=str(lib),
               GV_TEST_PORT=str(PORT), GV_TEST_WORK=str(work / name.removesuffix(".py")))
    log = open(work / f"server-{name}.log", "w", encoding="utf-8")
    server = subprocess.Popen([sys.executable, "serve.py"], cwd=ROOT, env=env,
                              stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(60):
            if _healthy():
                break
            time.sleep(0.5)
        else:
            print(f"server did not start, see {log.name}")
            return False
        (lib / "drop").mkdir(exist_ok=True)
        for f in media.iterdir():
            shutil.copy2(f, lib / "drop" / f.name)
        print(f"\n=== {name} ===", flush=True)
        return subprocess.run([sys.executable, str(TESTS / name)], cwd=TESTS, env=env).returncode == 0
    finally:
        _stop(server)
        log.close()


def main() -> int:
    if _healthy():
        print(f"port {PORT} is already in use - set GV_TEST_PORT to a free port")
        return 2
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    if Path(r"C:\ffmpeg\bin\ffmpeg.exe").exists():
        env["PATH"] = r"C:\ffmpeg\bin" + os.pathsep + env.get("PATH", "")
    work = Path(tempfile.mkdtemp(prefix="genvault-tests-"))
    media = work / "media"
    subprocess.run([sys.executable, str(TESTS / "make_test_media.py"), str(media)], env=env, check=True,
                   stdout=subprocess.DEVNULL)
    results = {name: run_suite(name, media, work, env) for name in SUITES}
    print("\n=== summary ===")
    for name, ok in results.items():
        print(f"{'OK  ' if ok else 'FAIL'} {name}")
    if all(results.values()):
        shutil.rmtree(work, ignore_errors=True)
        return 0
    print(f"logs and outputs kept in {work}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
