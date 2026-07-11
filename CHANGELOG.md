# Changelog

All notable user-facing changes to Waves are documented here, newest first.
Each release section becomes the GitHub Release notes for that version, so keep
bullets short, objective, and written for end users.

Format per release: `## vX.Y.Z (YYYY-MM-DD)` followed by any of the subheadings
**Added**, **Changed**, **Fixed**, **Removed**, each a bullet list. Section
headings and their bullets carry a leading emoji accent (for example ✨ Added,
🔧 Changed, 🐛 Fixed). Changes land under **Unreleased** as they are made;
cutting a release renames that section to the new version.

## Unreleased

### 🐛 Fixed

- 🔥 Downloads no longer peg the CPU or freeze the window. Every track segment
  was opening a brand-new encrypted connection (a fresh TLS handshake) instead of
  reusing one, so a high-resolution album fanned across many parallel connections
  became a storm of handshakes. Handshake crypto runs across all cores, so it
  could drive CPU to 100% and make the app unresponsive the moment a download
  started (worse the more cores a machine has). Segments now reuse pooled
  connections, which cuts the download CPU cost by roughly 16x and downloads
  faster, on any hardware and without lowering the parallelism.

## 🔄 v0.1.3 (2026-07-10)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### 🐛 Fixed

- 🔄 Browse now keeps up with TIDAL. The landing page (New tracks, New albums,
  Top playlists, and the rest) used to load once per session and then stay
  frozen, so "new" rows drifted days out of date, and scrolling deep into a row
  could surface newer tracks below older ones. Every return to the Browse tab
  now quietly re-checks the editorial pages and repaints only what actually
  changed, and an open row listing snaps to the fresh ordering too.

## 🩺 v0.1.2 (2026-07-09)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### ✨ Added

- 🩺 A new Diagnostics section in Settings, built around a privacy-guarded
  logger: every line is scrubbed of identity information (username, file
  paths, network addresses, account details, tokens) the moment it's written,
  not filtered afterward, so nothing sensitive is ever on disk to begin with.
  Turn on "Verbose diagnostics", reproduce the problem, then click "Export
  report" for a single text file that's safe to attach to a public bug
  report. An optional switch also hides what you searched for and the names
  of tracks, albums and artists. Verbose mode also watches for interface
  freezes and records what the app was doing when one happened. See the
  [README](README.md#diagnostics) for how the guard works.
- 🩺 Waves now keeps a crash log. If the app ever crashes or freezes, the
  technical details land in `crash.log` inside the Waves config folder
  (`~/.config/Waves` on macOS and Linux, `%USERPROFILE%\.config\Waves` on
  Windows), and the bug report form explains where to find it. The log holds
  only version numbers and stack traces of the app's own code, never personal
  data.

### 🔧 Changed

- 🎛️ The FFmpeg card in Settings now mirrors the Updates card: status and
  actions on the left, and a new "Check for updates automatically" toggle
  (every launch or once a day) on the right. Like app updates, the automatic
  check is off by default, only notifies you, and sends none of your data.

### 🐛 Fixed

- 🎨 The Settings section icons now all share the same green. The FFmpeg
  section's icon still works as a status light (red when missing, yellow for
  a system copy), but a healthy managed install now reads as the standard
  accent instead of a slightly minty green that made it stand out.

- 🌊 A settings section with an odd number of on/off tiles no longer leaves a
  blank spot in the grid; the empty slot is now filled with a calm ASCII-wave
  tile in the Waves style.

- 🪟 The window can no longer be resized narrow enough to cut off the left side
  of the top bar. The minimum window width now follows what the top bar
  actually needs.

- 📥 Adding several artists to "download discography" at once no longer crashes
  or stalls the app. The release scans now run one after another instead of all
  at the same time, so you can queue as many artists as you like and they simply
  line up. Downloads themselves still run in parallel as before.

- 🖼️ Cover art the app has seen before now paints straight from the local cache
  on launch (the Browse landing page no longer flashes the loading placeholder
  while every cover is re-checked against the server).

## 🌊 v0.1.1 (2026-07-07)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

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

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

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
