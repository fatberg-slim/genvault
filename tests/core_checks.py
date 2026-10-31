"""Core checks: import, media serving (range/ETag/traversal), filing/moving/
trashing while a file is being streamed, sidecars. Run via tests/run_tests.py,
which provides a throwaway server + library (GV_TEST_PORT / GV_TEST_LIB)."""
import http.client
import json
import os
import sys
import time
from pathlib import Path

HOST, PORT = "127.0.0.1", int(os.environ.get("GV_TEST_PORT", "8099"))
if not os.environ.get("GV_TEST_LIB"):
    sys.exit("GV_TEST_LIB is not set - run the checks via tests/run_tests.py")
LIB = Path(os.environ["GV_TEST_LIB"])
results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def req(method, path, body=None, headers=None):
    c = http.client.HTTPConnection(HOST, PORT, timeout=30)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    raw = r.read()
    c.close()
    try:
        return r.status, json.loads(raw), r
    except Exception:
        return r.status, raw, r


def open_stream(url):
    """Start a range request and read only 64 KB — like a paused <video>."""
    c = http.client.HTTPConnection(HOST, PORT, timeout=30)
    c.request("GET", url, headers={"Range": "bytes=0-"})
    r = c.getresponse()
    r.read(64 * 1024)
    return c, r


# wait for the watcher to import the drop folder
for _ in range(30):
    _, d, _ = req("GET", "/api/assets?status=inbox")
    if len(d["assets"]) >= 11:
        break
    time.sleep(1)
inbox = d["assets"]
check("watcher imported all 11 test files", len(inbox) == 11, len(inbox))
vids = [a for a in inbox if a["media_type"] == "video"]
check("ComfyUI metadata parsed", vids and vids[0]["meta"].get("positive_prompt", "").startswith("a test pattern"))

# ---- /lib serving ----------------------------------------------------------------
v = vids[0]
fpath = LIB / v["rel_path"]
blob = fpath.read_bytes()
st, body, r = req("GET", v["media_url"], headers={"Range": "bytes=100-199"})
check("range 206 + exact bytes", st == 206 and body == blob[100:200], st)
check("content-range header", r.getheader("Content-Range") == f"bytes 100-199/{len(blob)}")
st, body, r = req("GET", v["media_url"], headers={"Range": "bytes=-50"})
check("suffix range", st == 206 and body == blob[-50:])
st, body, r = req("GET", v["media_url"])
check("full GET 200 + content", st == 200 and body == blob and r.getheader("Content-Type") == "video/mp4")
etag = r.getheader("ETag")
st, _, _ = req("GET", v["media_url"], headers={"If-None-Match": etag})
check("304 on matching ETag", st == 304)
st, body, r = req("HEAD", v["media_url"])
check("HEAD ok", st == 200 and r.getheader("Content-Length") == str(len(blob)))
st, _, _ = req("GET", v["media_url"], headers={"Range": f"bytes={len(blob) + 10}-"})
check("416 past end", st == 416)
st, _, _ = req("GET", "/lib/..%2F..%2Fgenvault%2Fconfig.json")
check("path traversal blocked", st == 404, st)
st, _, _ = req("GET", "/lib/does/not/exist.mp4")
check("missing file 404", st == 404)
if inbox[0].get("thumb_url"):
    st, body, r = req("GET", inbox[0]["thumb_url"])
    check("thumbnail served", st == 200 and r.getheader("Content-Type") == "image/jpeg")

# ---- the original bug: file while streaming ------------------------------------
conn, resp = open_stream(v["media_url"])
st, body, _ = req("POST", f"/api/assets/{v['id']}/file", {"project": "Test Project", "kind": "clip"})
check("filing a file that is being streamed (was WinError 32)", st == 200, body)
if st == 200:
    rest = resp.read()                       # stream continues from the moved file
    check("open stream still delivers the full file after the move",
          64 * 1024 + len(rest) == len(blob), 64 * 1024 + len(rest))
conn.close()
check("no duplicate left in the inbox", not fpath.exists())
check("file arrived in library", (LIB / "library/Test Project/clip" / v["file_name"]).exists())

# bulk filing while several are streamed
rest_vids = vids[1:4]
streams = [open_stream(a["media_url"]) for a in rest_vids]
st, body, _ = req("POST", "/api/assets/file-bulk",
                  {"ids": [a["id"] for a in rest_vids], "project": "Test Project", "sub_project": "Sub A", "kind": "clip"})
check("bulk filing while streaming", st == 200 and len(body["filed"]) == 3 and not body["errors"], body.get("errors") if isinstance(body, dict) else body)
for c, _ in streams:
    c.close()

# library bulk move (new endpoint) while streaming
_, lib, _ = req("GET", "/api/assets?status=library")
ids = [a["id"] for a in lib["assets"]]
streams = [open_stream(a["media_url"]) for a in lib["assets"]]
st, body, _ = req("POST", "/api/assets/move-bulk", {"ids": ids, "sub_project": "Sub B"})
check("bulk move while streaming", st == 200 and len(body["moved"]) == len(ids) and not body["errors"], body)
for c, _ in streams:
    c.close()
_, lib, _ = req("GET", "/api/assets?status=library")
check("DB matches disk after move", all((LIB / a["rel_path"]).exists() and "/Sub B/" in a["rel_path"] for a in lib["assets"]))
st, body, _ = req("POST", "/api/assets/move-bulk", {"ids": ids})
check("bulk move without fields -> 400", st == 400)

# PATCH clearing kind of a library asset must not desync DB and disk
a0 = lib["assets"][0]
st, body, _ = req("PATCH", f"/api/assets/{a0['id']}", {"kind": ""})
_, after, _ = req("GET", f"/api/assets/{a0['id']}")
check("clearing kind rejected, DB unchanged", st == 400 and after["kind"] == "clip" and (LIB / after["rel_path"]).exists(), (st, body))

# trash + purge while streaming (were HTTP 500)
t = vids[4]
conn, _ = open_stream(t["media_url"])
st, body, _ = req("POST", f"/api/assets/{t['id']}/trash")
check("trash while streaming (was HTTP 500)", st == 200, body)
_, tt, _ = req("GET", f"/api/assets/{t['id']}")
st, body, _ = req("POST", f"/api/assets/{t['id']}/restore")
check("restore", st == 200 and body["status"] == "inbox")
req("POST", f"/api/assets/{t['id']}/trash")
_, tt, _ = req("GET", f"/api/assets/{t['id']}")
conn2, _ = open_stream(tt["media_url"])
st, body, _ = req("DELETE", f"/api/assets/{t['id']}")
check("delete forever while streaming (was HTTP 500)", st == 200, body)
check("file really gone", not (LIB / tt["rel_path"]).exists())
conn.close(); conn2.close()

# sidecars are written for every asset
_, allinbox, _ = req("GET", "/api/assets?status=inbox")
_, alllib, _ = req("GET", "/api/assets?status=library")
every = allinbox["assets"] + alllib["assets"]
check("sidecar exists for every asset", all((LIB / ".sidecars" / f"gv{a['id']:06d}.json").exists() for a in every))
orphans = [p.name for p in (LIB / "_inbox").iterdir() if p.is_file() and p.name not in {a["file_name"] for a in every}]
check("no orphan files in _inbox", not orphans, orphans)

failed = [n for n, ok in results if not ok]
print(f"\n{len(results) - len(failed)}/{len(results)} passed")
if __name__ == "__main__":
    sys.exit(1 if failed else 0)
REGRESSION_FAILED = failed
