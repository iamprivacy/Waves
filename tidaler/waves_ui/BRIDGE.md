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
| `recentlyAddedLoaded(items)`              | The merged newest-favourites strip on Home                                            |

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
