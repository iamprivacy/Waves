# Changelog

All notable user-facing changes to Waves are documented here, newest first.
Each release section becomes the GitHub Release notes for that version, so keep
bullets short, objective, and written for end users.

Format per release: `## vX.Y.Z (YYYY-MM-DD)` followed by any of the subheadings
**Added**, **Changed**, **Fixed**, **Removed**, each a bullet list. Section
headings and their bullets carry a leading emoji accent (for example ✨ Added,
🔧 Changed, 🐛 Fixed). Changes land under **Unreleased** as they are made;
cutting a release renames that section to the new version.

## 🌊 v0.1.1 (2026-07-07)

### ✨ Added

- 🏠 My Tidal opens on a new "Home" tab: a browse-style landing for your own
  account. A "Recently added" section previews your newest albums and tracks;
  clicking a card opens that album, and clicking a shelf heading ("Recent
  albums" or "Recent tracks") jumps to that tab sorted newest-first.
- ↕️ My Tidal can now be sorted (recently added, name, release date, or artist)
  with an ascending/descending toggle, the same control as the Search page.
- 👤 Opening an artist from inside My Tidal now shows an artist page scoped to
  your library: only the albums and tracks you have saved, not their whole
  catalogue. A "View full artist page" link opens their complete catalogue when
  you want it.
- 🖼️ The embedded cover art and the separate cover.jpg can now use different
  sizes. Open "Separate cover.jpg size" under Cover size in Settings, Metadata
  and artwork.
- 🎵 A separate cover.jpg can now be saved for single-track downloads too, not
  only full albums. Turn on "Also save for single tracks" under Save cover.jpg
  in Settings, Metadata and artwork (off by default, so nothing changes unless
  you ask for it).

### 🔧 Changed

- 🎛️ The FFmpeg download progress (in the setup pop-up and in Settings) now uses
  the same LED dot-matrix progress bar as the in-app updater.
- 🟩 The LED cells across the app (the popularity meter, the download and
  playback progress bars, and the FFmpeg and updater bars) now render as sharp
  squares instead of slightly rounded blocks.
- 🏷️ The "Clean album-artist tag" setting is now "Clean Album Artist", with a
  shorter description that fits its tile.
- 🗂️ My Tidal shows artists as a compact card grid instead of tall rows, so more
  fit on screen at once.
- 📁 Waves no longer starts with a default download folder. New installs pick a
  folder before the first download, with a prompt that links straight to the
  setting. Anyone who already has a folder set (including the previous default)
  keeps it. Users still on that old default are asked, before the download runs,
  whether to keep it or choose a new location: keeping it continues the download
  and settles the question, while choosing a new location holds the download so
  they can set a folder they can find, then start it again.

### 🐛 Fixed

- 🔁 A rare server response (an empty but otherwise successful segment) can no
  longer make a download re-fetch the same track over and over without end. Each
  part is now downloaded once, and the progress bar still settles at 100%.
- ⚡ A download no longer drives high CPU usage. The animated LED progress fills
  (on the download button, the queue rows, and the FFmpeg and updater bars) were
  redrawing the whole window on every screen refresh while they were active,
  which could push a CPU core to full load for the length of a download. They
  now animate on a shared lower-rate timer, so they look the same while using a
  small fraction of the CPU.
- ✅ A download that writes no file (for example on an account without an active
  TIDAL subscription, where playback is refused) now correctly shows as failed
  with a retry option, instead of incorrectly showing as downloaded.
- 🎨 The FFmpeg card's "Check for updates" button now uses the standard green
  button style instead of a grey outline, and "Remove" now uses the red danger
  style, matching buttons everywhere else in the app.
- 🌊 My Tidal no longer flashes a placeholder when you open it. It keeps the
  shelves it has already loaded and shows them instantly on return, and while a
  category is still loading (or is genuinely empty) the pane simply shows the
  ambient wave background, with no card or glyph that appears for a beat and
  fades away.
- 🎯 Opening a track's album no longer scrolls the page down to the track; it now
  lands already positioned on it, with no visible jump.

## 🚀 v0.1.0 (2026-07-06)

First public release of Waves: a native desktop TIDAL downloader built on the
Tidal-DL-NG engine (actively maintained as Tidaler) with a from-scratch
Qt Quick interface.

### ✨ Added

- 🖥️ A native dark "console" UI (PySide6 / Qt Quick): no web view, no Electron.
- 🧭 Browse: TIDAL's editorial front page (New Arrivals, TIDAL Rising, genres,
  moods, decades) rendered art-first, with hover Preview / Download controls
  and quality badges throughout.
- 🔍 Search-first navigation: one field searches artists, albums, tracks, videos,
  playlists, and mixes, and resolves pasted tidal.com links directly.
- ▶️ Full seekable track previews streamed from your own account, with a
  now-playing bar that follows you across views.
- 🎬 A built-in video player with seek, keyboard controls, and a per-video
  quality picker (up to 1080p) that can switch resolution mid-stream.
- 🎤 Artist pages (bio, discography, EPs and singles, top tracks) with one-click
  whole-artist downloads: per-source toggles, most-complete-edition selection,
  and features/compilations limited to the artist's own tracks.
- 🧩 "Best of both" album merging: when editions differ in tracks and quality,
  the download takes each song at its best, matched strictly by ISRC.
- 📚 A Plex-friendly library layout by default (Artist/[Year] Album/...), a
  clean album-artist tagging mode, and an explicit/clean version preference.
- ❤️ My TIDAL: favorite albums, tracks, artists, videos, playlists, and mixes
  with smooth virtualized scrolling.
- 📥 A grouped download queue (Completed / Downloading / Queued) with live
  per-track progress and per-album / per-artist roll-ups.
- 🛠️ One-click managed FFmpeg: Waves downloads a checksum-verified build for
  your OS and CPU, with a colour-coded status light in Settings.
- 🔄 Opt-in in-app updates: signed releases (Ed25519, fail-closed verification)
  installed from Settings with a one-click restart. Update checks are off by
  default and send no user data.
- 💾 Persistent page and artwork caches so previously seen pages render
  instantly, even on a fresh launch.

### 🐛 Fixed

- 🪟 FFmpeg jobs on Windows (FLAC extraction, video conversion, previews) run
  fully hidden, with no console windows stealing focus mid-download.
- ⚛️ Interrupted downloads can no longer leave a half-written file in the
  library: finished files are swapped into place atomically.
- 🛑 Downloads stop instantly on cancel or quit instead of waiting on a
  network read.
