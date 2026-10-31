r"""SQLite storage. One file in the library root, no ORM.

The DB is a cache: every asset is mirrored to a JSON sidecar in .sidecars\,
so the DB can always be rebuilt from them (see rebuild_from_sidecars, which
also runs automatically when the DB is empty but sidecars exist).

Schema migration is automatic: COLUMNS below is the canonical column set;
any column missing from an older DB is ADDed on startup.
"""
from __future__ import annotations

import json
import sqlite3
import threading

from . import config

SCHEMA_VERSION = 2

# Canonical column set. Never remove or rename entries — only add new ones
# (with a DEFAULT) so old databases migrate automatically via ALTER TABLE.
COLUMNS: dict[str, str] = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "status": "TEXT NOT NULL DEFAULT 'inbox'",          # inbox | library | trash
    "original_name": "TEXT",
    "file_name": "TEXT",                                 # canonical, app-assigned
    "rel_path": "TEXT",                                  # relative to library root, forward slashes
    "media_type": "TEXT",                                # video | image | audio
    "kind": "TEXT",
    "project": "TEXT",
    "sub_project": "TEXT",
    "character": "TEXT",
    "tags": "TEXT NOT NULL DEFAULT '[]'",
    "rating": "INTEGER NOT NULL DEFAULT 0",
    "favorite": "INTEGER NOT NULL DEFAULT 0",
    "meta": "TEXT NOT NULL DEFAULT '{}'",                # parsed + manually edited metadata
    "uploads": "TEXT NOT NULL DEFAULT '{}'",             # {platform: {posted, url, date}}
    "source_hint": "TEXT",                               # drop subfolder, display only
    "created_at": "TEXT",
    "added_at": "TEXT",
    "updated_at": "TEXT",
    "trash_info": "TEXT",                                # {orig_rel_path, deleted_at}
}

JSON_FIELDS = ("tags", "meta", "uploads", "trash_info")

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connection() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        config.ensure_dirs()
        _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
    return _conn


def init() -> None:
    with _lock:
        c = _connection()
        cols = ", ".join(f"{n} {ddl}" for n, ddl in COLUMNS.items())
        c.execute(f"CREATE TABLE IF NOT EXISTS assets ({cols})")
        c.execute("CREATE TABLE IF NOT EXISTS meta_kv (key TEXT PRIMARY KEY, value TEXT)")
        existing = {r["name"] for r in c.execute("PRAGMA table_info(assets)")}
        for name, ddl in COLUMNS.items():
            if name not in existing:
                c.execute(f"ALTER TABLE assets ADD COLUMN {name} {ddl}")
        c.execute(
            "INSERT OR REPLACE INTO meta_kv (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        c.commit()
    if count() == 0:
        n = rebuild_from_sidecars()
        if n:
            print(f"[db] rebuilt {n} assets from sidecars")


def _to_asset(row: sqlite3.Row) -> dict:
    d = dict(row)
    for f in JSON_FIELDS:
        raw = d.get(f)
        try:
            d[f] = json.loads(raw) if raw else ([] if f == "tags" else None if f == "trash_info" else {})
        except Exception:
            d[f] = [] if f == "tags" else None if f == "trash_info" else {}
    if d.get("meta") is None:
        d["meta"] = {}
    if d.get("uploads") is None:
        d["uploads"] = {}
    d["favorite"] = bool(d.get("favorite"))
    return d


def _to_row(fields: dict) -> dict:
    out = {}
    for k, v in fields.items():
        if k not in COLUMNS:
            continue
        if k in JSON_FIELDS:
            out[k] = json.dumps(v, ensure_ascii=False) if v is not None else None
        elif k == "favorite":
            out[k] = 1 if v else 0
        else:
            out[k] = v
    return out


def insert(fields: dict) -> int:
    row = _to_row(fields)
    keys = list(row.keys())
    with _lock:
        c = _connection()
        cur = c.execute(
            f"INSERT INTO assets ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )
        c.commit()
        return int(cur.lastrowid)


def update(asset_id: int, fields: dict) -> None:
    row = _to_row(fields)
    if not row:
        return
    keys = list(row.keys())
    with _lock:
        c = _connection()
        c.execute(
            f"UPDATE assets SET {', '.join(f'{k}=?' for k in keys)} WHERE id=?",
            [row[k] for k in keys] + [asset_id],
        )
        c.commit()


def delete(asset_id: int) -> None:
    with _lock:
        c = _connection()
        c.execute("DELETE FROM assets WHERE id=?", (asset_id,))
        c.commit()


def get(asset_id: int) -> dict | None:
    with _lock:
        row = _connection().execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    return _to_asset(row) if row else None


def all_assets(status: str | None = None) -> list[dict]:
    with _lock:
        c = _connection()
        if status:
            rows = c.execute("SELECT * FROM assets WHERE status=? ORDER BY id DESC", (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM assets ORDER BY id DESC").fetchall()
    return [_to_asset(r) for r in rows]


def count(status: str | None = None) -> int:
    with _lock:
        c = _connection()
        if status:
            r = c.execute("SELECT COUNT(*) FROM assets WHERE status=?", (status,)).fetchone()
        else:
            r = c.execute("SELECT COUNT(*) FROM assets").fetchone()
    return int(r[0])


def rebuild_from_sidecars() -> int:
    """Wipe the assets table and restore every asset from its JSON sidecar."""
    sidecars = sorted(config.SIDECAR_DIR.glob("gv*.json")) if config.SIDECAR_DIR.exists() else []
    assets = []
    for sc in sidecars:
        try:
            a = json.loads(sc.read_text(encoding="utf-8"))
            if isinstance(a, dict) and a.get("id"):
                assets.append(a)
        except Exception:
            print(f"[db] skipping unreadable sidecar: {sc.name}")
    if not assets:
        return 0
    with _lock:
        c = _connection()
        c.execute("DELETE FROM assets")
        for a in assets:
            row = _to_row(a)
            row["id"] = int(a["id"])
            keys = list(row.keys())
            c.execute(
                f"INSERT INTO assets ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
                [row[k] for k in keys],
            )
        c.commit()
    return len(assets)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "rebuild":
        init()
        print(f"rebuilt {rebuild_from_sidecars()} assets from {config.SIDECAR_DIR}")
    else:
        print("usage: python -m backend.db rebuild")
