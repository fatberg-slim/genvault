"""Feed the generated demo media into a running demo instance and organize it.
Usage: demo_organize.py <media_dir> <library_root>   (demo server on port 8079)"""
import datetime
import json
import os
import random
import shutil
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = "http://127.0.0.1:8079"
MEDIA = Path(sys.argv[1])
LIB = Path(sys.argv[2])
r = random.Random(7)


def call(method, path, body=None, raw=None, ctype="application/json"):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.loads(resp.read() or b"null")


manifest = json.loads((MEDIA / "manifest.json").read_text(encoding="utf-8"))

# 1) believable creation dates (file mtime -> "Created"), spread over the last months
start = datetime.datetime(2026, 7, 2, 10, 0)
for i, m in enumerate(manifest):
    ts = (start + datetime.timedelta(days=i * 2.9, hours=r.randint(0, 11), minutes=r.randint(0, 59))).timestamp()
    os.utime(MEDIA / m["file"], (ts, ts))

# 2) import through the drop folder (copy keeps the mtime), oldest first
for m in manifest:
    shutil.copy2(MEDIA / m["file"], LIB / "drop" / m["file"])
for _ in range(90):
    inbox = call("GET", "/api/assets?status=inbox")["assets"]
    if len(inbox) >= len(manifest):
        break
    time.sleep(2)
by_name = {a["original_name"]: a for a in inbox}
print("imported", len(by_name))

CHARACTERS = {"Neon Drift": "Nova", "Coastal Dreams": "Marina", "Whispering Woods": "Rowan"}
TAGS = {"Neon Drift": ["synthwave", "retro"], "Coastal Dreams": ["ocean", "calm"],
        "Northern Lights": ["night", "sky"], "Whispering Woods": ["forest", "fog"], "Highlands": ["landscape"]}

# 3) file everything that has a project
for m in manifest:
    if not m.get("project"):
        continue
    a = by_name[m["file"]]
    proj = m["project"]
    call("POST", f"/api/assets/{a['id']}/file", {
        "project": proj, "sub_project": m.get("sub_project"), "kind": m["kind"],
        "character": CHARACTERS.get(proj) if m["kind"] != "sound" and r.random() < 0.6 else None,
        "tags": TAGS.get(proj, []) + (["wallpaper"] if m["kind"] == "image" and r.random() < 0.4 else []),
    })
    patch = {"rating": r.choice([3, 4, 4, 5, 5, 0]), "favorite": r.random() < 0.3}
    if m.get("notes"):
        patch["meta"] = {"notes": m["notes"]}
    call("PATCH", f"/api/assets/{a['id']}", patch)

# 4) trash the rejects
for m in manifest:
    if m.get("trash"):
        call("POST", f"/api/assets/{by_name[m['file']]['id']}/trash")

# 5) watermark logo
logo = Image.new("RGBA", (520, 140), (0, 0, 0, 0))
d = ImageDraw.Draw(logo)
font = ImageFont.truetype(r"C:\Windows\Fonts\segoeuib.ttf", 84)
d.ellipse([10, 30, 90, 110], outline=(255, 255, 255, 235), width=8)
d.polygon([(50, 48), (72, 70), (50, 92), (28, 70)], fill=(255, 255, 255, 235))
d.text((110, 18), "dreamloop", font=font, fill=(255, 255, 255, 235))
tmp = MEDIA / "_logo.png"
logo.save(tmp)
boundary = "----gvdemo"
body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"logo.png\"\r\n"
        "Content-Type: image/png\r\n\r\n").encode() + tmp.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
call("POST", "/api/render/watermark", raw=body, ctype=f"multipart/form-data; boundary={boundary}")
tmp.unlink()

# 6) two real releases through the render module
wm = {"enabled": True, "position": "br", "size": 18, "size_unit": "%", "opacity": 75,
      "offset_x": 3, "offset_y": 4, "offset_unit": "%"}
ids = lambda *names: [by_name[n]["id"] for n in names]
jobs = call("POST", "/api/render/jobs", {
    "clip_ids": ids("NeonDrift_00012.mp4", "NeonDrift_00015.mp4", "NeonDrift_ep2_00003.mp4"), "mode": "concat",
    "quality": "standard", "watermark": wm,
    "output": {"type": "project", "project": "Neon Drift", "sub_project": None, "kind": "release"}})["jobs"]
jobs += call("POST", "/api/render/jobs", {
    "clip_ids": ids("coast_moonlight_00009.mp4"), "mode": "each", "quality": "high", "watermark": wm,
    "output": {"type": "project", "project": "Coastal Dreams", "sub_project": "Moonlight", "kind": "release"}})["jobs"]
for _ in range(240):
    state = {j["id"]: j for j in call("GET", "/api/render/jobs")["jobs"]}
    if all(state[j["id"]]["status"] in ("done", "error", "cancelled") for j in jobs):
        break
    time.sleep(2)
for j in jobs:
    s = state[j["id"]]
    print("render", s["status"], s.get("note"), s.get("error") or "")

# 7) upload tracking on the releases
releases = [a for a in call("GET", "/api/assets?status=library&kind=release")["assets"]]
posted = [{"X": {"posted": True, "url": "https://example.com/x/status/1", "date": "2026-10-02"},
           "Civitai": {"posted": True, "url": "https://example.com/civitai/posts/1", "date": "2026-10-03"}},
          {"Civitai": {"posted": True, "url": "https://example.com/civitai/posts/2", "date": "2026-10-11"}}]
for a, up in zip(releases, posted):
    call("PATCH", f"/api/assets/{a['id']}", {"uploads": up, "rating": 5, "favorite": True})

counts = call("GET", "/api/assets?status=library")["counts"]
print("counts", counts)
