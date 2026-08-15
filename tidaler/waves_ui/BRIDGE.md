# The QML/Python bridge

`WavesBridge` (backend.py) is exposed to QML as the context property
`waves`. QML calls its `@Slot`-decorated methods; the bridge answers by
emitting signals, which Main.qml consumes in one big
`Connections { target: waves }` block. Because slots run their blocking work
on thread pools and emit from worker threads, Qt delivers every signal on
the GUI thread (queued connection); QML handlers never see a race.

The signal declarations in backend.py carry inline comments with the exact
payload shapes. This file is the map of which signal belongs to which
feature.

## Session and status

| Signal                      | Fires when                                                             |
| --------------------------- | ---------------------------------------------------------------------- |
| `loggedInChanged`           | Login/logout completes (property `loggedIn`)                           |
| `sessionResolvedChanged`    | The restored session finishes resolving (property `sessionResolved`)   |
| `statusChanged`             | The status-bar text changes                                            |
| `busyChanged`               | A blocking operation starts/ends                                       |
| `loginUrlReady(url)`        | The browser-login URL is ready to open                                 |
| `backRequested`             | The platform back gesture (macOS trackpad swipe) asks to navigate back |
| `motionBgChanged`           | The motion-background preference flipped; Main.qml re-reads it         |
| `diagnosticsExported(path)` | A diagnostics export finished (`""` = failed)                          |

## Search, artist pages, library

| Signal                                    | Fires when                                                                            |
| ----------------------------------------- | ------------------------------------------------------------------------------------- |
| `searchResults(payload)`                  | A search or pasted-link resolve finishes; payload holds per-kind lists of plain dicts |
| `albumTracksLoaded(albumId, tracks)`      | An album's ordered track list arrives (album expansion)                               |
| `artistLoaded(payload)`                   | An artist page (bio, discography, top tracks) is ready                                |
| `artistMetaLoaded(artistId, popularity)`  | Late-arriving artist metadata                                                         |
| `libraryLoaded(category, items, hasMore)` | First page of a My Tidal category (replace)                                           |
| `libraryMore(category, items, hasMore)`   | Next page (append, infinite scroll)                                                   |
| `homeLoaded(sections)`                    | My Tidal's Home landing (Browse-shaped shelves, account-scoped)                       |

## Browse (editorial pages)

| Signal                         | Fires when                                                          |
| ------------------------------ | ------------------------------------------------------------------- |
| `browseLoaded(payload)`        | The Browse landing page (sections + genre/mood/decade chips)        |
| `browsePageLoaded(payload)`    | One drilled-into page, keyed by its TIDAL api path                  |
| `browseSectionMore(payload)`   | A section's "load more" page                                        |
| `browseTileArt(apiPath, urls)` | Cover mosaic for one genre/mood/decade tile, streamed progressively |

## Download queue

| Signal                                                             | Fires when                                                                       |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------- |
| `queueChanged(rows)`                                               | Any queue mutation; carries the whole queue as dicts                             |
| `queueItemProgress(qid, pct)`                                      | A queued item's aggregate progress ticks                                         |
| `queueTracksLoaded(qid, tracks)`                                   | Full per-track snapshot for an expanded queue row                                |
| `queueTrackState(qid, row)`                                        | One track's lifecycle change inside a job                                        |
| `queueTrackPct(qid, map)`                                          | Batched live percentages for downloading tracks                                  |
| `pausedChanged`                                                    | Global pause/resume toggled                                                      |
| `downloadProgress(mediaId, pct)` / `downloadState(mediaId, state)` | Per-media progress/state, drives the buttons and card controls outside the queue |
| `ownershipChanged(trackId)`                                        | A track's ownership or delivered quality changed; QML re-queries `ownershipOf`   |
| `collectionMembershipChanged(id)`                                  | A collection learned its member track ids; QML re-queries `collectionMemberIds`  |
| `downloadFolderMissing` / `downloadFolderDefault`                  | The download folder is invalid (blocking) / still the historical default (nudge) |
| `downloadFolderUnreachable(path)`                                  | The folder is an unreachable network share; queued work held for "Try again"     |
| `ffmpegMissingBlocked`                                             | A download would come out degraded without FFmpeg; a blocking choice is shown    |
| `editionMergeChanged`                                              | The "best of both" edition-merge opt-in flipped                                  |

## Local library presence (the "in your library" badge)

The scan family lives in `bridge_library.py` (`LibraryMixin`, mixed into
`WavesBridge`); `tidaler/library_index.py` walks the configured folder and
`tidaler/matching.py` decides what counts as the same album.

`decide_presence` answers at two strengths and the difference matters. `present`
lights the pill and is generous. Beyond it the verdict splits into two
independent axes: `sure` is IDENTITY (a year on both sides agreeing within one,
and the title matching with its edition qualifiers intact), rendered as the
badge's "?" (dropped when proven) and the choice between a green in-library
button and the gold MAYBE; `full` is COVERAGE (a known source track count the
local copy meets), rendered as N OF M and the cyan PARTIALLY button. `full`
alone picks the button's shape (a complete copy replaces Download, however
hedged its words; a short one keeps a live button), and every claim stays
clickable and opens the gate, so a wrong match never dead-ends. `partial` False
remains the strict both-axes bar for the consumers where being wrong costs a
download outright: the bulk skip claims. The download engine never sees any of
it (pinned by `test_presence_never_reaches_the_download_engine`).

| Signal                     | Fires when                                                                         |
| -------------------------- | ---------------------------------------------------------------------------------- |
| `libraryPresenceChanged`   | The presence index (re)built or was cleared; QML re-queries `libraryAlbumPresence` |
| `libraryScanStatusChanged` | The scan's status or live progress moved; Settings re-reads `libraryScanStatus()`  |
| `librarySourceChanged`     | A library pref committed (switch, source or folder); the Settings card re-reads    |

Synchronous slots (answered from the in-memory index, no disk I/O):
`libraryAlbumPresence(artist, title, year, tracks[, duration])` (duration is
TIDAL's total seconds, the play-length identity witness),
`libraryTrackPresence(artist, title[, album, album_year[, duration]])` (the
exact-song answer behind a track's pill and its download button's claim face;
the album pair and the track's seconds are what the identity can be proven
against, and callers that omit them get `sure` False),
`artistLibraryPresence(name)`, `libraryScanStatus()`, `libraryScanProgress()`,
`librarySource()`, `libraryDownloadFolder()`,
`rescanLibrary()` (forces a full re-list) and `revealLibraryAlbum(path)`
(opens the matched folder in the file manager, resolved on a worker).

With the opt-in `library_mb_arbiter` pref on (off by default: it sends artist
and album-title search terms to musicbrainz.org), `libraryAlbumPresence` may
also overlay a MusicBrainz verdict onto an unproven answer: the lookup runs on
a worker behind a 1 req/s gate, the badge shows the unproven verdict
immediately, and `libraryPresenceChanged` re-announces when a proof lands. The
overlay can only ever upgrade `sure`; the bulk claim gate never reads it.

The whole family is gated on the `library_enabled` pref, off by default: while
it is off `_library_root()` resolves nothing and no folder is ever scanned. The
Settings card stages `library_enabled` / `library_bulk_skip` /
`library_source` / `library_folder` into the page's edit map, and SAVE CHANGES
commits them through `applySettings`, which also starts the first scan of an
enabled, configured library. `rescanLibrary()` acts only on the saved
configuration.

Bulk claim gate (`library_bulk_skip`, on by default, inert while the master
switch is off): bulk downloads leave out what the scan claims. A discography
drops fully claimed albums and guest tracks before queueing
(`_library_claims_album` / `_library_claims_track`); a collection job gets a
`library_claim` callable injected into the engine, consulted per track only
after the exact-id ownership gate declines and never for a merge-plan member
(`_claim_verdict`). Single-item jobs never get the callable, and
`downloadAlbumAnyway(album_id)` (the claim dialog's DOWNLOAD ANYWAY) registers
a per-album override so that click really downloads.

Both claims are strict on IDENTITY, not just presence. The track claim asks
whether a copy is already filed under the release being fetched, so the album
has to reach it: an album job passes its own release (the only place the year
is reliably spelled out), a playlist or mix lets each track name its own, and
a track with no release to name is fetched. Reading bare presence as a claim
was issue #24: a title and artist match every compilation and re-release that
share them, so tracks were dropped out of albums the user had explicitly
asked for.

## Preview and video playback

| Signal                          | Fires when                                                       |
| ------------------------------- | ---------------------------------------------------------------- |
| `previewState(kind, id, state)` | Resolve lifecycle for a preview, addressed by (kind, id)         |
| `previewReady(kind, id, url)`   | A streamable URL for QML's shared MediaPlayer                    |
| `previewMeta(...)`              | Now-playing metadata (title, artist(s), art, ids for navigation) |
| `videoReady(payload)`           | A video stream URL resolved for the overlay player               |

The preview state model: exactly one preview plays at a time. `kind` is
what the user clicked ("track", "artist", "album", "playlist", "mix");
non-track kinds resolve to a concrete song, reported via `previewMeta`'s
`trackId`, which is how every surface showing that song displays live
state instead of offering a restart (see `pvActive` in Main.qml).

## FFmpeg manager and self-updater

| Signal                                                                                                              | Fires when                                                      |
| ------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- |
| `ffmpegStatusChanged` / `ffmpegProgress(pct)` / `ffmpegStateChanged(state, msg)` / `ffmpegUpdateChecked(...)`       | The managed-FFmpeg install/update lifecycle (ffmpeg_manager.py) |
| `appUpdateStatusChanged` / `appUpdateProgress(pct)` / `appUpdateStateChanged(state, msg)` / `appUpdateChecked(...)` | The self-updater lifecycle (updater.py)                         |

## Internal signals (thread hops)

Signals prefixed `_` are not for QML; they marshal work back onto the GUI
thread: `_albumsQueued` (batch-enqueue a resolved discography),
`_tracksQueued` (same batch marshalling for individual tracks),
`_mediaRefetched` (re-dispatch a download whose object was evicted from the
cache), `_queueTracksFetched` (merge a track snapshot without racing live
events).

## Adding a new signal

1. Declare it with the others in backend.py, with a comment saying what it
   carries and when it fires (payloads are plain dicts/lists/strings only;
   tidalapi objects never cross the bridge).
2. Emit it from the worker; do not touch bridge state from the worker.
3. Handle it in Main.qml's `Connections { target: waves }` block
   (`function onYourSignal(args) { ... }`).
4. Add a row here.
