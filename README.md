# genvault

A local catalog for your AI-generated videos, images and sounds — built for
people who produce a lot of ComfyUI / A1111 / Suno output and want to keep it
organized without handing it to a cloud service.

genvault runs a small web app on your own PC (`127.0.0.1`). Your media files
stay on your disk and are never uploaded anywhere.

**Platform:** Windows 10/11.

---

## Features

- **Inbox workflow** — new files land in an Inbox and are filed into
  *Project / Sub-project / Kind* by you. Nothing is sorted automatically, so
  your structure stays exactly the way you want it.
- **Two ways to import** — drag & drop into the browser window, or drop files
  (or whole folders) into the watched `drop\` folder.
- **Metadata reader** — prompt, negative prompt, model, seed, sampler,
  scheduler, steps, CFG, shift and LoRAs are read from:
  - ComfyUI videos (graph in the `comment` tag, incl. Kijai WanVideoWrapper nodes)
  - ComfyUI and A1111 PNGs (text chunks)

  Every field is editable, plus a free-text notes field (handy for Suno
  styles or anything without embedded data).
- **Search & filters** — full-text search over prompts, tags, projects, LoRAs
  and file names; sidebar facets with counts for project, sub-project, kind,
  media type, character, LoRA and upload status.
- **Gallery** — video hover previews, audio waveform thumbnails with play
  button, star rating (1–5), favorites tab, "Top rated" sort.
- **Bulk actions** — multi-select to file many inbox files at once, move
  library files to another project / sub-project / kind, or delete them.
- **Rename projects** — from the sidebar; the files are moved along.
  Renaming onto an existing project merges the two.
- **Copy helpers** — "Copy prompt", "Copy all parameters", "Copy workflow JSON".
- **Upload tracking** — per platform (default: X, Civitai, Reddit) with post
  URL and date, for assets of kind `release`.
- **Release module** — select clips, trim seconds off their start or end,
  add a PNG watermark (9 positions, size, opacity, margins, preview frame),
  join several clips into one video, optionally replace the audio with silence,
  and render in the background. GPU (NVENC) encoding with automatic CPU
  fallback. Embedded prompts/workflows are stripped from rendered files.
- **Privacy blur** — one click blurs every preview, e.g. while screen sharing.
- **Safe by design** — deleting moves files to a Trash you can restore from;
  the database can be rebuilt at any time from per-file JSON sidecars; one-click
  backup of all metadata.

---

## Requirements

1. **Python 3.10 or newer** — <https://www.python.org/downloads/>
   During installation, tick **"Add python.exe to PATH"**.
2. **ffmpeg** (for thumbnails, video metadata and the release module)
   - Download `ffmpeg-release-essentials.zip` from <https://www.gyan.dev/ffmpeg/builds/>
   - Unzip it and copy `ffmpeg.exe` and `ffprobe.exe` into `C:\ffmpeg\bin`
     (or put them anywhere on your `PATH`).
3. An internet connection when opening the app: the page styling (Tailwind
   CSS) is loaded from a CDN. Your media is never sent anywhere.

---

## Installation

1. Unzip the download and move the `genvault` folder to where it should live
   permanently (e.g. `D:\genvault`).
2. Double-click **`setup.bat`**. It will
   - create a private Python environment in `.venv` (takes 1–2 minutes),
   - ask **where your library should live** — all imported media goes there,
     so pick a drive with enough space (press Enter for `%USERPROFILE%\genvault`),
   - ask for the port (press Enter for `8077`).

## Starting and stopping

- Start: double-click **`genvault-tray.bat`**. A tray icon appears
  (gray = starting, green = running). Right-click it → **Open genvault**, or open
  <http://127.0.0.1:8077> in your browser.
- Stop: right-click the tray icon → **Quit**.

---

## Using genvault

1. **Import** — drag files into the browser window, or put them into
   `<library>\drop\`. Everything appears in the **Inbox**.
2. **File** — in the Inbox, enter a project and pick a kind for each file
   (sub-project, character and tags are optional), then click
   **Add to library**. For many files: tick their checkboxes and use the bar at
   the bottom.
3. **Browse** — the **Library** tab has search at the top and filters on the
   left. Click a card to open its details.
4. **Details** — rate, favorite, edit metadata, copy the prompt, change the
   assignment (the file is moved on disk), track uploads. Click the file path
   to open it in Explorer.
5. **Move many files** — tick cards in the Library, fill in only the fields you
   want to change in the bottom bar (empty fields keep their value) and click
   **Move**.
6. **Release** — tick video cards → **Create release**. Per clip you can enter
   how many seconds to **cut** from its start and end (the remaining length is
   shown right away) — or click **✂ Preview** to play the clip, step frame by
   frame, and set the cuts with **Set start here** / **Set end here** (the frame
   you see is always kept); **Play trimmed** plays only the part that stays. Upload a watermark logo (PNG), set position/size/opacity,
   order the clips by drag & drop, pick **Audio: Keep original** or **Replace
   with silence**, choose the destination (a project or any folder) and click
   **Start render**. A single clip can also be exported with only a trim or
   only silent audio. Progress is shown in the render queue; genvault stays
   usable meanwhile.
7. **Rename a project** — hover over the project in the left sidebar and
   click the ✎ pencil. All its files (including ones in the Trash) follow the
   new name; if the new name already exists, both projects are merged.
8. **Delete** — via the details panel, or tick several cards and click
   **Delete (N)** in the bottom bar. Files go to the **Trash** tab, where you can
   select them again to **Restore** or **Delete forever** —
   one by one or many at once. Deleting forever cannot be undone.

### Keyboard shortcuts

`1`–`5` rating · `F` favorite · `←` / `→` previous / next in the details panel ·
`Esc` close. Shortcuts are ignored while typing in a text field; without an
open details panel they apply to the card under the mouse.

---

## Updating

1. Quit genvault (tray icon → Quit).
2. Drag the new genvault zip onto **`update.bat`** (or double-click it — it
   picks the newest `genvault*.zip` from your Downloads folder).
3. Start `genvault-tray.bat` again. The version is shown next to the logo.

Updates replace program files only. Your `config.json`, library and database
are never touched; database changes migrate automatically on start.

---

## Configuration

Settings live in `config.json` in the program folder (created by `setup.bat`):

| Key | Meaning |
|---|---|
| `library_root` | Folder that holds your media, database and thumbnails |
| `port` | Local web port (default `8077`) |
| `platforms` | Platforms shown in upload tracking |
| `kinds` | Kinds offered in the dropdowns (add your own here) |
| `ffmpeg`, `ffprobe` | Optional full paths if they are not on `PATH` / in `C:\ffmpeg\bin` |

Restart genvault after editing. You can run a second, separate instance
(e.g. for testing) by copying the program folder and giving it its own
`library_root` and `port`.

### Library layout

```
<library_root>\
├── drop\                       watched import folder (files are moved out)
├── _inbox\                     imported, not yet filed
├── library\<project>\<sub-project>\<kind>\   filed media
├── .trash\                     deleted files (restorable)
├── .derivatives\               thumbnails
├── .sidecars\                  one JSON per file — the database can be rebuilt from these
├── .config\watermark.png       release watermark
├── backups\                    backup zips (Backup button)
└── genvault.db
```

Files are stored as `gv<id>_<original-name>.<ext>`; the original name is kept
and shown in the app.

---

## Troubleshooting

- **Page doesn't load** — is the tray icon green? Check `logs\server.log` in the
  program folder.
- **No thumbnails / no video info** — ffmpeg isn't found (see Requirements).
- **"File is in use by another program"** — another app (player, editor) has
  the file open. Close it and try again.
- **Database looks wrong** — quit genvault, then run
  `.venv\Scripts\python.exe -m backend.db rebuild` in the program folder to
  rebuild it from the sidecars.
- **Prompt missing, or the negative prompt shows up as the positive one** —
  metadata reading is best effort. There is no standard for where ComfyUI
  workflows keep their prompts, so not every node setup can be read correctly.
  Fields stay empty when a file has no embedded workflow (saved without
  metadata, or re-encoded afterwards) or the prompt comes from an unknown node.
  Tip: give your text-encode nodes titles containing **"Positive"** and
  **"Negative"** — genvault checks these titles first. You can always correct
  the fields by hand in the details panel; "Copy workflow JSON" still returns
  the complete embedded workflow.

When reporting a problem, please include the version number shown next to the
logo and what you were doing.

---

## Technical notes

- Python backend (FastAPI + SQLite), plain JavaScript frontend with no build
  step. Server binds to `127.0.0.1` only.
- `backend\` — `config.py` (settings, version), `db.py` (SQLite, auto-migration,
  rebuild from sidecars), `parser.py` (metadata), `media.py` (thumbnails),
  `library.py` (import, filing, trash, backup), `fsutil.py` (Windows-safe file
  moves), `watcher.py` (drop folder), `render.py` (release module), `main.py`
  (HTTP API). `web\` holds the frontend; `tray.py` is the tray launcher.
- Media is served with HTTP range support and share-delete file handles, so a
  file can be filed or deleted while it is playing in a preview.
- Release rendering: joining clips with identical codec/resolution/fps and no
  watermark or trim is a lossless stream copy; replacing the audio with silence
  keeps that video stream copy. Trimmed clips are always re-encoded so the cuts
  are frame-accurate. Everything else is re-encoded at the
  chosen quality — Standard (default; CRF 20 / NVENC CQ 21), High (17 / 17) or
  Near-lossless (5 / 10, very large files) — using the highest-resolution clip
  as target. Every output gets an AAC audio track (silence for silent clips)
  for web-player compatibility.
- There is no duplicate detection and no settings UI by design; see
  *Configuration*.

See `CHANGELOG.md` for the version history.

---

## Development

The repository contains the program plus two folders that are **not** part
of the release zip:

- `tests\` — integration checks. Run them with
  `.venv\Scripts\python.exe tests\run_tests.py`. The runner generates its own
  test media, starts a throwaway genvault server on port 8099 with a temporary
  library for every suite and removes everything afterwards; your
  `config.json` and your real library are never touched. Needs ffmpeg.
- `tools\` — `build_release.py` builds `genvault-<version>.zip` (program files
  only) from the current state; `demo\` generates a fictional sample library
  for screenshots.

Release checklist: bump `APP_VERSION` in `backend\config.py`, add a
`CHANGELOG.md` entry, run the tests, then build the zip.

---

## License

genvault is released under the [MIT License](LICENSE) — © 2026 fatberg_slim.

Python, ffmpeg and the libraries installed by `setup.bat` (FastAPI, Uvicorn,
watchdog, Pillow, pystray, python-multipart) are separate projects under their
own licenses; they are not bundled in this zip.
