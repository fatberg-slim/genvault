"""Library checks: project rename (incl. case-only + merge) and bulk
trash/restore/purge. Runs core_checks first (on import)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from core_checks import HOST, PORT, LIB, REGRESSION_FAILED, req, open_stream  # noqa: E402  (runs the regression suite on import)

results = []


def check(name, cond, info=""):
    results.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{info}]" if info and not cond else ""))


def lib_assets():
    return req("GET", "/api/assets?status=library")[1]["assets"]


def files_on_disk_match(assets):
    return all((LIB / a["rel_path"]).exists() for a in assets)


# fresh state from test_dev: library has 4 clips in "Test Project/Sub B/clip", inbox has the rest
_, inbox, _ = req("GET", "/api/assets?status=inbox")
ids = [a["id"] for a in inbox["assets"]]
st, body, _ = req("POST", "/api/assets/file-bulk", {"ids": ids, "project": "Old Name", "sub_project": "S1", "kind": "image"})
check("setup: filed inbox into 'Old Name'", st == 200 and not body["errors"], body)

# trash one of them so the rename must also fix its restore target
victim = body["filed"][0]
req("POST", f"/api/assets/{victim['id']}/trash")

# ---- rename while one file is being streamed ------------------------------------
streamed = body["filed"][1]
conn, _ = open_stream(streamed["media_url"])
st, r, _ = req("POST", "/api/projects/rename", {"old": "Old Name", "new": "New Name"})
conn.close()
check("rename project (incl. a streamed file)", st == 200 and r["renamed"] == len(ids) and not r["errors"], (st, r))
la = lib_assets()
check("no asset keeps the old project", not any(a["project"] == "Old Name" for a in la))
moved = [a for a in la if a["project"] == "New Name"]
check("files moved into New Name folder", moved and all(a["rel_path"].startswith("library/New Name/S1/image/") for a in moved))
check("DB matches disk", files_on_disk_match(la))
check("old project folder removed", not (LIB / "library/Old Name").exists())
_, v, _ = req("GET", f"/api/assets/{victim['id']}")
check("trashed file's restore target updated", v["project"] == "New Name" and v["trash_info"]["orig_rel_path"].startswith("library/New Name/"), v.get("trash_info"))
st, v, _ = req("POST", f"/api/assets/{victim['id']}/restore")
check("restore lands in renamed project", st == 200 and v["rel_path"].startswith("library/New Name/") and (LIB / v["rel_path"]).exists())
sc = json.loads((LIB / ".sidecars" / f"gv{victim['id']:06d}.json").read_text(encoding="utf-8"))
check("sidecar updated", sc["project"] == "New Name")

# case-only rename
st, r, _ = req("POST", "/api/projects/rename", {"old": "New Name", "new": "new name"})
la = lib_assets()
real = {p.name for p in (LIB / "library").iterdir()}
check("case-only rename", st == 200 and not r["errors"] and "new name" in real and "New Name" not in real, (st, r, real))
check("DB matches disk after case rename", files_on_disk_match(la) and all(a["rel_path"].startswith("library/new name/") for a in la if a["project"] == "new name"))

# merge into existing project
st, r, _ = req("POST", "/api/projects/rename", {"old": "new name", "new": "Test Project"})
la = lib_assets()
check("merge into existing project", st == 200 and not r["errors"] and {a["project"] for a in la} == {"Test Project"}, (st, r))
check("DB matches disk after merge", files_on_disk_match(la))

# validation
check("rename unknown project -> 404", req("POST", "/api/projects/rename", {"old": "nope", "new": "x"})[0] == 404)
check("rename to empty -> 400", req("POST", "/api/projects/rename", {"old": "Test Project", "new": "  "})[0] == 400)
check("rename to same -> 400", req("POST", "/api/projects/rename", {"old": "Test Project", "new": "Test Project"})[0] == 400)

# ---- bulk trash / restore / purge -------------------------------------------------
la = lib_assets()
three = [a["id"] for a in la[:3]]
streams = [open_stream(a["media_url"]) for a in la[:3]]
st, r, _ = req("POST", "/api/assets/bulk/trash", {"ids": three})
for c, _ in streams:
    c.close()
check("bulk trash (while streaming)", st == 200 and sorted(r["done"]) == sorted(three) and not r["errors"], r)
_, tr, _ = req("GET", "/api/assets?status=trash")
check("all three in trash", {a["id"] for a in tr["assets"]} >= set(three))
st, r, _ = req("POST", "/api/assets/bulk/restore", {"ids": three[:2]})
check("bulk restore", st == 200 and sorted(r["done"]) == sorted(three[:2]))
check("restored files on disk", files_on_disk_match(lib_assets()))
gone = three[2]
_, g, _ = req("GET", f"/api/assets/{gone}")
st, r, _ = req("POST", "/api/assets/bulk/purge", {"ids": [gone]})
check("bulk purge", st == 200 and r["done"] == [gone] and not (LIB / g["rel_path"]).exists())
st, r, _ = req("POST", "/api/assets/bulk/purge", {"ids": [three[0]]})
check("purge of a non-trashed file is refused per file", st == 200 and not r["done"] and r["errors"], r)
check("unknown bulk action -> 404", req("POST", "/api/assets/bulk/explode", {"ids": [1]})[0] == 404)

failed = [n for n, ok in results if not ok]
print(f"\nfeatures: {len(results) - len(failed)}/{len(results)} passed")
sys.exit(1 if failed or REGRESSION_FAILED else 0)
