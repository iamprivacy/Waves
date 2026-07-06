# Waves, desktop UI for tidaler

Waves is an alternative graphical front-end for tidaler: a search-first,
keyboard-friendly desktop app with a console (CRT phosphor-green) theme. It wraps
tidaler's existing download engine, search, browse, and queue downloads from a
single window while the backend handles authentication, streaming, and tagging.

It is a self-contained GUI layer: everything lives under `tidaler/waves_ui/` and
reuses tidaler's `Settings`, `Tidal`, and `Download` objects unchanged.

## Running

```bash
python -m tidaler.waves_ui
```

A paid TIDAL plan and a one-time sign-in are required (the app guides you through
the browser login on first launch and reuses the cached token afterwards).

Requirements: Python 3.12+, PySide6 (Qt 6 / QtQuick), and the rest of tidaler's
dependencies. Audio quality follows your tidaler settings, up to HiRes Lossless.

## What's here

- **Search-first**, one field searches artists, albums, tracks, videos,
  playlists, and mixes, or resolves a pasted TIDAL link. Paste a `tidal.com` or
  `listen.tidal.com` link (⌘V, or the clipboard glyph in the bar) and it
  "decodes" into the field and auto-opens the release; anything else just waits
  for you to search. Pasting works through the field's standard paste, the app
  never reads your clipboard on its own. Duplicate editions of a release are
  collapsed to a single best-quality row (capped at your configured maximum
  quality).
- **Rich results**, colour-coded quality badges (HI-RES / LOSSLESS / HIGH), a
  popularity meter, and per-artist links so every credited artist is clickable.
- **Artist pages**, bio, full discography, EPs/singles, and top tracks, with a
  one-click "download discography".
- **My Tidal**, your favourites (albums, tracks, artists, videos, playlists,
  mixes) with virtualised infinite scroll, so large libraries stay smooth.
- **Download queue**, grouped into **Completed**, **Downloading**, and
  **Queued** sections. Active rows show live per-track progress on an LED
  dot-matrix bar (`artist · done/total tracks`); a finished row shows a ✓ DONE
  chip, then settles into the collapsible Completed group. Pause/resume, stop
  all, per-item cancel, and retry are all supported.
- **Console theme**, a dark, monospace, terminal-inspired look with a wide,
  animated ASCII parallax-ocean logo in the top bar. Quitting always exits
  cleanly, even mid-download. (A small signature easter egg hides in the
  Settings footer.)

## Layout

| Path                   | Purpose                                                                                                              |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------- |
| `app.py`               | Application entry point; creates the QML engine and the bridge.                                                      |
| `backend.py`           | `WavesBridge`, the single `QObject` exposed to QML; runs blocking work (login, search, downloads) off the UI thread. |
| `qml/Main.qml`         | The main window: search, results, artist pages, library, and the download queue.                                     |
| `qml/SettingsPage.qml` | Settings editor (mirrors tidaler's preferences).                                                                     |
| `updater.py`           | In-app self-updater: fail-closed Ed25519 verification, staged swap, rollback.                                        |
| `signing.py`           | The embedded release public key and manifest verification.                                                           |
| `ffmpeg_manager.py`    | Downloads and updates a trusted static FFmpeg into the app data dir.                                                 |
| `devlog.py`            | Optional developer timing log (enable with `WAVES_DEBUG`).                                                           |
| `BRIDGE.md`            | Reference for every bridge signal and the slot/worker pattern.                                                       |
| `fonts/`               | Bundled JetBrains Mono for consistent monospace + block-glyph rendering.                                             |

## Architecture

QML draws, Python fetches. `Main.qml` calls slots on the `waves` context
property (a `WavesBridge` instance); the bridge runs the blocking work on
one of two `QThreadPool`s (metadata vs. downloads, so a long album download
never starves search) and answers with signals, which Qt delivers on the
GUI thread. QML only ever receives plain dicts, lists, and ids; the live
tidalapi objects stay in the bridge's per-kind object cache and are looked
up by id when QML asks for a download or an expansion.

Start with the `WavesBridge` class docstring in `backend.py` for the state
model, `BRIDGE.md` for the signal reference, and the repo-root
`DEVELOPER.md` for a worked example of adding a feature end to end.

Key concepts worth knowing before reading the big files:

- **Object cache with eviction** (`_objs`): buckets are FIFO-capped and a
  new search replaces them, so download slots must (and do) handle an id
  whose object is gone by re-fetching it.
- **Stale-while-revalidate pages**: artist and browse pages render from a
  session cache instantly and refresh in the background; a disk snapshot
  (`page_cache.json`, account-tagged, deleted on logout) warms the next
  launch.
- **One shared preview player**: previews are addressed by (kind, id), and
  non-track previews report the concrete song they resolved to, so every
  surface showing that song displays live playback state.
- **Progress relays**: each download job gets a `_ProgressSignals` object
  with GUI-thread affinity so per-track ticks from the engine's worker
  threads arrive safely in QML.

## Fonts

The monospace face is **JetBrains Mono**, bundled under the SIL Open Font
License 1.1. The full license text is in [`fonts/OFL.txt`](fonts/OFL.txt).
