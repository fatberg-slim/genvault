r"""Release module: watermark + concat rendering via ffmpeg.

Jobs run on a single background worker thread so renders never block the API.
Job state lives in memory only — genvault is single-user, the queue is
short-lived, and the durable output is the rendered file/asset itself.

Quality policy (per spec):
- pure concat of codec/resolution/fps-identical clips WITHOUT watermark
  -> lossless stream copy (concat demuxer, -c copy)
- everything else -> re-encode at visually lossless settings (NVENC constqp
  on the RTX GPU when available, libx264/libx265 with very low CRF otherwise),
  target parameters taken from the highest-resolution source clip.
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import threading
import uuid
from collections import deque
from pathlib import Path

from . import config, db, library

WATERMARK_DIR = config.LIBRARY_ROOT / ".config"
WATERMARK = WATERMARK_DIR / "watermark.png"
RENDER_TMP = config.LIBRARY_ROOT / ".render_tmp"
CREATE_NO_WINDOW = 0x08000000

_jobs: dict[str, dict] = {}
_order: list[str] = []
_lock = threading.Lock()
_wake = threading.Event()
_started = False
_current_proc: subprocess.Popen | None = None
_current_job: str | None = None
_nvenc: bool | None = None


# ---- probing / compatibility ---------------------------------------------------


def _probe(path: Path) -> dict:
    out = subprocess.run(
        [config.FFPROBE, "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, timeout=60, creationflags=CREATE_NO_WINDOW)
    data = json.loads(out.stdout or "{}")
    v = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
    a = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    num, _, den = str(v.get("r_frame_rate") or "0/1").partition("/")
    try:
        fps = round(float(num) / float(den or 1), 3)
    except (ValueError, ZeroDivisionError):
        fps = 0
    return {
        "codec": v.get("codec_name"), "width": v.get("width"), "height": v.get("height"),
        "fps": fps, "pix_fmt": v.get("pix_fmt"),
        "duration": float((data.get("format") or {}).get("duration") or 0),
        "has_audio": a is not None,
        "audio_codec": (a or {}).get("codec_name"),
    }


def check_clips(asset_ids: list[int]) -> dict:
    """Probe all clips and decide whether lossless stream-copy concat is possible."""
    clips = []
    for aid in asset_ids:
        asset = db.get(int(aid))
        if not asset:
            raise ValueError(f"asset {aid} not found")
        if asset.get("media_type") != "video":
            raise ValueError(f'"{asset.get("original_name")}" is not a video')
        p = library.abs_path(asset)
        if not p.exists():
            raise ValueError(f"file missing for asset {aid}: {p}")
        info = _probe(p)
        info.update(id=asset["id"], name=asset["original_name"], path=str(p))
        clips.append(info)
    if not clips:
        raise ValueError("no clips given")
    vkeys = ("codec", "width", "height", "fps", "pix_fmt")
    keys = vkeys + ("has_audio", "audio_codec")
    compatible = all(all(c[k] == clips[0][k] for k in keys) for c in clips)
    # with the audio replaced by silence only the video streams have to match
    video_compatible = all(all(c[k] == clips[0][k] for k in vkeys) for c in clips)
    best = max(clips, key=lambda c: (c["width"] or 0) * (c["height"] or 0))
    return {
        "clips": [{k: c[k] for k in ("id", "name", "codec", "width", "height", "fps",
                                     "duration", "has_audio")} for c in clips],
        "compatible": compatible,
        "video_compatible": video_compatible,
        "target": {
            "codec": best["codec"] if best["codec"] in ("h264", "hevc") else "h264",
            "width": best["width"], "height": best["height"],
            "fps": best["fps"] or 24,
            "audio": all(c["has_audio"] for c in clips),
        },
        "total_duration": round(sum(c["duration"] for c in clips), 2),
        "_paths": [c["path"] for c in clips],
        "_durations": [c["duration"] for c in clips],
    }


MIN_CLIP = 0.2   # seconds that must remain of a clip after trimming


def normalize_trims(trims, n: int) -> list[tuple[float, float]]:
    """[(cut_from_start, cut_from_end)] per clip, in seconds, aligned with the
    clip list. Accepts [start, end] pairs or {"start", "end"} dicts."""
    trims = trims or []
    out = []
    for i in range(n):
        t = trims[i] if i < len(trims) else None
        start = end = 0
        if isinstance(t, dict):
            start, end = t.get("start"), t.get("end")
        elif isinstance(t, (list, tuple)) and len(t) == 2:
            start, end = t
        try:
            start, end = max(0.0, float(start or 0)), max(0.0, float(end or 0))
        except (TypeError, ValueError):
            raise ValueError("trim values must be numbers (seconds)")
        out.append((round(start, 3), round(end, 3)))
    return out


def apply_trims(check: dict, trims: list[tuple[float, float]]) -> dict:
    """Attach trims + effective durations to a check_clips() result."""
    for c, (start, end) in zip(check["clips"], trims):
        dur = c["duration"] or 0
        if (start or end) and dur - start - end < MIN_CLIP:
            raise ValueError(f'cutting {start:g}s + {end:g}s leaves nothing of "{c["name"]}" '
                             f"({dur:.1f}s long)")
        c["trim_start"], c["trim_end"] = start, end
        c["eff_duration"] = round(dur - start - end, 3)
    check["trimmed"] = any(start or end for start, end in trims)
    check["total_duration"] = round(sum(c["eff_duration"] for c in check["clips"]), 2)
    return check


def nvenc_available() -> bool:
    """One-time runtime test encode with the SAME rate-control args real jobs
    use — an encoder listed by ffmpeg can still fail at runtime (driver,
    exhausted GPU sessions/VRAM while e.g. ComfyUI is generating)."""
    global _nvenc
    if _nvenc is None:
        try:
            r = subprocess.run(
                [config.FFMPEG, "-hide_banner", "-v", "error",
                 "-f", "lavfi", "-i", "color=black:s=1280x720:r=24:d=0.3",
                 "-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
                 "-rc", "vbr", "-cq", "21", "-b:v", "0", "-f", "null", "-"],
                capture_output=True, timeout=30, creationflags=CREATE_NO_WINDOW)
            _nvenc = r.returncode == 0
            if not _nvenc:
                print("[render] NVENC probe failed: "
                      + (r.stderr or b"").decode("utf-8", "replace")[-300:])
        except Exception:
            _nvenc = False
        print(f"[render] NVENC available: {_nvenc}")
    return _nvenc


# ---- watermark geometry --------------------------------------------------------

POSITIONS = {"tl", "tc", "tr", "ml", "c", "mr", "bl", "bc", "br"}


def _wm_geometry(vw: int, vh: int, wm: dict) -> dict:
    size_val = float(wm.get("size", 15))
    logo_w = int(vw * size_val / 100) if wm.get("size_unit", "%") == "%" else int(size_val)
    logo_w = max(8, min(logo_w, vw))
    opacity = max(0.0, min(1.0, float(wm.get("opacity", 80)) / 100))
    ox, oy = float(wm.get("offset_x", 3)), float(wm.get("offset_y", 3))
    if wm.get("offset_unit", "%") == "%":
        mx, my = int(vw * ox / 100), int(vh * oy / 100)
    else:
        mx, my = int(ox), int(oy)
    pos = wm.get("position", "br")
    if pos not in POSITIONS:
        pos = "br"
    col = "c" if pos in ("tc", "c", "bc") else ("l" if pos.endswith("l") else "r")
    row = "m" if pos in ("ml", "c", "mr") else ("t" if pos.startswith("t") else "b")
    x = {"l": str(mx), "c": "(W-w)/2", "r": f"W-w-{mx}"}[col]
    y = {"t": str(my), "m": "(H-h)/2", "b": f"H-h-{my}"}[row]
    return {"logo_w": logo_w, "opacity": opacity, "x": x, "y": y}


def _wm_filter(logo_label: str, base_label: str, out_label: str, geo: dict) -> str:
    return (f"[{logo_label}]format=rgba,colorchannelmixer=aa={geo['opacity']:.3f},"
            f"scale={geo['logo_w']}:-1[gvwm];"
            f"[{base_label}][gvwm]overlay={geo['x']}:{geo['y']}[{out_label}]")


# ---- ffmpeg command builders ----------------------------------------------------


# Quality presets. "standard" matches what typical ffmpeg CLI workflows
# produce (small files, visually clean) — the old always-crf-5 behavior made
# outputs ~10x larger than the source and is now the opt-in "max" preset.
QUALITY = {
    "standard": {"x264": "20", "x265": "22", "cq": "21"},
    "high":     {"x264": "17", "x265": "19", "cq": "17"},
    "max":      {"x264": "5",  "x265": "8",  "cq": "10"},
}


def _encoder_args(codec: str, use_nvenc: bool, quality: str) -> list[str]:
    q = QUALITY.get(quality) or QUALITY["standard"]
    if codec == "hevc":
        return (["-c:v", "hevc_nvenc", "-preset", "p5", "-tune", "hq",
                 "-rc", "vbr", "-cq", q["cq"], "-b:v", "0"]
                if use_nvenc else ["-c:v", "libx265", "-crf", q["x265"], "-preset", "medium"])
    return (["-c:v", "h264_nvenc", "-preset", "p5", "-tune", "hq",
             "-rc", "vbr", "-cq", q["cq"], "-b:v", "0"]
            if use_nvenc else ["-c:v", "libx264", "-crf", q["x264"], "-preset", "medium"])


def _cmd_copy_concat(paths: list[str], list_file: Path, out: Path,
                     has_audio: bool) -> list[str]:
    """Lossless concat. Sources without audio get a silent AAC track added
    (web players choke on audio-less mp4s) — the video stream stays -c copy."""
    lines = "\n".join("file '" + p.replace("'", "'\\''") + "'" for p in paths)
    list_file.write_text(lines, encoding="utf-8")
    cmd = [config.FFMPEG, "-y", "-v", "error", "-progress", "pipe:1", "-nostats",
           "-f", "concat", "-safe", "0", "-i", str(list_file)]
    if has_audio:
        cmd += ["-c", "copy"]
    else:
        cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
                "-map", "0:v", "-c:v", "copy",
                "-map", "1:a", "-c:a", "aac", "-b:a", "128k", "-shortest"]
    # -map_metadata -1: never leak embedded ComfyUI prompts/workflows into
    # published release files
    cmd += ["-map_metadata", "-1", "-movflags", "+faststart", str(out)]
    return cmd


_AFMT = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


def _cmd_encode(check: dict, wm: dict | None, out: Path,
                quality: str = "standard", mute: bool = False) -> list[str]:
    """Concat (n>=2) and/or watermark re-encode in one filter graph.

    The output ALWAYS carries an audio track (web-player compatibility):
    clips with sound keep it (re-encoded to AAC), silent clips contribute
    exactly-fitting anullsrc silence. Trims use the trim/atrim filters, which
    cut on the frames' own timestamps — input seeking (-ss/-t) was measured to
    let one extra frame through at the end. mute=True replaces every clip's
    sound with silence."""
    paths, clips, target = check["_paths"], check["clips"], check["target"]
    n = len(paths)
    w, h, fps = target["width"], target["height"], target["fps"]
    dur_of = lambda c: max(c.get("eff_duration", c["duration"]) or 0.1, 0.1)
    sound = lambda c: c["has_audio"] and not mute

    def cut(c, audio=False):
        """'trim=…,setpts=…,' prefix for a trimmed clip, '' otherwise."""
        if not (c.get("trim_start") or c.get("trim_end")):
            return ""
        start, end = c.get("trim_start") or 0, (c.get("trim_start") or 0) + c["eff_duration"]
        return (f"atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS," if audio
                else f"trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,")

    cmd = [config.FFMPEG, "-y", "-v", "error", "-progress", "pipe:1", "-nostats"]
    for p in paths:
        cmd += ["-i", p]
    next_idx = n
    wm_idx = None
    if wm:
        cmd += ["-i", str(WATERMARK)]
        wm_idx = next_idx
        next_idx += 1
    silence_idx: dict[int, int] = {}
    for i, c in enumerate(clips):
        if not sound(c):
            cmd += ["-f", "lavfi", "-t", f"{dur_of(c):.3f}",
                    "-i", "anullsrc=r=48000:cl=stereo"]
            silence_idx[i] = next_idx
            next_idx += 1

    filters, vlabel = [], "vcat"
    audio_tail: list[str]
    if n > 1:
        pairs = ""
        for i, c in enumerate(clips):
            dur = dur_of(c)
            filters.append(
                f"[{i}:v]{cut(c)}scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},setsar=1,"
                f"format=yuv420p,setpts=PTS-STARTPTS[v{i}]")
            if sound(c):
                filters.append(f"[{i}:a]{cut(c, audio=True)}aresample=async=1:first_pts=0,{_AFMT},"
                               f"apad,atrim=0:{dur:.3f},asetpts=PTS-STARTPTS[a{i}]")
            else:
                filters.append(f"[{silence_idx[i]}:a]{_AFMT}[a{i}]")
            pairs += f"[v{i}][a{i}]"
        filters.append(f"{pairs}concat=n={n}:v=1:a=1[vcat][acat]")
        audio_tail = ["-map", "[acat]", "-c:a", "aac", "-b:a", "192k"]
    else:
        filters.append(f"[0:v]{cut(clips[0])}format=yuv420p[vcat]")
        if sound(clips[0]) and cut(clips[0], audio=True):
            filters.append(f"[0:a]{cut(clips[0], audio=True).rstrip(',')}[acut]")
            audio_tail = ["-map", "[acut]", "-c:a", "aac", "-b:a", "192k"]
        elif sound(clips[0]):
            audio_tail = ["-map", "0:a", "-c:a", "aac", "-b:a", "192k"]
        else:
            audio_tail = ["-map", f"{silence_idx[0]}:a", "-c:a", "aac", "-b:a", "128k"]
    if wm:
        geo = _wm_geometry(w, h, wm)
        filters.append(_wm_filter(f"{wm_idx}:v", vlabel, "vout", geo))
        vlabel = "vout"
    cmd += ["-filter_complex", ";".join(filters), "-map", f"[{vlabel}]"]
    cmd += audio_tail
    cmd += _encoder_args(target["codec"], nvenc_available(), quality)
    cmd += ["-pix_fmt", "yuv420p", "-map_metadata", "-1",
            "-movflags", "+faststart", str(out)]
    return cmd


# ---- preview -------------------------------------------------------------------


def preview_frame(asset_id: int, wm: dict | None, trim_start: float = 0.0) -> bytes:
    """One frame from the clip with the exact same watermark filter the real
    render will use — WYSIWYG by construction."""
    check = check_clips([asset_id])
    path = check["_paths"][0]
    info = check["clips"][0]
    start = max(0.0, min(float(trim_start or 0), max(0.0, (info["duration"] or 1) - 0.1)))
    ts = start + min(1.0, max(0.0, ((info["duration"] or 1) - start) / 3))
    RENDER_TMP.mkdir(parents=True, exist_ok=True)
    out = RENDER_TMP / f"preview_{uuid.uuid4().hex}.jpg"
    cmd = [config.FFMPEG, "-y", "-v", "error", "-ss", f"{ts:.2f}", "-i", path]
    if wm and wm.get("enabled") and WATERMARK.exists():
        geo = _wm_geometry(info["width"], info["height"], wm)
        cmd += ["-i", str(WATERMARK),
                "-filter_complex", _wm_filter("1:v", "0:v", "vout", geo),
                "-map", "[vout]"]
    cmd += ["-frames:v", "1", "-q:v", "3", str(out)]
    r = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=CREATE_NO_WINDOW)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError((r.stderr or b"").decode("utf-8", "replace")[-400:] or "preview failed")
    data = out.read_bytes()
    out.unlink(missing_ok=True)
    return data


# ---- queue ---------------------------------------------------------------------


def submit(payload: dict) -> list[dict]:
    """Create job(s). mode 'concat' = one job for all clips; mode 'each' =
    one watermark job per clip (each stays its own output file)."""
    clip_ids = [int(i) for i in payload.get("clip_ids") or []]
    if not clip_ids:
        raise ValueError("no clips selected")
    mode = payload.get("mode", "concat")
    wm = payload.get("watermark") or None
    if wm and not wm.get("enabled"):
        wm = None
    if wm and not WATERMARK.exists():
        raise ValueError("no watermark logo uploaded yet")
    if mode == "concat" and len(clip_ids) == 1:
        mode = "each"                      # single clip: concat is meaningless
    audio = "silent" if payload.get("audio") == "silent" else "keep"
    trims = normalize_trims(payload.get("trims"), len(clip_ids))
    apply_trims(check_clips(clip_ids), trims)      # validate early -> HTTP 400
    if mode == "each" and not wm and audio == "keep":
        untouched = [cid for cid, t in zip(clip_ids, trims) if not any(t)]
        if untouched:
            name = (db.get(untouched[0]) or {}).get("original_name") or untouched[0]
            raise ValueError(f'nothing to do for "{name}": enable the watermark, '
                             "trim the clip or replace its audio with silence")
    out = payload.get("output") or {}
    if out.get("type") == "project":
        if not str(out.get("project") or "").strip():
            raise ValueError("output project is required")
        if out.get("kind") not in ("release", "pre-release"):
            out["kind"] = "release"
    elif out.get("type") == "folder":
        if not str(out.get("folder") or "").strip():
            raise ValueError("output folder is required")
    else:
        raise ValueError("output.type must be 'project' or 'folder'")

    quality = payload.get("quality") if payload.get("quality") in QUALITY else "standard"
    groups = ([(clip_ids, trims)] if mode == "concat"
              else [([cid], [t]) for cid, t in zip(clip_ids, trims)])
    created = []
    for group, group_trims in groups:
        job = {
            "id": uuid.uuid4().hex[:10], "clips": group, "mode": mode,
            "trims": group_trims, "audio": audio,
            "watermark": wm, "output": out, "quality": quality,
            "clip_names": [(db.get(c) or {}).get("original_name") for c in group],
            "status": "queued", "progress": 0, "error": None, "results": [],
            "note": None,
            "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
        }
        with _lock:
            _jobs[job["id"]] = job
            _order.append(job["id"])
        created.append(job)
    start()
    _wake.set()
    return created


def jobs() -> list[dict]:
    with _lock:
        return [dict(_jobs[j]) for j in reversed(_order)]


def cancel(job_id: str) -> dict:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            raise LookupError("job not found")
        if job["status"] == "queued":
            job["status"] = "cancelled"
        elif job["status"] == "running":
            job["cancel"] = True
            if _current_job == job_id and _current_proc and _current_proc.poll() is None:
                _current_proc.terminate()
        return dict(job)


def remove(job_id: str) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if job and job["status"] in ("done", "error", "cancelled"):
            _jobs.pop(job_id, None)
            if job_id in _order:
                _order.remove(job_id)


# ---- worker --------------------------------------------------------------------


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    threading.Thread(target=_worker, daemon=True, name="gv-render-worker").start()


def _worker() -> None:
    while True:
        _wake.wait(timeout=10)
        _wake.clear()
        while True:
            with _lock:
                jid = next((j for j in _order if _jobs[j]["status"] == "queued"), None)
                if jid:
                    _jobs[jid]["status"] = "running"
            if not jid:
                break
            job = _jobs[jid]
            try:
                _run_job(job)
                if job.get("cancel"):
                    job["status"] = "cancelled"
                else:
                    job["status"] = "done"
                    job["progress"] = 100
            except Exception as e:
                job["status"] = "cancelled" if job.get("cancel") else "error"
                job["error"] = str(e)[:600]
                print(f"[render] job {jid} failed: {e}")


def _run_ffmpeg(job: dict, cmd: list[str], total_duration: float) -> None:
    global _current_proc, _current_job
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace",
                            creationflags=CREATE_NO_WINDOW)
    with _lock:
        _current_proc, _current_job = proc, job["id"]
    tail: deque[str] = deque(maxlen=40)
    for line in proc.stdout:
        line = line.strip()
        if line:
            tail.append(line)
        if line.startswith("out_time_ms=") and total_duration > 0:
            try:
                sec = int(line.split("=", 1)[1]) / 1_000_000
                job["progress"] = max(job["progress"], min(99, int(sec / total_duration * 100)))
            except ValueError:
                pass
    rc = proc.wait()
    with _lock:
        _current_proc, _current_job = None, None
    if job.get("cancel"):
        raise RuntimeError("cancelled")
    if rc != 0:
        raise RuntimeError("ffmpeg failed: " + " | ".join(list(tail)[-6:]))


def _run_job(job: dict) -> None:
    global _nvenc
    check = apply_trims(check_clips(job["clips"]),
                        normalize_trims(job.get("trims"), len(job["clips"])))
    paths, total = check["_paths"], check["total_duration"]
    wm = job["watermark"]
    mute = job.get("audio") == "silent"
    RENDER_TMP.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(paths[0]).stem if len(paths) == 1 else "concat"
    # job id in the name: two jobs finishing in the same second must never
    # produce colliding filenames (the later move would silently overwrite)
    out = RENDER_TMP / f"gvrelease_{stamp}_{job['id'][:6]}_{library.slug(base, 40)}.mp4"

    # stream copy is only possible without watermark and trims (cuts must be
    # frame-accurate); replacing the audio doesn't touch the video stream
    lossless = not wm and not check["trimmed"] and (
        len(paths) == 1 or (check["video_compatible"] if mute else check["compatible"]))
    if lossless:
        has_audio = check["clips"][0]["has_audio"] and not mute   # compatible => uniform
        job["note"] = ("lossless stream copy" + (" (identical codec/resolution/fps)" if len(paths) > 1 else "")
                       + (" + audio replaced by silence" if mute
                          else "" if has_audio else " + silent audio track added"))
        list_file = RENDER_TMP / f"list_{job['id']}.txt"
        cmd = _cmd_copy_concat(paths, list_file, out, has_audio)
        try:
            _run_ffmpeg(job, cmd, total)
        finally:
            list_file.unlink(missing_ok=True)
    else:
        quality = job.get("quality", "standard")
        reasons = []
        if len(paths) > 1 and not (check["video_compatible"] if mute else check["compatible"]):
            reasons.append("clips differ in codec/resolution/fps/audio")
        if wm:
            reasons.append("watermark overlay")
        if check["trimmed"]:
            reasons.append("trimmed")
        job["note"] = (f"re-encode ({quality}): " + ", ".join(reasons)
                       + ("; audio replaced by silence" if mute else ""))
        cmd = _cmd_encode(check, wm, out, quality, mute)
        try:
            _run_ffmpeg(job, cmd, total)
        except RuntimeError as e:
            if job.get("cancel") or not nvenc_available():
                raise
            # NVENC accepted the probe but failed on this input (common cause:
            # GPU sessions/VRAM exhausted while ComfyUI renders) — fall back,
            # and SURFACE the real error so it can be diagnosed
            reason = str(e).replace("ffmpeg failed: ", "")[:160]
            print(f"[render] NVENC failed at runtime, CPU fallback. Cause: {e}")
            _nvenc = False
            job["note"] = (job["note"] or "") + f" — NVENC failed ({reason}), CPU fallback"
            job["progress"] = 0
            cmd = _cmd_encode(check, wm, out, quality, mute)
            _run_ffmpeg(job, cmd, total)
            _nvenc = None   # re-probe next job — the GPU may just have been busy

    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("render produced no output file")

    o = job["output"]
    if o["type"] == "folder":
        dest_dir = Path(str(o["folder"]))
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / out.name
        shutil.move(str(out), str(dest))
        job["results"].append({"path": str(dest)})
    else:
        asset = library.intake(out, original_name=out.name, hint="release-render", move=True)
        if not asset:
            raise RuntimeError("could not intake rendered file")
        asset = library.file_asset(
            asset["id"], project=o["project"], kind=o.get("kind", "release"),
            sub_project=o.get("sub_project"))
        asset = library.update_asset(asset["id"], {"meta": {"derived_from": job["clips"]}})
        job["results"].append({"asset_id": asset["id"], "rel_path": asset["rel_path"]})
