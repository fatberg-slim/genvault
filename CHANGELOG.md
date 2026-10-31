# Changelog

## Unreleased

- **Fix: batch files use Windows line endings again.** `setup.bat`,
  `update.bat` and `genvault-tray.bat` had Unix (LF) line endings, which
  cmd.exe can misread around `goto` labels. They now use CRLF, and
  `.gitattributes` keeps it that way.
- Development: integration checks (`tests\run_tests.py`) and a release
  builder (`tools\build_release.py`) are now part of the repository; see the
  *Development* section in the README. They are not included in the release zip.

## 2.4.0 — 2026-10-25

- **Release module: trim preview.** Every clip has a **✂ Preview** button that
  opens a player under the clip list. A timeline bar shows the kept part and
  the playhead (click it to jump). Step **frame by frame**, then use
  **Set start here** / **Set end here** — the frame you see is always kept —
  and **Play trimmed** to watch only the part that will be exported. The cut
  fields, lengths and total update automatically; typing values still works.
- **Fix: trims are now exact to the frame.** Cutting used ffmpeg input seeking,
  which could let one extra frame through at the end. Trims now use the
  timestamp-based `trim` filter (verified frame by frame against the source).

## 2.3.0 — 2026-10-25

- **Release module: trim clips.** Every clip in the release list has a
  "cut start" and "cut end" field (seconds, decimals allowed). The remaining
  length and the new total are shown while you type. Cuts are frame-accurate,
  so trimmed clips are re-encoded; in concat mode each clip is trimmed before
  joining. The watermark preview frame is taken from inside the trimmed range.
- **Release module: replace audio with silence.** New *Audio* option in the
  output section: *Keep original audio* (default) or *Replace with silence*.
  The output still always carries an audio track (silence) for web-player
  compatibility. Without watermark and trims the video stays a lossless
  stream copy — only the audio is swapped.
- A single clip can now be rendered with only a trim or only silent audio;
  previously the watermark was required.
- The release hint under the clip list explains why a render is lossless or
  needs a re-encode (now including trims and the audio option).

## 2.2.0 — 2026-10-15

- **Rename projects.** Hover over a project in the left sidebar and click the
  ✎ pencil. All its files are moved into the new project folder; files in the
  Trash are updated too, so restoring them lands in the renamed project.
  Renaming onto an existing project name merges both projects. Changing only
  the capitalization (e.g. "alpha" → "Alpha") renames the folder as well.
- **Multi-select delete.** The bottom bar in Library, Favorites and Inbox now
  has a **Delete (N)** button that moves all selected files to the Trash. The
  Trash view got checkboxes, "Select all" and a bar with **Restore (N)** and
  **Delete forever (N)**. Files that fail are named and stay selected.
- **Fix: the filter sidebar no longer jumps back to the top** when the page
  refreshes or a filter is clicked; it keeps its scroll position.
- Empty project / sub-project / kind folders are removed automatically after
  their last file was moved or deleted.
- The gallery leaves room at the bottom while the bulk bar is shown, so the
  last row of cards is no longer hidden behind it.

## 2.1.2 — 2026-10-13

- **Fix: "file is being used by another process" (WinError 32) when filing,
  moving or deleting.** The "other process" was genvault itself: while a video
  was being streamed to the browser (hover preview, drawer player), the server
  held the file open in a way that made Windows block moving it. Media is now
  served with share-delete access — a file can be moved while it plays — and
  the browser now really aborts preview downloads when you leave a card.
  Short locks by other programs (virus scanner, Explorer thumbnails) are
  ridden out with an automatic retry (~2.5 s).
- **Fix: HTTP 500 on "Delete" / "Delete forever" / "Restore"** when the file
  was locked — now a readable error message instead.
- **Fix: no more duplicate files after a failed move.** The old fallback (copy,
  then delete the original) left the file in two places when the delete
  failed. Moving is now an atomic rename; nothing is ever copied on the same
  drive.
- **Fix: database and folders stay consistent.** Moving via the bulk bar or
  "Save assignment" wrote the new project to the database *before* moving the
  file; if the move failed, the asset pointed to the wrong place. The file is
  now moved first.
- **Fix: no "ghost" inbox entries** when a file in `drop\` could not be moved
  (e.g. still being written) — the database entry is rolled back.
- **Fix: the bulk bar's autocomplete list docks to the bar immediately**
  instead of floating far above it until clicked (the list height was measured
  before its height limit took effect).
- "Move (N)" and inbox bulk filing now name the files that failed and why;
  those files stay selected so you can retry them right away.
- Browser upload: a single failing file no longer aborts the whole upload with
  HTTP 500.
- Documentation: one English `README.md` covering installation, usage,
  updating and troubleshooting; the separate tester guides and German docs
  were removed.
- genvault is now released under the MIT License (`LICENSE`).

## 2.1.1 — 2026-07-08

- **Gallery: multi-select + bulk move.** Checkboxes now on all cards (not only
  videos), "Select all" in the toolbar. The bottom bar gets fields for
  Project / Subproject / Kind — empty fields keep the current value, "Move (N)"
  physically moves all selected files (e.g. many files into a subproject at
  once). "Create release" now shows the number of selected videos.
- **Selectable render quality:** Standard (recommended, small files — new
  default) / High / Near-lossless (the previous setting; produced ~10x larger
  files and is now opt-in). NVENC uses `-preset p5 -rc vbr -cq`, CPU libx264
  crf 20/17/5.
- **Releases no longer contain source metadata** (`-map_metadata -1`):
  embedded ComfyUI prompts/workflows no longer end up in published files —
  including lossless concat.
- Improved NVENC fallback: the actual ffmpeg error reason is now shown in the
  job note (previously just "NVENC failed"), the probe test uses the same
  encoder arguments as real jobs, and after a fallback NVENC is re-checked on
  the next job (the GPU may just have been busy with ComfyUI).

## 2.1.0 — 2026-07-06

- **New Release module (Tab "Release"):** Select video clips in the gallery via checkbox → "Create release." Watermark (global PNG logo, swappable via the Release view, lives at `.config\watermark.png` and travels with backups) with 9 anchor positions, size (%/px, aspect ratio locked), opacity, and edge offsets; WYSIWYG preview frame before rendering. Multiple clips are concatenated (concat, order via drag & drop or arrows) — when codec/resolution/fps match, this is a true lossless stream-copy; otherwise an automatic re-encode is done, matched to the highest-quality source clip (GPU NVENC, CPU fallback), visually lossless. Output can go either as a new asset into a project (kind release/pre-release, with a `derived_from` reference to the source clips) or into a free export folder. The render queue runs in the background with progress/cancel — genvault stays fully usable in the meantime.
  **Every output is guaranteed to have an audio track** (for web-player compatibility): existing audio is carried over (AAC), silent clips get matching silence added — even during lossless concat (video remains stream-copy in that case).
- Inbox: filter chips by media type (All/Videos/Images/Audio) — "Select all" then only applies to the filtered files.
- Gallery: green "✓" badges on the thumbnail show per platform where a file has already been posted.
- Left facet column: stays visible while scrolling (sticky), and each section (e.g. LoRAs) can be collapsed by clicking its title (remembered across reloads).

## 2.0.2 — 2026-07-05

- Fix: "Open folder in Explorer" opened the Documents folder instead of the file's folder for paths containing spaces (`explorer /select` was receiving an incorrectly quoted argument). Now reliably opens the correct folder with the file selected.

## 2.0.1 — 2026-07-04

- Fix: The autocomplete dropdown on the bulk bar now opens upward instead of running off the bottom of the screen.
- Upload tracking now only applies to assets of kind `release`: facet counts, upload filters, and the drawer section ignore all other kinds.
- New global privacy blur: an 👁 button in the top right blurs all thumbnails/previews at once, until clicked again; the state survives reloads.
- Default platforms are now X / Civitai / Reddit (Instagram removed). Note: existing installations need to manually adjust the `platforms` list in their `config.json` — updates never touch the config.
- Added English documentation (README.en.md, TESTERS.en.md).

## 2.0.0 — 2026-07-03

Complete fresh start (v2). Same basic idea as v1, but a deliberately reduced, manual workflow.

- **No more auto-filing:** all new files land in the Inbox without exception; Project / Subproject / Kind are assigned exclusively manually. The drop-folder path is now shown only as a non-binding hint.
- Ingestion via browser drag & drop (copy) and a monitored `drop\` folder (move, recursive, with a stability check for large files).
- Inbox with single and bulk filing (checkboxes + "Select all").
- Metadata parsing as in v1 (ComfyUI comment atom, Kijai WanVideoWrapper, PNG chunks, A1111) — now additionally **all fields are manually editable** (e.g. for Suno tracks without embedded data), plus a free-text notes field.
- "Copy prompt" and "Copy all parameters" in the detail view.
- Facet sidebar with counts: Project, Subproject, Kind, Media type, Character, LoRA, Upload status; combinable with full-text search.
- Favorites (♥) with their own tab, star rating 1–5, "Top rated" sort.
- Video hover preview (autoplay/muted/loop), audio with waveform + play button.
- Upload tracking per platform (configurable) with URL + date.
- Clickable file path opens Explorer with the file selected.
- Trash view with restore / permanently delete.
- Backup button: sidecars + DB + config as a dated zip in `backups\`.
- Keyboard shortcuts: 1–5 rating, F favorite, ←/→ navigate, Esc close.
- Setup interactively asks for library path and port; port configurable for LIVE/DEV parallel operation.
- Automatic schema migration; DB rebuildable at any time from `.sidecars\`.
- UI language: English.