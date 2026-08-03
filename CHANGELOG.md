# Changelog

All notable user-facing changes to Waves are documented here, newest first.
Each release section becomes the GitHub Release notes for that version, so keep
bullets short, objective, and written for end users.

Format per release: `## vX.Y.Z (YYYY-MM-DD)` followed by any of the subheadings
**Added**, **Changed**, **Fixed**, **Removed**, each a bullet list, always in
that order (a test enforces it). Section
headings and their bullets carry a leading emoji accent (for example ✨ Added,
🔧 Changed, 🐛 Fixed). Changes land under **Unreleased** as they are made;
cutting a release renames that section to the new version.

A bullet that closes a reported issue names it in full and links to it:
`([issue #11](https://github.com/iamprivacy/Waves/issues/11))`, never a bare
`(#11)`. These notes are read outside the repo (release emails, the website,
package managers), where a bare number is neither a link nor obviously an
issue. A test enforces it.

## 🗂️ v0.1.14 (2026-08-03)

### ✨ Added

- ✨ Track previews are seekable: hover the progress ring around the album
  art for a ghost marker and time readout, then click or drag to seek. The
  ring is now a smooth arc instead of blocky cells.
- ✨ The playback line along the bottom of the window is a seek bar: hover
  for a time readout, click or drag to jump. Slightly taller, with a
  glowing spark at the playhead.
- ✨ Album covers tilt toward the cursor and lift while hovered, springing
  back on leave. The tilt waits for the pointer to rest, so lists scrolling
  under a still cursor stay calm. Round track thumbnails and the Browse
  front page's big cards join in. Settings > Advanced turns it off for
  anyone who would rather artwork stayed flat and still.
- ✨ Hover video peek: rest on a video thumbnail and a floating preview
  grows out of it, playing with sound but no controls, so you can tell what
  a video is before opening it. Clicking it opens the full player without a
  break (the preview simply becomes the player, then steps up to your usual
  quality). Move away and it goes away.
- ✨ The status bar's wordmark now ends with the version you are running, and
  clicking it checks for an update on the spot: it reports back "up to date",
  or raises the usual update notice when a newer build is waiting.

### 🔧 Changed

- 🔧 The full-width green PLAY bar across video thumbnails, which covered a
  quarter of the picture, is now a small play mark in the corner over a
  soft shadow.
- 🔧 When the download folder is on a network drive that dropped off, Waves
  now keeps checking for it and resumes the held downloads on its own the
  moment the drive is back, instead of waiting for you to click Browse or
  "Try again". On a Mac, a remounted volume is noticed immediately.
- 🔧 A network folder that is mounted but slow to wake (macOS quietly drops
  idle connections and reconnects on first touch) no longer triggers the
  "isn't reachable" dialog: the download waits out the wake-up and starts by
  itself. While Waves runs, it also keeps the connection warm so the folder
  rarely goes to sleep in the first place.
- 🔧 The Browse button next to path settings stays at full strength once a
  path is set, so it always looks as clickable as it is.

### 🐛 Fixed

- 🐛 Waves no longer crashes at launch on Windows when another program
  (an antivirus scan, a backup tool, or a second Waves instance) briefly
  holds the settings file: the save retries for a moment and, at startup,
  falls back to in-memory settings instead of failing to open.
- 🐛 Finished downloads are flushed to stable storage before they get their
  final name, so a power cut mid-move can no longer leave a truncated file
  under a name Waves would trust. Zero-byte leftovers from an older crash now
  read as "not downloaded" instead of being skipped forever.
- 🐛 An update whose unpacked payload is missing the app executable now
  restores the previous install instead of leaving the app uninstalled.
- 🐛 The path preview for video templates now pads numeric tokens exactly the
  way real video downloads do.
- 🐛 Signing out now fully forgets the previous account: cached items can no
  longer be fetched or downloaded through the old session, and the old
  account's page snapshot can no longer be re-written to disk mid-sign-out.
- 🐛 Several long-session memory and freshness gaps are closed: drilled
  Browse pages and the ownership cache are now bounded, tile-art mosaics
  refresh weekly even when the app never restarts, and cache eviction can no
  longer race between loaders and drop a page repaint.
- 🐛 Re-sorting a My Tidal category while it is still loading more rows no
  longer splices the old order into the new list or skips a window of items.
- 🐛 Opening Mixes first no longer makes Playlists briefly render (and save)
  a folder-less list.
- 🐛 The download-folder auto-heal is stricter and fairer: it only follows a
  remounted volume that actually carries your library folder (never a
  different drive that merely shares the name), a healed folder now reaches
  the download already in progress, changing the folder mid-download no
  longer skips the new folder's reachability check, and a share with odd
  delete semantics no longer reads as dead.
- 🐛 The download queue can no longer lose a just-added row when another
  download finishes at the same moment, and an album finishing quickly no
  longer skips recording which tracks belong to it.
- 🐛 A failed preview no longer leaves an orphaned temp file behind, and
  quitting mid-FFmpeg-install no longer orphans a partial archive.
- 🐛 Quitting no longer risks "database is closed" errors from ownership
  records still being written.
- 🐛 Cancelling an app update now also works during the install phase, and a
  failed update no longer leaves a full extracted app copy in the config
  folder.
- 🐛 Path templates: {album_date} and {isrc} without a value no longer write
  literal braces into folder names, {album_artist} on a release with no
  artist credit no longer fails the download, and de-duplicated filenames at
  deep paths are no longer shredded to "\_01.flac".
- 🐛 A hiccup loading Mixes no longer makes My Tidal report "0 playlists".
- 🐛 RETRY works on every Browse page again: drilled playlist grids and
  playlist/mix/album pages used to clear the error, show "loading" and never
  load; the button now re-requests the right page.
- 🐛 An expanded album no longer sits on "Loading tracks…" forever after a
  search; the track list is re-fetched and a failure leaves the row
  re-expandable instead of dead.
- 🐛 Fast scrolling through long album lists can no longer carry one album's
  selected tracks over to another row and download the wrong tracks.
- 🐛 Clicking Install twice (in Settings and on the update toast) no longer
  runs two updaters over the same staging folder, and cancelling an update
  can no longer be undone by a stray second Install click.
- 🐛 RETRY on a failed download row works even after searching for something
  else in between; a row whose item cannot be re-fetched keeps its RETRY
  instead of dying silently.
- 🐛 On Linux, an update started from a build that was launched out of another
  application's AppImage environment no longer overwrites that application's
  file.
- 🐛 The Settings page now refreshes when the app changes a setting on its own
  ("Don't ask again", the player's video quality menu, the download folder
  auto-heal), instead of showing the old value until a manual save.
- 🐛 Picking a network share (\\nas\music) as the download folder now saves
  the real share path; it used to be saved as a relative folder that landed
  downloads next to the app's working directory.
- 🐛 Drilled Browse pages with link tiles (e.g. Record Labels) keep their
  cover mosaics on revisits and from the second launch onward.
- 🐛 DOWNLOAD ALL on a Browse playlist category now waits for your playlist
  folders to load first, so foldered playlists are not written a second time
  outside their folder.
- 🐛 Clicking an album's download button again while its edition scan is
  still running no longer queues the album twice into two folders, and a
  failed scan returns the button instead of leaving it stuck.
- 🐛 Going Back to an artist page that fails to load (e.g. offline) no longer
  stops the app from recording navigation history.
- 🐛 Tracks with very long names now download reliably to network (NAS) and
  external libraries: the temporary staging file no longer overflows the
  filesystem's name-length limit.
- 🐛 With "symlink to track directory" enabled, downloading a playlist whose
  tracks are already in the track library no longer crashes the playlist job;
  the playlist folder is created for the symlinks, and a failed symlink is
  logged instead of failing the download.
- 🐛 A brief network blip while sizing a track no longer fails the download:
  the progress bar just runs without a percentage. Redirected download links
  no longer show a stuck 0% bar.
- 🐛 A track whose full path exceeds the operating system's limit (mostly a
  Windows concern) now shortens its folder names to fit instead of silently
  saving into the user's home folder, outside the library.
- 🐛 "Reset application" now also erases previously exported diagnostic
  bundles, matching what the confirmation dialog promises.
- 🐛 Saving the page cache while the app is busy loading in the background can
  no longer crash a loader and leave the busy indicator stuck on.
- 🐛 Bulk downloads (a folder, a Browse category, a whole discography) started
  without FFmpeg set up now ask about FFmpeg first and continue after "Continue
  anyway", instead of freezing the button at "running" for the whole session.
- 🐛 Downloading two discographies that share an album (or a guest track) now
  completes both artist buttons; before, the second one could stay stuck at
  "running" forever.
- 🐛 A download button no longer dies for the session when an aged-out item is
  re-fetched while no download folder is set, or when the download-folder
  question is dismissed: the button returns to idle so it can be clicked again.
- 🐛 An artist page whose fetch partly failed (for example a rate limit on the
  albums list) is no longer saved or allowed to wipe the album grid on screen;
  the last good page stays until a complete fetch succeeds.
- 🐛 A failed album track-list fetch no longer erases what Waves had already
  learned about that album's tracks (ownership badges kept working data).
- 🐛 A failed playlist-folder scan no longer retries in a loop with the
  download button spinning forever; it stops, says so, and your next click
  retries.
- 🐛 The favourites list used by library-scoped artist pages is no longer
  silently truncated when TIDAL returns a short page, and a failed load is no
  longer remembered as "no favourites" for ten minutes.
- 🐛 A library tab whose very first load failed no longer saves an empty page
  as the truth; reopening the tab retries instead of showing an empty library.
- 🐛 Downloading a discography now refuses to run on a partial scan (some
  release lists failed to load) instead of quietly downloading a truncated
  discography and reporting success, or claiming "No albums to download".
- 🐛 A "best of both" album merge no longer skips tracks you already own in a
  different folder: every track lands in the merged album's own folder, so the
  album is complete on disk when the download reports done.
- 🐛 If one edition's track list cannot be fetched during a best-of-both scan
  (a network hiccup, a region-locked edition), that edition is kept and the
  merge is called off instead of silently dropping its exclusive tracks.
- 🐛 A merged album now remembers it was downloaded: its download button stays
  on DOWNLOADED after you reopen the album or restart the app, instead of
  flipping back as if nothing had been fetched.
- 🐛 The queue drawer shows a merged album at its real length, with every row
  progressing, instead of doubled-up rows half stuck at "pending".
- 🐛 Exporting a diagnostic report no longer switches your two diagnostics
  settings back off. Turning on verbose diagnostics and "also hide titles and
  searches", reproducing a problem, then clicking Export used to silently
  disable both and produce a report containing the very details you had asked
  to hide. Settings also now shows the true state of those switches when you
  reopen the page.
- 🐛 Previewing or downloading an artist from a card that came out of the
  search cache no longer freezes the window while it fetches.
- 🐛 A stalled connection to TIDAL while Browse is loading no longer blocks
  search, artist pages and downloads along with it.
- 🐛 An artist whose name is only punctuation (`?`, `*`, `<>`) no longer sends
  the download outside your download folder. On Windows those tracks were
  written to the root of the drive and still reported as done; on macOS and
  Linux the download failed with an unexplained error.
- 🐛 Dialogs now dim the window behind them. Every modal (download folder,
  bulk-download confirm, settings reset, factory reset, folder unreachable,
  FFmpeg, terms) and the sign-in panel were drawing their backdrop at 2%
  opacity, so the interface behind stayed fully bright. The video overlay, the
  play/pause glyph disc and the "buffering" text outline had the same fault.
- 🐛 Answering the download-folder question with "keep it", or ticking "Don't
  ask again" on the bulk-download confirm, no longer silently switches off FLAC
  extraction and video conversion on disk (the loss only showed up on the next
  launch), and no longer writes a path containing your username into
  `settings.json`.
- 🐛 `crash.log` is now scrubbed the same way every other log is. Crashes could
  previously write your username, home folder path and the name of the track
  being handled into that file, which the bug-report template asks you to paste
  into a public issue.
- 🐛 "Also hide titles and searches" now actually hides them. The switch had
  nothing to act on, so an exported report taken with it turned on still
  contained your search terms and media names.
- 🐛 An album or playlist containing one track that fails to download no longer
  loops forever, re-downloading and rewriting every other track in it on every
  pass. The download now finishes and the queue row completes, instead of
  staying stuck on "running" until cancelled.
- 🐛 Downloading from a Mac to a WebDAV network drive no longer litters the
  server with hidden 4 KB `._` companion files next to every track, cover and
  playlist (macOS metadata files that other systems show as ghost files).
  Generated m3u playlists also no longer pick up any existing `._` files as
  tracks.
- 🐛 Album rows in search results now show the artist name, clickable in green
  like everywhere else, both on the collapsed row and in the expanded album
  panel.
- 🐛 Playlists longer than 200 tracks now show every track: the track list
  stopped after two pages of the paged playlist endpoint, truncating long
  playlists even though the header count was right
  ([issue #12](https://github.com/iamprivacy/Waves/issues/12)).
- 🐛 Very long playlists scroll smoothly again. Waves now builds only the rows
  around what you are looking at and fills the rest in as you scroll, instead
  of building all of them at once, so a several-hundred-track playlist no
  longer stays sluggish the whole time you are on it. Download buttons also
  stopped building their progress bar while nothing is downloading. Together
  these cut the memory a browsing session holds by roughly eight times.
- 🐛 Browse and My Tidal keep their pages alive behind the scenes: going Back
  or Forward between the Browse front page and an open playlist or album is
  instant and lands exactly where you left off, with nothing visibly
  assembling, scrolling or reloading, and each My Tidal category (Albums,
  Tracks, Artists, Playlists, Mixes, Videos) holds its rows, scroll position
  and expanded albums for as long as the app runs instead of reloading every
  time you switch. Leaving for another tab and returning is just as seamless,
  and fresh favourites are still picked up quietly in the background without
  moving the page under you.
- 🐛 A page reached by returning to its section through the nav tabs no longer
  leaves you without breadcrumbs: the trail now always begins with the
  section's home pill (Browse, Search, or My Tidal), so there is always a
  visible way back up, not just the Back gesture.
- 🐛 Breadcrumbs no longer wipe in whenever a page opens: crumbs now appear
  in place, with only a barely-there fade when one is removed.
- 🐛 The launch animation hands over cleanly again: the version readout always
  finishes its drain before the wordmark zooms away and the interface fades
  up, instead of the two overlapping on a busy start.
- 🐛 The Settings page holds your place: changing a setting, or leaving for
  another tab and coming back, no longer collapses the sections you had opened
  or throws you back to the top of the page.

## 🗂️ v0.1.13 (2026-07-28)

### 🐛 Fixed

- 🌊 The opening WAVES animation is clean water again: the dark scroll-edge
  fades of the page loading behind it no longer show through as bands across
  the top and bottom of the launch screen.

## 🗂️ v0.1.12 (2026-07-28)

### ✨ Added

- 🖱️ The mouse "forward" side button now navigates forward, completing
  [issue #8](https://github.com/iamprivacy/Waves/issues/8) alongside the back
  button shipped in v0.1.11.
- 📁 Your TIDAL playlist folders now appear in My Tidal > Playlists
  ([issue #11](https://github.com/iamprivacy/Waves/issues/11)).
  Folders drill in like a file manager with a breadcrumb strip, and each
  folder row has a "Download all" button with a count badge that ticks down
  like an odometer as each playlist finishes, ending on a checkmark. On disk,
  playlists mirror your TIDAL folder tree (for example
  Playlists/Country/Bluegrass/My Playlist/), even when downloaded one at a
  time, via a new {folder_path} piece in the playlist path template. A
  playlist that is in no folder lands exactly where it always did, and a
  customized "Playlist path & name" setting is left untouched: add
  {folder_path} wherever you want the mirroring. Files already on disk are
  not moved, so a foldered playlist you download again lands in its new
  nested spot beside the old flat copy; drop {folder_path} from the setting
  to keep the previous layout.
- 📄 Playlist rows in My Tidal now open the playlist page (the same art
  header and track list playlists get in Browse), inside folders too.
- 🗂️ Browse gains a folder-style "All Playlists" section: drill from playlist
  folders (moods, genres, decades) into a grid of just that category's
  playlists, no albums or tracks in the way. Hovering a folder offers
  PREVIEW and DOWNLOAD ALL: the whole category queues in one click, with
  the count badge ticking down as playlists finish. A confirm states how
  many playlists are about to download ("Don't ask again" available). Both
  landing styles get there: in the console style the section headlines are
  now openable too, so the genre and decade folders are reachable without
  switching to the art view.
- ↩️ Every path, name template and separator in Settings now carries its own
  default. A field you have changed offers "Restore default" beside its label
  and goes back to the shipped value on its own, with nothing else reset; an
  unchanged field just reads "Default", so you can see at a glance which
  settings you have edited.
- 🧿 Browse now carries the personalized shelves from TIDAL's home page:
  "Essentials to explore", "Popular playlists on TIDAL", "Albums you'll
  enjoy", "Suggested new albums for you", "Your forgotten favorites", your
  genre rows and more, each one downloadable like any other Browse row.
- 🍞 The "Back to X" bars on artist and Browse pages are now a breadcrumb
  trail: every step of how you got there as clickable pills, with the page
  you are on lit at the end. Click any crumb to jump straight back to that
  spot (scroll position and expanded albums included). Long trails fold
  their middle behind a "…" pill that expands in place and turns into "›‹"
  to fold back down. The trail shows where you drilled in, not the tabs you
  passed through: switching to Browse, Search or My Tidal starts it fresh
  rather than piling up Search > My Tidal > Search. Back and Forward still
  cross between tabs as they always did.

### 🔧 Changed

- ✨ The preview and download controls on covers no longer fade in on hover:
  they rise from the bottom edge with a soft bounce, and drop back out of
  sight when the pointer leaves. Settings > Advanced > "Hover controls slide
  in" turns the movement off and brings the plain fade back.
- 🧹 The two artist separator boxes now share one row instead of taking a
  full-width box each for a single character.
- 🔲 The download and preview buttons carry a slightly heavier outline, and so
  does the divider between PREVIEW and DOWNLOAD, so they hold their edge over
  busy artwork.
- 💡 Hovering a download control now lights it up: the outline of the part
  under the pointer (just that half, on the PREVIEW | DOWNLOAD pills) turns
  bright and breathes slowly, and fades back out in place when you leave.
- 🌊 The launch screen opens more gently: the WAVES wordmark is larger, the
  version sits tucked under it instead of adrift below, and both fade up
  together rather than appearing all at once. The hand-over to the app now
  reads as two clean beats as well: the version empties out completely, and
  the wordmark starts zooming only once it has, instead of the tail of one
  running under the start of the other.
- 🎞️ Rows that scroll sideways (the Browse and My Tidal shelves, the folder
  tiles, the artist strip in search) now fade at their left and right edges,
  the same soft edge the top and bottom of a page already had, so covers
  slide out of frame instead of being cut off. The fade only appears on the
  side you have actually scrolled past: a row that fits on screen keeps both
  edges clean.

### 🐛 Fixed

- 👯 Browse no longer shows the same covers twice. TIDAL serves one set of
  releases under several headlines ("New Albums", "New releases for you" and
  "Suggested new albums for you" were the same albums three times), so a
  shelf whose items all appear in a larger one is now dropped and only the
  row carrying the most of them is shown. Shelves that merely share a few
  entries are untouched.
- 💬 Settings descriptions read properly again. Every comma in them was being
  shown as a semicolon ("16 Bit, 44,1 kHz" appeared as "16 Bit; 44,1 kHz"),
  and the artist separator fields advertised a default of "; " when they
  actually ship with ", ".
- 🧭 Opening an album or playlist you had visited earlier in the session now
  remembers where you came from. Before, Back skipped that spot (a playlist
  folder, a My Tidal shelf) and jumped to somewhere older, often Search.
- 🎯 Clicking an artist or album name while a search (or a pasted TIDAL link)
  was still resolving no longer yanks you to the search page when its results
  finally arrive: late results are dropped, your click wins.
- 🌊 The launch sequence stays fluid, and hands over to a finished page. When
  Browse's landing refreshed behind the opening water (its editorial and For
  You shelves change between sessions), the rebuild froze the animations for
  a beat; and the wordmark lifted on its own schedule, so a landing still
  assembling was watched dropping in shelf by shelf. The refresh now builds
  in the background, and the launch screen holds until the page behind it is
  complete.

## 🖱️ v0.1.11 (2026-07-21)

### ✨ Added

- 🖱️ The mouse "back" side button now navigates back, same as the Back bar
  and the macOS three-finger swipe
  ([issue #8](https://github.com/iamprivacy/Waves/issues/8)).

### 🐛 Fixed

- ✂️ A download whose final piece fails can no longer be saved short and
  reported as done. TIDAL delivers tracks in pieces, and on very short tracks
  the server lists one piece more than the audio actually has, so a failure on
  the last piece used to be waved through for every track. Waves now reads the
  track's manifest to tell the harmless extra piece apart from a real final
  piece, and a genuine failure there marks the download failed instead of
  leaving a truncated file.
- 🚀 Waves can no longer hang on "Signing in…" at launch. If an internal cache
  file had been damaged, the sign-in never finished: the startup screen sat
  there forever and you stayed signed out. An unreadable cache is now discarded
  and start-up carries on without it.
- 🔑 A network problem at launch no longer signs you out for good. If Waves
  could not reach TIDAL while restoring your saved sign-in, it deleted the
  saved credential, so the only way back was a full re-login. It now keeps the
  credential and simply reports you as signed out; retrying, or restarting once
  you are back online, restores the session. A real rejected or damaged sign-in
  is still cleared, as before.
- 💾 Your settings and saved sign-in are now written whole or not at all. A
  crash or power cut during a save could leave a half-written file, which meant
  corrupted settings or a lost sign-in on the next launch. Waves now writes to a
  temporary file and swaps it in, so what is on disk is always either the old
  version or the new one.
- ⬇️ An album or playlist where some tracks failed no longer reports a clean
  "Done". A 20-track album that lost one track rode its 19 successes to a green
  done and dropped the missing one silently. It now shows as failed and says
  "1 of 20 tracks failed"; retrying re-downloads only the tracks you are
  missing, so it is cheap.
- 🚨 A download of a partially owned album now reports a failure when none of
  its new tracks could be fetched (for example on an account without download
  entitlement). Before, the tracks skipped as already-in-your-library counted
  as successes, so the job could show a green "done" while every new track had
  silently failed. An album where everything is already owned still completes
  as a success.
- 🎚️ Changing the audio quality in Settings now applies to the very next
  download; previously new downloads kept the old quality until the app was
  restarted ([issue #9](https://github.com/iamprivacy/Waves/issues/9)).
- ↩️ Going Back from a playlist (or any long page) to Browse now returns you to
  the spot you left, instead of jumping to the top of the page.

## 🔎 v0.1.10 (2026-07-15)

### ✨ Added

- 🧹 Advanced settings gained two reset actions at the bottom of the section:
  "Reset all settings" puts every option back to its factory default (you stay
  signed in), and "Reset application" erases everything Waves has saved on
  this computer (settings, sign-in, caches, ownership history and logs, never
  your downloaded music) and closes the app so the next launch starts like a
  brand-new install. Both ask for confirmation before anything happens.

- 🪟 Waves now remembers its window size, position and maximized state, and
  restores them the next time you open it. If the monitor it was on is gone or
  its resolution changed, the window is nudged back onto a visible screen so it
  can never open off-screen. On a first launch, before anything has been
  remembered, the window opens centered at a 4:3 size instead of wherever the
  OS drops it.
- 🖱️ Clicking the blank space of a track row now goes where clicking the
  track title goes (the track's album page, or the video player), so most of
  the row is clickable while the artist and album links beneath the title
  still drill into their own pages.
- 🔎 Clicking or tabbing into the search box selects the whole current term,
  so you can start typing to replace it (no highlighting or backspacing first).

### 🔧 Changed

- 🌊 While a page loads, the finished page now fades in gently over the ambient
  water animation rather than snapping on in one hard paint. The "Reading the
  wire…" hint rides that same living water while it works.
- 🎚️ The mini player in the bottom bar now sits in the right corner while that
  corner is free, leaving the middle of the bar clear. If an update notice
  needs the corner, the player slides to the centre in one smooth move and
  slides back when the corner frees up again.
- ⚡ The wave-logo box now carries an occasional lightning storm at rest:
  seven strikes spread across a slow 20-second loop, tall bolts framing the
  box at the left and right, smaller ones scattered between, and a big centre
  strike that lights the whole box with a brief flash. Hovering the box still
  summons the full storm.
- 🕶️ The soft darkening at the top and bottom scroll edges now appears only
  while rows are actually being cut off there. At the top or bottom of a page
  it fully lifts, so artist artwork, heroes and the back bar are no longer
  dimmed when the page is not scrolled.
- 🧭 Collapsing an expanded section with SHOW LESS now brings you back to the
  top of that section (with a little breathing room above), instead of
  dropping you at whatever the bottom of the shorter page happens to be.
- 🔤 Track titles in track rows (search results, top tracks, album pages,
  recent tracks) are now slightly larger and a touch heavier than the artist
  and album line beneath them, so the title leads the row at a glance.
- 📂 The Browse… button next to folder and file settings lights up green while
  the field is still empty (it is the thing to click) and settles to a faded
  green once a value is set.
- ✳️ The SHOW ALL links under top tracks, search sections and the artist strip
  are now a soft mint green at rest, so it is clear at a glance that they can
  be clicked (they used to sit grey until hovered).
- 🔊 ReplayGain tags are now written by default, so players that support it can
  level volume across your library without changing the audio. This update
  switches it on for existing installs too; you can turn it back off any time
  under Settings > Advanced > Write ReplayGain tags. Tracks TIDAL never measured
  are left untagged instead of stamped with a wrong level, and gain is written
  in the standard "-7.36 dB" form.
- 🔎 Every section on the search page now shows just its first few results with a
  SHOW ALL beneath it, so the page reads as a quick overview instead of a long
  page you scroll past: albums, tracks, videos, playlists, and mixes each show
  their first 5, and artists sit in a single sideways-scrolling row. Whichever
  sections you open are remembered and stay open on your next search, per
  section, so you do not have to expand them again each time. Picking a single
  category from the filters still shows everything in it. Results collapsed
  behind a SHOW ALL do not download their covers until you expand them, so the
  art you can actually see loads sooner on a new search.

### 🐛 Fixed

- 🖼️ Cover art keeps loading and the app stays smooth to scroll while
  downloads run: progress updates no longer redraw every download control on
  the screen dozens of times a second.
- 📁 On macOS, a download folder on a NAS or external drive stays valid
  between launches instead of reading as unreachable until you re-pick the
  same folder. macOS grants that access when you pick the folder (usually
  silently, sometimes with a one-time prompt) and now remembers it.
- 🖱️ The mouse cursor works normally on the search page again: buttons show
  the pointing hand instead of the plain arrow. The focused search box was
  quietly overriding the cursor for the whole window.
- ⌨️ Clicking outside any text field now releases it, the blinking cursor and
  green outline go away, matching how the search box already behaved. Settings
  fields like the download folder path used to hold their outline until you
  clicked another field.
- 🏷️ The search category filters (All, Artists, Albums, and so on) stay put
  instead of fading out and back in on every search, and they appear as soon
  as results arrive instead of only after the result cards finish drawing.
- 🖼️ Artist artwork no longer flickers to grey boxes while you resize the
  window; covers hold their image steadily instead of reloading on every frame
  of the drag.
- 🎞️ Result rows no longer hold a stale look after switching tabs or changing
  the result filter; the subtle curve at the top and bottom edges now settles
  into place right away instead of only correcting once you scroll.
- 🪟 Resizing the window on the search page no longer stutters or jumps: the
  matching and similar artist cards now hold a fixed size, so a resize reveals
  more or fewer of them instead of rescaling every card on screen as you drag.
- 🖼️ Track rows in search results no longer show an occasional blank grey circle
  where the album cover should be. The small round covers now load the same
  reliable way as the rest of the app (with caching and a retry), instead of a
  one-shot fetch that could silently fail and leave the circle empty until you
  reopened the album.
- 🧭 Browse stays current while you keep it open. Its New, Top, and For You rows
  now refresh on a timer as well as when you return to the tab, so an app left
  running for days follows what TIDAL is featuring instead of staying pinned to
  whatever loaded when you first opened it.

## 🚀 v0.1.9 (2026-07-14)

### ✨ Added

- ⏳ Download buttons acknowledge the click instantly with an animated
  QUEUED state, then flip to the usual progress bar when the download
  starts.

### 🔧 Changed

- 🚀 Finished tracks land on network drives much faster: a few large writes
  instead of hundreds of tiny ones, with far less folder and bookkeeping
  chatter per album.
- 🧵 Downloads use fewer threads and less memory.
- 🟩 The playback ring around track art in search results uses the same
  square LED blocks as every other progress bar and stays inside the
  artwork tile.

### 🐛 Fixed

- 🧊 No more multi-second freezes while downloading to a network drive
  (macOS SMB shares especially): finished-track bookkeeping now runs fully
  in the background.
- 📡 A busy network share no longer trips the "Download folder isn't
  reachable" dialog over and over: busy is no longer mistaken for dead, and
  slow shares get more time to answer.
- 🔁 "Try again" on the unreachable-folder dialog retries every queued
  download, not just the most recent click.
- 📂 A brief network-share hiccup no longer fails a whole album: creating
  the destination folders now retries with backoff.
- 🔄 Quitting during a background update check no longer records a bogus
  "background worker crashed" error in the diagnostic log.
- 🖼️ Cover art no longer stalls on its loading placeholder or fails in
  batches while downloads are running.
- 🏷️ Downloaded badges stop re-checking the download drive while you scroll
  during a download; freshly finished tracks still update instantly.
- 📸 Two tracks finishing at the same moment can no longer trip a "File
  exists" error while writing the shared album cover to a network drive.

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
