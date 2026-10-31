"""genvault v2 API.

Filtering and facet counts run in Python over the full asset list — at
single-user library scale (thousands of rows) that is simpler and fast enough.
Facet counts are global per status so the sidebar numbers stay stable while
filters are active.
"""
from __future__ import annotations

import mimetypes
import re
from collections import Counter
from contextlib import asynccontextmanager
from email.utils import formatdate
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse

from . import config, db, fsutil, library, media, render, watcher

WEB_DIR = config.PROJECT_DIR / "web"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init()
    watcher.start()
    print(f"[genvault] v{config.APP_VERSION} — library: {config.LIBRARY_ROOT}")
    yield


app = FastAPI(title="genvault", version=config.APP_VERSION, lifespan=lifespan)


# ---- serialization -----------------------------------------------------------


def public(asset: dict, include_raw: bool = False) -> dict:
    d = dict(asset)
    meta = dict(d.get("meta") or {})
    if not include_raw:
        meta.pop("raw_graph", None)
    d["meta"] = meta
    rel = d.get("rel_path") or ""
    d["media_url"] = "/lib/" + quote(rel) if rel else None
    d["abs_path"] = str(config.LIBRARY_ROOT / rel) if rel else None
    thumb = media.thumb_path(int(d["id"]))
    d["thumb_url"] = f"/lib/.derivatives/{d['id']}/thumb.jpg" if thumb.exists() else None
    return d


# ---- filtering / facets ------------------------------------------------------


def _haystack(a: dict) -> str:
    m = a.get("meta") or {}
    parts = [
        a.get("original_name"), a.get("file_name"), a.get("project"),
        a.get("sub_project"), a.get("character"), a.get("kind"),
        " ".join(a.get("tags") or []),
        m.get("positive_prompt"), m.get("negative_prompt"), m.get("model"),
        " ".join(l.get("name", "") for l in (m.get("loras") or [])),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def _matches(a: dict, q: str | None, f: dict) -> bool:
    if f.get("project") and (a.get("project") or "") != f["project"]:
        return False
    if f.get("sub_project") and (a.get("sub_project") or "") != f["sub_project"]:
        return False
    if f.get("kind") and (a.get("kind") or "") != f["kind"]:
        return False
    if f.get("media_type") and a.get("media_type") != f["media_type"]:
        return False
    if f.get("character") and (a.get("character") or "") != f["character"]:
        return False
    if f.get("favorite") and not a.get("favorite"):
        return False
    if f.get("lora"):
        loras = [l.get("name", "") for l in ((a.get("meta") or {}).get("loras") or [])]
        if f["lora"] not in loras:
            return False
    if f.get("upload"):
        # upload tracking only applies to assets marked as release
        if (a.get("kind") or "") != "release":
            return False
        platform, _, want = f["upload"].partition(":")
        posted = bool(((a.get("uploads") or {}).get(platform) or {}).get("posted"))
        if want == "yes" and not posted:
            return False
        if want == "no" and posted:
            return False
    if q:
        hay = _haystack(a)
        if not all(term in hay for term in q.lower().split()):
            return False
    return True


def _facets(assets: list[dict]) -> dict:
    projects, subs, kinds, mtypes, chars, loras = (Counter() for _ in range(6))
    uploads = {p: {"yes": 0, "no": 0} for p in config.PLATFORMS}
    favorites = 0
    for a in assets:
        if a.get("project"):
            projects[a["project"]] += 1
        if a.get("sub_project"):
            subs[(a.get("project") or "", a["sub_project"])] += 1
        if a.get("kind"):
            kinds[a["kind"]] += 1
        if a.get("media_type"):
            mtypes[a["media_type"]] += 1
        if a.get("character"):
            chars[a["character"]] += 1
        for l in (a.get("meta") or {}).get("loras") or []:
            if l.get("name"):
                loras[l["name"]] += 1
        if a.get("favorite"):
            favorites += 1
        # upload counts cover only releases — tracking other kinds is noise
        if a.get("kind") == "release":
            up = a.get("uploads") or {}
            for p in config.PLATFORMS:
                key = "yes" if (up.get(p) or {}).get("posted") else "no"
                uploads[p][key] += 1
    fmt = lambda c: [{"name": k, "count": v} for k, v in c.most_common()]
    return {
        "projects": fmt(projects),
        "sub_projects": [{"project": p, "name": s, "count": v} for (p, s), v in subs.most_common()],
        "kinds": fmt(kinds),
        "media_types": fmt(mtypes),
        "characters": fmt(chars),
        "loras": fmt(loras),
        "uploads": uploads,
        "favorites": favorites,
        "total": len(assets),
    }


def _sorted(assets: list[dict], sort: str) -> list[dict]:
    key_added = lambda a: a.get("added_at") or ""
    if sort == "created":
        return sorted(assets, key=lambda a: a.get("created_at") or "", reverse=True)
    if sort == "rating":
        return sorted(assets, key=lambda a: (a.get("rating") or 0, key_added(a)), reverse=True)
    if sort == "name":
        return sorted(assets, key=lambda a: (a.get("original_name") or "").lower())
    return sorted(assets, key=key_added, reverse=True)  # default: newest added


# ---- API ---------------------------------------------------------------------


@app.get("/api/health")
def health():
    return {"ok": True, "version": config.APP_VERSION}


@app.get("/api/config")
def get_config():
    return {
        "version": config.APP_VERSION,
        "port": config.PORT,
        "platforms": config.PLATFORMS,
        "kinds": config.KINDS,
        "library_root": str(config.LIBRARY_ROOT),
    }


@app.get("/api/assets")
def list_assets(status: str = "library", q: str | None = None,
                project: str | None = None, sub_project: str | None = None,
                kind: str | None = None, media_type: str | None = None,
                character: str | None = None, lora: str | None = None,
                favorite: int | None = None, upload: str | None = None,
                sort: str = "added"):
    all_of_status = db.all_assets(status)
    f = {"project": project, "sub_project": sub_project, "kind": kind,
         "media_type": media_type, "character": character, "lora": lora,
         "favorite": favorite, "upload": upload}
    hits = [a for a in all_of_status if _matches(a, q, f)]
    return {
        "assets": [public(a) for a in _sorted(hits, sort)],
        "facets": _facets(all_of_status if status == "library" else db.all_assets("library")),
        "counts": {
            "inbox": db.count("inbox"),
            "library": db.count("library"),
            "trash": db.count("trash"),
        },
        "total": len(hits),
    }


@app.get("/api/values")
def values():
    """Existing field values for autocomplete, most recently used first."""
    assets = sorted(db.all_assets(), key=lambda a: a.get("updated_at") or "", reverse=True)
    projects, subs, chars, tags = [], [], [], []
    for a in assets:
        p, s, c = a.get("project"), a.get("sub_project"), a.get("character")
        if p and p not in projects:
            projects.append(p)
        if s and {"project": p or "", "name": s} not in subs:
            subs.append({"project": p or "", "name": s})
        if c and c not in chars:
            chars.append(c)
        for t in a.get("tags") or []:
            if t not in tags:
                tags.append(t)
    used_kinds = [k for k in dict.fromkeys(a.get("kind") for a in assets) if k]
    kinds = list(dict.fromkeys([*config.KINDS, *used_kinds]))
    return {"projects": projects, "sub_projects": subs, "characters": chars,
            "tags": tags, "kinds": kinds}


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: int):
    asset = db.get(asset_id)
    if not asset:
        raise HTTPException(404, "asset not found")
    return public(asset, include_raw=True)


@app.patch("/api/assets/{asset_id}")
def patch_asset(asset_id: int, patch: dict = Body(...)):
    try:
        return public(library.update_asset(asset_id, patch))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/assets/{asset_id}/file")
def file_one(asset_id: int, body: dict = Body(...)):
    try:
        return public(library.file_asset(
            asset_id, project=body.get("project", ""), kind=body.get("kind", ""),
            sub_project=body.get("sub_project"), character=body.get("character"),
            tags=body.get("tags"),
        ))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))


@app.post("/api/assets/move-bulk")
def move_bulk(body: dict = Body(...)):
    """Library bulk move: apply the same project/sub_project/kind patch to
    many assets. Per-file errors are reported instead of failing the batch."""
    patch = {k: body[k] for k in ("project", "sub_project", "kind") if body.get(k)}
    if not patch:
        raise HTTPException(400, "set a project, sub-project or kind first")
    done, errors = [], []
    for asset_id in body.get("ids") or []:
        try:
            done.append(public(library.update_asset(int(asset_id), patch)))
        except Exception as e:
            name = (db.get(int(asset_id)) or {}).get("original_name") or asset_id
            errors.append({"id": asset_id, "name": name, "error": str(e)})
    return {"moved": done, "errors": errors}


_BULK_ACTIONS = {
    "trash": library.trash_asset,
    "restore": library.restore_asset,
    "purge": library.purge_asset,
}


@app.post("/api/assets/bulk/{action}")
def bulk_action(action: str, body: dict = Body(...)):
    """Trash / restore / permanently delete many assets; per-file errors are
    reported instead of failing the batch."""
    fn = _BULK_ACTIONS.get(action)
    if not fn:
        raise HTTPException(404, f"unknown bulk action: {action}")
    done, errors = [], []
    for asset_id in body.get("ids") or []:
        name = (db.get(int(asset_id)) or {}).get("original_name") or asset_id
        try:
            fn(int(asset_id))
            done.append(int(asset_id))
        except Exception as e:
            errors.append({"id": asset_id, "name": name, "error": str(e)})
    return {"done": done, "errors": errors}


@app.post("/api/projects/rename")
def rename_project(body: dict = Body(...)):
    try:
        r = library.rename_project(body.get("old", ""), body.get("new", ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"renamed": len(r["renamed"]), "errors": r["errors"]}


@app.post("/api/assets/file-bulk")
def file_bulk(body: dict = Body(...)):
    ids = body.get("ids") or []
    done, errors = [], []
    for asset_id in ids:
        try:
            done.append(public(library.file_asset(
                int(asset_id), project=body.get("project", ""), kind=body.get("kind", ""),
                sub_project=body.get("sub_project"), character=body.get("character"),
                tags=body.get("tags"),
            )))
        except Exception as e:
            name = (db.get(int(asset_id)) or {}).get("original_name") or asset_id
            errors.append({"id": asset_id, "name": name, "error": str(e)})
    return {"filed": done, "errors": errors}


@app.post("/api/assets/{asset_id}/trash")
def trash(asset_id: int):
    try:
        return public(library.trash_asset(asset_id))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except OSError as e:
        raise HTTPException(400, str(e))


@app.post("/api/assets/{asset_id}/restore")
def restore(asset_id: int):
    try:
        return public(library.restore_asset(asset_id))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except OSError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/assets/{asset_id}")
def purge(asset_id: int):
    try:
        library.purge_asset(asset_id)
    except (ValueError, OSError) as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/assets/{asset_id}/open-folder")
def open_folder(asset_id: int):
    try:
        return {"ok": True, "path": library.open_in_explorer(asset_id)}
    except LookupError as e:
        raise HTTPException(404, str(e))
    except FileNotFoundError as e:
        raise HTTPException(400, f"file not found: {e}")


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...)):
    """Browser drag&drop: the uploaded bytes are already a copy, so intake
    moves them from a temp staging dir into the inbox."""
    staging = config.LIBRARY_ROOT / ".upload_tmp"
    staging.mkdir(parents=True, exist_ok=True)
    imported, skipped, failed = [], [], []
    for uf in files:
        name = Path(uf.filename or "upload.bin").name
        if config.media_type_for(Path(name)) is None:
            skipped.append(name)
            continue
        tmp = staging / name
        i = 1
        while tmp.exists():
            tmp = staging / f"{Path(name).stem}_{i}{Path(name).suffix}"
            i += 1
        with tmp.open("wb") as out:
            while chunk := await uf.read(1024 * 1024):
                out.write(chunk)
        try:
            asset = library.intake(tmp, original_name=name, hint="browser-upload", move=True)
        except Exception as e:
            print(f"[upload] intake failed for {name}: {e}")
            failed.append({"name": name, "error": str(e)})
            tmp.unlink(missing_ok=True)
            continue
        if asset:
            imported.append(public(asset))
        else:
            skipped.append(name)
            tmp.unlink(missing_ok=True)
    return {"imported": imported, "skipped": skipped, "failed": failed}


@app.post("/api/backup")
def backup():
    return {"ok": True, "file": library.backup()}


@app.post("/api/rebuild")
def rebuild():
    return {"ok": True, "assets": db.rebuild_from_sidecars()}


# ---- release module (watermark + concat) ---------------------------------------


@app.get("/api/render/watermark")
def watermark_info():
    if not render.WATERMARK.exists():
        return {"exists": False}
    try:
        from PIL import Image
        with Image.open(render.WATERMARK) as img:
            w, h = img.size
    except Exception:
        w = h = None
    mtime = int(render.WATERMARK.stat().st_mtime)
    return {"exists": True, "width": w, "height": h,
            "url": f"/lib/.config/watermark.png?v={mtime}"}


@app.post("/api/render/watermark")
async def watermark_upload(file: UploadFile = File(...)):
    data = await file.read()
    try:
        from io import BytesIO
        from PIL import Image
        with Image.open(BytesIO(data)) as img:
            if img.format != "PNG":
                raise ValueError
    except Exception:
        raise HTTPException(400, "please upload a PNG (transparency supported)")
    render.WATERMARK_DIR.mkdir(parents=True, exist_ok=True)
    render.WATERMARK.write_bytes(data)
    return watermark_info()


@app.post("/api/render/check")
def render_check(body: dict = Body(...)):
    try:
        result = render.check_clips([int(i) for i in body.get("clip_ids") or []])
    except ValueError as e:
        raise HTTPException(400, str(e))
    result.pop("_paths", None)
    result.pop("_durations", None)
    result["nvenc"] = render.nvenc_available()
    return result


@app.post("/api/render/preview")
def render_preview(body: dict = Body(...)):
    try:
        png = render.preview_frame(int(body.get("clip_id")), body.get("watermark"),
                                   float(body.get("trim_start") or 0))
    except (ValueError, TypeError) as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(500, str(e))
    return Response(content=png, media_type="image/jpeg")


@app.get("/api/render/jobs")
def render_jobs():
    return {"jobs": render.jobs()}


@app.post("/api/render/jobs")
def render_submit(body: dict = Body(...)):
    try:
        return {"jobs": render.submit(body)}
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/render/jobs/{job_id}/cancel")
def render_cancel(job_id: str):
    try:
        return render.cancel(job_id)
    except LookupError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/render/jobs/{job_id}")
def render_remove(job_id: str):
    render.remove(job_id)
    return {"ok": True}


# ---- static ------------------------------------------------------------------


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/app.js")
def app_js():
    return FileResponse(WEB_DIR / "app.js", media_type="text/javascript")


# Media (originals + thumbnails) straight from the library, with HTTP range
# support for the <video> previews. Replaces the former StaticFiles mount:
# that held files with a plain open() while streaming, which on Windows
# blocked filing/trashing a file that was just being previewed (WinError 32).
# fsutil.open_shared() lets the file be moved while it is being streamed.

_LIB_CHUNK = 256 * 1024
_RANGE_RE = re.compile(r"bytes=(\d*)-(\d*)")
_MIME = {".mp4": "video/mp4", ".m4v": "video/mp4", ".mov": "video/quicktime",
         ".webm": "video/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo",
         ".gif": "image/gif", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
         ".webp": "image/webp", ".bmp": "image/bmp", ".tif": "image/tiff", ".tiff": "image/tiff",
         ".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac", ".ogg": "audio/ogg",
         ".opus": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac"}


def _iter_file(path: Path, start: int, length: int):
    with fsutil.open_shared(path) as f:
        f.seek(start)
        remaining = length
        while remaining > 0:
            chunk = f.read(min(_LIB_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@app.api_route("/lib/{rel:path}", methods=["GET", "HEAD"])
def serve_lib(rel: str, request: Request):
    root = config.LIBRARY_ROOT.resolve()
    path = (root / rel).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise HTTPException(404, "not found")
    st = path.stat()
    size = st.st_size
    etag = f'"{st.st_mtime_ns:x}-{size:x}"'
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": formatdate(st.st_mtime, usegmt=True),
        "Cache-Control": "no-cache",
    }
    media_type = (_MIME.get(path.suffix.lower())
                  or mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng and size:
        m = _RANGE_RE.match(rng.split(",")[0].strip())
        if not m or (not m.group(1) and not m.group(2)):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        if m.group(1):
            start = int(m.group(1))
            end = min(int(m.group(2)), size - 1) if m.group(2) else size - 1
        else:                                   # suffix range: last N bytes
            start = max(0, size - int(m.group(2)))
        if start >= size or start > end:
            return Response(status_code=416, headers={"Content-Range": f"bytes */{size}"})
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    elif request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)

    length = end - start + 1 if size else 0
    headers["Content-Length"] = str(length)
    if request.method == "HEAD":
        return Response(status_code=status, headers=headers, media_type=media_type)
    return StreamingResponse(_iter_file(path, start, length), status_code=status,
                             headers=headers, media_type=media_type)
