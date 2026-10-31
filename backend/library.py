r"""Library file operations: intake, manual filing, trash, sidecars, backup.

Rules of the house:
- Every new file lands in _inbox\ and waits for a manual assignment. Nothing
  about project/kind is ever derived from names, folders or metadata.
- Files inside the library are canonically named gv<id>_<original-stem>.<ext>
  and live under library\<project>\<sub_project>\<kind>\.
- Every mutation is mirrored to a JSON sidecar so the DB stays rebuildable.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

from . import config, db, fsutil, media, parser


def now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def today() -> str:
    return datetime.date.today().isoformat()


# ---- naming ------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")
_SEGMENT_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


def slug(name: str, maxlen: int = 60) -> str:
    s = _SLUG_RE.sub("-", name).strip("-._")
    return s[:maxlen].rstrip("-._") or "file"


def canonical_name(asset_id: int, original_name: str) -> str:
    p = Path(original_name)
    return f"gv{asset_id:06d}_{slug(p.stem)}{p.suffix.lower()}"


def safe_segment(name: str) -> str:
    """Folder-name-safe version of a user-supplied project/kind value."""
    s = _SEGMENT_BAD.sub(" ", str(name)).strip().strip(".")
    s = re.sub(r"\s+", " ", s)
    return s[:80] or "_"


def rel_str(p: Path) -> str:
    return str(p.relative_to(config.LIBRARY_ROOT)).replace("\\", "/")


def abs_path(asset: dict) -> Path:
    return config.LIBRARY_ROOT / str(asset.get("rel_path") or "")


# ---- sidecars ----------------------------------------------------------------


def sidecar_path(asset_id: int) -> Path:
    return config.SIDECAR_DIR / f"gv{asset_id:06d}.json"


def write_sidecar(asset: dict) -> None:
    config.SIDECAR_DIR.mkdir(parents=True, exist_ok=True)
    sidecar_path(int(asset["id"])).write_text(
        json.dumps(asset, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _saved(asset_id: int) -> dict:
    asset = db.get(asset_id)
    if asset:
        write_sidecar(asset)
    return asset


# ---- intake ------------------------------------------------------------------


def intake(src: Path, original_name: str | None = None, hint: str | None = None,
           move: bool = True) -> dict | None:
    """Bring a new file into the inbox. Returns the asset, or None if the
    extension is unsupported. Never derives project/kind from anything."""
    original = original_name or src.name
    media_type = config.media_type_for(Path(original))
    if media_type is None:
        return None
    meta = parser.extract(src)
    meta.pop("media_type", None)  # stored as a column, not in meta
    try:
        mtime = src.stat().st_mtime
        created = datetime.datetime.fromtimestamp(mtime).isoformat(timespec="seconds")
    except OSError:
        created = now()
    ts = now()
    asset_id = db.insert({
        "status": "inbox", "original_name": original, "media_type": media_type,
        "meta": meta, "source_hint": hint, "tags": [], "uploads": {},
        "created_at": created, "added_at": ts, "updated_at": ts,
    })
    name = canonical_name(asset_id, original)
    dest = config.INBOX_DIR / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if move:
            fsutil.move(src, dest)
        else:
            shutil.copy2(str(src), str(dest))
    except Exception:
        # no file, no row: otherwise every failed drop-folder scan left an
        # empty "ghost" inbox entry behind
        db.delete(asset_id)
        raise
    db.update(asset_id, {"file_name": name, "rel_path": rel_str(dest)})
    media.make_thumbnail(dest, asset_id, media_type, meta.get("duration"))
    return _saved(asset_id)


# ---- filing ------------------------------------------------------------------


def _library_dir(project: str, sub_project: str | None, kind: str) -> Path:
    parts = [safe_segment(project)]
    if sub_project and str(sub_project).strip():
        parts.append(safe_segment(sub_project))
    parts.append(safe_segment(kind))
    return config.LIBRARY_DIR.joinpath(*parts)


def _prune_empty_dirs(start: Path) -> None:
    """Remove now-empty folders from `start` upwards, never above library\\."""
    root = config.LIBRARY_DIR.resolve()
    d = start.resolve()
    while d != root and d.is_relative_to(root):
        try:
            d.rmdir()          # only succeeds when empty
        except OSError:
            break
        d = d.parent


def _move_file(asset: dict, dest_dir: Path) -> str:
    src = abs_path(asset)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / str(asset["file_name"])
    fsutil.move(src, dest)
    if src.parent != dest.parent:
        _prune_empty_dirs(src.parent)
    return rel_str(dest)


def file_asset(asset_id: int, project: str, kind: str,
               sub_project: str | None = None, character: str | None = None,
               tags: list[str] | None = None) -> dict:
    """Move an inbox asset into the library (or re-file a library asset)."""
    asset = db.get(asset_id)
    if not asset:
        raise LookupError(f"asset {asset_id} not found")
    if asset["status"] == "trash":
        raise ValueError("asset is in the trash")
    if not str(project).strip():
        raise ValueError("project is required")
    if not str(kind).strip():
        raise ValueError("kind is required")
    fields: dict = {
        "project": str(project).strip(),
        "sub_project": (str(sub_project).strip() or None) if sub_project else None,
        "kind": str(kind).strip(),
        "status": "library",
        "updated_at": now(),
    }
    if character is not None and str(character).strip():
        fields["character"] = str(character).strip()
    if tags:
        merged = list(dict.fromkeys([*(asset.get("tags") or []), *[t.strip() for t in tags if t.strip()]]))
        fields["tags"] = merged
    fields["rel_path"] = _move_file(asset, _library_dir(fields["project"], fields["sub_project"], fields["kind"]))
    db.update(asset_id, fields)
    return _saved(asset_id)


# ---- field updates -----------------------------------------------------------

EDITABLE = {"kind", "project", "sub_project", "character", "rating", "favorite", "tags"}
META_KEYS = {"positive_prompt", "negative_prompt", "model", "seed", "sampler",
             "scheduler", "steps", "cfg", "shift", "loras", "notes", "derived_from"}


def update_asset(asset_id: int, patch: dict) -> dict:
    """Apply a partial update. For library assets, changing project /
    sub_project / kind physically re-files the file."""
    asset = db.get(asset_id)
    if not asset:
        raise LookupError(f"asset {asset_id} not found")
    fields: dict = {}

    for key in EDITABLE & set(patch.keys()):
        v = patch[key]
        if key == "rating":
            v = max(0, min(5, int(v or 0)))
        elif key == "favorite":
            v = bool(v)
        elif key == "tags":
            v = [str(t).strip() for t in (v or []) if str(t).strip()]
        elif v is not None:
            v = str(v).strip() or None
        fields[key] = v

    if isinstance(patch.get("meta"), dict):
        meta = dict(asset.get("meta") or {})
        for k, v in patch["meta"].items():
            if k in META_KEYS:
                meta[k] = v if v not in ("", []) else None
        fields["meta"] = meta

    if isinstance(patch.get("uploads"), dict):
        uploads = dict(asset.get("uploads") or {})
        for platform, entry in patch["uploads"].items():
            if not isinstance(entry, dict):
                continue
            cur = dict(uploads.get(platform) or {})
            cur["posted"] = bool(entry.get("posted", cur.get("posted", False)))
            if "url" in entry:
                cur["url"] = str(entry["url"]).strip() or None
            if "date" in entry:
                cur["date"] = str(entry["date"]).strip() or None
            elif cur["posted"] and not cur.get("date"):
                cur["date"] = today()
            uploads[platform] = cur
        fields["uploads"] = uploads

    if not fields:
        return asset
    fields["updated_at"] = now()

    # Re-file a library asset when its location-defining fields changed.
    # The file moves FIRST: if that fails, the DB still matches the disk.
    if asset["status"] == "library" and {"project", "sub_project", "kind"} & set(fields.keys()):
        target = {**asset, **fields}
        if not (target.get("project") and target.get("kind")):
            raise ValueError("project and kind are required for library files")
        fields["rel_path"] = _move_file(
            asset, _library_dir(target["project"], target.get("sub_project"), target["kind"]))
    db.update(asset_id, fields)
    return _saved(asset_id)


# ---- project rename ------------------------------------------------------------


def _swap_project_prefix(rel: str | None, old: str, new: str) -> str | None:
    """library/<old>/... -> library/<new>/... (folder names as safe_segment)."""
    prefix = f"library/{safe_segment(old)}/"
    if rel and rel.lower().startswith(prefix.lower()):
        return f"library/{safe_segment(new)}/" + rel[len(prefix):]
    return rel


def rename_project(old: str, new: str) -> dict:
    """Rename a project everywhere: library files are moved into the new
    project folder, trashed files will be restored into it. Renaming onto an
    existing project name merges the two. Per-file errors are collected —
    files that failed keep the old name and can simply be renamed again."""
    old, new = str(old or "").strip(), str(new or "").strip()
    if not old or not new:
        raise ValueError("old and new project name are required")
    if old == new:
        raise ValueError("the new name is the same as the old one")
    assets = [a for a in db.all_assets() if a.get("project") == old]
    if not assets:
        raise LookupError(f'project "{old}" not found')

    old_dir = config.LIBRARY_DIR / safe_segment(old)
    new_dir = config.LIBRARY_DIR / safe_segment(new)
    # Case-only rename ("alpha" -> "Alpha"): on Windows both paths are the same
    # folder, so moving files would keep the old spelling. Rename the folder
    # itself (via a temp name) and just rewrite the stored paths.
    dir_renamed = False
    # (compare as strings: WindowsPath equality ignores case)
    if str(old_dir) != str(new_dir) and str(old_dir).lower() == str(new_dir).lower() and old_dir.exists():
        tmp = old_dir.with_name(old_dir.name + ".gv-rename")
        try:
            os.rename(old_dir, tmp)
            os.rename(tmp, new_dir)
            dir_renamed = True
        except OSError:
            if tmp.exists() and not old_dir.exists():
                os.rename(tmp, old_dir)        # put it back, fall through to per-file moves

    renamed, errors = [], []
    for a in assets:
        try:
            if a["status"] == "library" and not dir_renamed:
                renamed.append(update_asset(a["id"], {"project": new}))
                continue
            fields = {"project": new, "updated_at": now()}
            if a["status"] == "library":
                fields["rel_path"] = _swap_project_prefix(a.get("rel_path"), old, new)
            elif a["status"] == "trash" and a.get("trash_info"):
                info = dict(a["trash_info"])
                info["orig_rel_path"] = _swap_project_prefix(info.get("orig_rel_path"), old, new)
                fields["trash_info"] = info
            db.update(a["id"], fields)
            renamed.append(_saved(a["id"]))
        except Exception as e:
            errors.append({"id": a["id"], "name": a.get("original_name"), "error": str(e)})
    if old_dir.exists() and not dir_renamed:
        for d in sorted((p for p in old_dir.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            _prune_empty_dirs(d)
        _prune_empty_dirs(old_dir)
    return {"renamed": renamed, "errors": errors}


# ---- trash -------------------------------------------------------------------


def trash_asset(asset_id: int) -> dict:
    asset = db.get(asset_id)
    if not asset:
        raise LookupError(f"asset {asset_id} not found")
    if asset["status"] == "trash":
        return asset
    config.TRASH_DIR.mkdir(parents=True, exist_ok=True)
    dest = config.TRASH_DIR / str(asset["file_name"])
    src = abs_path(asset)
    if src.exists():
        fsutil.move(src, dest)
        _prune_empty_dirs(src.parent)
    db.update(asset_id, {
        "status": "trash", "rel_path": rel_str(dest),
        "trash_info": {"orig_rel_path": asset.get("rel_path"),
                       "orig_status": asset.get("status"),
                       "deleted_at": now()},
        "updated_at": now(),
    })
    return _saved(asset_id)


def restore_asset(asset_id: int) -> dict:
    asset = db.get(asset_id)
    if not asset or asset["status"] != "trash":
        raise LookupError(f"asset {asset_id} is not in the trash")
    info = asset.get("trash_info") or {}
    orig_rel = info.get("orig_rel_path") or f"_inbox/{asset['file_name']}"
    dest = config.LIBRARY_ROOT / orig_rel
    src = abs_path(asset)
    if src.exists():
        fsutil.move(src, dest)
    db.update(asset_id, {
        "status": info.get("orig_status") or ("inbox" if orig_rel.startswith("_inbox") else "library"),
        "rel_path": orig_rel, "trash_info": None, "updated_at": now(),
    })
    return _saved(asset_id)


def purge_asset(asset_id: int) -> None:
    """Hard delete: file, thumbnail, sidecar, DB row. Irreversible."""
    asset = db.get(asset_id)
    if not asset:
        return
    if asset["status"] != "trash":
        raise ValueError("only trashed assets can be deleted permanently")
    fsutil.unlink(abs_path(asset))
    deriv = config.DERIV_DIR / str(asset_id)
    if deriv.exists():
        shutil.rmtree(deriv, ignore_errors=True)
    sc = sidecar_path(asset_id)
    if sc.exists():
        sc.unlink()
    db.delete(asset_id)


# ---- explorer ----------------------------------------------------------------


def open_in_explorer(asset_id: int) -> str:
    """Open the containing folder in Windows Explorer with the file selected.
    The path always comes live from the DB, never from a cache."""
    asset = db.get(asset_id)
    if not asset:
        raise LookupError(f"asset {asset_id} not found")
    path = abs_path(asset)
    # explorer needs the raw command line: with the list form Python quotes
    # the whole "/select,<path>" token as soon as the path contains spaces,
    # explorer fails to parse it and silently opens the Documents folder.
    if path.exists():
        subprocess.Popen(f'explorer /select,"{path}"')
    elif path.parent.exists():
        subprocess.Popen(f'explorer "{path.parent}"')
    else:
        raise FileNotFoundError(str(path))
    return str(path)


# ---- backup ------------------------------------------------------------------


def backup() -> str:
    r"""Zip sidecars + DB + config into backups\ — the data everything can be
    rebuilt from. Media files are intentionally not included."""
    config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = config.BACKUP_DIR / f"genvault-backup-{stamp}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        if config.DB_PATH.exists():
            z.write(config.DB_PATH, "genvault.db")
        if config.CONFIG_FILE.exists():
            z.write(config.CONFIG_FILE, "config.json")
        if config.SIDECAR_DIR.exists():
            for sc in sorted(config.SIDECAR_DIR.glob("*.json")):
                z.write(sc, f".sidecars/{sc.name}")
        cfg_dir = config.LIBRARY_ROOT / ".config"     # watermark logo etc.
        if cfg_dir.exists():
            for f in sorted(cfg_dir.iterdir()):
                if f.is_file():
                    z.write(f, f".config/{f.name}")
    return str(out)
