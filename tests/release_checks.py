"""Release module checks: trims (frame-exact), silent audio, lossless paths,
validation. Runs core_checks first (on import)."""
import json
import os
import shutil
import tempfile
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core_checks import LIB, req  # noqa: E402  (regression suite runs on import)

WORK = Path(os.environ.get("GV_TEST_WORK") or tempfile.mkdtemp(prefix="gvtest-"))
EXPORT = WORK / "release_exports"
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, text=True).stdout
    d = json.loads(out)
    v = next(s for s in d["streams"] if s["codec_type"] == "video")
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    return float(d["format"]["duration"]), v, a


def max_volume(path):
    err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-map", "0:a", "-af", "volumedetect", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    line = next((l for l in err.splitlines() if "max_volume" in l), "max_volume: -999 dB")
    return float(line.split("max_volume:")[1].split("dB")[0])


def video_hash(path):
    """MD5 over all DECODED video frames — identical pictures, i.e. no re-encode
    (a stream copy may re-embed the codec header in the first packet, so raw
    packet hashes are not a fair comparison)."""
    return subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v",
                           "-f", "hash", "-hash", "md5", "-"], capture_output=True, text=True).stdout.strip()


def render(payload):
    st, body, _ = req("POST", "/api/render/jobs", payload)
    if st != 200:
        return st, body, []
    ids = [j["id"] for j in body["jobs"]]
    for _ in range(180):
        jobs = {j["id"]: j for j in req("GET", "/api/render/jobs")[1]["jobs"]}
        if all(jobs[i]["status"] in ("done", "error", "cancelled") for i in ids):
            break
        time.sleep(1)
    return st, body, [jobs[i] for i in ids]


# two 10 s clips WITH sound (440 Hz tone), same codec/res/fps -> concat-compatible
src = WORK / "tone_src"
src.mkdir(parents=True, exist_ok=True)
for i, (pattern, freq) in enumerate([("testsrc2", 440), ("smptebars", 660)], 1):
    subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"{pattern}=s=640x360:r=24:d=10",
                    "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=10",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                    str(src / f"tone clip {i}.mp4")], check=True)
    shutil.copy2(src / f"tone clip {i}.mp4", LIB / "drop" / f"tone clip {i}.mp4")
for _ in range(30):
    inbox = req("GET", "/api/assets?status=inbox")[1]["assets"]
    tones = [a for a in inbox if a["original_name"].startswith("tone clip")]
    if len(tones) == 2:
        break
    time.sleep(1)
tones.sort(key=lambda a: a["original_name"])
t1, t2 = tones[0]["id"], tones[1]["id"]
silent_clips = [a for a in req("GET", "/api/assets?status=library")[1]["assets"] if a["media_type"] == "video"]
s1 = silent_clips[0]["id"]      # 12 s test clip without audio

shutil.rmtree(EXPORT, ignore_errors=True)
folder = {"type": "folder", "folder": str(EXPORT)}


def out_file(job):
    return Path(job["results"][0]["path"])


# 1) single clip, trim only (no watermark, no audio change) — used to be refused
st, body, jobs = render({"clip_ids": [s1], "mode": "each", "trims": [[2, 3]], "output": folder})
j = jobs[0] if jobs else {}
check("single clip trim-only accepted", st == 200 and j.get("status") == "done", (st, body, j.get("error")))
if j.get("status") == "done":
    dur, v, a = probe(out_file(j))
    check("trimmed length 12-2-3 = 7 s", abs(dur - 7.0) < 0.15, dur)
    check("trim note mentions re-encode", "trimmed" in (j.get("note") or ""), j.get("note"))
    check("output still has an audio track", a is not None)

# 2) single clip with sound, audio -> silence only: video stream copied
st, body, jobs = render({"clip_ids": [t1], "mode": "each", "audio": "silent", "output": folder})
j = jobs[0] if jobs else {}
check("silence-only render", st == 200 and j.get("status") == "done", (st, body, j.get("error")))
if j.get("status") == "done":
    dur, v, a = probe(out_file(j))
    _, sv, _ = probe(src / "tone clip 1.mp4")
    check("video stream copied losslessly (identical decoded frames)",
          "lossless" in j["note"] and video_hash(out_file(j)) == video_hash(src / "tone clip 1.mp4"), j["note"])
    check("audio track present and silent", a is not None and max_volume(out_file(j)) < -80, max_volume(out_file(j)))
    check("length unchanged (10 s)", abs(dur - 10) < 0.15, dur)

# 3) concat of the two tone clips with trims, keep audio
st, body, jobs = render({"clip_ids": [t1, t2], "mode": "concat", "trims": [[1, 0], [0, 2.5]], "output": folder})
j = jobs[0] if jobs else {}
check("concat with trims", st == 200 and j.get("status") == "done", (st, body, j.get("error")))
if j.get("status") == "done":
    dur, v, a = probe(out_file(j))
    check("concat length 9 + 7.5 = 16.5 s", abs(dur - 16.5) < 0.2, dur)
    check("original sound kept", a is not None and max_volume(out_file(j)) > -30, max_volume(out_file(j)))

# 4) concat, no trims, silence -> lossless video copy + silent audio
st, body, jobs = render({"clip_ids": [t1, t2], "mode": "concat", "audio": "silent", "output": folder})
j = jobs[0] if jobs else {}
check("lossless concat with silence", st == 200 and j.get("status") == "done" and "lossless" in (j.get("note") or ""), (st, j.get("note"), j.get("error")))
if j.get("status") == "done":
    dur, v, a = probe(out_file(j))
    check("concat silent: 20 s, silent track", abs(dur - 20) < 0.2 and a is not None and max_volume(out_file(j)) < -80, (dur, max_volume(out_file(j))))

# 5) concat with trims + silence + watermark (if a logo exists) through the full filter graph
wm_exists = req("GET", "/api/render/watermark")[1].get("exists")
wm = {"enabled": True, "position": "br", "size": 15, "size_unit": "%", "opacity": 80,
      "offset_x": 3, "offset_y": 3, "offset_unit": "%"} if wm_exists else None
st, body, jobs = render({"clip_ids": [t1, s1], "mode": "concat", "trims": [[0, 4], [3, 0]], "audio": "silent",
                         "watermark": wm, "output": folder})
j = jobs[0] if jobs else {}
check("mixed concat (sound + silent clip), trims, silence", st == 200 and j.get("status") == "done", (st, body, j.get("error")))
if j.get("status") == "done":
    dur, v, a = probe(out_file(j))
    check("mixed length 6 + 9 = 15 s, silent", abs(dur - 15) < 0.25 and max_volume(out_file(j)) < -80, (dur, max_volume(out_file(j))))

# 5b) frame-exact cuts: values as the trim editor sets them for "visible frame
#     50 is the first kept, visible frame 228 the last kept" (24 fps, 12 s clip)
s1_asset = req("GET", f"/api/assets/{s1}")[1]
s1_path = LIB / s1_asset["rel_path"]
st, body, jobs = render({"clip_ids": [s1], "mode": "each", "trims": [[2.062, 2.479]], "output": folder})
j = jobs[0] if jobs else {}
check("frame-exact render done", st == 200 and j.get("status") == "done", (st, body, j.get("error")))
if j.get("status") == "done":
    o = out_file(j)
    n = int(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries",
                            "stream=nb_read_frames", "-of", "csv=p=0", str(o)], capture_output=True, text=True).stdout)
    check("exactly 179 frames (source frames 50..228)", n == 179, n)

    def psnr(out_idx, src_idx):
        err = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(o), "-i", str(s1_path), "-filter_complex",
                              f"[0:v]select=eq(n\\,{out_idx}),setpts=PTS-STARTPTS[a];[1:v]select=eq(n\\,{src_idx}),setpts=PTS-STARTPTS[b];[a][b]psnr",
                              "-frames:v", "1", "-f", "null", "-"], capture_output=True, text=True).stderr
        v = err.split("average:")[1].split()[0]
        return 99.0 if v == "inf" else float(v)
    first = {k: psnr(0, k) for k in (49, 50, 51)}
    last = {k: psnr(n - 1, k) for k in (227, 228, 229)}
    check("first kept frame is source frame 50", max(first, key=first.get) == 50, first)
    check("last kept frame is source frame 228", max(last, key=last.get) == 228, last)

# 6) validation
st, body, _ = req("POST", "/api/render/jobs", {"clip_ids": [s1], "mode": "each", "trims": [[6, 6]], "output": folder})
check("trim longer than clip -> 400", st == 400 and "leaves nothing" in body.get("detail", ""), body)
st, body, _ = req("POST", "/api/render/jobs", {"clip_ids": [s1], "mode": "each", "output": folder})
check("single clip with nothing to do -> 400", st == 400 and "nothing to do" in body.get("detail", ""), body)
st, body, _ = req("POST", "/api/render/jobs", {"clip_ids": [s1], "mode": "each", "trims": [["x", 0]], "output": folder})
check("non-numeric trim -> 400", st == 400, body)
st, raw, _ = req("POST", "/api/render/preview", {"clip_id": t1, "watermark": None, "trim_start": 8})
check("preview frame inside trimmed range", st == 200 and isinstance(raw, bytes) and raw[:2] == b"\xff\xd8", st)

failed = [n for n, ok in results if not ok]
print(f"\nrelease trim/silence: {len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed else 0)
