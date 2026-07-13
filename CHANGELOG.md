# Changelog

All notable user-facing changes to Waves are documented here, newest first.
Each release section becomes the GitHub Release notes for that version, so keep
bullets short, objective, and written for end users.

Format per release: `## vX.Y.Z (YYYY-MM-DD)` followed by any of the subheadings
**Added**, **Changed**, **Fixed**, **Removed**, each a bullet list. Section
headings and their bullets carry a leading emoji accent (for example ✨ Added,
🔧 Changed, 🐛 Fixed). Changes land under **Unreleased** as they are made;
cutting a release renames that section to the new version.

## 🌊 v0.1.8 (2026-07-13)

### ✨ Added

- 🌊 Waves now makes a splash on open (pun intended): a new launch
  sequence that also shows the version you are running.
- 📁 Path templates now show a live example: each "path & name" field in
  Settings → File organization displays the exact folders and file name a
  download would get, updating as you type. The example uses a built-in
  generic sample (Example Artist / Example Album), so it works before
  anything is downloaded; unknown `{tokens}` are highlighted so typos jump
  out.
- 🏷️ New "Want to know more about these paths and tags?" reference under
  Settings → File organization, below the path & name fields: every
  available `{token}`, grouped by category, with a short description, an
  example value, and a one-click copy button.

### 🔧 Changed

- 📜 Expanding an album now gently scrolls it up toward the top of the
  window, so the track list it reveals is on screen instead of below the
  fold. Rows already near the top stay where they are.
- ⬆️ A subtle TOP pill rides the top of any page you scroll down; one
  click glides you back to the top.
- 🎞️ Pages now scroll with more depth: rows fade in and out of frame at
  the top and bottom edges, with a subtle rolodex tilt as they cross,
  instead of being cut off hard at the chrome edge.
- 🗂️ The My Tidal tab now reopens in the category you left, exactly as you
  left it; pressing it again returns to Home (like Browse's second press).

### 🐛 Fixed

- 📰 The TIDAL Magazine tile no longer appears in Browse rows like Moods &
  Activities: it is editorial articles, so opening it always showed an empty
  page.
- ⚡ Expanding an album's track list is instant after the first time: track
  lists are now remembered for the session instead of re-fetched each time.
- 🏠 My Tidal opens instantly after launch: the Home shelves are remembered
  from your last session and shown immediately, then quietly refreshed in
  the background.
- 🌱 Home and the library lists stay current while the app runs: new
  favourites show up on their own, no restart needed.
- 📜 Scrolling or re-sorting the Playlists and Mixes tabs no longer
  re-downloads your entire collection for every page, so large collections
  stay snappy.
- 🔍 Search results no longer freeze the app while they appear: the cards
  are built in the background and the finished page appears all at once,
  same look as before.
- 🔁 Repeating a recent search shows its results instantly, popularity
  meters included.
- ▶️ Replaying a recent track or artist preview starts instantly instead of
  rebuilding the clip each time.
- 🎯 Going Back to an artist page now lands exactly where you left it:
  scroll position, expanded albums, expanded bio and top-tracks all come
  back, with no visible jump. Opening a different artist also starts at the
  top of the page instead of inheriting the previous page's scroll offset.
- 🏄 Switching to the Browse tab no longer stutters: the shelves are built
  in the background and the finished page appears all at once, same look as
  before.
- ⚡ Returning to Browse (and the My Tidal home) is instant: the cards stay
  alive while the tab is hidden, so switching back just shows them.
- 🖱️ Top-bar tabs (Browse, Search, My Tidal, Settings) now respond the
  instant you click: the tube starts expanding on the click itself with the
  static riding on top, and the outgoing tab collapses in half the time.
  Same look, no dead time.
- 📺 Video playback in installed builds now really selects a resolution:
  videos start at the best your connection and Video-quality setting allow,
  the quality menu works, and seeking jumps straight to the chosen spot.
  Video downloads get the same fix.

## 📦 v0.1.7 (2026-07-12)

### ✨ Added

- 🍺 Waves is now installable on macOS via Homebrew:

  ```bash
  brew tap iamprivacy/waves && brew install --cask waves
  ```

  Keeping it current is a `brew upgrade`, or just the usual Update & restart
  button in the app.

- 🐧 Linux releases now also ship as an AppImage: one file, no unzipping, no
  install step, just download and run. The built-in one-click updater works
  on it too, replacing the file in place.

### 🔧 Changed

- 📁 Waves settings now live where your system expects them: Application
  Support on macOS and AppData on Windows (Linux keeps ~/.config). Existing
  settings, login and history move over automatically the first time the new
  version starts; nothing needs to be set up again.
- 📦 Copies of Waves installed through a package manager (like the Homebrew
  tap) update through that package manager instead: the familiar Update &
  restart button simply runs its one-line upgrade for you, same one click,
  and the manager's records stay correct. Direct downloads keep the
  built-in updater, unchanged.

### 🐛 Fixed

- 🐛 Album, playlist and mix DOWNLOADED badges now reflect what is actually
  on disk, not just the current session: an album downloaded on its own now
  shows DOWNLOADED the same as one downloaded as part of a full discography,
  and playlists and mixes pick up the badge too. Collapsed album rows and
  browse shelf cards get the same accurate badge as an opened page, learned
  locally the first time Waves sees that album, playlist or mix, so it never
  needs a network check to answer.

## 💾 v0.1.6 (2026-07-12)

### ✨ Added

- ⬇️ Waves now remembers what it has downloaded. Track and video download
  buttons show DOWNLOADED across sessions when the file from an earlier
  download still exists on disk, and clicking a DOWNLOADED button will not
  re-download the file. Album, artist, playlist and mix downloads skip the
  tracks you already have too (marked HAVE in the queue), fetching only
  what is missing. The check follows the real file: delete or move it and
  the item downloads again. Quality upgrades still work: raise the audio
  quality setting and copies below it show DOWNLOAD again, and downloading
  replaces the old file in place with the better one.

  An honest limitation for now: this only knows about downloads made from
  this version onward, so files downloaded before updating, or with other
  tools, are not detected. Proper library detection and management
  (recognizing music already in your folders, whatever put it there) is
  coming in a bigger future update, no ETA yet.

### 🐛 Fixed

- 🐛 Download progress bars no longer flash or jump backward when a track finishes while others are still downloading or finalizing.
- 🖥️ Checking whether a track is already downloaded no longer risks freezing the app when the download folder sits on a network drive that has dropped or gone slow: the check now answers from a short‑lived cache and refreshes in the background, so the interface stays responsive either way.

## ⚡ v0.1.5 (2026-07-11)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### 🔧 Changed

- 📋 The Completed section of the download queue now lists the most recently
  finished item first, oldest at the bottom.
- 🔍 Pressing Search puts the cursor in the search bar, ready to type. Any
  previous query is selected, so typing starts a fresh search.
- 🎤 Artist pages show the first 5 top tracks with a SHOW ALL link for the
  rest, and the Top tracks, Albums and EPs & Singles sections are now
  collapsible. A collapsed section stays collapsed on every artist page
  until you reopen it, so album hunters skip the top tracks for good.
- ✨ Download bars no longer freeze at 100% while the final steps run
  (merging, decrypting, tagging): the dots twinkle softly until the item is
  actually done. Applies to the queue rows, download buttons and the small
  hover-card bars alike.

### 🐛 Fixed

- 🖥️ Starting a download no longer spikes the CPU to 100% (most visible on
  Windows). TLS setup for the segment connections is now done once and
  shared, and at most 10 connections open at a time instead of up to 60 at
  once.
- 🎬 The connection check that picks the starting video quality no longer
  counts connection setup time as slowness, so slower machines get a more
  accurate (often higher) starting quality.
- ⚡ The Download button reacts instantly. The safety check that verifies the
  download folder is reachable used to run before anything appeared on
  screen, which could freeze the click for several seconds when the folder
  lives on a network drive. The queue row now appears immediately and the
  check runs in the background; an unreachable folder still shows the same
  warning with Try again.

## 🔄 v0.1.4 (2026-07-11)

<p align="center">
  <strong>🌊 If you enjoy Waves, a star on this repo goes a long way, and if you'd like to help me afford to keep developing it, consider donating.</strong>
</p>

<p align="center">
  <a href="https://www.buymeacoffee.com/iamprivacy"><img src="https://img.shields.io/badge/-Buy%20me%20a%20coffee-ffdd00?style=flat&logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
</p>

### ✨ Added

- 🔔 A new-release toast that carries the whole update. When the automatic
  update check (opt-in) finds a new version, a small notice appears at the
  bottom of the window and INSTALL runs everything right there: download,
  signature verification and staging with live progress, then a RESTART NOW
  prompt (CANCEL available mid-install, RETRY on failure). It stays up until
  you act on it or dismiss it, and returns at every launch until you do (per
  version). A manual check from Settings never toasts (you are already on the
  updater card), and the gold update notice in the status bar plus the full
  Settings updater card remain as before.
- 🛡️ Downloads now check for FFmpeg up front. Starting a download without
  FFmpeg used to quietly produce degraded files (FLAC left in its stream
  container, no video conversion, track lengths unrepaired so strict players
  show 0:00). The download is now held while a dialog explains the problem,
  with "Set up FFmpeg" jumping straight to the one-click install and
  "Continue anyway" available for those who want the files regardless (asked
  once per session).
- ▶️ Artist cards in My Tidal now carry the same compact preview player as the
  Browse cards, centered under the artist's name: one click plays that
  artist's top track (with the elapsed counter and STOP control), filling the
  blank strip at the bottom of each card.

### 🐛 Fixed

- 🔥 Downloads no longer peg the CPU or freeze the window. Every track segment
  was opening a brand-new encrypted connection (a fresh TLS handshake) instead of
  reusing one, so a high-resolution album fanned across many parallel connections
  became a storm of handshakes. Handshake crypto runs across all cores, so it
  could drive CPU to 100% and make the app unresponsive the moment a download
  started (worse the more cores a machine has). Segments now reuse pooled
  connections, which cuts the download CPU cost by roughly 16x and downloads
  faster, on any hardware and without lowering the parallelism.
- 🌡️ Starting an album download no longer causes a brief CPU spike. The
  connection pool that keeps downloads cheap was being rebuilt from scratch for
  every queued album, so each one began with a burst of encrypted-connection
  handshakes (CPU jumps to 100% for a moment, then settles). The warm pool is
  now shared across the whole session, so only the very first download pays
  that cost.
- 🎧 Downloaded tracks now carry their real length everywhere. Tracks delivered
  as segmented streams (most AAC and lossless files) were saved in a container
  whose header reported a length of zero, so strict players (for example Winamp)
  showed 0:00 and refused to play them, even though VLC played the same file
  fine. Waves now rebuilds the container after downloading so the correct
  duration is written, keeping the audio bit for bit identical (this needs
  FFmpeg).
- 💾 Downloads to a network drive or NAS no longer fail one by one after the
  drive drops off (for example when the laptop lid was closed). Waves now
  checks that the download folder really accepts writes before starting, and
  if the same share simply reconnected under a new name (macOS often remounts
  it with a "1" suffix) it follows the live mount automatically. If the folder
  is genuinely unreachable, the download is held and a dialog explains what
  happened, with "Try again" (after reconnecting) and "Choose a new location"
  actions, instead of a wall of silently failed tracks.
- 📂 Finished downloads now tuck themselves into the queue's Completed section
  even while the queue panel is closed. The 5-second tidy-up only ran while a
  row was on screen, so opening the queue after a big batch made every finished
  row fold away at once, in one distracting cascade. Rows you are watching
  still fade out gently; everything else is already in place when you look.
- 🪟 Windows: downloads no longer flash open a command-prompt window for every
  track. FLAC extraction and format conversion run ffmpeg as a child process,
  and the flag that keeps that process windowless was being discarded before the
  process started, so a console popped up (and vanished) for each one. It now
  runs fully hidden.

### 🔧 Changed

- 🔍 The Search tab now remembers where you were. Coming back from My Tidal or
  Browse returns you to the exact page you left, artist page, expanded album,
  scroll position and all, instead of dropping you back on the results list.
  Pressing Search again while already on it starts a fresh, blank search, the
  same two-step behaviour the Browse tab already had.
- 📊 Album, playlist, and discography progress bars now move continuously.
  They used to sit still and then jump each time a whole track finished; the
  bar (and the matching media buttons) now creeps along with the tracks that
  are currently downloading, and the "N/total tracks" count only ticks up
  when a track really completes.
- 🛠️ When FFmpeg is missing, Waves says so instead of quietly degrading. Without
  FFmpeg it cannot extract FLAC, convert video, or repair track length, so it now
  warns once per session, and it records which FFmpeg it used (managed, custom,
  system, or none) in your settings file so a pasted config shows whether FFmpeg
  was available. The FFmpeg path field itself is left untouched.
- 💡 The dot-matrix progress pill's status text now sits on a dark backing
  plate, so it stays readable as the lit cells fill in behind it (updater
  cards, FFmpeg installs, and the new update toast all share the fix).

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
