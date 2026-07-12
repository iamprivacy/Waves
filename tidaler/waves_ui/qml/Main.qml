import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Effects
import QtCore
import QtMultimedia

ApplicationWindow {
    id: root
    visible: true
    width: 1120
    height: 780
    // Never allow a width that clips the header: the top bar's content
    // (logo, wordmark, nav tabs, queue, connection pill, sign out) sets the
    // real floor. headerRow reports 0 until it is laid out, hence the max.
    minimumWidth: Math.max(880, Math.ceil(headerRow.implicitWidth) + 44)
    minimumHeight: 560
    title: "Waves"
    color: bg

    // ---- Console palette (phosphor-green CRT, dark only) ----------------
    // Legacy names kept (values repointed) so every existing binding recolours
    // for free; new tokens add the gold / cyan / outline / surface-tier ideas.
    readonly property color accent:       "#3dff6e"   // phosphor green (primary)
    readonly property color accentText:   "#03210e"   // ink on a green fill
    readonly property color surface:      "#15181d"   // primary card surface
    readonly property color surface2:     "#191c22"   // hover / nested
    readonly property color border1:      "#262a31"   // default card border (outline-variant)
    readonly property color line1:        "#22262d"   // row dividers
    readonly property color textHi:       "#e6e8ec"
    readonly property color textLo:       "#a8acb4"
    readonly property color textDim:      "#6b6f78"
    // New Console tokens
    readonly property color bg:           "#0d0f12"
    readonly property color surface0:     "#121418"   // topbar / statusbar / expand panel
    readonly property color surface3:     "#1d2128"   // art bg / unlit meter / inset
    readonly property color surfaceHi:    "#22262e"   // toast
    readonly property color outline:      "#3a3f49"   // strong border (search / qtag / switch)
    readonly property color divider:      "#22262d"
    readonly property color accentDim:    "#22a64a"   // terminal-button border
    readonly property color accentCont:   "#06210f"   // active chip / nav bg
    readonly property color accentContTx: "#86ffaa"   // text on accent container
    readonly property color gold:         "#ffb01f"   // HI-RES / VIDEO tier + meter mid band
    readonly property color goldDim:      "#b07d18"
    readonly property color goldCont:     "#2a2008"
    readonly property color goldContTx:   "#ffd27a"
    readonly property color green:        "#3ef08a"   // LOSSLESS tier + done state
    readonly property color greenDim:     "#2aa862"
    readonly property color greenCont:    "#08230f"
    readonly property color greenContTx:  "#8bf0b8"
    readonly property color cyan:         "#56c8d8"   // HIGH tier + queued
    readonly property color cyanDim:      "#3a8d99"
    readonly property color red:          "#ff5a52"   // failed / peak / heart
    readonly property color redCont:      "#2a0e0c"
    readonly property string mono:        monoFont    // bundled JetBrains Mono (see app.py)
    // ---- Console button spec (chosen in the Button Lab, 2026-07-02) -----
    // One voice for every button/tab label: the native system sans, Bold,
    // UPPERCASE (nav tabs sentence case), lit-cell primaries; mono stays the
    // "data voice" (badges, numbers, ASCII art).
    readonly property string uiFont:   uiFontFamily   // native system sans (see app.py)
    readonly property real   btnTrack: 0              // label letter-spacing
    readonly property int    btnRad:   8              // button corner radius
    readonly property int    btnPadH:  12             // label padding, left/right
    readonly property int    btnPadV:  7              // label padding, top/bottom
    readonly property color accentSoft:   "#9dffbe"   // CRT flash / phosphor highlight
    // Nav-tab idle ("dimmed phosphor display"): a grey label on a faint green
    // panel that warms to green as the tab powers on (see NavTab).
    readonly property color navIdleBg:       "#0b140f"
    readonly property color navIdleBorder:   "#1f3d2a"
    readonly property color navIdleBorderHi: "#2c5c3e"
    readonly property color navIdleText:     "#8f949e"
    readonly property color navIdleTextHi:   "#bcc1c9"

    // ---- View routing --------------------------------------------------
    // Exactly one main surface shows at a time: Browse (default), search
    // results, an artist page, My Tidal, or Settings. The booleans below are
    // the router; search results show when none of them are on.
    property string filterType: "all"     // search-results chip: all/artists/albums/...
    property var trackCache: ({})         // albumId -> [tracks], filled by albumTracksLoaded
    property var artistsById: ({})        // artistId -> name, for link resolution
    property bool artistOpen: false
    property bool settingsOpen: false
    property bool libraryOpen: false
    // A newer release found by the updater (startup check or a manual one on
    // the Settings page). Drives the gold notice in the status bar's right
    // slot so the news is visible from any page, not just Settings.
    property bool appUpdAvailable: false
    property string appUpdLatest: ""
    // Browse is the launch view: the tab is open from the start, and the
    // landing page fetch fires as soon as login completes (onLoggedInChanged
    // re-fetches whenever the user is sitting on the Browse tab), or right
    // away below if the bridge finished its token login before QML loaded.
    property bool browseOpen: true
    Component.onCompleted: {
        if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
            browseLoading = true
            waves.loadBrowse()
        }
    }
    // True once a search has populated any result model. Gates the filter chips
    // and the empty-state hint, so the chips materialize only after a search.
    readonly property bool hasResults: artistsModel.count > 0 || albumsModel.count > 0
                                       || tracksModel.count > 0 || videosModel.count > 0
                                       || playlistsModel.count > 0 || mixesModel.count > 0
    // ---- Search results / artist page / My Tidal ------------------------
    // Result rows live in the *Model ListModels (declared further down) and
    // are replaced wholesale on each search; these hold the sort order and
    // per-page state around them.
    property bool sortAsc: false
    property bool bioExpanded: false
    property var albumsRaw: []            // unsorted album dicts, re-sorted into albumsModel
    property string libraryCategory: "albums"
    // Album expand state lives here (keyed by album id) rather than inside each
    // AlbumBlock, so it survives ListView delegate recycling in the virtualised
    // My Tidal lists.
    property var expandedAlbums: ({})
    // My Tidal infinite scroll: whether more pages exist, and whether one is in
    // flight (to avoid firing duplicate page requests while scrolling).
    property bool libHasMore: false
    property bool libLoadingMore: false
    // My Tidal per-category sort, {cat: {key, asc}}. Kept in step with the
    // backend's own per-category sort (both mutate only via libApplySort).
    property var libSort: ({})
    // Download-folder gate dialogs: the blocking "no folder set" gate and the
    // one-time soft nudge for users still on the old default. Driven by the
    // downloadFolderMissing / downloadFolderDefault signals.
    property bool folderGateBlocking: false
    property bool folderNudge: false
    // The set folder failed the reachability probe (NAS asleep, stale mount);
    // the download is held backend-side until Try again / a new folder.
    property bool folderUnreachable: false
    property string folderUnreachablePath: ""
    // FFmpeg missing at download time: the download is held backend-side
    // until the user sets FFmpeg up or explicitly continues degraded.
    property bool ffmpegBlocked: false
    property var artistData: ({})         // payload of the open artist page (artistLoaded)
    // Artist-page section collapse: persisted (prefs), so a section a user
    // folds away stays folded on every artist page until reopened. Album/EP
    // hunters shouldn't have to scroll past Top tracks on each visit.
    property bool artistTracksCollapsed: waves.wavesPref("artist_sec_tracks_collapsed") === true
    property bool artistAlbumsCollapsed: waves.wavesPref("artist_sec_albums_collapsed") === true
    property bool artistEpsCollapsed: waves.wavesPref("artist_sec_eps_collapsed") === true
    function toggleArtistSection(which) {
        var v
        if (which === "tracks") { v = artistTracksCollapsed = !artistTracksCollapsed }
        else if (which === "albums") { v = artistAlbumsCollapsed = !artistAlbumsCollapsed }
        else { v = artistEpsCollapsed = !artistEpsCollapsed }
        waves.setWavesPref("artist_sec_" + which + "_collapsed", v)
    }
    // Top tracks show only the first 5; SHOW ALL reveals the rest for this
    // page visit only (deliberately not persisted).
    property bool topTracksExpanded: false
    // ---- Download state (mirrors the bridge) ----------------------------
    // mediaId -> percent / state ("running"|"done"|"failed"), fed by the
    // downloadProgress and downloadState signals; dlPct()/dlSt() read these.
    property var dlProgress: ({})
    property var dlState: ({})
    // In-app preview: exactly one preview plays at a time, addressed by
    // (previewKind, previewId), kind "track" or "artist". previewStopMs caps
    // playback (0 = whole track, the norm; a positive value clips it, same path).
    property string previewKind: ""
    property string previewId: ""
    property bool   previewPlaying: false
    property bool   previewLoading: false
    property int    previewStopMs: 0
    // Live playback position/duration of the active preview (ms), for the artist
    // scrubber bar. 0 duration = not yet known.
    property int    previewPosition: 0
    property int    previewDuration: 0
    // True while a scrub gesture is in progress: the fill follows the cursor
    // and the player's own position clock is ignored, so exactly one real seek
    // fires (on release) instead of one per press/drag tick.
    property bool   previewScrubbing: false
    property string previewNowTitle: ""
    property string previewNowArtist: ""
    property string previewNowArt: ""
    // Ids of the playing item, so the now-playing bar's track name opens its
    // album page (track highlighted) and its artist name opens the artist page.
    property string previewNowArtistId: ""
    property string previewNowAlbumId: ""
    property string previewNowTrackId: ""
    // Full credit list [{name, id}] so each collaborator in the now-playing bar
    // is separately clickable; falls back to the single primary artist.
    property var previewNowArtists: []
    // "kind:id" of the preview whose resolve just failed, flashes the button red
    // briefly (previewErrorTimer clears it), so a failed preview isn't silent.
    property string previewError: ""
    // Count of items still waiting/downloading (excludes done/failed/cancelled),
    // drives the header badge.
    property int activeQueueCount: 0

    // ---- Download-queue grouping (Completed / Downloading / Queued) ----------
    // A finished row lingers 5s with its ✓ DONE chip, then slides up into the
    // collapsible Completed group. These counts feed the sticky section headers;
    // compBump ticks on each promotion so the Completed header count can pulse.
    property bool completedCollapsed: true
    property int completedCount: 0
    property int downloadingCount: 0
    // Finished rows still lingering before their move to Completed; arms the
    // root lingerClock so the fold happens with the queue drawer closed too.
    property int lingerCount: 0
    property int queuedCount: 0
    property int compBump: 0
    // Queue-row album expansion: which rows are open (by qid) and each row's
    // ordered per-track list ({qid: [{id,num,title,duration,status,pct}]}),
    // streamed live from the bridge while the album downloads.
    property var queueExpanded: ({})
    property var queueTracks: ({})

    // ---- Browse (TIDAL editorial pages) --------------------------------
    // The landing payload (content rows + the genre/mood/decade chip sets)
    // arrives via onBrowseLoaded; drilling a chip loads that page into
    // browsePage, keyed by its TIDAL api path so a slow load for a chip the
    // user has already left is ignored (see onBrowsePageLoaded).
    property var browseSections: []
    property var browseChips: ({ genres: [], moods: [], decades: [] })
    property bool browseLoading: false
    property bool browseError: false
    property var browsePage: null          // {key, title, sections} when drilled in
    property string browsePageKey: ""      // "" = the Browse landing page
    property bool browsePageLoading: false
    property bool browsePageError: false
    property var browseStack: []           // pages beneath the current one (Back pops)
    property string browseHighlightId: ""  // track to highlight + scroll to on an album page
    // Opening an album by clicking one of its tracks scrolls the page down to that
    // row. Keep the page hidden (but laid out) until that scroll has been applied,
    // so the reader lands already on the track instead of watching it jump down,
    // matching every other navigation that drops you in place. The highlighted row
    // raises this as it lays out and lowers it once centred; the guard clears it if
    // the track never appears, so a page can never stay hidden.
    property bool browseHighlightPending: false
    onBrowseHighlightPendingChanged: if (browseHighlightPending) hiRevealGuard.restart()
    Timer { id: hiRevealGuard; interval: 500; onTriggered: root.browseHighlightPending = false }
    // Presentation: "art" = artwork-first (hero shelf, unframed covers, hover
    // download, genre/mood/decade colour tiles at the bottom, the streaming-
    // service look); "console" = chip sets up top + framed cards. Persisted.
    property string browseStyle: ("" + (waves.wavesPref("browse_style") || "art"))
    // Cover mosaics for the genre/mood/decade tiles: api path -> [urls],
    // streamed in by the backend's background sampler (see onBrowseTileArt).
    property var browseTileArt: ({})
    // True while the browse pane is being dragged/flicked. Cheap background
    // churn (tile-cover rotation) pauses during a scroll so the frame budget
    // goes to the scroll itself.
    property bool browseMoving: false
    // Tile-cover arrivals are buffered here and flushed on a timer: rebuilding
    // browseTileArt rebinds EVERY tile, and the sampler streams ~46 pages, so
    // coalescing turns dozens of full re-evaluations into a handful.
    property var _tileArtPending: ({})

    // ---- Ambient wave-loop background -----------------------------------
    // A muted, seamlessly looping ocean video (public-domain loop, re-encoded
    // 720p) sits behind every page under a heavy scrim so the Console palette
    // and text contrast survive. z:-1 keeps it below all content; playback
    // pauses while the window is hidden/minimised to spare battery.
    Video {
        id: bgWave
        anchors.fill: parent
        z: -1
        // Settings > Advanced > "Motion background". An empty source (off)
        // tears down the whole decode pipeline, so disabled means zero cost.
        property bool motionOn: waves.wavesPref("motion_background") !== false
        visible: motionOn
        source: motionOn ? Qt.resolvedUrl("assets/wave_loop.mp4") : ""
        loops: MediaPlayer.Infinite
        muted: true
        fillMode: VideoOutput.PreserveAspectCrop
        autoPlay: true
        onErrorOccurred: visible = false   // missing/undecodable asset: fall back to flat bg
        Connections {
            target: waves
            function onMotionBgChanged() {
                bgWave.motionOn = waves.wavesPref("motion_background") !== false
                bgWave.visible = bgWave.motionOn   // undo a hide from a stale onErrorOccurred
                if (bgWave.motionOn) bgWave.play()
            }
        }
        Connections {
            target: root
            function onVisibilityChanged() {
                if (!bgWave.motionOn) return
                if (root.visibility === Window.Hidden || root.visibility === Window.Minimized)
                    bgWave.pause()
                else
                    bgWave.play()
            }
        }
    }
    Rectangle {   // scrim: keeps the CRT-dark reading surface over the moving water
        anchors.fill: parent
        z: -1
        visible: bgWave.motionOn
        color: root.bg
        opacity: 0.93
    }

    // One shared 20 Hz "breathe" clock for the next-to-fill cell in every LED
    // matrix (the download button, queue rows, progress bars). A per-frame
    // SequentialAnimation on each cell marks the whole window dirty every vsync;
    // with the full-window wave-loop video behind it, that recomposites the
    // entire scene at the display refresh for the whole download and pegs a CPU
    // core, the exact trap the WaveMark logo hit (see its note near line 2096).
    // Stepping one value at 20 Hz keeps the pulse visually identical while
    // repainting ~6x less. Cells bind opacity straight to ledPulse; when none
    // are pulsing nothing reads it, so the ticks cost nothing.
    property real ledPulse: 0.85
    // Companion phase for the "finishing" twinkle (bar at 100% while the
    // final steps run): each lit dot breathes on its own offset of this.
    // Same stepped-clock discipline as ledPulse, one write per tick; when no
    // bar is finishing nothing binds it, so it costs nothing.
    property real shimmerPhase: 0
    Timer {
        running: root.active
        interval: 50; repeat: true
        property real phase: 0
        onTriggered: {
            phase = (phase + 0.05 / 1.04) % 1   // 1.04s breathe = 2 x 520ms
            root.ledPulse = 0.28 + 0.57 * (0.5 + 0.5 * Math.cos(2 * Math.PI * phase))
            root.shimmerPhase = (root.shimmerPhase + 0.05 / 1.6) % 1   // 1.6s twinkle cycle
        }
    }

    Timer {
        id: tileArtFlush
        interval: 220; repeat: false
        onTriggered: {
            root.browseTileArt = Object.assign({}, root.browseTileArt, root._tileArtPending)
            root._tileArtPending = ({})
        }
    }
    function setBrowseStyle(s) { browseStyle = s; waves.setWavesPref("browse_style", s) }
    // Shared by BrowseCard (console) and ArtCard (art) so both layouts speak
    // the same subtitle language and download dispatch.
    function cardSubtitle(card) {
        var kind = card.kind || ""
        return kind === "album" ? (card.artist || "") + (card.year ? "  ·  " + card.year : "")
             : kind === "playlist" ? (card.tracks > 0 ? card.tracks + " tracks" : (card.creator || "Playlist"))
             : kind === "mix" ? (card.subtitle || "Mix")
             : kind === "artist" ? "Artist"
             : (card.artist || "")
    }
    // Card captions render in two tones: the artist reads as a link (green),
    // the year / track-count metadata in white. Kinds with neither fall back
    // to the plain grey cardSubtitle.
    function cardSubLead(card) {
        var kind = card.kind || ""
        return kind === "album" || kind === "track" ? (card.artist || "") : ""
    }
    function cardSubMeta(card) {
        var kind = card.kind || ""
        return kind === "album" ? (card.date || card.year || "") + ""
             : kind === "playlist" ? (card.tracks > 0 ? card.tracks + " tracks" : (card.creator || "Playlist"))
             : ""
    }
    // The artists a card caption should link: the per-artist array when the
    // payload carries one, else a single entry built from artist/artist_id
    // (ArtistLinks renders id-less names green but inert).
    function cardLeadArtists(card) {
        if (card.artists && card.artists.length > 0) return card.artists
        if (card.artist) return [{ id: card.artist_id || "", name: card.artist }]
        return []
    }
    component CardCaption: Item {
        id: cap
        property var card: ({})
        property int px: 11
        property bool center: false
        property color metaColor: root.textHi
        readonly property string lead: root.cardSubLead(card)
        readonly property string meta: root.cardSubMeta(card)
        readonly property string fallback: lead === "" && meta === "" ? root.cardSubtitle(card) : ""
        implicitHeight: capRow.implicitHeight
        Row {
            id: capRow
            spacing: 4
            anchors.left: cap.center ? undefined : parent.left
            anchors.horizontalCenter: cap.center ? parent.horizontalCenter : undefined
            ArtistLinks {
                visible: cap.lead !== ""
                artists: cap.lead !== "" ? root.cardLeadArtists(cap.card) : []
                px: cap.px
                width: Math.min(implicitWidth, cap.width - (cap.meta !== "" ? capMeta.implicitWidth + capDot.implicitWidth + 8 : 0))
            }
            Text { id: capDot; textFormat: Text.PlainText; visible: cap.lead !== "" && cap.meta !== ""; text: "\u00b7"; color: root.textDim; font.pixelSize: cap.px }
            Text {
                id: capMeta
                textFormat: Text.PlainText
                visible: cap.meta !== ""
                text: cap.meta
                color: cap.metaColor; font.pixelSize: cap.px
            }
            Text {
                textFormat: Text.PlainText
                visible: cap.fallback !== ""
                text: cap.fallback
                color: root.textDim; font.pixelSize: cap.px
                elide: Text.ElideRight
                width: Math.min(implicitWidth, cap.width)
            }
        }
    }
    function browseCardDownload(card) {
        var kind = card.kind || ""
        if (kind === "album") waves.downloadAlbum(card.id)
        else if (kind === "playlist") waves.downloadPlaylist(card.id)
        else if (kind === "mix") waves.downloadMix(card.id)
        else if (kind === "track") waves.downloadTrack(card.id)
        else if (kind === "artist") waves.downloadArtist(card.id)
    }

    // ---- Dev timing: measure how long a section switch takes to process -----
    // markNav() stamps the start and arms a zero-interval Timer; the Timer fires
    // on the next GUI-thread event-loop turn, after the visibility bindings and
    // layout for the new section have been processed, and reports the elapsed
    // time to the backend dev log (see WavesBridge.uiLog / devlog.py). A Timer
    // (not the window's afterRendering signal) is used deliberately: afterRendering
    // runs on the scene-graph render thread, where calling a Python slot is unsafe.
    property string _navLabel: ""
    property double _navT0: 0
    property bool _navPending: false
    function markNav(label) { _navLabel = label; _navT0 = Date.now(); _navPending = true; navTimer.restart() }
    Timer {
        id: navTimer; interval: 0; repeat: false
        onTriggered: {
            if (root._navPending) {
                root._navPending = false
                waves.uiLog("nav", root._navLabel, Date.now() - root._navT0)
            }
        }
    }

    function dlPct(id) { return dlProgress[id] !== undefined ? dlProgress[id] : -1 }
    function dlSt(id) { return dlState[id] !== undefined ? dlState[id] : "" }

    // Flat list of every track/video id across a browse page's sections
    // (multi-disc albums split into one "tracks" section per disc). Feeds
    // DownloadButton.collectionIds so an album/playlist/mix header can show
    // DOWNLOADED once every member track is owned, the same live-checked way
    // a single track row already does.
    function collectionTrackIds(sections) {
        var ids = []
        var secs = sections || []
        for (var i = 0; i < secs.length; ++i) {
            var items = secs[i].items || []
            for (var j = 0; j < items.length; ++j) {
                if (items[j].id) ids.push(items[j].id)
            }
        }
        return ids
    }

    // --- In-app video player (simple modal overlay; first-ship scope) -----
    // videoNow: {id, title, artist} while the overlay is up, else null. The
    // backend resolves the stream URL asynchronously (waves.playVideo); the
    // overlay shows FETCHING until videoReady lands, then streams directly.
    property var videoNow: null
    property bool videoLoading: false
    property bool videoError: false
    property real videoPendingSeek: -1   // restore position across a quality switch
    property bool videoSwitching: false  // quality switch in flight, old stream keeps playing
    function openVideo(id, title, artist) {
        if (!id) return
        stopPreview()   // one thing plays at a time
        videoPlayer.stop(); videoPlayer.source = ""
        videoNow = { id: "" + id, title: title || "", artist: artist || "" }
        videoLoading = true; videoError = false; videoSwitching = false
        _videoSwapDone()
        waves.playVideo("" + id)
    }
    function closeVideo() {
        videoPlayer.stop(); videoPlayer.source = ""
        videoNow = null; videoLoading = false; videoError = false
        videoSwitching = false
        _videoSwapDone()
        vqMenu.visible = false
    }
    // Switch the running video to a newly chosen resolution as seamlessly as
    // Qt allows: the current stream KEEPS PLAYING while the new variant URL
    // resolves in the background; only once it arrives do we swap the source
    // and jump back to the live position (a sub-second hiccup, not a restart).
    // The choice persists app-wide (setVideoQuality writes the same setting
    // the Settings page does).
    function changeVideoQuality(h) {
        if (!videoNow || videoSwitching) return
        waves.setVideoQuality(h)
        videoSwitching = true
        waves.playVideo(videoNow.id)
    }

    // --- In-app preview control (single shared player, see previewPlayer) ---
    function pvActive(kind, id) {
        if (previewKind === kind && previewId === id) return true
        // Same underlying song, reached from a different surface: an artist/
        // album/playlist preview is always playing some concrete track, and
        // once the backend reports which one (previewNowTrackId) any track
        // control for that song adopts the live state, its art ring, row
        // bar, and card counter show pause/position instead of offering to
        // restart the very track that's already playing.
        return kind === "track" && previewKind !== "" && "" + id !== "" && "" + id === previewNowTrackId
    }
    // "" | "loading" | "playing" | "paused" | "error" for a given (kind, id).
    function pvSt(kind, id) {
        if (previewError === kind + ":" + id) return "error"
        if (!pvActive(kind, id)) return ""
        return previewLoading ? "loading" : (previewPlaying ? "playing" : "paused")
    }
    // Fraction 0..1 of the active preview's playback (drives the scrubber fill).
    function pvFrac(kind, id) {
        if (!pvActive(kind, id) || previewDuration <= 0) return 0
        return Math.max(0, Math.min(1, previewPosition / previewDuration))
    }
    // stopMs: playback cap in ms (0 = whole track, the default intent; a positive
    // value caps the clip). Track art and the artist scrubber both pass 0, so a
    // preview spans, and seeks across, the full song.
    function startPreview(kind, id, stopMs) {
        if (!id) return
        previewStopMs = (stopMs === undefined ? 0 : stopMs)
        previewError = ""; previewErrorTimer.stop()
        // Never inherit a pending seek-mute from the previous preview.
        seekUnmuteTimer.stop(); previewOut.muted = false
        previewPlayer.stop(); previewPlayer.source = ""
        previewKind = kind; previewId = id
        previewLoading = true; previewPlaying = false
        previewPosition = 0; previewDuration = 0
        // Clear the now-playing metadata up front: it only refreshes when the
        // backend emits previewMeta after a multi-second resolve, so without this
        // the bar would keep showing the previous track (and open its artist).
        previewNowTitle = ""; previewNowArtist = ""; previewNowArt = ""
        previewNowArtistId = ""; previewNowAlbumId = ""; previewNowTrackId = ""; previewNowArtists = []
        if (kind === "artist") waves.previewArtist(id)
        else if (kind === "album" || kind === "playlist" || kind === "mix") waves.previewMedia(kind, id)
        else waves.previewTrack(id)
    }
    // Click on the active preview toggles play/pause (or replays after a stop);
    // a click on a failed or on any other preview (re)starts it.
    function togglePreview(kind, id, stopMs) {
        if (previewError === kind + ":" + id) { startPreview(kind, id, stopMs); return }
        if (pvActive(kind, id)) {
            if (previewLoading) return  // still resolving, ignore taps (no source yet)
            if (previewPlayer.playbackState === MediaPlayer.PlayingState) previewPlayer.pause()
            else previewPlayer.play()
        } else {
            startPreview(kind, id, stopMs)
        }
    }
    // Seek the active preview to a fraction 0..1 (scrubber click/drag). Updates
    // previewPosition optimistically so the fill tracks the cursor with no lag.
    function seekPreview(frac) {
        if (previewDuration <= 0) return
        var ms = Math.round(Math.max(0, Math.min(1, frac)) * previewDuration)
        previewPosition = ms
        // Mute across the seek. The FFmpeg backend flushes and re-primes its
        // decoder on a position change, which stutters/pops as playback picks
        // back up; a brief mute (lifted by seekUnmuteTimer once the pipeline has
        // re-synced) hides that so audio returns cleanly, mid-track.
        previewOut.muted = true
        previewPlayer.position = ms
        seekUnmuteTimer.restart()
    }
    // Lifts the seek mute after the backend has settled on the new position.
    Timer {
        id: seekUnmuteTimer
        interval: 160
        onTriggered: previewOut.muted = false
    }
    // Drag feedback only: move the fill without touching the player. Seeking
    // the FFmpeg backend mid-gesture flushes and restarts its audio output,
    // audible as a pop/double-start, so the actual seek is deferred to the
    // one seekPreview call on release, leaving the drag silent and smooth.
    function scrubPreviewVisual(frac) {
        if (previewDuration <= 0) return
        previewPosition = Math.round(Math.max(0, Math.min(1, frac)) * previewDuration)
    }
    function stopPreview() {
        seekUnmuteTimer.stop(); previewOut.muted = false
        previewPlayer.stop(); previewPlayer.source = ""
        previewPlaying = false; previewLoading = false
        previewKind = ""; previewId = ""
        previewPosition = 0; previewDuration = 0
        previewNowTitle = ""; previewNowArtist = ""; previewNowArt = ""
        previewNowArtistId = ""; previewNowAlbumId = ""; previewNowTrackId = ""; previewNowArtists = []
    }
    // --- Now-playing bar (bottom status bar) controls ---------------------------
    // Play/pause the shared player without touching which item is active, so the
    // bar keeps working after the user navigates away from the source row.
    function nowToggle() {
        if (previewKind === "" || previewLoading) return
        if (previewPlayer.playbackState === MediaPlayer.PlayingState) previewPlayer.pause()
        else previewPlayer.play()
    }
    function nowStop() { stopPreview() }
    // Now-playing bar: the track name opens the album page with the track
    // highlighted (fade), falling back to the artist page if there's no album
    // id. Each artist name links to its own page inline (see the bar itself).
    function nowOpenAlbum() {
        if (previewNowAlbumId !== "") openAlbumPage(previewNowAlbumId, previewNowTrackId)
        else if (previewNowArtistId !== "") waves.loadArtist(previewNowArtistId)
    }
    // "M:SS" from milliseconds, for the scrubber time readout.
    function fmtMs(ms) {
        if (!(ms > 0)) return "0:00"
        var s = Math.floor(ms / 1000); var m = Math.floor(s / 60)
        var r = s % 60
        return m + ":" + (r < 10 ? "0" + r : "" + r)
    }
    // Clears the red error flash a couple of seconds after a failed resolve.
    Timer { id: previewErrorTimer; interval: 2500; onTriggered: root.previewError = "" }

    function qualBg(q) { return surface2 }
    function qualFg(q) { return (q === "HI-RES" || q === "VIDEO") ? gold : q === "LOSSLESS" ? green : q === "HIGH" ? cyan : textLo }
    function qualBorder(q) { return (q === "HI-RES" || q === "VIDEO") ? goldDim : q === "LOSSLESS" ? greenDim : outline }
    function qualDot(q) { return q === "LOW" ? textDim : qualFg(q) }
    // Standard spec for each TIDAL quality tier (the exact hi-res sample rate
    // isn't exposed without a per-track stream lookup, so we show the tier's
    // baseline: lossless is always FLAC 16-bit/44.1kHz, hi-res is 24-bit FLAC).
    function qualSpec(q) {
        return q === "HI-RES" ? "24-bit" : q === "LOSSLESS" ? "16/44.1"
             : q === "HIGH" ? "AAC 320" : q === "LOW" ? "AAC 96" : q === "VIDEO" ? "1080p" : ""
    }
    function qualSpecFg(q) { return (q === "HI-RES" || q === "VIDEO") ? goldContTx : q === "LOSSLESS" ? greenContTx : q === "HIGH" ? "#a6e7f1" : textLo }
    function statusColor(s) { return s === "running" ? accent : s === "done" ? accent : s === "failed" ? red : s === "queued" ? cyanDim : textLo }
    function sectionVisible(name, count) { return count > 0 && (filterType === "all" || filterType === name) }

    // ---- Console helpers: ASCII download bar + popularity-meter segments ----
    // asciiBar renders a monospace progress bar of filled (█) + dim (░) cells.
    function asciiBar(pct, n) { n = n || 9; var f = Math.max(0, Math.min(n, Math.round((pct / 100) * n))); return "█".repeat(f) }
    function asciiBarDim(pct, n) { n = n || 9; var f = Math.max(0, Math.min(n, Math.round((pct / 100) * n))); return "░".repeat(n - f) }
    function popLit(v) { return Math.round(Math.max(0, Math.min(100, v)) / 100 * 10) }
    function segColor(i) { return i < 5 ? green : i < 8 ? gold : red }

    // Back navigation (triggered by the back bar or the native swipe gesture
    // detected app-side in WavesBridge.eventFilter).
    // ---- Navigation history --------------------------------------------
    // Swipe-back / back bars return to where you actually WERE (search page,
    // a genre page, an artist), not to a fixed hierarchy. Each view change
    // pushes a snapshot of the view being left; navBack() pops and restores.
    property var navHistory: []
    property bool _navRestoring: false
    // Which top-level section the user is "in" for the nav tabs: drilling into
    // an artist or album page keeps the tab of the section it was opened from
    // lit (to the user they never left Browse/Search/My Tidal). Only explicit
    // section switches (tab clicks, a new search, Back across sections) move it.
    property string navOrigin: "browse"
    function navSig(s) { return s.v + "|" + (s.key || "") + "|" + (s.id || "") + "|" + (s.cat || "") }
    function navSnapshot() {
        if (settingsOpen) return { v: "settings", label: "Settings" }
        if (libraryOpen) return { v: "library", cat: libraryCategory, label: "My Tidal" }
        if (artistOpen) return { v: "artist", id: artistData ? "" + artistData.id : "",
                                 label: artistData ? (artistData.name || "Artist") : "Artist" }
        if (browseOpen) return { v: "browse", key: browsePageKey, page: browsePage,
                                 stack: browseStack.slice(), hi: browseHighlightId,
                                 scrollY: browsePane.contentY,
                                 label: browsePageKey === "" ? "Browse"
                                       : (browsePage ? (browsePage.title || "Browse") : "Browse") }
        return { v: "search", label: "Search" }
    }
    function navPush() {
        if (_navRestoring) return
        var s = navSnapshot()
        s.o = navOrigin   // restore the lit tab along with the view
        if (navHistory.length > 0 && navSig(navHistory[navHistory.length - 1]) === navSig(s)) return
        navHistory = navHistory.concat([s]).slice(-50)
    }
    function navBackLabel() { return navHistory.length > 0 ? navHistory[navHistory.length - 1].label : "" }
    function navBack() {
        markNav("back")
        saveSearchView()   // leaving Search via Back must also keep its drill-in restorable
        if (navHistory.length === 0) {
            // Nothing recorded (fresh session view): the old level-up fallback.
            if (settingsOpen) settingsOpen = false
            else if (libraryOpen) libraryOpen = false
            else if (artistOpen) artistOpen = false
            else if (browseOpen && browsePageKey !== "") browseBack()
            else if (browseOpen) browseOpen = false
            navOrigin = libraryOpen ? "library" : browseOpen ? "browse" : settingsOpen ? navOrigin : "search"
            return
        }
        var s = navHistory[navHistory.length - 1]
        navHistory = navHistory.slice(0, navHistory.length - 1)
        // Restore the snapshot's lit tab; older snapshots without one fall back
        // to the section the snapshot itself shows.
        navOrigin = s.o || (s.v === "library" ? "library" : s.v === "browse" ? "browse"
                          : s.v === "search" ? "search" : navOrigin)
        _navRestoring = true
        if (s.v === "settings") { settingsOpen = true; artistOpen = false; libraryOpen = false }
        else if (s.v === "library") {
            libraryOpen = true; settingsOpen = false; artistOpen = false
            if (libraryCategory !== s.cat) loadLib(s.cat)
        } else if (s.v === "artist") {
            if (artistData && ("" + artistData.id) === s.id) { artistOpen = true; settingsOpen = false; libraryOpen = false }
            else if (s.id) { waves.loadArtist(s.id); return }   // flag cleared in onArtistLoaded
        } else if (s.v === "browse") {
            browseOpen = true; settingsOpen = false; artistOpen = false; libraryOpen = false
            browseStack = s.stack || []
            browseHighlightId = s.hi || ""
            // Arm the scroll restore BEFORE changing the page key: it's tagged
            // with the destination key so on_PageKeyChanged applies it (rather
            // than jumping to the top) once that page is showing, Back lands you
            // exactly where you left off.
            browsePane.pendingRestoreKey = s.key || ""
            browsePane.pendingRestoreY = (s.scrollY !== undefined ? s.scrollY : -1)
            browsePageKey = s.key || ""
            browsePage = s.page || null
            browsePageLoading = false; browsePageError = false
            if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
                browseLoading = true; browseError = false; waves.loadBrowse()
            } else if (waves.loggedIn) {
                waves.refreshBrowse()   // silent, throttled; repaints only on change
            }
        } else {   // search
            settingsOpen = false; artistOpen = false; libraryOpen = false; browseOpen = false
        }
        _navRestoring = false
    }
    // Set the target view true BEFORE clearing the others: the Search tab's
    // `active` is `!artistOpen && !libraryOpen && !settingsOpen`, so clearing the
    // old view first would transiently make Search active and fire its power-on
    // animation mid-switch. Target-first keeps Search inactive throughout.
    function openLibrary() { saveSearchView(); navPush(); markNav("library"); navOrigin = "library"; libraryOpen = true; settingsOpen = false; artistOpen = false; loadLib("home") }

    // ---- Search tab state save/restore -----------------------------------
    // The artist drill-in state (artistData/expandedAlbums) is SHARED between
    // tabs, and other tabs overwrite it (My Tidal opens its own artist pages,
    // loadLib clears expandedAlbums). So the Search tab's exact view is
    // snapshotted the moment the user leaves the tab, and the Search nav
    // button restores it: first press returns exactly where you were (artist
    // page, expanded album, scroll); a second press while already on Search
    // resets to a blank search page, mirroring Browse's two-step behaviour.
    property var searchSaved: null
    function saveSearchView() {
        if (navOrigin !== "search" || settingsOpen) return
        searchSaved = (artistOpen && artistData && artistData.id)
            ? { artistData: artistData, expandedAlbums: expandedAlbums,
                artistY: artistView.contentY, resultsY: results.contentY }
            : { resultsY: results.contentY }
    }
    function openSearch() {
        var onSearchTab = navOrigin === "search" && !settingsOpen && !libraryOpen && !browseOpen
        if (!onSearchTab) {
            navPush()
            markNav("search restore")
            var fromOtherTab = navOrigin !== "search"
            navOrigin = "search"
            if (fromOtherTab) {
                // Restore the saved drill-in BEFORE clearing the tab flags so
                // the results pane never flashes underneath (same target-first
                // rule as openLibrary).
                var s = searchSaved
                if (s && s.artistData && s.artistData.id) {
                    artistData = s.artistData
                    expandedAlbums = s.expandedAlbums || ({})
                    artistOpen = true
                    browseOpen = false; libraryOpen = false; settingsOpen = false
                    // Same-frame restore: the pane becomes visible this frame,
                    // so clamping contentY now lands pre-paint (no visible jump).
                    artistView.contentY = Math.min(s.artistY || 0, Math.max(0, artistView.contentHeight - artistView.height))
                } else {
                    artistOpen = false
                    browseOpen = false; libraryOpen = false; settingsOpen = false
                    if (s) results.contentY = Math.min(s.resultsY || 0, Math.max(0, results.contentHeight - results.height))
                }
            } else {
                // Only Settings was covering the Search view: uncover it as-is.
                settingsOpen = false; browseOpen = false; libraryOpen = false
            }
            // Ready to type immediately: the Search press hands the keyboard
            // to the field (existing text selected, so typing replaces it).
            searchField.forceActiveFocus()
            searchField.selectAll()
            return
        }
        // Second press while already on Search: a fresh, blank search page.
        navPush()
        markNav("search blank")
        artistOpen = false
        searchSaved = null
        searchField.text = ""
        trackCache = ({}); expandedAlbums = ({})
        artistsModel.clear(); albumsRaw = []; applySort()
        tracksModel.clear(); videosModel.clear(); playlistsModel.clear(); mixesModel.clear()
        searchField.forceActiveFocus()
    }
    function loadLib(cat) {
        libraryCategory = cat
        libLoadingMore = false
        libHasMore = false
        expandedAlbums = ({})
        // "Home" is a self-contained, Browse-shaped landing. Like the Browse tab,
        // it is fetched once and then kept: re-opening My Tidal shows the shelves
        // it already has, instantly, instead of clearing to an empty pane and
        // flashing blank while the async load runs. The placeholder glyph shows
        // only on the very first load (nothing cached yet). Logout drops the cache
        // (onLoggedInChanged), so a different account still refetches.
        if (cat === "home") {
            if (root.homeSections.length === 0) waves.loadHome()
            return
        }
        // Every other category is a paginated favourites list. Clear the old
        // category's rows immediately so they don't linger under the loading
        // state; a cached category refills in the same tick, so there's no flash.
        libAlbumsModel.clear(); libTracksModel.clear(); libArtistsModel.clear()
        libPlaylistsModel.clear(); libMixesModel.clear(); libVideosModel.clear()
        waves.loadLibrary(cat)
    }
    // Deep-link to the download-folder setting (from the folder gate/nudge), the
    // same instant jump the update notice uses, no scroll animation.
    function openDownloadSetting() {
        navPush(); markNav("settings")
        settingsOpen = true; artistOpen = false; libraryOpen = false; browseOpen = false
        Qt.callLater(function() { settingsPage.jumpToCard("downloads") })
    }
    // Deep-link to the FFmpeg card (from the pre-download gate).
    function openFfmpegSetting() {
        navPush(); markNav("settings")
        settingsOpen = true; artistOpen = false; libraryOpen = false; browseOpen = false
        Qt.callLater(function() { settingsPage.jumpToCard("ffmpeg") })
    }
    // Browse: open the tab (fetching the landing page once per session) and
    // drill into an editorial page. Target-first flag order, same as above.
    function openBrowse() {
        // Coming from another section, the tab RETURNS to Browse exactly as it
        // was left (open sub-page, stack, scroll all intact). Only a second
        // click, Browse already active and highlighted, goes home.
        saveSearchView()
        var alreadyActive = browseOpen && !artistOpen && !libraryOpen && !settingsOpen
                            && navOrigin === "browse"
        if (!alreadyActive) {
            navPush()
            markNav("browse return")
            navOrigin = "browse"
            browseOpen = true
            settingsOpen = false; artistOpen = false; libraryOpen = false
            if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
                browseLoading = true; browseError = false
                waves.loadBrowse()
            } else if (waves.loggedIn) {
                waves.refreshBrowse()   // silent, throttled; repaints only on change
            }
            return
        }
        navPush()
        markNav("browse")
        // The tab button always lands on the main Browse page: drop any open
        // sub-page (genre / playlist / album) and its stack.
        browseStack = []
        browsePageKey = ""
        browsePage = null
        browsePageLoading = false
        browsePageError = false
        browseHighlightId = ""
        browseOpen = true
        settingsOpen = false; artistOpen = false; libraryOpen = false
        // The tab button is an explicit "take me to the top of Browse": cancel
        // any armed Back-restore and reset the scroll (on_PageKeyChanged only
        // fires when the key actually changes, which it won't if already home).
        browsePane.pendingRestoreY = -1
        browsePane.contentY = 0
        if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
            browseLoading = true; browseError = false
            waves.loadBrowse()
        } else if (waves.loggedIn) {
            waves.refreshBrowse()   // silent, throttled; repaints only on change
        }
    }
    function openBrowseLink(path, title) {
        navPush()
        if (browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = ""
        browsePageKey = path
        browsePage = null
        browsePageError = false
        browsePageLoading = true
        waves.openBrowsePage(path, title)
    }
    // Some rows (Custom mixes, Radio stations, New releases…) have no TIDAL
    // "show more" path. Their headline still opens a full listing: a local
    // page synthesized from the row's own items, rendered by the same grid
    // page as fetched listings, no network fetch, instant, Back just works
    // because the page object is a plain snapshot like any loaded page.
    function openBrowseSection(sec) {
        var key = "local:" + (sec.title || "More")
        if (browsePageKey === key) return   // already there
        navPush()
        if (browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = ""
        browsePageKey = key
        browsePageError = false
        browsePageLoading = false
        browsePage = { key: key, title: sec.title || "More",
                       sections: [{ rowKind: sec.rowKind, title: sec.title || "More",
                                    items: sec.items || [], more: "",
                                    // carry the paging handle so a local
                                    // listing endless-scrolls like fetched ones
                                    data: sec.data || "", total: sec.total || 0,
                                    offset: sec.offset || 0, modType: sec.modType || "" }] }
    }
    // A local: page is a snapshot of its landing row taken at click time; a
    // background revalidation can deliver a fresher ordering afterwards (e.g.
    // "New tracks" gaining releases at the top). Re-snapshot from the fresh
    // row when its head no longer matches; a page whose head still agrees is
    // left alone, preserving any endless-scroll growth and the user's place.
    function localPageFresh(pg, rows) {
        if (!pg || ("" + pg.key).indexOf("local:") !== 0) return null
        if (!pg.sections || pg.sections.length === 0) return null
        var cur = pg.sections[0]
        function ids(list, n) {
            var out = []
            for (var k = 0; k < list.length && (n < 0 || k < n); k++)
                out.push("" + list[k].kind + ":" + list[k].id)
            return out.join("\n")
        }
        for (var i = 0; i < rows.length; i++) {
            var r = rows[i]
            var match = cur.data ? r.data === cur.data
                                 : (r.rowKind === cur.rowKind && r.title === cur.title)
            if (!match) continue
            var head = r.items || []
            if (ids(head, -1) === ids(cur.items || [], head.length)) return null   // unchanged
            return { key: pg.key, title: pg.title,
                     sections: [{ rowKind: r.rowKind, title: r.title || pg.title,
                                  items: head, more: "",
                                  data: r.data || "", total: r.total || 0,
                                  offset: r.offset || 0, modType: r.modType || "" }] }
        }
        return null
    }
    function refreshLocalBrowsePages(rows) {
        var fresh = localPageFresh(browsePage, rows)
        if (fresh) browsePage = fresh
        var changed = false
        var st = browseStack.map(function(pg) {
            var f = localPageFresh(pg, rows)
            if (f) changed = true
            return f || pg
        })
        if (changed) browseStack = st
    }
    // Open all of one wayfinding cloud (Genres / Moods / Decades) as its own
    // page: the landing shows these as horizontal tile shelves, and their
    // headline drills into a wrapping grid of the same tiles (a "links"
    // section, see the Flow in the browse delegate). Local, no fetch: the
    // clouds already hold every tile TIDAL's "show more" would return.
    function openBrowseCloud(title, chips) {
        var key = "cloud:" + title
        if (browsePageKey === key) return
        navPush()
        if (browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = ""
        browsePageKey = key
        browsePageError = false
        browsePageLoading = false
        browsePage = { key: key, title: title,
                       sections: [{ rowKind: "links", title: title, items: chips || [] }] }
    }
    // Shared grid geometry so every drilled box view (album/mix/playlist cards
    // AND genre/mood/label tiles) centers and re-columns identically: how many
    // fixed-width cards fit across `avail`, capped at the item count so a short
    // row centers its items instead of hugging the left.
    function gridCols(cardW, spacing, count, avail) {
        var fit = Math.max(1, Math.floor((avail + spacing) / (cardW + spacing)))
        return Math.max(1, Math.min(fit, count))
    }
    // ---- browse endless scroll -------------------------------------------
    // A row that carries a paging handle (data/total/offset) can grow: shelves
    // ask when scrolled to their end, drilled pages when the view hits bottom.
    // One in-flight fetch per data path; results splice into whichever views
    // hold the row at that offset (backend keeps its caches in step).
    property var browseGrowing: ({})
    function browseCanGrow(sec) {
        return !!(sec && sec.data) && (sec.offset || 0) < (sec.total || 0)
    }
    function browseGrow(sec) {
        if (!browseCanGrow(sec) || browseGrowing[sec.data]) return
        var g = Object.assign({}, browseGrowing); g[sec.data] = true; browseGrowing = g
        waves.loadBrowseSectionMore(browsePageKey, sec.data, sec.offset || 0,
                                    sec.modType || "", sec.title || "")
    }
    function browseGrew(p) {
        var g = Object.assign({}, browseGrowing); delete g[p.data]; browseGrowing = g
        if (p.error || (p.items || []).length === 0) return
        browseArtistsSideMap([p])
        function grown(rows) {
            var hit = false
            var out = (rows || []).map(function(r) {
                if (r.data !== p.data || (r.offset || 0) !== p.reqOffset) return r
                hit = true
                return Object.assign({}, r, { items: (r.items || []).concat(p.items),
                                              offset: p.offset,
                                              total: p.more ? r.total : p.offset })
            })
            return hit ? out : null
        }
        var s = grown(browseSections)
        if (s) browseSections = s
        if (browsePage && browsePage.sections) {
            var ps = grown(browsePage.sections)
            if (ps) browsePage = Object.assign({}, browsePage, { sections: ps })
        }
    }
    // Open one playlist / mix / album as its own page inside Browse (art
    // header + track list). Drilling from an editorial page pushes it onto
    // browseStack so Back walks up one level at a time.
    function openBrowseItem(kind, id, highlight) {
        if (browsePageKey === "item:" + kind + ":" + id) return   // already there
        navPush()
        if (browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = highlight || ""
        browseHighlightPending = false   // the highlighted row re-arms this on layout
        browsePageKey = "item:" + kind + ":" + id
        browsePage = null
        browsePageError = false
        browsePageLoading = true
        waves.openBrowseItem(kind, id)
    }
    function browseBack() {
        browsePageLoading = false
        browsePageError = false
        browseHighlightId = ""
        if (browseStack.length > 0) {
            var s = browseStack.slice()
            var prev = s.pop()
            browseStack = s
            browsePage = prev
            browsePageKey = prev.key
        } else {
            browsePageKey = ""
            browsePage = null
        }
    }
    // A track title anywhere (search, library, artist, browse) leads to its
    // album's browse page with the track highlighted, from outside Browse
    // too, so switch the tab in.
    // True when the browse album page for `albumId` is what's on screen,
    // its title links go inert then (clicking "go to album" on the album
    // you're already reading shouldn't re-navigate or grow the Back stack).
    function onAlbumPage(albumId) {
        return browseOpen && !artistOpen && !settingsOpen && !libraryOpen
               && browsePageKey === "item:album:" + albumId
    }
    // Same idea for artist links: inert while that artist's page is open.
    function onArtistPage(artistId) {
        return artistOpen && artistData && ("" + artistData.id) === ("" + artistId)
    }
    function openAlbumPage(albumId, highlight) {
        if (!albumId || onAlbumPage(albumId)) return
        markNav("browse")
        openBrowseItem("album", albumId, highlight || "")   // snapshots the view being left
        settingsOpen = false; artistOpen = false; libraryOpen = false
        browseOpen = true
        if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
            browseLoading = true; browseError = false
            waves.loadBrowse()
        }
    }
    // Where a card's art leads: artists to the artist page; albums, playlists
    // and mixes to their own synthesized page; tracks to their album's page
    // with the track highlighted (falling back to the artist page for the
    // rare track without an album id).
    function openBrowseCard(card) {
        var kind = card.kind || ""
        // The artist page switches the active surface itself in onArtistLoaded,
        // so those branches return before the browse-surface flip below.
        if (kind === "artist") { waves.loadArtist(card.id); return }
        if (kind === "playlist" || kind === "mix" || kind === "album") {
            openBrowseItem(kind, card.id)
        } else if (kind === "track") {
            if (card.album_id) openBrowseItem("album", card.album_id, card.id)
            else { if (card.artist_id) waves.loadArtist(card.artist_id); return }
        } else return
        // This card is reused on My Tidal's Home shelves, which live in the
        // library pane, so make Browse the active surface; when the click came
        // from within Browse these flags are already set, so it is a no-op.
        browseOpen = true; settingsOpen = false; artistOpen = false; libraryOpen = false
    }
    // Browse rows carry per-item `artists` arrays; stash them in the same
    // artistsById side map the search/library rows use, so ArtistLinks inside
    // reused components (TrackRow) resolve for browse items too.
    function browseArtistsSideMap(sections) {
        var m = root.artistsById
        for (var s = 0; s < sections.length; ++s) {
            var items = sections[s].items || []
            for (var i = 0; i < items.length; ++i) if (items[i].artists) m[items[i].id] = items[i].artists
        }
        root.artistsById = m
    }

    // The one and only audio player. Every preview button drives this single
    // instance, so starting a new preview structurally replaces the old one.
    MediaPlayer {
        id: previewPlayer
        audioOutput: AudioOutput { id: previewOut }
        // Optional stop: previewStopMs > 0 caps the clip (default 30s); 0 lets
        // the whole track play. Both leave the same idle state via stopPreview.
        onPositionChanged: {
            // While scrubbing, the fill tracks the cursor, don't let the
            // player's clock stomp it back to the pre-seek spot.
            if (!root.previewScrubbing) root.previewPosition = previewPlayer.position
            if (root.previewStopMs > 0 && previewPlayer.position >= root.previewStopMs) root.stopPreview()
        }
        onDurationChanged: root.previewDuration = previewPlayer.duration
        onPlaybackStateChanged: {
            root.previewPlaying = (playbackState === MediaPlayer.PlayingState)
            if (playbackState !== MediaPlayer.StoppedState) root.previewLoading = false
        }
        onMediaStatusChanged: {
            // Whole-track end resolves to the SAME idle state as the 30s cap.
            if (mediaStatus === MediaPlayer.EndOfMedia) root.stopPreview()
        }
        onErrorOccurred: function(err, errStr) {
            // Playback/network failure on a resolved source, flash red like a
            // failed resolve, then fall back to idle. Logged for diagnosis.
            waves.uiLog("preview", "player error " + err + ": " + errStr, -1)
            if (root.previewId !== "") root.previewError = root.previewKind + ":" + root.previewId
            root.stopPreview()
            if (root.previewError !== "") previewErrorTimer.restart()
        }
    }

    // ====================================================================
    // Video player overlay (deliberately simple for first ship)
    // ====================================================================
    // Quality-switch seek, verify-and-retry. One-shot seeks fired from
    // readiness signals were swallowed: the signals often belong to the DYING
    // old stream, the ffmpeg backend drops setPosition while the new source is
    // still loading, and the code then believed the seek had landed (the
    // "restarts + frozen frame" bug). A headless probe confirmed these HLS
    // variants seek fine once actually loaded, so this timer re-seeks every
    // 300 ms and only stops when the OBSERVED position reaches the target
    // (or it gives up after ~6 s and lifts the freeze).
    property var _videoGrab: null        // grabToImage result, kept alive while frozen
    property real _videoSeekTarget: -1   // where the freeze-frame lifts
    property int _videoSeekTries: 0
    function _videoSwapDone() {
        videoFreeze.visible = false
        _videoGrab = null
        _videoSeekTarget = -1
        videoPendingSeek = -1
        videoSeekRetry.stop()
    }
    Timer {
        id: videoSeekRetry
        interval: 300; repeat: true
        onTriggered: {
            if (root.videoPendingSeek <= 0 || !root.videoNow) { root._videoSwapDone(); return }
            if (++root._videoSeekTries > 20) { root._videoSwapDone(); return }  // give up, show live stream
            if (videoPlayer.mediaStatus !== MediaPlayer.LoadedMedia
                    && videoPlayer.mediaStatus !== MediaPlayer.BufferedMedia) return  // new source not ready yet
            if (videoPlayer.position >= root.videoPendingSeek - 2000) {
                // Verified: playback is actually at (or past) the target.
                root._videoSeekTarget = root.videoPendingSeek
                root.videoPendingSeek = -1
                return  // freeze lifts in onPositionChanged
            }
            if (videoPlayer.duration > 0)
                videoPlayer.position = Math.min(root.videoPendingSeek, videoPlayer.duration - 500)
        }
    }
    MediaPlayer {
        id: videoPlayer
        videoOutput: videoSurface
        audioOutput: AudioOutput {}
        onPositionChanged: {
            // Lift the freeze-frame only once playback has actually reached the
            // restored spot, the blank/black gap stays hidden the whole time.
            if (videoFreeze.visible && root.videoPendingSeek < 0
                    && root._videoSeekTarget >= 0 && position >= root._videoSeekTarget - 2000) {
                videoFreeze.visible = false
                root._videoGrab = null
                root._videoSeekTarget = -1
            }
        }
        onErrorOccurred: function(err, errStr) {
            waves.uiLog("video", "player error " + err + ": " + errStr, -1)
            root._videoSwapDone()
            if (root.videoNow) { root.videoError = true; root.videoLoading = false }
        }
    }
    Shortcut { sequence: "Esc"; enabled: root.videoNow !== null; onActivated: root.closeVideo() }
    Shortcut {
        sequence: "Space"; enabled: root.videoNow !== null && !root.videoLoading && !root.videoError
        onActivated: videoPlayer.playbackState === MediaPlayer.PlayingState ? videoPlayer.pause() : videoPlayer.play()
    }
    Rectangle {
        anchors.fill: parent
        z: 999
        visible: root.videoNow !== null
        color: "#000000e0"
        // Click outside the card closes; swallow wheel so pages don't scroll.
        MouseArea { anchors.fill: parent; onClicked: root.closeVideo(); onWheel: function(w){ w.accepted = true } }
        Rectangle {
            id: videoCard
            anchors.centerIn: parent
            width: Math.min(parent.width - 72, 960)
            height: Math.min(Math.round((width - 2) * 9 / 16), parent.height - 140) + 46
            color: "#0b0d10"; radius: 10; border.color: root.border1; border.width: 1
            clip: true
            MouseArea { anchors.fill: parent }   // swallow clicks inside the card
            VideoOutput {
                id: videoSurface
                anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                anchors.margins: 1
                height: parent.height - 46
            }
            // Last frame of the outgoing stream, held over the surface during a
            // quality switch so the source swap never shows a blank panel.
            Image {
                id: videoFreeze
                visible: false
                anchors.fill: videoSurface
                fillMode: Image.PreserveAspectFit
                cache: false
            }
            // Click anywhere on the picture to toggle play/pause.
            MouseArea {
                anchors.fill: videoSurface
                onClicked: videoPlayer.playbackState === MediaPlayer.PlayingState ? videoPlayer.pause() : videoPlayer.play()
            }
            // GET / ERR states, in the terminal voice of the Art placeholders.
            Text {
                anchors.centerIn: videoSurface
                visible: root.videoLoading || root.videoError
                textFormat: Text.PlainText
                text: root.videoError ? "ERR  stream unavailable" : "GET  video stream…"
                color: root.videoError ? root.red : root.accentDim
                font.family: root.mono; font.pixelSize: 13
            }
            // Controls bar: play/pause, seek, time, title, close.
            Item {
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                height: 46
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 10; spacing: 12
                    Text {
                        textFormat: Text.PlainText
                        text: videoPlayer.playbackState === MediaPlayer.PlayingState ? "[||]" : "[>]"
                        color: root.accent; font.family: root.mono; font.pixelSize: 14
                        MouseArea {
                            anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor
                            enabled: !root.videoLoading && !root.videoError
                            onClicked: videoPlayer.playbackState === MediaPlayer.PlayingState ? videoPlayer.pause() : videoPlayer.play()
                        }
                    }
                    // Seek bar: click or drag anywhere on the track.
                    Rectangle {
                        Layout.fillWidth: true; height: 5; radius: 2; color: root.surface3
                        Rectangle {
                            width: videoPlayer.duration > 0 ? parent.width * videoPlayer.position / videoPlayer.duration : 0
                            height: parent.height; radius: 2; color: root.accent
                        }
                        MouseArea {
                            anchors.fill: parent; anchors.margins: -8
                            enabled: videoPlayer.seekable
                            function seekTo(x) {
                                var w = width - 16
                                if (w > 0 && videoPlayer.duration > 0)
                                    videoPlayer.position = Math.max(0, Math.min(1, (x - 8) / w)) * videoPlayer.duration
                            }
                            onPressed: function(m){ seekTo(m.x) }
                            onPositionChanged: function(m){ if (pressed) seekTo(m.x) }
                        }
                    }
                    Text {
                        textFormat: Text.PlainText
                        text: root.fmtMs(videoPlayer.position) + " / " + root.fmtMs(videoPlayer.duration)
                        color: root.textLo; font.family: root.mono; font.pixelSize: 11
                    }
                    // Title -> the video's album page (when TIDAL links one).
                    Text {
                        id: vpTitle
                        readonly property bool linkable: root.videoNow !== null && (root.videoNow.albumId || "") !== ""
                        textFormat: Text.PlainText
                        Layout.maximumWidth: videoCard.width * 0.22
                        text: root.videoNow ? (root.videoNow.title || "") : ""
                        color: vpTitleMa.containsMouse && vpTitle.linkable ? "#ffffff" : root.textHi
                        font.pixelSize: 12; elide: Text.ElideRight
                        MouseArea {
                            id: vpTitleMa
                            anchors.fill: parent; hoverEnabled: true
                            enabled: vpTitle.linkable
                            cursorShape: vpTitle.linkable ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: {
                                // Highlight the matching track when the album was
                                // found via the song lookup, else the video id.
                                var a = root.videoNow.albumId
                                var t = root.videoNow.trackId || root.videoNow.id
                                root.closeVideo()
                                root.openAlbumPage(a, t)
                            }
                        }
                    }
                    // Artist credits: the shared green links (navigating closes
                    // the player, see onArtistLoaded).
                    ArtistLinks {
                        Layout.maximumWidth: videoCard.width * 0.2
                        artists: root.videoNow ? (root.videoNow.artists || []) : []
                    }
                    // Quality: green like the artist links; opens the resolution menu.
                    Text {
                        id: vqLabel
                        textFormat: Text.PlainText
                        visible: root.videoNow !== null
                        text: root.videoSwitching ? "…"
                            : root.videoNow && root.videoNow.res ? root.videoNow.res + "p" : "AUTO"
                        color: root.accent; font.family: root.mono; font.pixelSize: 11
                        font.underline: vqMa.containsMouse || vqMenu.visible
                        MouseArea {
                            id: vqMa
                            anchors.fill: parent; anchors.margins: -6; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: vqMenu.visible = !vqMenu.visible
                        }
                    }
                    Text {
                        id: vpClose
                        textFormat: Text.PlainText
                        text: "[x]"
                        color: vpCloseMa.containsMouse ? "#ff7b74" : root.red
                        font.family: root.mono; font.pixelSize: 14
                        MouseArea {
                            id: vpCloseMa
                            anchors.fill: parent; anchors.margins: -6; hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.closeVideo()
                        }
                    }
                }
            }
            // Resolution picker, floating just above the controls bar.
            Rectangle {
                id: vqMenu
                visible: false
                width: 78; height: vqCol.implicitHeight + 14
                radius: 8; color: root.surface0; border.color: root.outline; border.width: 1
                // Open directly above the quality label, wherever the controls
                // row happens to have laid it out.
                onVisibleChanged: if (visible) {
                    var p = vqLabel.mapToItem(videoCard, vqLabel.width / 2, 0)
                    x = Math.max(8, Math.min(videoCard.width - width - 8, p.x - width / 2))
                    y = videoCard.height - 46 - height - 6
                }
                Column {
                    id: vqCol
                    anchors.centerIn: parent; spacing: 3
                    Repeater {
                        // Only what this video's playlist actually offers.
                        model: root.videoNow && (root.videoNow.heights || []).length
                             ? root.videoNow.heights : [1080, 720, 480, 360]
                        delegate: Text {
                            id: vqOpt
                            required property var modelData
                            textFormat: Text.PlainText
                            width: 62; horizontalAlignment: Text.AlignHCenter
                            text: modelData + "p"
                            color: root.videoNow && root.videoNow.res === modelData ? root.accent
                                 : vqOptMa.containsMouse ? root.textHi : root.textLo
                            font.family: root.mono; font.pixelSize: 12
                            MouseArea {
                                id: vqOptMa
                                anchors.fill: parent; anchors.margins: -3; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: { vqMenu.visible = false; root.changeVideoQuality(vqOpt.modelData) }
                            }
                        }
                    }
                }
            }
        }
    }

    // ====================================================================
    // Warm cover-art pool
    // ====================================================================
    // Qt only keeps a small budget of decoded-but-unreferenced images, so
    // revisiting a page used to re-decode every cover (the placeholder→art
    // pop-in that made every visit feel like a first load). This invisible
    // pool holds a live Image for the last ~220 covers shown, keeping their
    // decoded pixels referenced in the pixmap cache, a rebuilt delegate with
    // the same url+sourceSize then paints instantly. LRU-capped; worst case
    // ~100 MB of RAM at typical tile sizes, usually far less.
    property var _warmSeen: ({})   // "url@w" -> true; mutated in place (nothing binds to it)
    function warmArt(u, w, h) {
        if (!u || w <= 0) return
        var k = u + "@" + w
        if (_warmSeen[k]) return
        _warmSeen[k] = true
        warmArtModel.append({ u: "" + u, w: w, h: h })
        if (warmArtModel.count > 220) {
            var old = warmArtModel.get(0)
            delete _warmSeen[old.u + "@" + old.w]
            warmArtModel.remove(0)
        }
    }
    ListModel { id: warmArtModel }
    Item {
        visible: false
        Repeater {
            model: warmArtModel
            Image {
                source: model.u
                sourceSize.width: model.w
                sourceSize.height: model.h
                asynchronous: true
                cache: true
                visible: false
            }
        }
    }

    // ====================================================================
    // Reusable components
    // ====================================================================
    component Art: Rectangle {
        id: artRoot
        property string url: ""
        // "loading" while the fetch is in flight, "failed" on a load error
        // (e.g. network down), "ready" once decoded, "none" when there is no
        // art URL at all (keeps the neutral ♪, absence isn't an error).
        readonly property string artState: url === "" ? "none"
            : artImg.status === Image.Error ? "failed"
            : artImg.status === Image.Ready ? "ready" : "loading"
        // Set only when a load has been in flight past the grace timer below:
        // warm-pool cache hits and sub-perceptual reloads (delegate rebuilds,
        // sourceSize settling during layout) must keep painting instantly,
        // the placeholder and fade-in are only for covers that made us wait.
        property bool artWaited: false
        // Recycled delegates keep component state, a new cover starts its
        // own grace window instead of inheriting the previous one's verdict.
        onUrlChanged: artWaited = false
        Timer {
            interval: 250
            running: artRoot.artState === "loading"
            onTriggered: artRoot.artWaited = true
        }
        color: surface3
        radius: 6
        border.color: border1
        border.width: 1
        clip: true
        Image {
            id: artImg
            anchors.fill: parent
            source: parent.url
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: true
            // Art fades in over the placeholder instead of popping.
            opacity: artRoot.artState === "ready" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { enabled: artRoot.artWaited; NumberAnimation { duration: 220; easing.type: Easing.OutQuad } }
            // Decode covers at (roughly) display resolution instead of the full
            // 320-480px source. Without this each tiny 34-54px thumbnail keeps a
            // full-size bitmap in memory and burns decode time, the main reason
            // image-heavy lists felt heavy. ×2 keeps it crisp on HiDPI screens.
            sourceSize.width: parent.width > 0 ? Math.round(parent.width * 2) : 96
            sourceSize.height: parent.height > 0 ? Math.round(parent.height * 2) : 96
            // Pin this cover's decoded pixels in the warm pool so the next
            // page that shows it (or a revisit) paints without a re-decode.
            onStatusChanged: if (status === Image.Ready) root.warmArt("" + source, sourceSize.width, sourceSize.height)
        }
        Text {
            anchors.centerIn: parent
            visible: artRoot.artState === "none"
            text: "≈"
            color: textDim
            font.family: root.mono; font.pixelSize: parent.width * 0.4
        }
        // Terminal-fetch placeholder: a CRT box showing "art: GET" with a
        // blinking cursor while loading, cross-fading to "art: ERR / no link"
        // on failure, and fading out underneath the cover as it arrives.
        Rectangle {
            id: artTerm
            anchors.fill: parent
            radius: artRoot.radius
            color: "#04140a"
            border.width: 1
            border.color: artRoot.artState === "failed" ? root.red : root.accentDim
            Behavior on border.color { ColorAnimation { duration: 350 } }
            // Loading face waits out the grace timer so quick loads (cache
            // hits, delegate rebuilds on page revisits) never flash the box;
            // a hard failure shows immediately.
            opacity: ((artRoot.artState === "loading" && artRoot.artWaited) || artRoot.artState === "failed") ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { enabled: artRoot.artWaited; NumberAnimation { duration: 300; easing.type: Easing.InQuad } }
            // Below this the "art: GET / ERR" labels are unreadable, collapse
            // to the bare prompt (loading) / a red x (failed).
            readonly property bool compact: width < 64
            // loading face
            Column {
                anchors.centerIn: parent; spacing: 4
                opacity: artRoot.artState === "failed" ? 0 : 1
                Behavior on opacity { NumberAnimation { duration: 250 } }
                Text {
                    visible: !artTerm.compact
                    anchors.horizontalCenter: parent.horizontalCenter
                    textFormat: Text.PlainText; text: "art: GET"
                    font.family: root.mono; font.pixelSize: Math.max(1, Math.round(artTerm.width * 0.11))
                    color: root.accentContTx
                }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 2
                    Text {
                        textFormat: Text.PlainText; text: ">"
                        font.family: root.mono
                        font.pixelSize: Math.max(1, Math.round(artTerm.width * (artTerm.compact ? 0.22 : 0.11)))
                        color: root.accent
                    }
                    Rectangle {
                        width: artTerm.width * (artTerm.compact ? 0.16 : 0.08)
                        height: artTerm.width * (artTerm.compact ? 0.26 : 0.13)
                        anchors.verticalCenter: parent.verticalCenter
                        color: root.accent
                        SequentialAnimation on opacity {
                            // Pause the blink when hidden or unfocused, same
                            // reasoning as the WaveMark parallax scroll.
                            running: artRoot.artState === "loading" && artTerm.visible && root.active
                            loops: Animation.Infinite
                            NumberAnimation { from: 1; to: 0; duration: 60 } PauseAnimation { duration: 420 }
                            NumberAnimation { from: 0; to: 1; duration: 60 } PauseAnimation { duration: 420 }
                        }
                    }
                }
            }
            // failed face
            Column {
                anchors.centerIn: parent; spacing: 4
                opacity: artRoot.artState === "failed" ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 250 } }
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    textFormat: Text.PlainText
                    text: artTerm.compact ? "x" : "art: ERR"
                    font.family: root.mono
                    font.pixelSize: Math.max(1, Math.round(artTerm.width * (artTerm.compact ? 0.30 : 0.11)))
                    font.bold: artTerm.compact
                    color: root.red
                }
                Text {
                    visible: !artTerm.compact
                    anchors.horizontalCenter: parent.horizontalCenter
                    textFormat: Text.PlainText; text: "no link"
                    font.family: root.mono; font.pixelSize: Math.max(1, Math.round(artTerm.width * 0.11))
                    color: root.textDim
                }
            }
        }
    }

    // One colour-coded badge carrying the quality tier and, when known, its
    // format spec (e.g. "LOSSLESS 16/44.1", "HI-RES 24-bit"). The spec rides in
    // a dimmer shade of the same tier colour, so the tier stays prominent while
    // spelling out what that tier means for this specific album/track.
    // Tier counts for a loaded track list: [] when uniform, else
    // [{q, n}, ...] best-tier-first, drives the MIXED variant of QualTag.
    function qualMixList(tracks) {
        var counts = {}
        for (var i = 0; i < tracks.length; ++i) {
            var q = tracks[i].quality || ""
            if (q !== "") counts[q] = (counts[q] || 0) + 1
        }
        var keys = Object.keys(counts)
        if (keys.length < 2) return []
        var rank = { "HI-RES": 0, "LOSSLESS": 1, "HIGH": 2 }
        keys.sort(function(a, b) { return (rank[a] !== undefined ? rank[a] : 9) - (rank[b] !== undefined ? rank[b] : 9) })
        return keys.map(function(k) { return { q: k, n: counts[k] } })
    }

    component QualTag: Row {
        id: qt
        property string q: ""
        // Set (via qualMixList) when the album mixes tiers, replaces the
        // single-tier pill with MIXED + per-tier counts (/ sep).
        property var mix: []
        readonly property bool mixed: mix.length > 1
        visible: q !== "" || mixed
        Rectangle {
            visible: !qt.mixed
            radius: 4; color: root.surface2
            border.color: root.qualBorder(qt.q); border.width: 1
            implicitHeight: 22; implicitWidth: tagRow.implicitWidth + 16
            Row {
                id: tagRow
                anchors.centerIn: parent; spacing: 6
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 6; height: 6; radius: 3; color: root.qualDot(qt.q)
                }
                Text {
                    textFormat: Text.PlainText
                    anchors.verticalCenter: parent.verticalCenter
                    text: qt.q; color: root.qualFg(qt.q); font.family: root.mono; font.pixelSize: 9; font.bold: true
                }
                Text {
                    textFormat: Text.PlainText
                    anchors.verticalCenter: parent.verticalCenter
                    visible: root.qualSpec(qt.q) !== ""
                    text: root.qualSpec(qt.q); color: root.qualSpecFg(qt.q); font.family: root.mono; font.pixelSize: 9
                }
            }
        }
        // MIXED, one dot per tier, then per-tier track counts as the spec
        Rectangle {
            visible: qt.mixed
            radius: 4; color: root.surface2
            border.color: root.outline; border.width: 1
            implicitHeight: 22; implicitWidth: mixRow.implicitWidth + 16
            Row {
                id: mixRow
                anchors.centerIn: parent; spacing: 6
                Row {
                    spacing: 2; anchors.verticalCenter: parent.verticalCenter
                    Repeater {
                        model: qt.mix
                        delegate: Rectangle {
                            required property var modelData
                            width: 6; height: 6; radius: 3; color: root.qualDot(modelData.q)
                        }
                    }
                }
                Text {
                    textFormat: Text.PlainText
                    anchors.verticalCenter: parent.verticalCenter
                    text: "MIXED"; color: root.textHi; font.family: root.mono; font.pixelSize: 9; font.bold: true
                }
                Repeater {
                    model: qt.mix
                    delegate: Row {
                        required property var modelData
                        required property int index
                        spacing: 6; anchors.verticalCenter: parent.verticalCenter
                        Text {
                            textFormat: Text.PlainText
                            visible: index > 0
                            anchors.verticalCenter: parent.verticalCenter
                            text: "/"; color: root.textDim; font.family: root.mono; font.pixelSize: 9
                        }
                        Text {
                            textFormat: Text.PlainText
                            anchors.verticalCenter: parent.verticalCenter
                            text: modelData.n + "× " + modelData.q
                            color: root.qualSpecFg(modelData.q); font.family: root.mono; font.pixelSize: 9
                        }
                    }
                }
            }
        }
    }

    // Popularity (0-100) shown as a thin meter + number
    // Popularity as a 2-row LED matrix (pop lab option 5): one fixed-width
    // block in the download-bar language, no trailing number, so stacked
    // rows align instead of staggering.
    component PopMeter: Row {
        id: pm
        property int value: 0
        property bool showNum: true   // kept for call-site compat; the matrix has no number
        visible: value >= 0
        spacing: 5
        Ico { name: "heart"; color: root.red; size: 12; anchors.verticalCenter: parent.verticalCenter }
        Column {
            spacing: 1.5; anchors.verticalCenter: parent.verticalCenter
            Repeater {
                model: 2
                delegate: Row {
                    required property int index
                    readonly property int rowTop: index
                    spacing: 1.5
                    Repeater {
                        model: 10
                        delegate: Rectangle {
                            required property int index
                            // Column-major bottom-up fill, same as the download matrices.
                            readonly property int fillIndex: index * 2 + (1 - rowTop)
                            readonly property bool lit: fillIndex < Math.round(Math.max(0, Math.min(100, pm.value)) / 100 * 20)
                            width: 3; height: 3; radius: 0   // sharp LED cells, not rounded
                            color: root.segColor(index)
                            opacity: lit ? 1.0 : 0.16
                        }
                    }
                }
            }
        }
    }

    // Vector retry mark: two opposing arcs with arrowheads (sync-twin, diagonal),
    // chosen in the vector retry lab. Drawn geometry instead of a font glyph so
    // it renders identically on every OS: JetBrains Mono's only retry-shaped
    // characters (↩ ↞) read thin and off-centre next to the heavy ↓ ✓ ▶, and
    // the classic circular arrows (↺ ↻ ⟳) are missing from the font entirely.
    component RetryMark: Item {
        id: rm
        property color color: root.red
        property real box: 16
        implicitWidth: box; implicitHeight: box
        onColorChanged: rmCanvas.requestPaint()
        Canvas {
            id: rmCanvas
            anchors.fill: parent
            antialiasing: true
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                var b = rm.box, cx = width / 2, cy = height / 2, r = b * 0.335
                ctx.strokeStyle = rm.color; ctx.fillStyle = rm.color
                ctx.lineWidth = Math.max(1.5, b * 0.115)
                ctx.lineCap = "round"; ctx.lineJoin = "round"
                ctx.translate(cx, cy); ctx.rotate(-Math.PI / 4); ctx.translate(-cx, -cy)
                function head(x, y, ang, s) {
                    ctx.save(); ctx.translate(x, y); ctx.rotate(ang)
                    ctx.beginPath()
                    ctx.moveTo(s * 0.95, 0)
                    ctx.lineTo(-s * 0.55, -s * 0.62)
                    ctx.lineTo(-s * 0.55, s * 0.62)
                    ctx.closePath(); ctx.fill(); ctx.restore()
                }
                var g = (Math.PI - Math.PI * 0.70) / 2
                var t0 = -Math.PI + g, t1 = -g
                ctx.beginPath(); ctx.arc(cx, cy, r, t0, t1, false); ctx.stroke()
                head(cx + r * Math.cos(t1), cy + r * Math.sin(t1), t1 + Math.PI / 2, b * 0.22)
                var b0 = g, b1 = Math.PI - g
                ctx.beginPath(); ctx.arc(cx, cy, r, b0, b1, false); ctx.stroke()
                head(cx + r * Math.cos(b1), cy + r * Math.sin(b1), b1 + Math.PI / 2, b * 0.22)
            }
        }
    }

    // Compact outlined terminal download icon (rows). Shows ↓ / mono % / ✓ / ↺
    // with a thin bottom progress line while running.
    component DownIcon: Rectangle {
        id: di
        property var onTap: (function(){})
        property string mediaId: ""
        // Opt-in: mediaId is an album/playlist/mix id, not a track id, so it
        // must be resolved through the locally learned collection membership
        // instead of being looked up as if it were a track (see DownloadButton
        // for the same distinction, and why this can never require a fetch).
        property bool collectionCheck: false
        // A copy from an earlier session, straight from the ownership store
        // (checked against the disk, so a deleted file reads as not owned).
        // Live job state always wins; this only fills the idle state.
        property bool owned: false
        // Owned AND current for today's quality setting: an owned copy below the
        // target quality shows Download again (clicking upgrades in place).
        function refreshOwned() {
            if (collectionCheck) {
                var ids = di.mediaId !== "" ? waves.collectionMemberIds(di.mediaId) : null
                if (!ids || ids.length === 0) { owned = false; return }
                var n = 0
                for (var i = 0; i < ids.length; ++i) {
                    var oi = waves.ownershipOf(ids[i])
                    if (oi.owned === true && oi.up_to_date === true) ++n
                }
                owned = n === ids.length
                return
            }
            var o = di.mediaId !== "" ? waves.ownershipOf(di.mediaId) : ({})
            owned = o.owned === true && o.up_to_date === true
        }
        Component.onCompleted: refreshOwned()
        onMediaIdChanged: refreshOwned()
        onCollectionCheckChanged: refreshOwned()
        Connections {
            target: waves
            // Empty id = broadcast (the quality setting changed).
            function onOwnershipChanged(tid) {
                if (di.collectionCheck) { di.refreshOwned() }
                else if (tid === di.mediaId || tid === "") { di.refreshOwned() }
            }
            function onCollectionMembershipChanged(cid) { if (di.collectionCheck && cid === di.mediaId) di.refreshOwned() }
        }
        readonly property string liveSt: di.mediaId !== "" ? root.dlSt(di.mediaId) : ""
        readonly property string st: liveSt !== "" ? liveSt : (owned ? "done" : "")
        readonly property real pct: di.mediaId !== "" ? root.dlPct(di.mediaId) : -1
        implicitWidth: 32; implicitHeight: 30
        radius: root.btnRad; clip: true
        color: st === "done" ? root.greenCont : st === "failed" ? root.redCont : root.accentCont
        border.width: 1
        border.color: st === "failed" ? root.red : st === "done" ? root.greenDim : root.accentDim
        scale: 1
        Behavior on scale { NumberAnimation { duration: 130; easing.type: Easing.OutBack } }
        // RUNNING: an LED matrix backdrop fills the button edge to edge (cells
        // stretch fractionally so there is no leftover padding on any side),
        // filling column by column from the bottom left like the album bar;
        // the % reads on top. Done converts to the usual ✓ chip.
        Item {
            id: diGrid
            visible: di.st === "running"
            anchors.fill: parent; anchors.margins: 1   // sit inside the 1px border
            readonly property int gcols: 7
            readonly property int grows: 6
            readonly property real ggap: 1.5
            readonly property real cellW: (width - (gcols - 1) * ggap) / gcols
            readonly property real cellH: (height - (grows - 1) * ggap) / grows
            readonly property int total: gcols * grows
            readonly property int lit: Math.round(Math.max(0, Math.min(100, di.pct)) / 100 * total)
            opacity: 0.5
            // Item clip is square; mask the grid to the button's rounded shape
            // so edge-to-edge cells never poke past the corners.
            layer.enabled: true
            layer.effect: MultiEffect {
                maskEnabled: true
                maskSource: ShaderEffectSource { sourceItem: diGridMask; hideSource: false }
            }
            Repeater {
                model: diGrid.total
                delegate: Rectangle {
                    required property int index
                    readonly property int col: index % diGrid.gcols
                    readonly property int rowTop: Math.floor(index / diGrid.gcols)
                    // column-major, bottom-up, mirroring DotMatrix's rising fill
                    readonly property int fillIndex: col * diGrid.grows + (diGrid.grows - 1 - rowTop)
                    readonly property bool litCell: fillIndex < diGrid.lit
                    readonly property bool pulsing: fillIndex === diGrid.lit && diGrid.lit < diGrid.total
                    // Same finishing twinkle as DotMatrix (this grid is the
                    // same visual language, just inlined for the mask effect).
                    readonly property real twinkleR: { var r = Math.sin(index * 12.9898) * 43758.5453; return r - Math.floor(r) }
                    x: col * (diGrid.cellW + diGrid.ggap)
                    y: rowTop * (diGrid.cellH + diGrid.ggap)
                    width: diGrid.cellW; height: diGrid.cellH; radius: 0   // sharp LED cells
                    color: root.accent
                    // Breathe off the shared 20 Hz clock (root.ledPulse) rather than
                    // a per-frame animation: this grid also runs a layer + mask
                    // effect, so a per-frame pulse re-rendered the masked layer every
                    // vsync for the whole download. See root.ledPulse.
                    opacity: (di.pct >= 99.9 && litCell)
                           ? 0.62 + 0.38 * (0.5 + 0.5 * Math.cos(2 * Math.PI * (root.shimmerPhase * 2 + twinkleR)))
                           : pulsing ? root.ledPulse : (litCell ? 1.0 : 0.16)
                }
            }
        }
        Item {
            id: diGridMask
            anchors.fill: parent; anchors.margins: 1
            visible: false
            Rectangle { anchors.fill: parent; radius: root.btnRad - 1; color: "#ffffff" }
        }
        Text {
            textFormat: Text.PlainText
            anchors.centerIn: parent; visible: di.st === "running"
            text: di.pct >= 0 ? Math.round(di.pct) + "%" : "…"
            color: root.textHi; font.family: root.mono; font.pixelSize: 9; font.bold: true
            style: Text.Outline; styleColor: root.bg
        }
        Ico {
            anchors.centerIn: parent; visible: di.st !== "running" && di.st !== "failed"
            name: di.st === "done" ? "check" : "arrow-down"
            color: di.st === "done" ? root.green : root.accent
            size: 15; bold: di.st === "done" ? 0 : 10
        }
        RetryMark { anchors.centerIn: parent; visible: di.st === "failed"; color: root.red; box: 15 }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            onPressed: di.scale = 0.85
            onReleased: di.scale = 1.0
            onCanceled: di.scale = 1.0
            onClicked: { if (di.st === "running" || di.st === "done") return; di.onTap() }
        }
    }

    // Track preview: the album art IS the play button. Idle it's just artwork;
    // hover reveals a ▶ over a dark scrim; while previewing, a ring of dots hugging
    // the art fills clockwise with playback position (⏸ over the scrim). Only the
    // active track shows its ring, one shared player.
    component PreviewArt: Item {
        id: pa
        property string url: ""
        property string kind: "track"
        property string pid: ""
        readonly property string st: pa.pid !== "" ? root.pvSt(pa.kind, pa.pid) : ""
        readonly property bool active: pa.st === "playing" || pa.st === "paused" || pa.st === "loading"
        readonly property real frac: root.pvFrac(pa.kind, pa.pid)
        property bool hovered: false
        // Show the play/pause glyph on hover, or while connecting/errored. Plain
        // playback shows through the ring alone, keeping the cover unobscured.
        readonly property bool showGlyph: pa.hovered || pa.st === "loading" || pa.st === "error"
        implicitWidth: 48; implicitHeight: 48

        // Circular cover + perimeter progress ring painted together. A Rectangle's
        // clip ignores its radius (Qt draws children to the square bounds), so the
        // art is clipped to a real circle here via ctx.arc; the ring hugs it and
        // fills clockwise from 12 o'clock with playback. renderTarget Image keeps
        // it a software raster, reliable everywhere and cheap at this size.
        Canvas {
            id: pring
            anchors.fill: parent
            renderTarget: Canvas.Image
            antialiasing: true
            readonly property real artR: 17
            readonly property real ringR: 22
            onImageLoaded: requestPaint()
            Component.onCompleted: if (pa.url) loadImage(pa.url)
            Connections {
                target: pa
                function onUrlChanged() { if (pa.url) pring.loadImage(pa.url); else pring.requestPaint() }
                function onFracChanged() { pring.requestPaint() }
                function onActiveChanged() { pring.requestPaint() }
            }
            onPaint: {
                var ctx = getContext("2d"); ctx.reset()
                var cx = width / 2, cy = height / 2
                ctx.save()
                ctx.beginPath(); ctx.arc(cx, cy, artR, 0, 2 * Math.PI); ctx.closePath()
                if (pa.url !== "" && isImageLoaded(pa.url)) {
                    ctx.clip()
                    var d = artR * 2
                    ctx.drawImage(pa.url, cx - artR, cy - artR, d, d)
                } else {
                    ctx.fillStyle = root.surface3; ctx.fill()
                }
                ctx.restore()
                if (pa.active) {
                    var n = 32, lit = Math.round(pa.frac * n)
                    for (var i = 0; i < n; i++) {
                        var ang = -Math.PI / 2 + i / n * 2 * Math.PI
                        var x = cx + ringR * Math.cos(ang), y = cy + ringR * Math.sin(ang)
                        ctx.beginPath(); ctx.arc(x, y, 1.6, 0, 2 * Math.PI)
                        ctx.fillStyle = i < lit ? root.accent : root.surface3
                        ctx.fill()
                    }
                }
            }
        }
        // Only a whisper of a scrim, and only when the glyph is up.
        Rectangle {
            anchors.centerIn: parent; width: 34; height: 34; radius: 17; color: "#000000"
            opacity: pa.showGlyph ? 0.34 : 0
            Behavior on opacity { NumberAnimation { duration: 120 } }
        }
        Item {
            anchors.centerIn: parent; visible: pa.showGlyph; width: 30; height: 30
            // Play/pause/error as a vector glyph; the loading state keeps the
            // breathing "buffering" word. A soft dark disc behind the glyph gives
            // the same legibility over art the Text.Outline used to.
            Rectangle {
                anchors.centerIn: parent; width: 26; height: 26; radius: 13
                color: "#00000055"; visible: pa.st !== "loading"
            }
            Ico {
                anchors.centerIn: parent
                visible: pa.st !== "loading"
                name: pa.st === "playing" ? "pause" : (pa.st === "error" ? "close" : "play")
                color: pa.st === "error" ? root.red : root.accent
                size: 17
            }
            Text {
                anchors.centerIn: parent; textFormat: Text.PlainText
                visible: pa.st === "loading"
                text: "buffering"
                color: root.accent; font.family: root.mono
                font.pixelSize: 8; font.bold: true
                style: Text.Outline; styleColor: "#000000cc"
                property real breathe: 1
                opacity: breathe
                SequentialAnimation on breathe {
                    running: pa.st === "loading"; loops: Animation.Infinite
                    NumberAnimation { from: 1.0; to: 0.3; duration: 520; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 0.3; to: 1.0; duration: 520; easing.type: Easing.InOutSine }
                }
            }
        }
        MouseArea {
            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onEntered: pa.hovered = true
            onExited: pa.hovered = false
            onClicked: root.togglePreview(pa.kind, pa.pid, 0)
        }
    }

    // Compact ASCII preview toggle for album track rows, where a per-row cover
    // would just repeat the album art. Idle it's a tight mono "[>]"; on play it
    // wipes open into the SAME DotMatrix the download buttons use (4 rows,
    // column-major bottom-up, pulsing lead) and fills across the whole track.
    // The entire expanded bar is one hit target, so a click anywhere pauses and
    // collapses it back to "[>]". Loading shows a breathing "[buffering]"; error "[✕]".
    component TrackPreview: Item {
        id: tp
        property string kind: "track"
        property string pid: ""
        readonly property string st: tp.pid !== "" ? root.pvSt(tp.kind, tp.pid) : ""
        readonly property bool playing: tp.st === "playing"
        readonly property bool loading: tp.st === "loading"
        readonly property bool err: tp.st === "error"
        readonly property real pct: root.pvFrac(tp.kind, tp.pid) * 100
        // Live (playing or paused) expands into the row scrubber (player lab
        // option 3): pause / red stop / seekable dot matrix / elapsed.
        readonly property bool live: tp.st === "playing" || tp.st === "paused"
        readonly property bool expanded: tp.live
        // Matrix geometry mirrors DownloadButton's running bar (rows 4, dot 3, gap 2).
        readonly property int matCols: 12
        readonly property real matW: matCols * 3 + (matCols - 1) * 2   // 58
        readonly property real collapsedW: 12

        implicitWidth: bracketRow.implicitWidth
        implicitHeight: 24

        // Underneath the live controls so their MouseAreas win while expanded.
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: root.togglePreview(tp.kind, tp.pid, 0)
        }
        Row {
            id: bracketRow
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Text {
                textFormat: Text.PlainText; text: "["
                color: tp.err ? root.red : root.accentDim
                font.family: root.mono; font.pixelSize: 13; font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }
            // Morphing centre: the caret and the matrix crossfade while the box
            // width animates, so it reads as one control expanding / collapsing.
            Item {
                id: centre
                anchors.verticalCenter: parent.verticalCenter
                clip: true
                // Loading widens the box to fit "buffering", the surrounding
                // brackets make it read as "[buffering]" like the card labels.
                width: tp.expanded ? liveRow.implicitWidth : (tp.loading ? caret.implicitWidth + 2 : tp.collapsedW)
                height: 18   // 4*3 + 3*2
                Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                // Standalone pulse value (never bound elsewhere) so animating it
                // can't clobber the caret's opacity binding; the caret only reads it
                // while loading, to breathe during the multi-second full-track resolve.
                property real loadPulse: 1
                SequentialAnimation on loadPulse {
                    running: tp.loading; loops: Animation.Infinite
                    NumberAnimation { from: 1.0; to: 0.3; duration: 520; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 0.3; to: 1.0; duration: 520; easing.type: Easing.InOutSine }
                }
                Text {
                    id: caret
                    anchors.centerIn: parent
                    textFormat: Text.PlainText
                    text: tp.loading ? "buffering" : (tp.err ? "✕" : ">")
                    color: tp.err ? root.red : root.accent
                    font.family: root.mono; font.pixelSize: 13; font.bold: true
                    opacity: tp.loading ? centre.loadPulse : (tp.expanded ? 0 : 1)
                    Behavior on opacity { NumberAnimation { duration: 130 } }
                }
                Row {
                    id: liveRow
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8
                    opacity: tp.expanded ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 130 } }
                    Ico {
                        anchors.verticalCenter: parent.verticalCenter
                        name: tp.playing ? "pause" : "play"
                        color: root.accent; size: 11
                        MouseArea {
                            anchors.fill: parent; anchors.margins: -4
                            enabled: tp.live; cursorShape: Qt.PointingHandCursor
                            onClicked: function(m){ m.accepted = true; root.togglePreview(tp.kind, tp.pid, 0) }
                        }
                    }
                    Ico {
                        anchors.verticalCenter: parent.verticalCenter
                        name: "stop"
                        color: tpStopMa.containsMouse ? "#ff7d76" : root.red
                        size: 11
                        MouseArea {
                            id: tpStopMa
                            anchors.fill: parent; anchors.margins: -5
                            enabled: tp.live; hoverEnabled: enabled; cursorShape: Qt.PointingHandCursor
                            onClicked: function(m){ m.accepted = true; root.stopPreview() }
                        }
                    }
                    Item {
                        width: tp.matW; height: 18
                        anchors.verticalCenter: parent.verticalCenter
                        DotMatrix {
                            anchors.fill: parent
                            rows: 4; dot: 3; gap: 2; pct: tp.pct
                        }
                        MouseArea {
                            anchors.fill: parent
                            enabled: tp.live; cursorShape: Qt.PointingHandCursor
                            preventStealing: true
                            property bool scrubbing: false
                            // Press/drag only move the fill; the single real seek
                            // fires on release (same gesture as the PreviewBar).
                            function frac(x) { return width > 0 ? x / width : 0 }
                            onPressed: function(m){ m.accepted = true; scrubbing = true; root.previewScrubbing = true; root.scrubPreviewVisual(frac(m.x)) }
                            onPositionChanged: function(m){ if (scrubbing) root.scrubPreviewVisual(frac(m.x)) }
                            onReleased: function(m){ if (scrubbing) { scrubbing = false; root.previewScrubbing = false; root.seekPreview(frac(m.x)) } }
                            onCanceled: { scrubbing = false; root.previewScrubbing = false }
                        }
                    }
                    Text {
                        textFormat: Text.PlainText
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.fmtMs(root.previewPosition) + " / " + root.fmtMs(root.previewDuration)
                        color: root.textLo; font.family: root.mono; font.pixelSize: 10
                    }
                }
            }
            Text {
                textFormat: Text.PlainText; text: "]"
                color: tp.err ? root.red : root.accentDim
                font.family: root.mono; font.pixelSize: 13; font.bold: true
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    // Full-width preview control shaped like DownloadButton. Idle: ▶ + "PREVIEW
    // ARTIST". Once playing it becomes a scrubber: the leading ▶/⏸ glyph toggles
    // play/pause, and clicking or dragging the DotMatrix track seeks/scrubs the
    // whole track (position readout on the right). One shared player, so only the
    // active artist's bar shows the scrubber; the rest stay idle.
    component PreviewBar: Rectangle {
        id: pbar
        property string pid: ""
        property string kind: "artist"
        property string label: "Preview Artist"
        readonly property string st: pbar.pid !== "" ? root.pvSt(pbar.kind, pbar.pid) : ""
        readonly property bool live: st === "playing" || st === "paused"
        readonly property real frac: root.pvFrac(pbar.kind, pbar.pid)
        width: 140; height: 30; radius: root.btnRad; clip: true
        // natural (content) size per the shared button rules, callers may
        // still set explicit width/height (the artist page's fixed bar does)
        implicitWidth: pbIdleRow.implicitWidth + root.btnPadH * 2
        implicitHeight: pbIdleRow.implicitHeight + root.btnPadV * 2
        color: st === "error" ? root.redCont : "transparent"
        border.width: 1
        border.color: st === "error" ? root.red : root.accentDim
        // consume clicks so the card-wide open-artist MouseArea (z:-1) never fires
        MouseArea { anchors.fill: parent; onPressed: function(m){ m.accepted = true } }

        // IDLE / LOADING / ERROR, centered glyph + label (mirrors DownloadButton)
        Row {
            id: pbIdleRow
            anchors.centerIn: parent; spacing: 7
            visible: !pbar.live
            Ico {
                visible: pbar.st !== "loading"
                name: pbar.st === "error" ? "close" : "play"
                color: pbar.st === "error" ? root.red : root.accent
                size: 13; anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                textFormat: Text.PlainText
                text: pbar.st === "loading" ? "[buffering]" : (pbar.st === "error" ? "PREVIEW FAILED" : pbar.label.toUpperCase())
                color: pbar.st === "error" ? root.red : root.accent
                font.family: pbar.st === "loading" ? root.mono : root.uiFont
                font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack
                anchors.verticalCenter: parent.verticalCenter
                property real breathe: 1
                opacity: pbar.st === "loading" ? breathe : 1
                SequentialAnimation on breathe {
                    running: pbar.st === "loading"; loops: Animation.Infinite
                    NumberAnimation { from: 1.0; to: 0.3; duration: 520; easing.type: Easing.InOutSine }
                    NumberAnimation { from: 0.3; to: 1.0; duration: 520; easing.type: Easing.InOutSine }
                }
            }
        }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            visible: !pbar.live
            enabled: !pbar.live
            onClicked: root.togglePreview(pbar.kind, pbar.pid, 0)  // 0 = whole track (scrubbable)
        }

        // PLAYING / PAUSED, [⏵/⏸ toggle][DotMatrix scrub track][m:ss / m:ss]
        Item {
            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 10
            visible: pbar.live
            // Play/pause glyph, its own click zone so it never seeks.
            Ico {
                id: pbarGlyph
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                name: pbar.st === "playing" ? "pause" : "play"
                color: root.accent; size: 13; width: 18
                MouseArea {
                    anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                    onClicked: function(m){ m.accepted = true; root.togglePreview(pbar.kind, pbar.pid, 0) }
                }
            }
            // Stop, red ■ beside the ⏸ so play/pause and stop sit together;
            // red at rest so it stands out, brighter on hover.
            Ico {
                id: pbarStop
                anchors.left: pbarGlyph.right; anchors.verticalCenter: parent.verticalCenter
                name: "stop"
                color: pbarStopMa.containsMouse ? "#ff7d76" : root.red
                size: 11
                MouseArea {
                    id: pbarStopMa
                    anchors.fill: parent; anchors.margins: -5
                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: function(m){ m.accepted = true; root.stopPreview() }
                }
            }
            Text {
                id: pbarTime
                textFormat: Text.PlainText
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                text: root.fmtMs(root.previewPosition) + " / " + root.fmtMs(root.previewDuration)
                color: root.textLo; font.family: root.mono; font.pixelSize: 10
            }
            // Scrub track, click seeks, drag scrubs. The DotMatrix fill follows
            // previewPosition, which seekPreview updates instantly for zero lag.
            Item {
                id: pbarTrack
                anchors.left: pbarStop.right; anchors.leftMargin: 8
                anchors.right: pbarTime.left; anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter; height: parent.height
                DotMatrix {
                    anchors.left: parent.left; anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    rows: 4; dot: 3; gap: 2; pct: pbar.frac * 100
                }
                MouseArea {
                    id: pbarScrub
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    preventStealing: true
                    property bool scrubbing: false
                    // Press/drag only move the fill; the single real seek fires
                    // on release, so the backend seeks once and playback resumes
                    // cleanly (no mid-gesture flush pop).
                    function frac(x) { return width > 0 ? x / width : 0 }
                    onPressed: function(m){ m.accepted = true; scrubbing = true; root.previewScrubbing = true; root.scrubPreviewVisual(frac(m.x)) }
                    onPositionChanged: function(m){ if (scrubbing) root.scrubPreviewVisual(frac(m.x)) }
                    onReleased: function(m){ if (scrubbing) { scrubbing = false; root.previewScrubbing = false; root.seekPreview(frac(m.x)) } }
                    onCanceled: { scrubbing = false; root.previewScrubbing = false }
                }
            }
        }
    }

    // Outlined terminal download button. Idle: ↓ + uppercase label. Running:
    // a monospace ASCII bar (█ filled + ░ dim) + %. Done/failed: colour + glyph.
    component DownloadButton: Rectangle {
        id: db
        property string mediaId: ""
        property string label: "Download"
        property var onTap: (function(){})
        // Opt-in for track-scoped buttons only: the ownership store is keyed by
        // exact track id, so a plain album/playlist/artist mediaId must not be
        // looked up as if it were one.
        property bool ownedCheck: false
        // Collection rollup: when set (non-null array of member track/video
        // ids), DOWNLOADED means every one of those ids is individually owned.
        // Set where the caller already has the member ids in hand (an opened
        // album/playlist/mix page, an expanded album panel) — never triggers
        // a fetch itself.
        property var collectionIds: null
        // Collection rollup, discovered locally: mediaId is a collection id
        // (album/playlist/mix) and its member ids are looked up from what
        // Waves has already LEARNED locally (see collectionMemberIds) — no
        // caller-supplied list needed, so this also covers collapsed rows and
        // shelf cards that have never had their track list fetched. A
        // collection Waves has genuinely never opened or downloaded reads as
        // unknown (not owned) until the first time it is: this is a local
        // cache, not a live query, so it can never require a network call.
        property bool collectionCheck: false
        property bool owned: false
        function _rollup(ids) {
            if (!ids || ids.length === 0) return false
            var n = 0
            for (var i = 0; i < ids.length; ++i) {
                var oi = waves.ownershipOf(ids[i])
                if (oi.owned === true && oi.up_to_date === true) ++n
            }
            return n === ids.length
        }
        function refreshOwned() {
            if (collectionIds !== null) { owned = _rollup(collectionIds); return }
            if (collectionCheck && mediaId !== "") { owned = _rollup(waves.collectionMemberIds(mediaId)); return }
            var o = ownedCheck && mediaId !== "" ? waves.ownershipOf(mediaId) : ({})
            owned = o.owned === true && o.up_to_date === true
        }
        Component.onCompleted: refreshOwned()
        onMediaIdChanged: refreshOwned()
        onOwnedCheckChanged: refreshOwned()
        onCollectionIdsChanged: refreshOwned()
        onCollectionCheckChanged: refreshOwned()
        Connections {
            target: waves
            enabled: db.ownedCheck || db.collectionIds !== null || db.collectionCheck
            // Empty id = broadcast (the quality setting changed).
            function onOwnershipChanged(tid) {
                if (db.collectionIds !== null) {
                    if (tid === "" || db.collectionIds.indexOf(tid) !== -1) db.refreshOwned()
                } else if (db.collectionCheck) {
                    db.refreshOwned()
                } else if (tid === db.mediaId || tid === "") {
                    db.refreshOwned()
                }
            }
            function onCollectionMembershipChanged(cid) {
                if (db.collectionCheck && cid === db.mediaId) db.refreshOwned()
            }
        }
        readonly property real pct: root.dlPct(mediaId)
        readonly property string liveSt: root.dlSt(mediaId)
        readonly property string st: liveSt !== "" ? liveSt : (owned ? "done" : "")
        implicitHeight: dbRow.implicitHeight + root.btnPadV * 2
        // Width is pinned to the idle label ("⭳ DOWNLOAD …") so the button
        // doesn't shrink when the state text changes to DONE/RETRY, keeps
        // row columns aligned and avoids layout jumps mid-download.
        implicitWidth: Math.max(dbRow.implicitWidth, dbMetric.implicitWidth, dbMetricDone.implicitWidth) + root.btnPadH * 2
        Row {
            id: dbMetric
            visible: false; spacing: 7
            Ico { name: "arrow-down"; color: root.accent; size: 14; bold: 10 }
            Text { textFormat: Text.PlainText; text: db.label.toUpperCase(); font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack }
        }
        Row {
            id: dbMetricDone
            visible: false; spacing: 7
            Ico { name: "check"; color: root.accent; size: 14 }
            Text { textFormat: Text.PlainText; text: "DOWNLOADED"; font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack }
        }
        radius: root.btnRad
        // Filled like DOWNLOAD SELECTED so every download button reads primary.
        color: st === "done" ? root.greenCont : st === "failed" ? root.redCont : root.accentCont
        border.width: 1
        border.color: st === "done" ? root.greenDim : st === "failed" ? root.red : root.accentDim
        clip: true
        scale: 1
        Behavior on scale { NumberAnimation { duration: 130; easing.type: Easing.OutBack } }

        // RUNNING, dot matrix fills the full button width; % pinned to the right
        Item {
            visible: db.st === "running"
            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
            Text {
                textFormat: Text.PlainText
                // pct is -1 until the first progress event (mirrors DownIcon's "…")
                id: dbPct; text: db.pct >= 0 ? Math.round(db.pct) + "%" : "…"
                color: root.accent; font.family: root.mono; font.pixelSize: 11; font.bold: true
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            }
            DotMatrix {
                anchors.left: parent.left; anchors.right: dbPct.left; anchors.rightMargin: 8
                anchors.verticalCenter: parent.verticalCenter
                rows: 4; dot: 3; gap: 2; pct: Math.max(0, db.pct)
                // Only visible while st === "running", so 100% here means the
                // final steps are still in flight: twinkle in step with the
                // queue row for the same item.
                finishing: db.pct >= 99.9
            }
        }
        // IDLE / DONE / FAILED, centered glyph + label
        Row {
            id: dbRow; anchors.centerIn: parent; spacing: 7
            visible: db.st !== "running"
            Ico {
                visible: db.st !== "failed"
                name: db.st === "done" ? "check" : "arrow-down"
                color: root.accent
                size: 14; bold: db.st === "done" ? 0 : 10; anchors.verticalCenter: parent.verticalCenter
            }
            RetryMark { visible: db.st === "failed"; color: root.red; box: 16; anchors.verticalCenter: parent.verticalCenter }
            Text {
                textFormat: Text.PlainText  // db.label carries a remote artist name
                text: db.st === "done" ? "DOWNLOADED" : db.st === "failed" ? "RETRY" : db.label.toUpperCase()
                color: db.st === "done" ? root.accent : db.st === "failed" ? root.red : root.accent
                font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            onPressed: db.scale = 0.96
            onReleased: db.scale = 1.0
            onCanceled: db.scale = 1.0
            onClicked: { if (db.st === "running" || db.st === "done") return; db.onTap() }
        }
    }

    // ASCII ocean-wave logo (the "Parallax Ocean" mark):
    // four depth layers of wave glyphs - foam specks, small ripples, rolling swell,
    // and a big foreground crest - all scrolling left at parallax speeds (back layers
    // slower) for moving-water depth, rather than flat parallel lines.
    component WaveMark: Rectangle {
        id: wm
        property int boxW: 133                        // width
        property int boxH: 34
        readonly property real uscale: boxH / 34      // glyph px reference = box height
        // Front-layer scroll speed (px/sec); back layers scale by their parallax factor.
        // 46 px/s baseline x 0.21 ~= 9.66 px/s - calm.
        // One shared loop clock (0..1 over loopSecs); each layer derives its own
        // offset as (t x k + phase) tiles, the exact math of the README banner GIF
        // (96 frames @ 14 fps), so box and banner show the same water.
        property real loopSecs: 96 / 14
        property real t: 0
        Timer {
            // Pause while the window is unfocused/minimised, no one's watching
            // the logo then, so don't burn CPU animating it. Resumes on refocus.
            running: wm.visible && root.active
            interval: 50; repeat: true                // 20 Hz step (see delegate note)
            onTriggered: wm.t = (wm.t + 0.05 / wm.loopSecs) % 1
        }
        property bool storm: false                    // hover -> ascii lightning strikes
        // Content is clipped by an inner item inset past the rounded corners, so a
        // glyph (or bolt) can never poke out a corner, a clip on the rounded
        // Rectangle itself is rectangular and leaves the corner triangles exposed.
        readonly property int inset: 3
        width: boxW; height: boxH; radius: 8
        color: "#04140a"; border.color: root.accentDim; border.width: 1

        Item {
            id: wmClip
            anchors.fill: parent; anchors.margins: wm.inset; clip: true

            // Six-layer water lifted verbatim from the README banner GIF generator
            // (make_logo_gif.py): faint foam up top fading into brighter, denser
            // swell. k = pattern-tiles travelled per loop (parallax: front faster);
            // ph = initial offset (0..1 of a tile) so crests don't line up. px is
            // the GIF's glyph size normalised to the 34px box reference.
            Repeater {
                model: [
                    { yf: 0.05, px: 3.5, op: 0.34, k: 1, ph: 0.00, pat: "   '    .     *   :   .   ", col: root.accentContTx },
                    { yf: 0.18, px: 4.0, op: 0.46, k: 1, ph: 0.42, pat: ".~-~..-~-.~..-~-.",          col: root.green },
                    { yf: 0.31, px: 4.0, op: 0.56, k: 2, ph: 0.75, pat: "-.~-..~.-~-..~.-",           col: root.green },
                    { yf: 0.45, px: 5.0, op: 0.70, k: 2, ph: 0.18, pat: "_.-~-._.,-~-._.-",           col: root.accent },
                    { yf: 0.59, px: 5.5, op: 0.84, k: 3, ph: 0.60, pat: ".-~^-._,.~-^._.-~",          col: root.accent },
                    { yf: 0.72, px: 6.5, op: 1.00, k: 3, ph: 0.10, pat: "_.-~^~-._.~^'~._",           col: root.accent }
                ]
                delegate: Row {
                    required property var modelData
                    readonly property int reps: 9
                    // One pattern-tile's width, NOT the full repeated strip: the
                    // (t x k + ph) fraction is in units of a single tile, exactly as
                    // in the GIF generator, so the drift speed matches the README.
                    readonly property real tileW: tile.width / reps
                    y: Math.round(wm.boxH * modelData.yf) - wm.inset
                    // Stepped by wm.t (a coarse Timer) rather than a per-frame
                    // animation: a per-frame NumberAnimation forces a scene repaint at
                    // the display's full refresh (120 Hz on ProMotion), which idled the
                    // whole app at ~30% CPU. At these drift speeds a 20 Hz step
                    // (<0.5 px per tick) is visually identical and repaints 6x less.
                    x: tile.width > 0 ? -Math.round(((wm.t * modelData.k + modelData.ph) % 1) * tileW) : 0
                    Text { textFormat: Text.PlainText; id: tile; text: modelData.pat.repeat(reps); font.family: root.mono; font.pixelSize: Math.max(2, Math.round(modelData.px * wm.uscale)); color: modelData.col; opacity: modelData.op; font.letterSpacing: -0.5 }
                    Text { textFormat: Text.PlainText; text: modelData.pat.repeat(reps); font.family: root.mono; font.pixelSize: tile.font.pixelSize; color: modelData.col; opacity: modelData.op; font.letterSpacing: -0.5 }
                }
            }

            // ASCII lightning: four independent strike slots. Each strike picks a
            // random shape, size and position, then waits a random beat before the
            // next, so bolts land in different places at different times, sometimes
            // spread out, sometimes overlapping, instead of a fixed metronome loop.
            Repeater {
                model: 4
                delegate: Text {
                    id: boltTx
                    textFormat: Text.PlainText
                    required property int index
                    readonly property var shapes: [
                        "\\\n \\\n /\n/",  "/\n\\\n \\\n  /", "\\\n \\/\n  \\",
                        "\\\n/\n\\\n \\",  "\\\n \\",          " /\n/\n\\",
                        "/\n \\\n  \\\n  /", "\\\n \\\n  \\/\n  /\n /"
                    ]
                    function strike() {
                        text = shapes[Math.floor(Math.random() * shapes.length)]
                        font.pixelSize = Math.max(6, Math.round((7 + Math.random() * 5) * wm.uscale))
                        x = Math.round(wm.boxW * (0.05 + Math.random() * 0.84)) - wm.inset
                        y = Math.round(wm.boxH * (Math.random() * 0.16)) - wm.inset
                        bolt.restart()
                    }
                    function rearm(first) {
                        // First strike after hover lands quickly (slot-staggered);
                        // afterwards each slot free-runs on its own random beat.
                        pauseT.interval = first ? index * 140 + Math.random() * 500
                                                : 150 + Math.random() * 2100
                        pauseT.restart()
                    }
                    color: "#eafff1"
                    font.family: root.mono
                    font.bold: true
                    lineHeight: 0.78
                    opacity: 0
                    // One full strike cycle (flicker -> fade to 0). It re-arms itself
                    // rather than binding `running: wm.storm` so that when the mouse
                    // leaves mid-strike the bolt finishes fading out instead of
                    // freezing at whatever opacity it was caught on.
                    SequentialAnimation on opacity {
                        id: bolt
                        running: false
                        NumberAnimation { to: 1.0;  duration: 40 }
                        NumberAnimation { to: 0.12; duration: 60 }
                        NumberAnimation { to: 0.88; duration: 50 }
                        NumberAnimation { to: 0.0;  duration: 130 }
                        onStopped: if (wm.storm) boltTx.rearm(false)
                    }
                    Timer { id: pauseT; repeat: false; onTriggered: if (wm.storm) boltTx.strike() }
                    Connections {
                        target: wm
                        function onStormChanged() { if (wm.storm && !bolt.running && !pauseT.running) boltTx.rearm(true) }
                    }
                }
            }
        }

        // Hover -> storm (lightning). NoButton so it never eats clicks.
        MouseArea {
            anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton
            onEntered: wm.storm = true
            onExited: wm.storm = false
        }
    }

    // Clipboard glyph that "decrypt-fills" on paste. Shared by the search bar and
    // the login redirect field so both get the identical paste affordance.
    component PasteGlyph: Rectangle {
        id: pg
        signal clicked()
        function play() { decAnim.restart() }
        implicitWidth: 30; implicitHeight: 30; radius: 7
        color: pgMa.containsMouse ? root.accentCont : root.surface3
        border.color: pgMa.containsMouse ? root.accentDim : root.border1
        property real fillT: 0          // 0..1 decrypt-fill level
        property real fillOpacity: 0

        Rectangle {            // clipboard body (clips the rising fill)
            id: clipBody
            anchors.centerIn: parent; anchors.verticalCenterOffset: 1
            width: 14; height: 16; radius: 2; clip: true
            color: "transparent"; border.color: root.accent; border.width: 1.4
            Rectangle {        // rising decrypt fill
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                height: parent.height * pg.fillT
                color: root.accent; opacity: pg.fillOpacity
            }
            Rectangle { x: 3; y: 4;  width: 8; height: 1.4; color: root.accent
                        opacity: pg.fillT >= (1 - 4 / clipBody.height) ? 1 : 0.4 }
            Rectangle { x: 3; y: 8;  width: 8; height: 1.4; color: root.accent
                        opacity: pg.fillT >= (1 - 8 / clipBody.height) ? 1 : 0.4 }
            Rectangle { x: 3; y: 12; width: 6; height: 1.4; color: root.accent
                        opacity: pg.fillT >= (1 - 12 / clipBody.height) ? 1 : 0.4 }
            Rectangle {        // bright scan line riding the top of the fill
                anchors.left: parent.left; anchors.right: parent.right
                y: Math.max(0, parent.height * (1 - pg.fillT) - 1)
                height: 2; color: root.accentContTx
                visible: pg.fillT > 0.001 && pg.fillT < 0.999
            }
        }
        Rectangle {            // clipboard tab/clamp
            anchors.horizontalCenter: clipBody.horizontalCenter
            y: clipBody.y - 2; width: 7; height: 4; radius: 1
            color: pg.color; border.color: root.accent; border.width: 1.4
        }
        SequentialAnimation {
            id: decAnim
            PropertyAction { target: pg; property: "fillOpacity"; value: 0.32 }
            NumberAnimation { target: pg; property: "fillT"; from: 0; to: 1; duration: 430; easing.type: Easing.OutCubic }
            NumberAnimation { target: pg; property: "fillOpacity"; from: 0.32; to: 0; duration: 240 }
            PropertyAction { target: pg; property: "fillT"; value: 0 }
        }
        MouseArea {
            id: pgMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: pg.clicked()
        }
    }

    // Tap-card action: the pop-ups' shared primary-action shape. A full-width
    // tinted card with the label left and an arrow right, so actions read the
    // same as the option cards (the card IS the button). danger tints it red.
    component GateAction: Rectangle {
        id: ga
        property string label: ""
        property bool danger: false
        property bool neutral: false      // grey secondary style (e.g. "keep" beside a green CTA)
        property bool showArrow: true     // hide when two GateActions sit side by side
        signal clicked()
        readonly property color fg: neutral ? root.textHi : (danger ? root.red : root.accent)
        readonly property color bg: neutral ? root.surface3 : (danger ? root.redCont : root.accentCont)
        readonly property color bd: neutral ? root.outline : (danger ? Qt.alpha(root.red, 0.55) : root.accentDim)
        Layout.fillWidth: true; implicitHeight: 46; radius: 10
        color: gaMa.containsMouse && ga.enabled ? Qt.lighter(bg, 1.35) : bg
        border.width: 1; border.color: bd
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 10
            Text { textFormat: Text.PlainText; text: ga.label; color: ga.fg; font.pixelSize: 13; font.bold: true; font.family: root.uiFont; Layout.fillWidth: true; elide: Text.ElideRight; horizontalAlignment: ga.showArrow ? Text.AlignLeft : Text.AlignHCenter }
            Ico { visible: ga.showArrow; name: "arrow-right"; color: ga.fg; size: 15 }
        }
        MouseArea { id: gaMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: ga.clicked() }
    }

    // Tap-card option: title + description + optional mono chip, arrow right.
    // Used where a pop-up offers a choice; tapping the card takes the action,
    // so there is no separate confirm button. highlight marks the recommended
    // (accent-tinted) option.
    component GateCard: Rectangle {
        id: gcard
        property string title: ""
        property string desc: ""
        property string chip: ""
        property bool highlight: false
        signal clicked()
        Layout.fillWidth: true
        implicitHeight: gcRow.implicitHeight + 24; radius: 10
        color: highlight ? (gcMa.containsMouse ? Qt.lighter(root.accentCont, 1.35) : root.accentCont)
                         : (gcMa.containsMouse ? root.surface3 : root.surface0)
        border.width: 1; border.color: highlight ? root.accentDim : (gcMa.containsMouse ? root.outline : root.border1)
        RowLayout {
            id: gcRow
            anchors.verticalCenter: parent.verticalCenter
            x: 14; width: parent.width - 28; spacing: 10
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Text { textFormat: Text.PlainText; text: gcard.title; color: gcard.highlight ? root.accent : root.textHi; font.pixelSize: 13; font.bold: true }
                    Text { textFormat: Text.PlainText; visible: gcard.chip !== ""; text: gcard.chip; color: root.accent; font.family: root.mono; font.pixelSize: 9; font.bold: true; font.letterSpacing: 0.8 }
                    Item { Layout.fillWidth: true }
                }
                Text { textFormat: Text.PlainText; visible: gcard.desc !== ""; text: gcard.desc; color: root.textDim; font.pixelSize: 11; wrapMode: Text.WordWrap; lineHeight: 1.2; Layout.fillWidth: true }
            }
            Ico { name: "arrow-right"; color: gcard.highlight ? root.accent : root.textDim; size: 15; Layout.alignment: Qt.AlignVCenter }
        }
        MouseArea { id: gcMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: gcard.clicked() }
    }

    // Drives the "matrix decrypt" paste-in for a TextField: scrambled glyphs settle
    // left-to-right into the pasted text, then decoded(text) fires. We never read the
    // clipboard, only react to what the field received. Shared by search + login.
    component DecodeController: QtObject {
        id: dc
        required property var field          // the TextField it animates
        property var glyph: null             // optional PasteGlyph to fill in sync
        property bool decoding: false
        signal decoded(string text)
        property string _final: ""
        property int _locked: 0
        property int _step: 1                 // chars revealed per tick, keeps the
        property int _prevLen: 0              // total decode ~fixed even for long URLs
        readonly property int _maxTicks: 24   // ~24 * 26ms ~= 0.6s, any length
        readonly property string _glyphs: "ABCDEF0123456789/:.~#@$%&abcdefxyz"
        function _scr(n) {
            var s = ""
            for (var i = 0; i < n; i++) s += _glyphs.charAt(Math.floor(Math.random() * _glyphs.length))
            return s
        }
        // Call from the field's onTextChanged: a multi-char jump that typing can't
        // produce is treated as a paste and animated in.
        function noteTextChanged() {
            if (!decoding && field.text.length - _prevLen >= 4) run(field.text)
            _prevLen = field.text.length
        }
        function run(t) {
            if (!t) return
            t = ("" + t).trim()
            if (!t.length) return
            _final = t; _locked = 0; decoding = true
            _step = Math.max(1, Math.ceil(_final.length / _maxTicks))   // cap the run length
            field.text = _scr(t.length)        // start scrambled, no flash of the raw text
            field.forceActiveFocus()
            _timer.restart()
            if (glyph) glyph.play()
        }
        property Timer _timer: Timer {
            interval: 26; repeat: true
            onTriggered: {
                dc._locked += dc._step
                if (dc._locked >= dc._final.length) {
                    dc.field.text = dc._final
                    dc.decoding = false
                    stop()
                    dc.decoded(dc._final)
                } else {
                    dc.field.text = dc._final.substring(0, dc._locked) + dc._scr(dc._final.length - dc._locked)
                }
            }
        }
    }

    // Wide "Welcome to Waves" banner for the login card: the WaveMark parallax ocean
    // at banner scale, with the title scrolling as a marquee (or centered) over it.
    component WelcomeBanner: Rectangle {
        id: banner
        property string title: "Welcome to Waves"
        property bool marquee: false
        property bool mono: false
        property color ink: "#eef1f4"
        property int titleSize: 40
        property bool bold: true
        property bool scrim: true
        property real wavePxPerSec: 12          // calm, matches the header logo
        property real waveScale: 0.80           // glyph size vs. the 34px logo reference
        property real titleSpeed: 26            // marquee px/sec
        readonly property int inset: 3
        readonly property string titleFamily: mono ? root.mono : Qt.application.font.family
        readonly property string phrase: title + "    •    "

        implicitWidth: 404; implicitHeight: 75; radius: 8
        color: "#04140a"; border.color: root.accentDim; border.width: 1

        Item {
            id: bclip
            anchors.fill: parent; anchors.margins: banner.inset; clip: true

            // ---- parallax ASCII ocean (the same patterns as the header WaveMark) ----
            Repeater {
                model: [
                    { yf: 0.04, px: 8,  op: 0.50, par: 0.42, pat: "   '    .     *   :   .   ", col: root.accentContTx },
                    { yf: 0.17, px: 8,  op: 0.44, par: 0.55, pat: ".~-~..-~-.~..-~-.",          col: root.green },
                    { yf: 0.30, px: 10, op: 0.60, par: 0.70, pat: "~-._.,~-._.,~-._.,",         col: root.green },
                    { yf: 0.43, px: 11, op: 0.76, par: 0.84, pat: "_.-~-._.,-~-._.-",           col: root.accent },
                    { yf: 0.56, px: 14, op: 0.94, par: 1.00, pat: "_.-~^~-._.~^'~._",           col: root.accent },
                    { yf: 0.69, px: 17, op: 1.00, par: 1.18, pat: "_.-~^'~-._/\\.-~^~_.",       col: root.accent }
                ]
                delegate: Row {
                    required property var modelData
                    y: Math.round(banner.height * modelData.yf) - banner.inset
                    Text { textFormat: Text.PlainText; id: wtile; text: modelData.pat.repeat(8); font.family: root.mono; font.pixelSize: Math.round(modelData.px * banner.waveScale); color: modelData.col; opacity: modelData.op; font.letterSpacing: -1 }
                    Text { textFormat: Text.PlainText; text: modelData.pat.repeat(8); font.family: root.mono; font.pixelSize: Math.round(modelData.px * banner.waveScale); color: modelData.col; opacity: modelData.op; font.letterSpacing: -1 }
                    NumberAnimation on x {
                        // Only run while the login panel is actually showing (banner
                        // lives on it, visible: !waves.loggedIn). QML animations don't
                        // stop on invisibility, so gate them off once signed in.
                        running: wtile.width > 0 && !waves.loggedIn && root.active
                        from: 0; to: -wtile.width
                        duration: Math.max(1, Math.round(wtile.width / (banner.wavePxPerSec * modelData.par) * 1000))
                        loops: Animation.Infinite
                    }
                }
            }

            // ---- legibility scrim: dark central band, transparent top & bottom ----
            Rectangle {
                anchors.fill: parent; visible: banner.scrim
                gradient: Gradient {
                    GradientStop { position: 0.0;  color: "#00060810" }
                    GradientStop { position: 0.32; color: "#bf060810" }
                    GradientStop { position: 0.68; color: "#bf060810" }
                    GradientStop { position: 1.0;  color: "#00060810" }
                }
            }

            TextMetrics { id: phraseM; font.family: banner.titleFamily; font.pixelSize: banner.titleSize; font.bold: banner.bold; text: banner.phrase }

            // marquee: the phrase scrolls continuously (a •-separated ticker)
            Item {
                anchors.fill: parent; clip: true; visible: banner.marquee
                Item {
                    id: mqMove
                    anchors.verticalCenter: parent.verticalCenter
                    width: mqMain.implicitWidth; height: mqMain.implicitHeight
                    Text { textFormat: Text.PlainText; x: 0; y: 1; text: mqMain.text; font: mqMain.font; color: "#0a160d"; opacity: 0.9 }   // shadow for legibility
                    Text { textFormat: Text.PlainText; id: mqMain; x: 0; y: 0; text: banner.phrase.repeat(8); font.family: banner.titleFamily; font.pixelSize: banner.titleSize; font.bold: banner.bold; color: banner.ink }
                    NumberAnimation on x {
                        // Same gating as the wave layers: stop once signed in / hidden.
                        running: phraseM.advanceWidth > 0 && !waves.loggedIn && root.active
                        from: 0; to: -phraseM.advanceWidth
                        duration: Math.max(1, Math.round(phraseM.advanceWidth / banner.titleSpeed * 1000))
                        loops: Animation.Infinite
                    }
                }
            }

            // static: the phrase centered, waves moving behind it
            Item {
                anchors.fill: parent; visible: !banner.marquee
                Text { textFormat: Text.PlainText; anchors.centerIn: parent; anchors.verticalCenterOffset: 1; text: banner.title; font.family: banner.titleFamily; font.pixelSize: banner.titleSize; font.bold: banner.bold; color: "#0a160d"; opacity: 0.9 }   // shadow
                Text { textFormat: Text.PlainText; anchors.centerIn: parent; text: banner.title; font.family: banner.titleFamily; font.pixelSize: banner.titleSize; font.bold: banner.bold; color: banner.ink }
            }

            // ---- marquee edge fades (soften the wrap at both ends) ----
            Rectangle {
                visible: banner.marquee
                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 30
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "#04140a" }
                    GradientStop { position: 1.0; color: "#0004140a" }
                }
            }
            Rectangle {
                visible: banner.marquee
                anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom; width: 30
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0; color: "#0004140a" }
                    GradientStop { position: 1.0; color: "#04140a" }
                }
            }
        }
    }

    // Section header: small tracked uppercase label + thin rule + mono count chip.
    // CRT-tube nav tab ("Tube C" static-burst). At rest each tab is a dim
    // phosphor-green panel with a grey label. On select it powers on like an old
    // CRT: a burst of scanline static, then the panel strikes in from a collapsed
    // line (x, then y overshoot). Power-off crackles with static and collapses
    // back to a line, leaving a brief afterglow bar. Shared by the nav tabs.
    component NavTab: Item {
        id: nt
        property string label: ""
        property bool active: false
        signal clicked()
        implicitHeight: navMetric.implicitHeight + root.btnPadV * 2
        implicitWidth: navMetric.implicitWidth + root.btnPadH * 2

        // Colours for the CRT look. accent/accentCont/accentDim/accentSoft are
        // shared app tokens; the dim phosphor-panel tones are local to this look.
        readonly property color navDimBg:     "#0b140f"
        readonly property color navDimBorder: "#1f3d2a"
        readonly property color navDimHover:  "#2c5c3e"
        readonly property color navText:      "#8f949e"
        readonly property color navTextHi:    "#bcc1c9"
        readonly property color navStatic:    "#aeb4ad"

        // hidden metric, reserves width so the label never reflows
        Text { id: navMetric; visible: false; textFormat: Text.PlainText; text: nt.label; font.pixelSize: 13; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack }

        // idle body: dim phosphor panel; grey label that greys → green
        Rectangle {
            id: navDim; anchors.fill: parent; radius: root.btnRad; color: nt.navDimBg
            border.width: 1; border.color: navMa.containsMouse ? nt.navDimHover : nt.navDimBorder
            Behavior on border.color { ColorAnimation { duration: 180 } }
            Text {
                anchors.centerIn: parent; text: nt.label; textFormat: Text.PlainText
                font.pixelSize: 13; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack
                color: navMa.containsMouse ? nt.navTextHi : nt.navText
                opacity: nt.active ? 0 : 1
                Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutQuad } }
                Behavior on color { ColorAnimation { duration: 180 } }
            }
        }

        // lit body, collapses in / out like a CRT tube
        Rectangle {
            id: navLit; anchors.fill: parent; radius: root.btnRad; color: root.accentCont
            border.width: 1; border.color: root.accentDim; opacity: 0
            transform: Scale { id: navSc; origin.x: nt.width / 2; origin.y: nt.height / 2; xScale: 0.02; yScale: 0.02 }
            Text { anchors.centerIn: parent; text: nt.label; textFormat: Text.PlainText; color: root.accent; font.pixelSize: 13; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack }
        }

        // static: a stack of uneven scan segments that flicker together
        Item {
            id: navStat; anchors.fill: parent; opacity: 0; clip: true
            Column {
                anchors.centerIn: parent; spacing: 3
                Repeater { model: 5
                    delegate: Rectangle {
                        required property int index
                        width: nt.width * (0.35 + 0.12 * ((index * 3 + 1) % 5)); height: 2; radius: 1
                        anchors.horizontalCenter: parent.horizontalCenter
                        color: index % 2 === 0 ? root.accent : nt.navStatic
                        opacity: 0.5 + 0.1 * (index % 3)
                    } }
            }
        }

        Rectangle { id: navFlash; anchors.centerIn: parent; width: parent.width; height: 3; radius: 2; color: root.accentSoft; opacity: 0 }
        Rectangle { id: navAfter; anchors.centerIn: parent; width: 7; height: 3; radius: 2; color: root.accent; opacity: 0 }

        MouseArea { id: navMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: nt.clicked() }

        states: State { name: "on"; when: nt.active
            PropertyChanges { navLit.opacity: 1 }
            PropertyChanges { navSc.xScale: 1; navSc.yScale: 1 } }
        transitions: [
            Transition { to: "on"
                SequentialAnimation {
                    PropertyAction { target: navAfter; property: "opacity"; value: 0 }
                    SequentialAnimation {   // static burst as it strikes
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.7; duration: 24 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.2; duration: 24 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.6; duration: 21 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.0; duration: 63 } }
                    PropertyAction { target: navLit; property: "opacity"; value: 1 }
                    NumberAnimation { target: navSc; property: "xScale"; from: 0.02; to: 1; duration: 83; easing.type: Easing.OutCubic }
                    ParallelAnimation {
                        NumberAnimation { target: navSc; property: "yScale"; from: 0.02; to: 1; duration: 111; easing.type: Easing.OutBack }
                        SequentialAnimation {
                            NumberAnimation { target: navFlash; property: "opacity"; to: 0.65; duration: 38 }
                            NumberAnimation { target: navFlash; property: "opacity"; to: 0.0; duration: 128 } } } } },
            Transition { from: "on"
                SequentialAnimation {
                    NumberAnimation { target: navSc; property: "yScale"; to: 0.05; duration: 90; easing.type: Easing.InCubic }
                    PropertyAction { target: navLit; property: "opacity"; value: 0 }
                    ParallelAnimation {
                        SequentialAnimation {   // static crackle during collapse
                            NumberAnimation { target: navStat; property: "opacity"; to: 0.75; duration: 24 }
                            NumberAnimation { target: navStat; property: "opacity"; to: 0.25; duration: 28 }
                            NumberAnimation { target: navStat; property: "opacity"; to: 0.6; duration: 24 }
                            NumberAnimation { target: navStat; property: "opacity"; to: 0.0; duration: 77 } }
                        NumberAnimation { target: navFlash; property: "opacity"; to: 0.85; duration: 83 } }
                    NumberAnimation { target: navSc; property: "xScale"; to: 0.02; duration: 83; easing.type: Easing.InCubic }
                    ParallelAnimation {
                        NumberAnimation { target: navFlash; property: "opacity"; to: 0.0; duration: 63 }
                        SequentialAnimation {
                            PropertyAction { target: navAfter; property: "opacity"; value: 0.55 }
                            NumberAnimation { target: navAfter; property: "opacity"; to: 0.0; duration: 306; easing.type: Easing.InQuad } } } } }
        ]
    }

    component SectionHeader: Item {
        id: secHead
        property string label: ""
        property int count: -1
        // Collapsible mode (artist-page sections): shows a chevron, makes the
        // whole header a click target, and the caller owns the state (so it
        // can persist it via prefs).
        property bool collapsible: false
        property bool collapsed: false
        signal toggled()
        anchors.left: parent ? parent.left : undefined
        anchors.right: parent ? parent.right : undefined
        implicitHeight: 36
        RowLayout {
            anchors.left: parent.left; anchors.right: parent.right
            anchors.bottom: parent.bottom; anchors.bottomMargin: 7
            spacing: 12
            ExpandChevron {
                visible: secHead.collapsible
                open: !secHead.collapsed; hovered: secMa.containsMouse
                tile: 20; glyph: 14; showTile: false
                stroke: secMa.containsMouse ? root.accent : root.textLo
                Layout.alignment: Qt.AlignVCenter
            }
            Text { textFormat: Text.PlainText; text: label; color: secHead.collapsible && secMa.containsMouse ? root.textHi : root.textLo; font.pixelSize: 12; font.bold: true; font.letterSpacing: 1.9 }
            Rectangle { Layout.fillWidth: true; height: 1; color: root.divider }
            Rectangle {
                visible: count >= 0; radius: 4; color: "transparent"; border.color: root.border1
                implicitHeight: 18; implicitWidth: cntT.implicitWidth + 16
                Text { textFormat: Text.PlainText; id: cntT; anchors.centerIn: parent; text: count; color: root.textDim; font.family: root.mono; font.pixelSize: 11 }
            }
        }
        MouseArea {
            id: secMa
            anchors.fill: parent
            enabled: secHead.collapsible; hoverEnabled: secHead.collapsible
            cursorShape: secHead.collapsible ? Qt.PointingHandCursor : Qt.ArrowCursor
            onClicked: secHead.toggled()
        }
    }

    // Video thumbnail: 16:9-ish art with a scanline play strip.
    // The one video-play affordance: a green data-strip along the bottom edge
    // of the art with an ink triangle and PLAY label, like a terminal status
    // bar. `lit` brightens and thickens it while the row/thumb is hovered.
    // Fills its parent (the thumb): the strip anchors itself to the bottom.
    component PlayBadge: Item {
        property bool lit: false
        anchors.fill: parent
        Rectangle {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: lit ? 16 : 13
            radius: 6   // follow the art's rounded bottom corners
            color: lit ? root.accentSoft : root.accent
            Behavior on height { NumberAnimation { duration: 90 } }
            // Square off the strip's top edge (radius rounds all four corners).
            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                height: parent.radius
                color: parent.color
            }
            Row {
                anchors.verticalCenter: parent.verticalCenter
                anchors.left: parent.left; anchors.leftMargin: 6
                spacing: 5
                Canvas {
                    width: 7; height: 8
                    anchors.verticalCenter: parent.verticalCenter
                    onPaint: {
                        var c = getContext("2d")
                        c.reset()
                        c.fillStyle = "" + root.accentText
                        c.beginPath(); c.moveTo(0, 0); c.lineTo(0, height); c.lineTo(width, height / 2)
                        c.closePath(); c.fill()
                    }
                }
                Text {
                    textFormat: Text.PlainText
                    anchors.verticalCenter: parent.verticalCenter
                    text: "PLAY"; color: root.accentText
                    font.family: root.mono; font.pixelSize: 8
                    font.bold: true; font.letterSpacing: 2
                }
            }
        }
    }
    component VideoThumb: Item {
        property string url: ""
        property bool lit: false
        width: 88; height: 50
        Art { anchors.fill: parent; radius: 6; url: parent.url }
        PlayBadge { lit: parent.lit }
    }

    // Old-school LED dot-matrix progress. Dots sit in a fixed grid and brighten
    // (faded → bright) in a bottom-up, left-to-right "stacking" order as pct
    // rises, each dot is a precise fraction of the whole.
    component DotMatrix: Item {
        id: dm
        property real pct: 0
        property int rows: 4
        property real dot: 3
        property real gap: 2
        property int maxCols: 0
        property bool pulse: true
        property color onColor: root.accent
        // "Finishing" twinkle (chosen in the shimmer lab): the bar sits at
        // 100% while the final steps run (merge, decrypt, FLAC extract,
        // tagging), so instead of freezing, every lit dot breathes on its
        // own pseudo-random offset of the shared shimmerPhase clock. Only
        // download surfaces set this; the player's scrub track is playback
        // position, not work, and must stay static at 100%.
        property bool finishing: false
        readonly property int cols: {
            var c = Math.max(1, Math.floor((width + gap) / (dot + gap)))
            return (maxCols > 0 && c > maxCols) ? maxCols : c
        }
        readonly property int total: rows * cols
        readonly property int litCount: Math.round(Math.max(0, Math.min(100, pct)) / 100 * total)
        implicitHeight: rows * dot + (rows - 1) * gap
        Repeater {
            model: dm.total
            delegate: Rectangle {
                required property int index
                readonly property int col: index % dm.cols
                readonly property int rowTop: Math.floor(index / dm.cols)
                // column-major, bottom-up: fill one column from the bottom to the
                // top, then start the next column, like rising bars.
                readonly property int fillIndex: col * dm.rows + (dm.rows - 1 - rowTop)
                readonly property bool lit: fillIndex < dm.litCount
                // the single next block pulses while a download is in progress
                readonly property bool pulsing: dm.pulse && fillIndex === dm.litCount && dm.litCount < dm.total
                // Per-dot pseudo-random phase offset for the finishing twinkle
                // (fract(sin(i)*const), the classic shader hash: cheap, stable,
                // uniform enough for eyes).
                readonly property real twinkleR: { var r = Math.sin(index * 12.9898) * 43758.5453; return r - Math.floor(r) }
                x: col * (dm.dot + dm.gap)
                y: rowTop * (dm.dot + dm.gap)
                width: dm.dot; height: dm.dot; radius: 0   // sharp LED cells
                color: dm.onColor
                // Breathe off the shared 20 Hz clock (root.ledPulse) rather than a
                // per-frame animation, so a running download doesn't repaint the
                // whole window every vsync. See root.ledPulse.
                opacity: (dm.finishing && lit)
                       ? 0.62 + 0.38 * (0.5 + 0.5 * Math.cos(2 * Math.PI * (root.shimmerPhase * 2 + twinkleR)))
                       : pulsing ? root.ledPulse : (lit ? 1.0 : 0.16)
            }
        }
    }

    // Dark-themed dropdown (shared by sort + quality selectors).
    component StyledCombo: ComboBox {
        id: sc
        implicitHeight: 42
        background: Rectangle { radius: 8; color: root.surface2; border.color: sc.popup.visible ? root.accent : root.outline }
        contentItem: Text { textFormat: Text.PlainText; text: sc.displayText; color: root.textHi; font.pixelSize: 14; leftPadding: 14; rightPadding: 28; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        indicator: ExpandChevron {
            x: sc.width - 26; y: (sc.height - 18) / 2; tile: 18; glyph: 13
            showTile: false; closedAngle: -90; openAngle: 0
            stroke: root.accent; open: sc.popup.visible
        }
        delegate: ItemDelegate {
            width: sc.width
            contentItem: Text { textFormat: Text.PlainText; text: modelData; color: root.textHi; font.pixelSize: 14; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { color: highlighted ? root.surface3 : root.surface2 }
            highlighted: sc.highlightedIndex === index
        }
        popup: Popup {
            y: sc.height + 4; width: sc.width; padding: 4
            implicitHeight: contentItem.implicitHeight + 8
            background: Rectangle { radius: 8; color: root.surface2; border.color: root.outline }
            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: sc.popup.visible ? sc.delegateModel : null; ScrollBar.vertical: ScrollBar {} }
        }
    }

    // Small square checkbox.
    component Check: Rectangle {
        property bool checked: false
        signal toggled()
        width: 18; height: 18; radius: 4
        color: checked ? root.accent : "transparent"
        border.color: checked ? root.accent : root.outline; border.width: 2
        Ico { anchors.centerIn: parent; visible: parent.checked; name: "check"; color: root.accentText; size: 12; bold: 8 }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: parent.toggled() }
    }

    // Album row + inline expand. Local expanded state so it works in both the
    // search results and inside an artist page.
    component AlbumBlock: Column {
        id: ab
        property string albumId: ""
        property string title: ""
        property string artistName: ""
        property string artistId: ""
        property string art: ""
        property string year: ""
        property string releaseDate: ""
        property int trackCount: 0
        property string quality: ""
        property int popularity: 0
        // Expand state is held globally (keyed by album id) so it survives
        // ListView delegate recycling in the virtualised My Tidal lists.
        readonly property bool expanded: root.expandedAlbums[albumId] === true
        property var sel: ({})
        readonly property var trackList: root.trackCache[albumId] || []
        // Tier split once the tracks are known (expand fetches them), flips
        // the quality badge to MIXED when the album spans tiers.
        readonly property var qualMix: root.qualMixList(trackList)
        readonly property int selCount: Object.keys(sel).length
        readonly property bool allSelected: trackList.length > 0 && selCount === trackList.length
        spacing: 6

        function toggle() {
            var e = Object.assign({}, root.expandedAlbums)
            if (e[albumId]) { delete e[albumId] }
            else { e[albumId] = true; if (!root.trackCache[albumId]) waves.loadAlbumTracks(albumId) }
            root.expandedAlbums = e
        }
        function setSel(tid, v) { var s = Object.assign({}, sel); if (v) s[tid] = true; else delete s[tid]; sel = s }
        function toggleAll() {
            if (allSelected) { sel = ({}) }
            else { var s = {}; for (var i = 0; i < trackList.length; ++i) s[trackList[i].id] = true; sel = s }
        }
        function downloadSelected() { for (var k in sel) waves.downloadTrack(k) }

        // --- Row ---
        Rectangle {
            width: parent.width
            height: 64
            radius: 10
            color: rowMa.containsMouse ? root.surface2 : root.surface
            border.color: expanded ? root.outline : root.border1
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 14; spacing: 12
                Text {
                    text: "›"; rotation: expanded ? 90 : 0
                    color: expanded ? root.accent : root.textDim; font.pixelSize: 16
                    Layout.preferredWidth: 12; horizontalAlignment: Text.AlignHCenter
                    Behavior on rotation { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                }
                Art { width: 46; height: 46; url: art }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 2
                    Text {
                        textFormat: Text.PlainText; text: title
                        color: abRowTitleMa.containsMouse ? "#ffffff" : root.textHi
                        font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true
                        // Title -> the album's dedicated page (row click still expands)
                        MouseArea {
                            id: abRowTitleMa
                            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                            width: Math.min(parent.width, parent.implicitWidth)
                            enabled: !root.onAlbumPage(albumId)
                            hoverEnabled: enabled
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: root.openAlbumPage(albumId, "")
                        }
                    }
                    ArtistLinks {
                        Layout.fillWidth: true
                        artists: root.artistsById[albumId] || []
                        suffix: (releaseDate !== "" ? releaseDate : year) + (trackCount > 0 ? " · " + trackCount + " trks" : "")
                    }
                }
                PopMeter { value: popularity; Layout.alignment: Qt.AlignVCenter }
                // Badge slot like the track rows: sized to the widest real badge,
                // badge right-aligned, so the popularity column doesn't stagger
                // with badge width. A wider MIXED tag gets its natural width.
                Item {
                    QualTag { id: abQtMetric; visible: false; q: "LOSSLESS" }
                    Layout.preferredWidth: Math.max(abQtMetric.implicitWidth, abQt.implicitWidth)
                    Layout.preferredHeight: 22; Layout.alignment: Qt.AlignVCenter
                    QualTag { id: abQt; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; q: quality; mix: qualMix }
                }
                DownloadButton { Layout.alignment: Qt.AlignVCenter; mediaId: albumId; collectionCheck: true; label: "Download album"; onTap: function(){ waves.downloadAlbum(albumId) } }
            }
            MouseArea { id: rowMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; z: -1; onClicked: toggle() }
        }

        // --- Expanded rich panel ---
        Rectangle {
            width: parent.width
            visible: expanded
            height: visible ? expandedCol.height + 24 : 0
            color: root.surface0
            border.color: root.border1
            radius: 10
            Column {
                id: expandedCol
                x: 16; y: 12; width: parent.width - 32; spacing: 12
                Row {
                    width: parent.width; spacing: 16
                    Art { width: 116; height: 116; url: art }
                    Column {
                        width: parent.width - 132 - abPanelMeta.width - 16; spacing: 6
                        Text { text: "ALBUM"; color: root.textDim; font.pixelSize: 11 }
                        Text {
                            textFormat: Text.PlainText; text: title
                            color: abPanelTitleMa.containsMouse ? "#ffffff" : root.textHi
                            font.pixelSize: 21; font.bold: true; width: parent.width; elide: Text.ElideRight
                            MouseArea {
                                id: abPanelTitleMa
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: Math.min(parent.width, parent.implicitWidth)
                                enabled: !root.onAlbumPage(albumId)
                                hoverEnabled: enabled
                                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: root.openAlbumPage(albumId, "")
                            }
                        }
                        Text { textFormat: Text.PlainText; text: artistName + (releaseDate !== "" ? "  ·  " + releaseDate : (year !== "" ? "  ·  " + year : "")) + (trackCount > 0 ? "  ·  " + trackCount + " tracks" : ""); color: root.textLo; font.pixelSize: 14 }
                        Row {
                            spacing: 10; topPadding: 6
                            // With the merge preference on the backend runs the
                            // best-of-both scan behind this same button; no
                            // separate action.
                            DownloadButton {
                                mediaId: albumId; label: "Download album"
                                collectionIds: ab.trackList.length > 0 ? ab.trackList.map(function(t){ return t.id }) : []
                                onTap: function(){ waves.downloadAlbum(albumId) }
                            }
                            Text {
                                text: "Copy link"; color: root.textLo; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.copyShareUrl("album", albumId) }
                            }
                        }
                    }
                    // Quality + popularity stacked top-right, out of the action row.
                    Column {
                        id: abPanelMeta
                        spacing: 8; topPadding: 4
                        QualTag { anchors.right: parent.right; q: quality; mix: qualMix }
                        PopMeter { anchors.right: parent.right; value: popularity }
                    }
                }
                Text { visible: !root.trackCache[albumId]; text: "Loading tracks…"; color: root.textLo; font.pixelSize: 13 }
                Column {
                    width: parent.width; spacing: 0
                    // Select-all header
                    RowLayout {
                        visible: ab.trackList.length > 0
                        width: parent.width; height: 40; spacing: 12
                        Check { Layout.alignment: Qt.AlignVCenter; checked: ab.allSelected; onToggled: ab.toggleAll() }
                        Text { text: "Select all"; color: root.textLo; font.pixelSize: 13 }
                        Text { textFormat: Text.PlainText; text: "· " + ab.selCount + " of " + ab.trackList.length + " selected"; color: root.textDim; font.pixelSize: 12 }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            Layout.alignment: Qt.AlignVCenter
                            opacity: ab.selCount > 0 ? 1 : 0.4
                            // Sized like DownloadButton so it matches DOWNLOAD ALBUM above.
                            radius: root.btnRad; color: root.accentCont; border.color: root.accentDim; border.width: 1
                            implicitHeight: dsRow.implicitHeight + root.btnPadV * 2; implicitWidth: dsRow.implicitWidth + root.btnPadH * 2
                            Row { id: dsRow; anchors.centerIn: parent; spacing: 7
                                Ico { name: "arrow-down"; color: root.accent; size: 14; bold: 10; anchors.verticalCenter: parent.verticalCenter }
                                Text { text: "DOWNLOAD SELECTED"; color: root.accent; font.pixelSize: 11; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack; anchors.verticalCenter: parent.verticalCenter }
                            }
                            MouseArea { anchors.fill: parent; enabled: ab.selCount > 0; cursorShape: ab.selCount > 0 ? Qt.PointingHandCursor : Qt.ArrowCursor; onClicked: ab.downloadSelected() }
                        }
                    }
                    // Tracks
                    Repeater {
                        model: ab.trackList
                        delegate: Rectangle {
                            required property var modelData
                            width: parent.width; height: 40; color: "transparent"
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.divider }
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 4; anchors.rightMargin: 4; spacing: 12
                                Check { Layout.alignment: Qt.AlignVCenter; checked: ab.sel[modelData.id] === true; onToggled: ab.setSel(modelData.id, !(ab.sel[modelData.id] === true)) }
                                Text { textFormat: Text.PlainText; text: modelData.num; color: root.textDim; font.family: root.mono; font.pixelSize: 15; font.bold: true; Layout.preferredWidth: 16; Layout.leftMargin: -4; horizontalAlignment: Text.AlignLeft }
                                TrackPreview { kind: "track"; pid: modelData.id; Layout.alignment: Qt.AlignVCenter }
                                Text { textFormat: Text.PlainText; text: modelData.title; color: root.textHi; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                                PopMeter { value: modelData.popularity; showNum: false }
                                Text { textFormat: Text.PlainText; text: modelData.duration; color: root.textLo; font.family: root.mono; font.pixelSize: 12; Layout.preferredWidth: 42 }
                                DownIcon { mediaId: modelData.id; onTap: function(){ waves.downloadTrack(modelData.id) } }
                            }
                        }
                    }
                }
            }
        }
    }

    // A line of comma-separated artist names, each individually clickable.
    component ArtistLinks: Row {
        id: al
        property var artists: []
        property string suffix: ""
        property string albumId: ""      // set -> the suffix (album name) links to the album page
        property int px: 12
        clip: true
        Repeater {
            model: al.artists
            delegate: Row {
                required property var modelData
                required property int index
                Text {
                    id: alName
                    readonly property bool linkable: modelData.id && modelData.id !== "" && !root.onArtistPage(modelData.id)
                    // Artist names stay accent-green even when inert (e.g. on
                    // their own page), only the affordances go away.
                    text: modelData.name; textFormat: Text.PlainText; color: root.accent; font.pixelSize: al.px
                    font.underline: alMa.containsMouse && linkable
                    MouseArea {
                        id: alMa
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: alName.linkable ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: if (alName.linkable) waves.loadArtist(modelData.id)
                    }
                }
                Text { visible: index < al.artists.length - 1; text: ", "; color: root.textLo; font.pixelSize: al.px }
            }
        }
        Text { visible: al.suffix !== ""; textFormat: Text.PlainText; text: " · "; color: root.textLo; font.pixelSize: al.px }
        Text {
            id: alSuffix
            readonly property bool linkable: al.albumId !== "" && !root.onAlbumPage(al.albumId)
            visible: al.suffix !== ""; textFormat: Text.PlainText; text: al.suffix
            color: alSfMa.containsMouse && linkable ? "#ffffff" : root.textLo; font.pixelSize: al.px
            font.underline: alSfMa.containsMouse && linkable
            MouseArea {
                id: alSfMa
                anchors.fill: parent
                enabled: alSuffix.linkable; hoverEnabled: enabled
                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: root.openAlbumPage(al.albumId, "")
            }
        }
    }

    // Full track row (search results + artist top tracks)
    component TrackRow: Rectangle {
        id: trow
        property string tId: ""
        property string kind: "track"   // "video" rows play/download as videos
        property string title: ""
        property string artistName: ""
        property string artistId: ""
        property string album: ""
        property string art: ""
        property string year: ""
        property string date: ""
        property string duration: ""
        property string quality: ""
        property int popularity: 0
        property bool hi: false          // "you came here for this track" marker
        property int num: 0              // track # (album position, or playlist order); 0 hides
        property string albumId: ""      // set -> the title links to the album page
        height: 62
        color: "transparent"
        // Framed card row:
        // same surface/border/hover language as the album section, and the
        // same DownloadButton as everywhere else, labeled for the track.
        Rectangle {
            anchors.fill: parent; anchors.topMargin: 3; anchors.bottomMargin: 3
            radius: 10
            color: trowMa.containsMouse ? root.surface2 : root.surface
            border.color: root.border1
            MouseArea { id: trowMa; anchors.fill: parent; hoverEnabled: true; acceptedButtons: Qt.NoButton }
            // Highlight = the "Fade" treatment: a green tint strongest at the
            // left, gone before the metadata columns so numbers and badges sit
            // on clean background.
            Rectangle {
                visible: hi; anchors.fill: parent; radius: 10
                gradient: Gradient {
                    orientation: Gradient.Horizontal
                    GradientStop { position: 0.0;  color: "#e006210f" }
                    GradientStop { position: 0.45; color: "#7006210f" }
                    GradientStop { position: 1.0;  color: "#0006210f" }
                }
            }
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12; spacing: 12
                Text {
                    textFormat: Text.PlainText
                    visible: num > 0
                    text: num + "."
                    // textLo, not textDim: the number must stay readable over the
                    // highlight fade too (brightest on the highlighted row itself).
                    color: hi ? root.textHi : root.textLo
                    font.family: root.mono; font.pixelSize: 12
                    horizontalAlignment: Text.AlignRight
                    Layout.preferredWidth: 24; Layout.alignment: Qt.AlignVCenter
                }
                Item {
                    // Video rows get a 16:9 thumb, the shape alone says "video".
                    Layout.preferredWidth: trow.kind === "video" ? 78 : 44
                    Layout.preferredHeight: 44; Layout.alignment: Qt.AlignVCenter
                    PreviewArt { visible: trow.kind !== "video"; anchors.fill: parent; kind: "track"; pid: tId; url: art }
                    Art { visible: trow.kind === "video"; anchors.fill: parent; url: art }
                    PlayBadge { visible: trow.kind === "video"; lit: thumbMa.containsMouse }
                    MouseArea {
                        id: thumbMa
                        anchors.fill: parent
                        enabled: trow.kind === "video"; hoverEnabled: enabled
                        cursorShape: Qt.PointingHandCursor
                        onClicked: root.openVideo(trow.tId, trow.title, trow.artistName)
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 1
                    Text {
                        id: trTitle
                        textFormat: Text.PlainText; text: title
                        color: trTitleMa.containsMouse ? "#ffffff" : root.textHi
                        font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true
                        // Title -> the track's album page (highlighting this track);
                        // for a video row it opens the in-app video player instead.
                        MouseArea {
                            id: trTitleMa
                            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                            width: Math.min(parent.width, parent.implicitWidth)
                            enabled: trow.kind === "video" || (albumId !== "" && !root.onAlbumPage(albumId))
                            hoverEnabled: enabled
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: trow.kind === "video" ? root.openVideo(trow.tId, trow.title, trow.artistName)
                                                             : root.openAlbumPage(albumId, tId)
                        }
                    }
                    ArtistLinks { Layout.fillWidth: true; artists: root.artistsById[tId] || []; suffix: album; albumId: trow.albumId }
                }
                PopMeter { value: popularity; Layout.alignment: Qt.AlignVCenter }
                Text { textFormat: Text.PlainText; text: date !== "" ? date : year; color: root.textLo; font.family: root.mono; font.pixelSize: 12; Layout.preferredWidth: 84; Layout.alignment: Qt.AlignVCenter }
                Text { textFormat: Text.PlainText; text: duration; color: root.textLo; font.family: root.mono; font.pixelSize: 12; Layout.preferredWidth: 40; Layout.alignment: Qt.AlignVCenter }
                // Fixed-width slot, badge anchored right so it hugs the button,
                // short badges (HI-RES) leave the slack on their left, not as a
                // hole between badge and button. Sized to the widest real badge
                // (LOSSLESS 16/44.1) via a hidden metric, no padded guess.
                Item {
                    QualTag { id: qtMetric; visible: false; q: "LOSSLESS" }
                    Layout.preferredWidth: qtMetric.implicitWidth; Layout.preferredHeight: 22; Layout.alignment: Qt.AlignVCenter
                    QualTag { anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; q: quality }
                }
                DownloadButton {
                    Layout.alignment: Qt.AlignVCenter; mediaId: tId; ownedCheck: true
                    label: trow.kind === "video" ? "Download video" : "Download track"
                    onTap: function(){ trow.kind === "video" ? waves.downloadVideo(tId) : waves.downloadTrack(tId) }
                }
            }
        }
    }

    // A square art-forward card for the Browse shelves (albums, playlists,
    // mixes, artists, any editorial item). The download icon consumes its
    // clicks; the card-wide MouseArea (z:-1) beneath opens the artist page
    // where the card can name one.
    // A horizontal shelf (ListView) otherwise swallows vertical wheel/trackpad
    // scroll to move itself sideways, trapping the enclosing page: on a landing
    // or genre page that is nothing but shelves, the user can barely scroll
    // down. Dropping one of these on a shelf redirects vertical-dominant wheel
    // to the page while leaving horizontal-dominant wheel for the shelf's own
    // flick, so the row still scrolls sideways with a trackpad swipe.
    component ShelfWheelRedirect: WheelHandler {
        property Flickable pane   // the vertical page to drive
        property var shelf: parent  // the horizontal shelf this rides on (a ListView)
        // Both axes are driven explicitly (and the event always accepted) so
        // behaviour never depends on wheel fall-through between the handler and
        // the Flickable: vertical wheel scrolls the page, horizontal wheel (a
        // trackpad sideways swipe) scrolls the shelf itself.
        onWheel: function(ev) {
            if (Math.abs(ev.angleDelta.y) >= Math.abs(ev.angleDelta.x)) {
                if (pane) {
                    var maxY = Math.max(0, pane.contentHeight - pane.height)
                    pane.contentY = Math.max(0, Math.min(maxY, pane.contentY - ev.angleDelta.y))
                }
            } else if (shelf) {
                var maxX = Math.max(0, shelf.contentWidth - shelf.width)
                shelf.contentX = Math.max(0, Math.min(maxX, shelf.contentX - ev.angleDelta.x))
            }
            ev.accepted = true
        }
    }

    component BrowseCard: Rectangle {
        id: bc
        property var card: ({})
        readonly property string kind: card.kind || ""
        readonly property string subtitle: root.cardSubtitle(card)
        width: 156; height: 236
        radius: 12; color: root.surface; border.color: root.border1
        // Only the artwork and the title open the card's page, the caption
        // handles its own artist link, and dead space stays inert.
        readonly property bool openable: bc.kind !== "track" || !!bc.card.artist_id
        // Everything the art view previews, previewable here too (videos have
        // no audio-preview path).
        readonly property bool previewable: bc.kind !== "video" && !!bc.card.id
        readonly property string pvSt: previewable ? root.pvSt(bc.kind, bc.card.id || "") : ""
        readonly property string dlSt: root.dlSt(bc.card.id || "")
        readonly property real dlPct: root.dlPct(bc.card.id || "")
        Column {
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.margins: 8; spacing: 5
            Art {
                width: parent.width; height: parent.width; url: bc.card.art || ""
                MouseArea {
                    anchors.fill: parent
                    cursorShape: bc.openable ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.openBrowseCard(bc.card)
                }
            }
            Text {
                id: bcTitle
                textFormat: Text.PlainText
                text: bc.card.title || ""
                color: root.textHi; font.pixelSize: 12; font.bold: true
                // Height hugs the actual line count, a one-line title no
                // longer leaves a blank second line above the caption.
                width: parent.width
                elide: Text.ElideRight; maximumLineCount: 2; wrapMode: Text.Wrap
                font.underline: bcTitleMa.containsMouse && bc.openable
                MouseArea {
                    id: bcTitleMa
                    anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                    width: Math.min(parent.implicitWidth, parent.width)
                    hoverEnabled: true
                    cursorShape: bc.openable ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.openBrowseCard(bc.card)
                }
            }
            // Full-width caption line: the album's artist link + date (or
            // "N tracks") gets the whole row now that the download control
            // lives on its own line below.
            CardCaption { card: bc.card; px: 11; width: parent.width }
        }
        // Control line pinned to the bottom edge so shelf rows align:
        // ▶ PREVIEW on the left, bare-text download on the right.
        Item {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            anchors.margins: 8; height: 16
            // ---- preview: ▶ PREVIEW -> ■ + mono elapsed while active ----
            Item {
                visible: bc.previewable
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                width: bcPvRow.implicitWidth; height: 16
                Row {
                    id: bcPvRow
                    anchors.verticalCenter: parent.verticalCenter; spacing: 4
                    Ico {
                        // The click pauses (togglePreview), it doesn't stop,
                        // so the active glyph is a pause, matching the art view.
                        visible: bc.pvSt !== "loading"
                        name: bc.pvSt === "playing" ? "pause" : "play"
                        color: bc.pvSt === "error" ? root.red : root.accent
                        size: 10
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        textFormat: Text.PlainText
                        text: bc.pvSt === "" ? "PREVIEW"
                            : bc.pvSt === "loading" ? "[buffering]"
                            : bc.pvSt === "error" ? "RETRY"
                            : root.fmtMs(root.previewPosition)
                        color: bc.pvSt === "error" ? root.red : root.accent
                        font.family: bc.pvSt === "playing" || bc.pvSt === "paused" || bc.pvSt === "loading" ? root.mono : root.uiFont
                        font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack
                        anchors.verticalCenter: parent.verticalCenter
                        // Breathe while buffering (same cadence as the dot
                        // matrix's pulsing next-block). The animation drives a
                        // side property so opacity snaps back to 1 the moment
                        // loading ends, instead of freezing mid-breath.
                        property real breathe: 1
                        opacity: bc.pvSt === "loading" ? breathe : 1
                        SequentialAnimation on breathe {
                            running: bc.pvSt === "loading"; loops: Animation.Infinite
                            NumberAnimation { from: 1.0; to: 0.3; duration: 520; easing.type: Easing.InOutSine }
                            NumberAnimation { from: 0.3; to: 1.0; duration: 520; easing.type: Easing.InOutSine }
                        }
                    }
                    // "· STOP" rides after the elapsed counter while a preview
                    // is live; quiet at rest, red on hover, resets to idle.
                    Text {
                        textFormat: Text.PlainText
                        visible: bc.pvSt === "playing" || bc.pvSt === "paused"
                        text: "· STOP"
                        color: bcStopMa.containsMouse ? root.red : root.textDim
                        font.family: root.uiFont; font.pixelSize: 9; font.bold: true; font.letterSpacing: root.btnTrack
                        anchors.verticalCenter: parent.verticalCenter
                        MouseArea {
                            id: bcStopMa
                            anchors.fill: parent; anchors.margins: -3
                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: root.stopPreview()
                        }
                    }
                }
                MouseArea {
                    anchors.fill: parent; z: -1; cursorShape: Qt.PointingHandCursor
                    onClicked: root.togglePreview(bc.kind, bc.card.id || "", 0)
                }
            }
            // ---- download: DOWNLOAD -> dot bar + fixed-width % -> ✓ DONE ----
            Item {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                width: Math.max(bcDlIdle.implicitWidth, bcDlRun.implicitWidth); height: 16
                Text {
                    id: bcDlIdle
                    textFormat: Text.PlainText
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    visible: bc.dlSt !== "running"
                    text: bc.dlSt === "done" ? "DONE" : bc.dlSt === "failed" ? "RETRY" : "DOWNLOAD"
                    color: bc.dlSt === "done" ? root.green : bc.dlSt === "failed" ? root.red : root.accent
                    font.family: root.uiFont; font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack
                }
                Row {
                    id: bcDlRun
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    visible: bc.dlSt === "running"; spacing: 5
                    DotMatrix { width: 34; rows: 2; dot: 4; gap: 2; pct: Math.max(0, bc.dlPct); finishing: bc.dlPct >= 99.9; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        textFormat: Text.PlainText
                        text: bc.dlPct >= 0 ? Math.round(bc.dlPct) + "%" : "…"
                        // Reserve the widest label ("100%") so the digit count
                        // changing never shifts the dot bar.
                        width: bcDlMetric.implicitWidth; horizontalAlignment: Text.AlignRight
                        color: root.accent; font.family: root.mono; font.pixelSize: 9; font.bold: true
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text { id: bcDlMetric; visible: false; textFormat: Text.PlainText; text: "100%"; font.family: root.mono; font.pixelSize: 9; font.bold: true }
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: { if (bc.dlSt === "running" || bc.dlSt === "done") return; root.browseCardDownload(bc.card) }
                }
            }
        }
    }

    // Art-first Browse card (the streaming-service look): the artwork IS the
    // card, no frame, quiet caption beneath, download surfacing only on hover
    // (or while it carries live download state). hero:true renders the big
    // top-shelf variant with the caption overlaid on a bottom scrim.
    component ArtCard: Item {
        id: ac
        property var card: ({})
        property bool hero: false
        readonly property string kind: card.kind || ""
        readonly property real artSize: hero ? 280 : 200
        width: artSize
        height: hero ? artSize : artSize + 46
        readonly property bool openable: ac.kind !== "track" || !!ac.card.artist_id
        Art {
            id: acArt
            width: ac.artSize; height: ac.artSize
            radius: 12
            url: ac.card.art || ""
            // Declared first so the hover controls' own MouseAreas sit above it:
            // the artwork opens the page, the buttons keep their clicks.
            MouseArea {
                anchors.fill: parent
                cursorShape: ac.openable ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: root.openBrowseCard(ac.card)
            }
            // hero caption rides ON the art over a bottom scrim (children are
            // clipped to the rounded rect, so the scrim keeps the corners)
            Rectangle {
                // scrim yields to the hover controls so they never overlap the caption
                visible: ac.hero && opacity > 0
                opacity: acArt.controlsOn ? 0 : 1
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutQuad } }
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                height: 96
                gradient: Gradient {
                    GradientStop { position: 0; color: "transparent" }
                    GradientStop { position: 1; color: "#e60b0d10" }
                }
            }
            Column {
                visible: ac.hero && opacity > 0
                opacity: acArt.controlsOn ? 0 : 1
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutQuad } }
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                anchors.margins: 14; spacing: 3
                Text {
                    textFormat: Text.PlainText
                    text: ac.card.title || ""
                    color: "#f2f4f7"; font.pixelSize: 16; font.bold: true
                    width: parent.width; elide: Text.ElideRight
                }
                CardCaption { card: ac.card; px: 12; metaColor: "#f2f4f7"; width: parent.width }
            }
            // Hover controls. Collections (album / playlist / mix) get the
            // full stacked pair on the art, Preview (a random track for
            // playlists/mixes) over Download; other kinds keep the corner icon.
            readonly property bool collection: ac.kind === "album" || ac.kind === "playlist" || ac.kind === "mix"
            readonly property string kindLabel: ac.kind === "album" ? "album" : ac.kind === "playlist" ? "playlist" : "mix"
            readonly property bool controlsOn: collection
                                               && (acMa.containsMouse || acWrapHover.hovered
                                                   || root.dlSt(ac.card.id || "") !== "" || root.pvSt(ac.kind, ac.card.id || "") !== "")
            HoverHandler { id: acWrapHover }
            Column {
                // Fade in/out with hover instead of popping (visible gates the
                // MouseAreas so a faded-out strip can't swallow clicks).
                opacity: acArt.controlsOn ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutQuad } }
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                anchors.margins: 10; spacing: 6
                readonly property string acPvSt: root.pvSt(ac.kind, ac.card.id || "")
                readonly property string acDlSt: root.dlSt(ac.card.id || "")
                // Live preview: the full scrubber bar (same one as everywhere else)
                PreviewBar {
                    visible: parent.acPvSt !== ""
                    width: parent.width; height: implicitHeight
                    kind: ac.kind; pid: ac.card.id || ""
                    label: "Preview " + acArt.kindLabel
                    // opaque backing so the controls read over any artwork
                    Rectangle { anchors.fill: parent; z: -1; radius: root.btnRad; color: "#d90d0f12" }
                }
                // Live download: the full dot-matrix progress bar / done / retry
                DownloadButton {
                    id: acDl
                    visible: parent.acDlSt !== ""
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: st === "running" ? parent.width : implicitWidth
                    mediaId: ac.card.id || ""
                    collectionCheck: ac.kind === "album" || ac.kind === "playlist" || ac.kind === "mix"
                    label: "Download " + acArt.kindLabel
                    onTap: function() { root.browseCardDownload(ac.card) }
                    Rectangle { anchors.fill: parent; z: -1; radius: root.btnRad; color: "#d90d0f12" }
                }
                // Idle: the slim strip, ▶ PREVIEW | ⭳ DOWNLOAD in one thin pill
                Rectangle {
                    visible: parent.acPvSt === "" && parent.acDlSt === ""
                    anchors.horizontalCenter: parent.horizontalCenter
                    width: acStripRow.implicitWidth; height: 30; radius: root.btnRad
                    color: "#d90d0f12"; border.width: 1; border.color: root.accentDim
                    Row {
                        id: acStripRow
                        anchors.verticalCenter: parent.verticalCenter
                        Item {
                            implicitWidth: acStripPv.implicitWidth + 20; implicitHeight: 30
                            Row {
                                id: acStripPv
                                anchors.centerIn: parent; spacing: 6
                                Ico { name: "play"; color: root.accent; size: 11; anchors.verticalCenter: parent.verticalCenter }
                                Text { textFormat: Text.PlainText; text: "PREVIEW"; color: root.accent; font.family: root.uiFont; font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack; anchors.verticalCenter: parent.verticalCenter }
                            }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: root.togglePreview(ac.kind, ac.card.id || "", 0)
                            }
                        }
                        Rectangle { width: 1; height: 30; color: root.accentDim; anchors.verticalCenter: parent.verticalCenter }
                        Item {
                            implicitWidth: acStripDl.implicitWidth + 20; implicitHeight: 30
                            Row {
                                id: acStripDl
                                anchors.centerIn: parent; spacing: 6
                                Ico { name: "arrow-down"; color: root.accent; size: 12; bold: 10; anchors.verticalCenter: parent.verticalCenter }
                                Text { textFormat: Text.PlainText; text: "DOWNLOAD"; color: root.accent; font.family: root.uiFont; font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack; anchors.verticalCenter: parent.verticalCenter }
                            }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: root.browseCardDownload(ac.card)
                            }
                        }
                    }
                }
            }
            Rectangle {
                visible: !acArt.collection && opacity > 0
                opacity: (acMa.containsMouse || acWrapHover.hovered || root.dlSt(ac.card.id || "") !== "") ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutQuad } }
                anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 10
                width: 38; height: 36; radius: root.btnRad
                color: "#d90d0f12"
                DownIcon {
                    anchors.centerIn: parent
                    mediaId: ac.card.id || ""
                    collectionCheck: ac.kind === "album" || ac.kind === "playlist" || ac.kind === "mix"
                    onTap: function() { root.browseCardDownload(ac.card) }
                }
            }
        }
        Column {
            visible: !ac.hero
            anchors.left: parent.left; anchors.right: parent.right
            anchors.top: acArt.bottom; anchors.topMargin: 8
            spacing: 2
            Text {
                id: acTitle
                textFormat: Text.PlainText
                text: ac.card.title || ""
                color: root.textHi; font.pixelSize: 13; font.bold: true
                width: parent.width; elide: Text.ElideRight
                horizontalAlignment: ac.kind === "artist" ? Text.AlignHCenter : Text.AlignLeft
                font.underline: acTitleMa.containsMouse && ac.openable
                MouseArea {
                    id: acTitleMa
                    anchors.top: parent.top; anchors.bottom: parent.bottom
                    anchors.horizontalCenter: ac.kind === "artist" ? parent.horizontalCenter : undefined
                    anchors.left: ac.kind === "artist" ? undefined : parent.left
                    width: Math.min(parent.implicitWidth, parent.width)
                    hoverEnabled: true
                    cursorShape: ac.openable ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: root.openBrowseCard(ac.card)
                }
            }
            CardCaption { card: ac.card; px: 11; center: ac.kind === "artist"; width: parent.width }
        }
        // Hover tracking only (feeds controlsOn), clicks belong to the art,
        // the title, and the caption's artist links.
        MouseArea {
            id: acMa
            anchors.fill: parent; z: -1; hoverEnabled: true
            acceptedButtons: Qt.NoButton
        }
    }

    // One quadrant of a tile mosaic: two stacked covers that crossfade when
    // src changes (the first assignment fills in without a fade). Keeps the
    // swap gentle, the tile never blinks to the background between covers.
    component MosaicCell: Item {
        id: mc
        property string src: ""
        property bool _showingA: true
        Image {
            id: mcA
            anchors.fill: parent; fillMode: Image.PreserveAspectCrop
            asynchronous: true; cache: true
            sourceSize.width: 200; sourceSize.height: 200
            opacity: 1
        }
        Image {
            id: mcB
            anchors.fill: parent; fillMode: Image.PreserveAspectCrop
            asynchronous: true; cache: true
            sourceSize.width: 200; sourceSize.height: 200
            opacity: 0
        }
        onSrcChanged: {
            var front = _showingA ? mcA : mcB
            var back = _showingA ? mcB : mcA
            if (("" + front.source) === "") { front.source = src; return }
            if (("" + front.source) === src) return
            back.source = src
            mcFade.stop()
            mcFadeIn.target = back; mcFadeOut.target = front
            mcFade.start()
            _showingA = !_showingA
        }
        ParallelAnimation {
            id: mcFade
            NumberAnimation { id: mcFadeIn; property: "opacity"; to: 1; duration: 900; easing.type: Easing.InOutQuad }
            NumberAnimation { id: mcFadeOut; property: "opacity"; to: 0; duration: 900; easing.type: Easing.InOutQuad }
        }
    }

    // Tile for genre / mood / decade entries in the art-first layout. The
    // artwork is a mosaic of real covers sampled from that page (drawn from
    // different rows, Top Artists, New/Classic Albums, Essentials…, see
    // backend _page_art_sample), streamed in after the landing loads and
    // cached for a week. The sample holds up to 12 covers: four show at once
    // and the tile slowly rotates one quadrant at a time through the rest,
    // each tile on its own beat. Until (or unless) covers arrive, the tile
    // falls back to the tone panel: wave glyphs from the WaveMark, or a big
    // era numeral for "1950s"-style titles.
    component BrowseTile: Rectangle {
        id: bt
        property string title: ""
        property string path: ""
        property int idx: 0
        readonly property var tones: [
            [root.accentCont, root.accentDim, root.accentContTx],
            [root.goldCont, root.goldDim, root.goldContTx],
            ["#0a2126", root.cyanDim, "#9fdbe6"],
            [root.redCont, "#8a3a34", "#ffb3ad"],
            [root.greenCont, root.greenDim, root.greenContTx],
            [root.surface3, root.outline, root.textHi],
        ]
        readonly property var tone: tones[idx % 6]
        readonly property string era: /^\d{4}s$/.test(title) ? "'" + title.substring(2) : ""
        readonly property var arts: root.browseTileArt[path] || []
        // 4+ covers -> 2x2 mosaic; 2-3 -> two half tiles; 1 -> full bleed.
        readonly property int artN: arts.length >= 4 ? 4 : arts.length >= 2 ? 2 : arts.length
        // Which pool index each visible cell shows; advanced one cell at a
        // time by the rotation timer once the pool is deeper than the grid.
        property var cells: [0, 1, 2, 3]
        property int _rotPtr: 3
        property int _rotCursor: 0
        onArtsChanged: { cells = [0, 1, 2, 3]; _rotPtr = 3; _rotCursor = 0 }
        // Square, so the 2x2 cells are square too and album covers show whole
        // (a 90x48 cell was cropping every cover to a letterbox slice). Sized to
        // match the album art blocks (ArtCard.artSize), one art scale across
        // the browse cards, the drilled shelves, and these wayfinding tiles.
        width: 200; height: 200; radius: 12
        clip: true
        gradient: Gradient {
            GradientStop { position: 0; color: bt.tone[0] }
            GradientStop { position: 1; color: Qt.darker(bt.tone[0], 1.45) }
        }
        border.width: 1
        border.color: btMa.containsMouse ? tone[2] : tone[1]
        // cover mosaic (clips to the tile's rounded corners)
        Repeater {
            model: bt.artN
            delegate: MosaicCell {
                required property int index
                x: bt.artN === 1 ? 0 : (index % 2) * bt.width / 2
                y: bt.artN <= 2 ? 0 : Math.floor(index / 2) * bt.height / 2
                width: bt.artN === 1 ? bt.width : bt.width / 2
                height: bt.artN <= 2 ? bt.height : bt.height / 2
                src: bt.arts[bt.cells[index]] || bt.arts[index] || ""
            }
        }
        // Slow rotation: every few seconds one cell crossfades to the next
        // unseen cover in the pool, each tile on its own beat (staggered
        // interval), paused while the window is unfocused, so the wall of
        // tiles feels alive without ever churning.
        Timer {
            interval: 5200 + (bt.idx % 7) * 1150
            repeat: true
            running: root.active && !root.browseMoving && bt.visible && bt.arts.length > bt.artN && bt.artN > 0
            onTriggered: {
                var pool = bt.arts.length
                var n = bt.artN
                var next = (bt._rotPtr + 1) % pool
                var guard = 0
                var c = bt.cells.slice()
                // Scan only the VISIBLE cells: stale indices in the unused
                // tail (when artN < 4) would otherwise block covers forever
                // and, once the guard exhausted, let a duplicate on screen.
                var shown = c.slice(0, n)
                while (shown.indexOf(next) !== -1 && guard++ < pool) next = (next + 1) % pool
                if (shown.indexOf(next) !== -1) return  // pool too small to rotate cleanly
                c[bt._rotCursor % n] = next
                bt._rotPtr = next
                bt._rotCursor = bt._rotCursor + 1
                bt.cells = c
            }
        }
        // scrim so the title stays readable over any artwork
        Rectangle {
            visible: bt.artN > 0
            anchors.fill: parent
            gradient: Gradient {
                GradientStop { position: 0; color: "#c20a0c0f" }
                GradientStop { position: 0.55; color: "#590a0c0f" }
                GradientStop { position: 1; color: "#26000000" }
            }
        }
        // no-art fallback: the WaveMark's wave layers, or the era numeral
        Item {
            visible: bt.artN === 0
            anchors.fill: parent; anchors.margins: 3; clip: true
            Repeater {
                model: bt.era !== "" ? [] : [
                    { yf: 0.48, px: 9,  op: 0.26, pat: ".~-~..-~-.~..-~-." },
                    { yf: 0.62, px: 12, op: 0.36, pat: "_.-~-._.,-~-._.-" },
                    { yf: 0.76, px: 16, op: 0.50, pat: "_.-~^~-._.~^'~._" }
                ]
                delegate: Text {
                    required property var modelData
                    textFormat: Text.PlainText
                    x: -8 - (bt.idx % 5) * 9   // stagger so neighbours don't sync
                    y: Math.round(bt.height * modelData.yf)
                    text: modelData.pat.repeat(4)
                    font.family: root.mono; font.pixelSize: modelData.px
                    font.letterSpacing: -1
                    // Foam-bright neutral: tone-on-tone glyphs vanish into the
                    // darker containers, near-white reads evenly on every tile.
                    color: root.textHi; opacity: modelData.op
                }
            }
            Text {
                visible: bt.era !== ""
                textFormat: Text.PlainText
                anchors.right: parent.right; anchors.bottom: parent.bottom
                anchors.rightMargin: 4; anchors.bottomMargin: -10
                text: bt.era
                font.family: root.mono; font.pixelSize: 54; font.bold: true
                color: bt.tone[2]; opacity: 0.20
            }
        }
        Text {
            textFormat: Text.PlainText
            anchors.left: parent.left; anchors.top: parent.top; anchors.right: parent.right
            anchors.margins: 12
            text: bt.title
            color: bt.artN > 0 ? "#f2f4f7" : bt.tone[2]
            font.pixelSize: 17; font.bold: true
            wrapMode: Text.Wrap; maximumLineCount: 2; elide: Text.ElideRight
        }
        MouseArea {
            id: btMa
            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: root.openBrowseLink(bt.path, bt.title)
        }
    }

    // A virtualised My Tidal list: only the rows in (and near) the viewport are
    // instantiated, so a multi-thousand-item category renders instantly and
    // scrolls smoothly. Each instance sets its own `cat`, `model` and `delegate`.
    // It prefetches the next page as it scrolls and shows a footer while loading.
    component LibList: ListView {
        id: lv
        property string cat: ""
        // Inset the list itself (not the delegate's x) so rows sit 22px from
        // each edge, matching the search results' centered column. A vertical
        // ListView manages its delegates' x, so an `x: 22` on the delegate is
        // silently overridden to 0; insetting the view is the reliable way.
        anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22
        visible: root.libraryCategory === cat
        clip: true
        spacing: 8
        cacheBuffer: 800
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        // Re-check on scroll AND on size/content changes: after a page is
        // appended (contentHeight grows) or if the first page doesn't fill the
        // viewport (so it can't scroll), contentY never changes on its own,
        // without these the loader would stall. All three are idempotent thanks
        // to the libHasMore / libLoadingMore guards in libMaybeLoadMore.
        onContentYChanged: root.libMaybeLoadMore(lv, lv.cat)
        onContentHeightChanged: root.libMaybeLoadMore(lv, lv.cat)
        onHeightChanged: root.libMaybeLoadMore(lv, lv.cat)
        footer: Item {
            width: lv.width
            height: (root.libLoadingMore && root.libraryCategory === lv.cat) ? 48 : 20
            Text {
                anchors.centerIn: parent
                visible: root.libLoadingMore && root.libraryCategory === lv.cat
                text: "Loading more…"; color: root.textLo; font.pixelSize: 13
            }
        }
    }

    // ---- Models ---------------------------------------------------------
    ListModel { id: artistsModel }
    ListModel { id: albumsModel }
    ListModel { id: tracksModel }
    ListModel { id: videosModel }
    ListModel { id: playlistsModel }
    ListModel { id: mixesModel }
    ListModel { id: queueModel }
    ListModel { id: artistAlbumsModel }
    ListModel { id: artistEpModel }
    ListModel { id: artistTracksModel }
    ListModel { id: libAlbumsModel }
    ListModel { id: libTracksModel }
    ListModel { id: libArtistsModel }
    ListModel { id: libPlaylistsModel }
    ListModel { id: libMixesModel }
    ListModel { id: libVideosModel }
    property var homeSections: []   // "Home" tab: Browse-shaped, account-scoped shelves

    function appendPlain(model, arr) { if (arr) for (var i = 0; i < arr.length; ++i) model.append(arr[i]) }
    function fill(model, arr) { model.clear(); appendPlain(model, arr) }

    // In-place reconcile for the download queue: update existing rows by qid,
    // append new ones, drop removed, then partition into grouped order. Keeping
    // each row's delegate alive (vs fill()'s clear+rebuild) lets a status change
    // be observed in place, its completion animation fires once, and lets the
    // 5s "move to Completed" slide animate via ListView's move transition.
    function reconcileQueue(arr) {
        var m = queueModel
        arr = arr || []
        var pos = ({})
        for (var i = 0; i < m.count; ++i) pos[m.get(i).qid] = i
        var seen = ({})
        for (var j = 0; j < arr.length; ++j) {
            var it = arr[j]
            seen[it.qid] = true
            if (it.qid in pos) {
                var idx = pos[it.qid]
                var row = m.get(idx)
                if (row.status !== it.status) m.setProperty(idx, "status", it.status)
                // Wall-clock stamp of when the row finished: the root
                // lingerClock promotes on doneAt+5s whether or not the queue
                // drawer (and so this row's delegate) exists.
                if (it.status === "done" && !row.moved && !row.doneAt)
                    m.setProperty(idx, "doneAt", Date.now())
                if (row.progress !== it.progress) m.setProperty(idx, "progress", it.progress)
                if (row.name !== it.name) m.setProperty(idx, "name", it.name)
                if (row.artist !== it.artist) m.setProperty(idx, "artist", it.artist || "")
                if (row.tracks !== it.tracks) m.setProperty(idx, "tracks", it.tracks || 0)
                // Keep a row that has already moved to Completed there; otherwise
                // group by status (queued vs everything-in-progress).
                var grp = row.moved ? "completed" : (it.status === "queued" ? "queued" : "downloading")
                if (row.uiGroup !== grp) m.setProperty(idx, "uiGroup", grp)
            } else {
                m.append({ qid: it.qid, name: it.name, type: it.type, status: it.status,
                           progress: it.progress, media_id: it.media_id, template: it.template,
                           collection: it.collection, artist: it.artist || "", tracks: it.tracks || 0,
                           art: it.art || "",
                           uiGroup: (it.status === "queued" ? "queued" : "downloading"), moved: false,
                           doneAt: (it.status === "done" ? Date.now() : 0), leaving: false })
            }
        }
        for (var k = m.count - 1; k >= 0; --k) if (!seen[m.get(k).qid]) m.remove(k)
        queuePartition()
        updateQueueCounts()
    }

    // Stable-partition the queue model into [completed, downloading, queued],
    // preserving each group's internal order. Only emits move()s when something
    // is out of place (so a steady queue animates nothing).
    function queuePartition() {
        var m = queueModel, w = 0, order = ["completed", "downloading", "queued"]
        for (var g = 0; g < order.length; ++g) {
            for (var i = w; i < m.count; ++i) {
                if (m.get(i).uiGroup === order[g]) { if (i !== w) m.move(i, w, 1); w++ }
            }
        }
    }

    function updateQueueCounts() {
        var m = queueModel, c = 0, d = 0, q = 0, l = 0
        for (var i = 0; i < m.count; ++i) {
            var row = m.get(i)
            var grp = row.uiGroup
            if (grp === "completed") c++
            else if (grp === "downloading") d++
            else q++
            if (row.status === "done" && !row.moved) l++
        }
        root.completedCount = c; root.downloadingCount = d; root.queuedCount = q
        root.lingerCount = l
    }

    // The linger clock: finished rows fold into Completed on a wall clock
    // (doneAt + 5s), whether or not the queue drawer is open. The per-row
    // delegate used to own this timing, so with the drawer closed nothing
    // ever moved, and opening it after a big batch animated every row at
    // once. Rows on screen still get the leaving fade first; with the drawer
    // closed the promotion is silent (there is nothing to animate).
    Timer {
        id: lingerClock
        interval: 1000; repeat: true; running: root.lingerCount > 0
        onTriggered: {
            var m = queueModel, now = Date.now()
            for (var i = m.count - 1; i >= 0; --i) {
                var row = m.get(i)
                if (row.status !== "done" || row.moved) continue
                if (!row.doneAt) { m.setProperty(i, "doneAt", now); continue }
                var age = now - row.doneAt
                if (age < 5000) continue
                if (!queueDrawer.visible) { promoteCompleted(row.qid); continue }
                // Visible: fade the row out (leaving), then move it once the
                // fade has finished (next tick).
                if (!row.leaving) m.setProperty(i, "leaving", true)
                else if (age >= 5500) promoteCompleted(row.qid)
            }
        }
    }

    // Move a finished row into the Completed group (after its 5s linger). It
    // slides to the top of Completed via the ListView move transition.
    function promoteCompleted(qid) {
        var m = queueModel
        for (var i = 0; i < m.count; ++i) {
            if (m.get(i).qid === qid) {
                if (m.get(i).uiGroup === "completed") return
                // Land at the TOP of the Completed group (newest first, oldest
                // at the bottom); Completed is the first group, so that is
                // model index 0. The ListView move transition slides it up.
                m.setProperty(i, "moved", true)
                m.setProperty(i, "uiGroup", "completed")
                m.setProperty(i, "leaving", false)
                if (i !== 0) m.move(i, 0, 1)
                root.compBump += 1
                updateQueueCounts()
                return
            }
        }
    }

    // Media rows carry an `artists` array (clickable per-artist); ListModel
    // doesn't handle nested arrays well, so stash them in a side map by id.
    function appendMedia(model, arr) {
        var m = root.artistsById
        if (arr) for (var i = 0; i < arr.length; ++i) {
            var it = arr[i]
            if (it.artists) m[it.id] = it.artists
            var copy = {}
            for (var k in it) if (k !== "artists") copy[k] = it[k]
            model.append(copy)
        }
        root.artistsById = m
    }
    function fillMedia(model, arr) { model.clear(); appendMedia(model, arr) }

    // ---- My Tidal: model routing + infinite-scroll prefetch ----------------
    function libModelFor(cat) {
        return cat === "albums" ? libAlbumsModel : cat === "tracks" ? libTracksModel
             : cat === "artists" ? libArtistsModel : cat === "playlists" ? libPlaylistsModel
             : cat === "mixes" ? libMixesModel : cat === "videos" ? libVideosModel : null
    }
    // ---- My Tidal sort (per category) --------------------------------------
    // Options adapt to the category; every category shares a "Recently added"
    // default so it matches the backend's default order with no extra fetch.
    function libSortOptions(cat) {
        if (cat === "albums") return [["Recently added", "date"], ["Name", "name"], ["Release date", "release"], ["Artist", "artist"]]
        if (cat === "tracks" || cat === "videos") return [["Recently added", "date"], ["Name", "name"], ["Artist", "artist"]]
        return [["Recently added", "date"], ["Name", "name"]]   // artists, playlists, mixes
    }
    function libSortLabels(cat) { return root.libSortOptions(cat).map(function(o){ return o[0] }) }
    function libSortGet(cat) { var s = root.libSort[cat]; return s ? s : ({ key: "date", asc: false }) }
    function libSortCurrentIndex(cat) {
        var opts = root.libSortOptions(cat), k = root.libSortGet(cat).key
        for (var i = 0; i < opts.length; ++i) if (opts[i][1] === k) return i
        return 0
    }
    function libApplySort(cat, key, asc) {
        // Clone into a NEW object: mutating and reassigning the SAME reference does
        // not fire the var-property change signal, so the direction-arrow binding
        // (libSortGet().asc) never re-evaluated and the arrow appeared stuck.
        var m = {}
        for (var k in root.libSort) m[k] = root.libSort[k]
        m[cat] = { key: key, asc: asc }
        root.libSort = m
        waves.setLibrarySort(cat, key, asc ? "asc" : "desc")
    }
    // From a Home "Recently added" preview shelf, open the full My Tidal tab for
    // that kind, forced to newest-first so it lands on the very items the preview
    // showed and the complete list beneath them. If the tab is already
    // newest-first, just switch to it (loadLib reuses its cache, no re-fetch);
    // otherwise reset the sort, which reloads page one in date order.
    function openLibrarySorted(cat) {
        if (!cat) return
        var g = root.libSortGet(cat)
        if (g.key === "date" && !g.asc) { root.loadLib(cat); return }
        libraryCategory = cat
        libLoadingMore = false
        libHasMore = false
        expandedAlbums = ({})
        libAlbumsModel.clear(); libTracksModel.clear(); libArtistsModel.clear()
        libPlaylistsModel.clear(); libMixesModel.clear(); libVideosModel.clear()
        var m = {}
        for (var k in root.libSort) m[k] = root.libSort[k]
        m[cat] = { key: "date", asc: false }
        root.libSort = m
        waves.setLibrarySort(cat, "date", "desc")   // one date-desc reload; onLibraryLoaded fills
    }
    function libIsMedia(cat) { return cat === "albums" || cat === "tracks" || cat === "videos" }
    function libFill(cat, items) { var m = libModelFor(cat); if (m) { if (libIsMedia(cat)) fillMedia(m, items); else fill(m, items) } }
    function libAppend(cat, items) { var m = libModelFor(cat); if (m) { if (libIsMedia(cat)) appendMedia(m, items); else appendPlain(m, items) } }
    // Called as the active list scrolls; loads the next page well before the
    // bottom (~1.5 viewports early) so it feels endless.
    function libMaybeLoadMore(view, cat) {
        if (cat !== root.libraryCategory || !root.libHasMore || root.libLoadingMore) return
        if (view.count === 0 || view.contentHeight <= 0) return
        if (view.contentY + view.height > view.contentHeight - view.height * 1.5) {
            root.libLoadingMore = true
            waves.loadMoreLibrary(cat)
        }
    }

    Connections {
        target: waves
        function onLibraryLoaded(cat, items, more) {
            // Drop a stale first-page load for a category the user already left
            // (the backend caches it, so returning to it re-emits from cache).
            if (cat !== root.libraryCategory) return
            libAlbumsModel.clear(); libTracksModel.clear(); libArtistsModel.clear()
            libPlaylistsModel.clear(); libMixesModel.clear(); libVideosModel.clear()
            root.libHasMore = more
            root.libLoadingMore = false
            root.libFill(cat, items)
        }
        function onLibraryMore(cat, items, more) {
            if (cat !== root.libraryCategory) return
            root.libHasMore = more
            root.libLoadingMore = false
            root.libAppend(cat, items)
        }
        function onHomeLoaded(sections) {
            if (root.libraryCategory !== "home") return
            root.libHasMore = false          // Home is one self-contained landing
            root.libLoadingMore = false
            // Only populate when the load actually returned shelves. An empty
            // result (a transient fetch failure) must not wipe shelves already on
            // screen; a still-empty first load simply leaves the placeholder glyph
            // up, and the next visit retries (homeSections is still empty).
            if (sections && sections.length) root.homeSections = sections
        }
        function onDownloadFolderMissing() { root.folderGateBlocking = true }
        function onDownloadFolderDefault() { root.folderNudge = true }
        function onDownloadFolderUnreachable(path) { root.folderUnreachablePath = path; root.folderUnreachable = true }
        function onFfmpegMissingBlocked() { root.ffmpegBlocked = true }
        function onLoggedInChanged() {
            // Drop every QML-side copy of Browse data when the account flips:
            // the landing embeds personalized For You rows, and the backend's
            // own logout cache-clear can't reach these copies. Re-fetch right
            // away if the user is sitting on the Browse tab.
            root.browseSections = []
            root.browseChips = { genres: [], moods: [], decades: [] }
            root.browsePage = null
            root.browsePageKey = ""
            root.browseStack = []
            root.browseError = false
            root.browsePageError = false
            root.browsePageLoading = false
            root.browseLoading = false
            // History snapshots hold page payloads (personalized rows) and
            // artist ids from the previous account, drop them too.
            root.navHistory = []
            root._navRestoring = false
            root.browseHighlightId = ""
            root.homeSections = []
            if (waves.loggedIn && root.browseOpen) {
                root.browseLoading = true
                waves.loadBrowse()
            }
        }
        function onBrowseLoaded(p) {
            root.markNav("browse render")
            root.browseLoading = false
            root.browseError = !!p.error
            root.browseArtistsSideMap(p.sections || [])
            root.browseSections = p.sections || []
            root.browseChips = { genres: p.genres || [], moods: p.moods || [], decades: p.decades || [] }
            // An open (or stacked) local: row page snapshotted a row this
            // payload may have just refreshed; bring it in line.
            root.refreshLocalBrowsePages(p.sections || [])
        }
        function onBrowsePageLoaded(p) {
            if (p.key !== root.browsePageKey) return   // stale: user already left this page
            root.markNav("browse page render")
            root.browsePageLoading = false
            root.browsePageError = !!p.error
            root.browseArtistsSideMap(p.sections || [])
            // Opening an album ON a track: arm the hide BEFORE assigning the page,
            // so its rows lay out already invisible (browseCol opacity 0) and never
            // paint at the top for a frame before the highlighted row centers
            // itself. The row's own timer then scrolls into place and reveals;
            // hiRevealGuard (re-armed by this pending change) clears the hide if the
            // row never appears. Without this the pane flashed the top then scrolled
            // for an uncached album, e.g. one opened from a My Tidal Home shelf.
            if (!p.error && root.browseHighlightId !== "") root.browseHighlightPending = true
            root.browsePage = p.error ? null : p
        }
        function onBrowseSectionMore(p) {
            root.browseGrew(p)
        }
        function onVideoReady(p) {
            // Stale resolve (overlay closed, or another video opened since).
            if (!root.videoNow || ("" + p.id) !== root.videoNow.id) return
            if (p.error) {
                if (root.videoSwitching) { root.videoSwitching = false; return }  // keep the old stream playing
                root.videoLoading = false; root.videoError = true
                return
            }
            if (root.videoSwitching) {
                // Seamless-as-possible swap: freeze the current frame over the
                // surface (the source change blanks the VideoOutput), swap,
                // then resume at the captured position once the new stream is
                // seekable (applyVideoSeek retries until it is).
                root.videoSwitching = false
                root.videoNow = Object.assign({}, root.videoNow, { res: p.res || 0, heights: p.heights || [] })
                var swapUrl = p.url
                videoSurface.grabToImage(function(result) {
                    root._videoGrab = result   // keep alive while displayed
                    videoFreeze.source = result.url
                    videoFreeze.visible = true
                    root.videoPendingSeek = videoPlayer.position
                    root._videoSeekTries = 0
                    videoPlayer.source = swapUrl
                    videoPlayer.play()
                    videoSeekRetry.restart()
                })
                return
            }
            root.videoNow = { id: root.videoNow.id,
                              title: p.title || root.videoNow.title,
                              artist: p.artist || root.videoNow.artist,
                              artists: p.artists || [],
                              albumId: p.album_id || "",
                              trackId: p.track_id || "",
                              res: p.res || 0,
                              heights: p.heights || [] }
            root.videoLoading = false
            videoPlayer.source = p.url
            videoPlayer.play()
        }
        function onBrowseTileArt(path, arts) {
            // Mutate the buffer in place (no rebind) and coalesce via the timer.
            root._tileArtPending[path] = arts
            tileArtFlush.restart()
        }
        function onSearchResults(r) {
            root.navPush()
            root.markNav("search render")
            root.searchSaved = null   // a fresh search replaces the saved drill-in
            root.navOrigin = "search"
            root.browseOpen = false
            root.artistOpen = false
            root.libraryOpen = false
            root.trackCache = ({})
            root.expandedAlbums = ({})
            root.fill(artistsModel, r.artists)
            root.albumsRaw = r.albums || []
            root.applySort()
            root.fillMedia(tracksModel, r.tracks)
            root.fillMedia(videosModel, r.videos)
            root.fill(playlistsModel, r.playlists)
            root.fill(mixesModel, r.mixes)
        }
        // Assign a NEW object so the `var` property fires a change notification
        // (mutating + reassigning the same reference does not update bindings).
        function onAlbumTracksLoaded(id, tracks) { var c = Object.assign({}, root.trackCache); c[id] = tracks; root.trackCache = c }
        function onArtistMetaLoaded(id, pop) {
            for (var i = 0; i < artistsModel.count; ++i) {
                if (artistsModel.get(i).id === id) { artistsModel.setProperty(i, "popularity", pop); break }
            }
        }
        function onArtistLoaded(p) {
            // Background revalidation of a cached page: update in place only
            // if the user is still looking at this artist, never navigate. Never
            // let a full-page refresh overwrite a library-scoped view of the same
            // artist (scoped loads never emit refresh, so this only guards the
            // full page from clobbering a scoped one at the same id).
            if (p.refresh) {
                if (!root.artistOpen || !root.artistData || root.artistData.libraryScoped
                    || ("" + root.artistData.id) !== ("" + p.id)) return
                root.artistData = p
                root.fillMedia(artistAlbumsModel, p.albums)
                root.fillMedia(artistEpModel, p.eps)
                root.fillMedia(artistTracksModel, p.tracks)
                return
            }
            if (root.videoNow) root.closeVideo()   // artist link from the video player
            if (root._navRestoring) root._navRestoring = false
            else root.navPush()
            root.markNav("artist render")
            root.artistData = p
            root.bioExpanded = false
            root.topTracksExpanded = false
            root.expandedAlbums = ({})
            root.artistOpen = true      // target-first (see openLibrary): keep Search inactive mid-switch
            root.libraryOpen = false
            root.fillMedia(artistAlbumsModel, p.albums)
            root.fillMedia(artistEpModel, p.eps)
            root.fillMedia(artistTracksModel, p.tracks)
        }
        function onQueueChanged(q) {
            root.reconcileQueue(q)
            var n = 0
            for (var i = 0; i < q.length; ++i) { var s = q[i].status; if (s === "queued" || s === "running") n++ }
            root.activeQueueCount = n
        }
        function onQueueItemProgress(qid, pct) {
            for (var i = 0; i < queueModel.count; ++i) {
                if (queueModel.get(i).qid === qid) { queueModel.setProperty(i, "progress", pct); break }
            }
        }
        // ---- queue-row album expansion: per-track snapshot + live updates ----
        function onQueueTracksLoaded(qid, tracks) {
            var m = Object.assign({}, root.queueTracks); m[qid] = tracks; root.queueTracks = m
        }
        function onQueueTrackState(qid, row) {
            var arr = root.queueTracks[qid]
            if (!arr) return
            var copy = arr.slice(), found = false
            for (var i = 0; i < copy.length; ++i) {
                if (copy[i].id === row.id) {
                    copy[i] = Object.assign({}, copy[i], { status: row.status, pct: row.pct })
                    found = true; break
                }
            }
            if (!found) copy.push({ id: row.id, num: copy.length + 1, title: row.title,
                                    duration: row.duration, status: row.status, pct: row.pct })
            var m = Object.assign({}, root.queueTracks); m[qid] = copy; root.queueTracks = m
        }
        function onQueueTrackPct(qid, ticks) {
            var arr = root.queueTracks[qid]
            if (!arr) return
            var copy = arr.slice(), hit = false
            for (var i = 0; i < copy.length; ++i) {
                var p = ticks[copy[i].id]
                if (p !== undefined) { copy[i] = Object.assign({}, copy[i], { pct: p }); hit = true }
            }
            if (!hit) return
            var m = Object.assign({}, root.queueTracks); m[qid] = copy; root.queueTracks = m
        }
        function onDownloadProgress(id, pct) { var p = Object.assign({}, root.dlProgress); p[id] = pct; root.dlProgress = p }
        function onDownloadState(id, st) { var s = Object.assign({}, root.dlState); s[id] = st; root.dlState = s }
        // Preview resolves are async; drop any that arrive after the user moved
        // on to a different preview (guard on the current kind+id).
        function onPreviewReady(kind, id, url) {
            if (kind !== root.previewKind || id !== root.previewId) return
            previewPlayer.source = url
            previewPlayer.play()
        }
        function onPreviewState(kind, id, st) {
            if (kind !== root.previewKind || id !== root.previewId) return
            if (st === "error") {
                // Flash the button red briefly, then fall back to idle.
                root.previewError = kind + ":" + id
                root.stopPreview()
                previewErrorTimer.restart()
            } else if (st === "") {
                root.stopPreview()
            } else if (st === "loading") {
                root.previewLoading = true
            }
        }
        function onPreviewMeta(kind, id, title, artist, art, artistId, albumId, trackId, artists) {
            if (kind !== root.previewKind || id !== root.previewId) return  // superseded
            root.previewNowTitle = title; root.previewNowArtist = artist; root.previewNowArt = art
            root.previewNowArtistId = artistId; root.previewNowAlbumId = albumId
            root.previewNowTrackId = trackId
            root.previewNowArtists = artists || []
        }
        function onLoginUrlReady(url) { Qt.openUrlExternally(url); loginPanel.urlOpened = true }
        function onBackRequested() { root.navBack() }
        function onAppUpdateChecked(available, current, latest, manual) {
            root.appUpdAvailable = available
            root.appUpdLatest = latest
            // Toast on detection; re-shows each launch until dismissed or
            // acted on (per version). The status-bar notice stays regardless.
            // Only for the automatic (opt-in) startup check: a manual check
            // means the user is already on the Settings updater card, so a
            // toast pointing them at the update would be noise.
            if (available && !manual) updateToast.offer(latest)
        }
        // Once the new build is staged the notice would be advertising a
        // version the user already has; the Settings card carries the
        // restart prompt from here.
        function onAppUpdateStateChanged(state, message) {
            if (state === "done") root.appUpdAvailable = false
        }
    }

    // ====================================================================
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // ---- Header -----------------------------------------------------
        // The top bar and the search controls share one surface: the panel
        // grows downward to reveal the search tier when on a page that uses it,
        // rather than a separate strip butted against the bar. The hairline
        // always rides the panel's bottom edge as it expands / collapses.
        Rectangle {
            id: consoleHeader
            Layout.fillWidth: true
            implicitHeight: 56 + searchTier.height
            color: root.surface0
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.border1 }
            RowLayout {
                id: headerRow
                anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                height: 56; anchors.leftMargin: 22; anchors.rightMargin: 22; spacing: 12
                WaveMark { id: headerMark }
                // Wordmark sized to fill the logo box height (sublabel removed).
                Text {
                    text: "WAVES"; color: root.textHi; font.bold: true; font.letterSpacing: 2.6
                    font.pixelSize: 40
                    Layout.alignment: Qt.AlignVCenter
                }
                Item { Layout.fillWidth: true }
                // nav tabs, dim phosphor cells at rest; the active tab is a lit
                // cell (accent-container fill, accent label) that wipes in via the
                // CRT tube-collapse animation. Labels are sentence case by design
                // (Button Lab navCase verdict) while other buttons stay UPPERCASE.
                NavTab {
                    label: "Browse"
                    // Lit by origin, not by view flags: drilling into an artist
                    // or album keeps the tab you came from highlighted.
                    active: root.navOrigin === "browse" && !root.settingsOpen
                    onClicked: root.openBrowse()
                }
                NavTab {
                    label: "Search"
                    active: root.navOrigin === "search" && !root.settingsOpen
                    // First press returns to Search exactly as it was left
                    // (artist page, expanded album, scroll); a second press
                    // while already there resets to a blank search page.
                    onClicked: root.openSearch()
                }
                NavTab {
                    label: "My Tidal"
                    active: root.navOrigin === "library" && !root.settingsOpen
                    onClicked: root.openLibrary()
                }
                NavTab {
                    label: "Settings"
                    active: root.settingsOpen
                    onClicked: { root.navPush(); root.markNav("settings"); root.settingsOpen = true; root.artistOpen = false; root.libraryOpen = false }
                }
                // queue (outlined) with count badge
                Rectangle {
                    implicitHeight: qrow.implicitHeight + root.btnPadV * 2; implicitWidth: qrow.implicitWidth + root.btnPadH * 2; radius: root.btnRad
                    color: "transparent"; border.color: root.border1
                    RowLayout {
                        id: qrow; anchors.centerIn: parent; spacing: 7
                        Ico { name: "arrow-down"; color: root.accent; size: 15; bold: 10 }
                        Text { text: "QUEUE"; color: root.textLo; font.pixelSize: 13; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack }
                        Rectangle {
                            visible: root.activeQueueCount > 0; radius: 9; color: root.accent
                            implicitWidth: Math.max(18, qc.implicitWidth + 10); implicitHeight: 18
                            Text { textFormat: Text.PlainText; id: qc; anchors.centerIn: parent; text: root.activeQueueCount; color: root.accentText; font.family: root.mono; font.pixelSize: 11; font.bold: true }
                        }
                    }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: queueDrawer.open() }
                }
                // connection pill
                Rectangle {
                    implicitHeight: 26; implicitWidth: connRow.implicitWidth + 22; radius: 13
                    color: root.surface2; border.color: root.border1
                    RowLayout {
                        id: connRow; anchors.centerIn: parent; spacing: 7
                        Rectangle { width: 7; height: 7; radius: 3.5; color: waves.loggedIn ? root.green : root.textDim }
                        Text { textFormat: Text.PlainText; text: waves.loggedIn ? "CONNECTED" : "OFFLINE"; color: waves.loggedIn ? root.green : root.textDim; font.pixelSize: 11; font.family: root.uiFont; font.bold: true; font.letterSpacing: 1.1 }
                    }
                }
                // sign out, uppercase, red on hover
                Text {
                    visible: waves.loggedIn; text: "SIGN OUT"
                    color: soMa.containsMouse ? root.red : root.textDim
                    font.pixelSize: 12; font.letterSpacing: 0.85
                    MouseArea { id: soMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: waves.logout() }
                }
            }

            // ---- Search tier: shares the bar's surface and slides down out of
            // it on Search / artist pages; collapses up on Settings / My Tidal. ----
            Item {
                id: searchTier
                anchors.top: headerRow.bottom
                anchors.left: parent.left; anchors.right: parent.right
                clip: true
                // Drop out of the scene graph entirely once fully collapsed
                // (visible stays true through the height animation). clip alone
                // is not enough: the sort dropdown's caret is a QtQuick.Shapes
                // item, and Shapes can leak through an ancestor's clip/opacity,
                // it kept painting over Browse / My Tidal at the tier's old spot.
                visible: height > 0
                readonly property bool shown: !root.settingsOpen && !root.libraryOpen && !root.browseOpen
                height: shown ? tierContent.implicitHeight : 0
                Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

                ColumnLayout {
                    id: tierContent
                    anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                    spacing: 0
                    opacity: searchTier.shown ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutQuad } }

                    // ---- Search + sort ---------------------------------------------
                    RowLayout {
                        Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22; Layout.topMargin: 10; spacing: 10
                        enabled: waves.loggedIn; opacity: waves.loggedIn ? 1 : 0.5
                        Rectangle {
                            id: searchBox
                            Layout.fillWidth: true; implicitHeight: 44; radius: 8; color: root.surface2
                            border.color: (searchField.activeFocus || searchDecoder.decoding) ? root.accent : root.outline
                            Behavior on border.color { ColorAnimation { duration: 160; easing.type: Easing.OutQuad } }

                            // A genuine TIDAL link auto-resolves once it has decoded in: host must
                            // be tidal.com or any *.tidal.com subdomain (e.g. listen.tidal.com) AND
                            // have a path. Lookalikes (eviltidal.com, tidal.com.evil.com) never fire.
                            function isTidalUrl(s) {
                                var u = ("" + s).trim().replace(/^https?:\/\//i, "")
                                var slash = u.indexOf("/")
                                if (slash < 1) return false                       // need a host and a path
                                var host = u.substring(0, slash).toLowerCase().replace(/^[^@]*@/, "").replace(/:\d+$/, "")
                                var path = u.substring(slash + 1)
                                if (!path.length) return false                    // need something to resolve
                                return host === "tidal.com" || /\.tidal\.com$/.test(host)
                            }
                            // Matrix-decrypt paste-in; a pasted TIDAL link auto-searches once settled.
                            DecodeController {
                                id: searchDecoder; field: searchField; glyph: pasteGlyph
                                onDecoded: function(text) {
                                    if (searchBox.isTidalUrl(text)) {
                                        root.browseOpen = false; root.artistOpen = false; root.libraryOpen = false; root.settingsOpen = false
                                        waves.search(text)
                                    }
                                }
                            }

                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 6; spacing: 10
                                Ico { name: "search"; color: root.accent; size: 18 }
                                TextField {
                                    id: searchField
                                    Layout.fillWidth: true
                                    placeholderText: "Search, or paste a TIDAL link…"
                                    color: searchDecoder.decoding ? root.accent : root.textHi
                                    placeholderTextColor: root.textLo; font.pixelSize: 15
                                    background: Rectangle { color: "transparent" }
                                    onAccepted: waves.search(text)
                                    // A standard paste (a multi-char jump typing can't produce) is
                                    // detected and animated in, without ever reading the clipboard.
                                    onTextChanged: searchDecoder.noteTextChanged()
                                }
                                PasteGlyph {
                                    id: pasteGlyph
                                    Layout.alignment: Qt.AlignVCenter
                                    // standard OS paste into the field; the field's onTextChanged
                                    // animates it. The app itself never reads the clipboard.
                                    onClicked: { searchField.forceActiveFocus(); searchField.clear(); searchField.paste() }
                                }
                            }
                        }
                        ComboBox {
                            id: sortBox
                            implicitHeight: 44; implicitWidth: 156
                            model: ["Relevance", "Release date", "Name"]
                            onActivated: root.applySort()
                            background: Rectangle { radius: 8; color: root.surface2; border.color: sortBox.popup.visible ? root.accent : root.outline }
                            contentItem: Text {
                                textFormat: Text.PlainText
                                text: sortBox.displayText; color: root.textHi; font.pixelSize: 14
                                leftPadding: 14; rightPadding: 28; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                            }
                            indicator: ExpandChevron {
                                x: sortBox.width - 26; y: (sortBox.height - 18) / 2; tile: 18; glyph: 13
                                showTile: false; closedAngle: -90; openAngle: 0
                                // Blank the stroke as soon as the tier starts collapsing (not when
                                // it finishes hiding): the caret is a QtQuick.Shapes node, and a
                                // hidden Shape can keep painting its last-synced stroke, this
                                // bled over Browse / My Tidal at the tier's old position.
                                stroke: searchTier.shown ? root.accent : "transparent"
                                open: sortBox.popup.visible
                            }
                            delegate: ItemDelegate {
                                width: sortBox.width
                                contentItem: Text { textFormat: Text.PlainText; text: modelData; color: root.textHi; font.pixelSize: 14; verticalAlignment: Text.AlignVCenter }
                                background: Rectangle { color: highlighted ? root.surface3 : root.surface2 }
                                highlighted: sortBox.highlightedIndex === index
                            }
                            popup: Popup {
                                y: sortBox.height + 4; width: sortBox.width; padding: 4
                                implicitHeight: contentItem.implicitHeight + 8
                                background: Rectangle { radius: 8; color: root.surface2; border.color: root.outline }
                                contentItem: ListView {
                                    clip: true; implicitHeight: contentHeight
                                    model: sortBox.popup.visible ? sortBox.delegateModel : null
                                    ScrollBar.vertical: ScrollBar {}
                                }
                            }
                        }
                        Rectangle {
                            implicitHeight: 44; implicitWidth: 44; radius: 8
                            color: root.surface2; border.color: root.outline
                            Text {
                                textFormat: Text.PlainText
                                anchors.centerIn: parent; text: root.sortAsc ? "↑" : "↓"
                                color: sortBox.currentIndex === 0 ? root.textDim : root.textHi; font.family: root.mono; font.pixelSize: 18
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { root.sortAsc = !root.sortAsc; root.applySort() } }
                        }
                        // The audio-quality picker that used to sit here was a duplicate of
                        // the Quality setting in Settings; it set the same value but never
                        // filtered results. Results are now capped to the Settings quality,
                        // so the redundant control is gone.
                    }

                    // ---- Type chips -------------------------------------------------
                    // Hidden until a search returns results; the chips then cascade
                    // in (staggered fade + downward settle) as the bar grows down.
                    RowLayout {
                        Layout.fillWidth: true; Layout.leftMargin: 22; Layout.topMargin: 8; spacing: 8
                        visible: waves.loggedIn && root.hasResults && !root.artistOpen && !root.settingsOpen && !root.libraryOpen && !root.browseOpen
                        Repeater {
                            model: [["all", "All"], ["artists", "Artists"], ["albums", "Albums"], ["tracks", "Tracks"], ["videos", "Videos"], ["playlists", "Playlists"], ["mixes", "Mixes"]]
                            delegate: Rectangle {
                                id: tchip
                                required property var modelData
                                required property int index
                                readonly property bool on: root.filterType === modelData[0]
                                radius: 8; implicitHeight: 30; implicitWidth: chipRow.implicitWidth + 26
                                color: on ? root.accentCont : "transparent"
                                border.color: on ? root.accentDim : root.border1
                                opacity: 0
                                transform: Translate { id: chipTr; y: -7 }
                                Row {
                                    id: chipRow; anchors.centerIn: parent; spacing: 7
                                    Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter; color: tchip.on ? root.accent : root.textDim }
                                    Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: tchip.modelData[1]; color: tchip.on ? root.accent : root.textLo; font.pixelSize: 13 }
                                }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.filterType = tchip.modelData[0] }
                                // Cascade in left-to-right once results exist; reset when cleared.
                                states: State {
                                    name: "in"; when: root.hasResults
                                    PropertyChanges { target: tchip; opacity: 1 }
                                    PropertyChanges { target: chipTr; y: 0 }
                                }
                                transitions: [
                                    Transition {
                                        to: "in"
                                        SequentialAnimation {
                                            PauseAnimation { duration: tchip.index * 45 }
                                            ParallelAnimation {
                                                NumberAnimation { target: tchip; property: "opacity"; to: 1; duration: 240; easing.type: Easing.OutCubic }
                                                NumberAnimation { target: chipTr; property: "y"; to: 0; duration: 300; easing.type: Easing.OutCubic }
                                            }
                                        }
                                    },
                                    Transition {
                                        from: "in"
                                        ParallelAnimation {
                                            NumberAnimation { target: tchip; property: "opacity"; to: 0; duration: 120 }
                                            NumberAnimation { target: chipTr; property: "y"; to: -7; duration: 120 }
                                        }
                                    }
                                ]
                            }
                        }
                    }
                    // bottom padding so the controls don't kiss the panel hairline
                    Item { Layout.fillWidth: true; implicitHeight: 10 }
                }                 // ColumnLayout tierContent
            }                     // Item searchTier
        }                         // Rectangle consoleHeader

        // ---- Browse page (TIDAL editorial: new / top / genres / moods / decades)
        // Sub-page back bar, pinned above the scroll area so Back is always
        // reachable without scrolling to the top (mirrors the artist page).
        Item {
            Layout.fillWidth: true; Layout.topMargin: 8
            visible: root.browseOpen && !root.artistOpen && !root.settingsOpen && !root.libraryOpen
                     && root.browsePageKey !== ""
            implicitHeight: 30
            Row {
                id: browseBackRow; spacing: 8; x: 22; anchors.verticalCenter: parent.verticalCenter
                Ico { name: "arrow-left"; color: root.textLo; size: 16; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    textFormat: Text.PlainText
                    // One level at a time: name the page Back returns to.
                    text: "Back to " + (root.navBackLabel() || "Browse")
                    color: root.textLo; font.pixelSize: 14
                }
                Text {
                    textFormat: Text.PlainText
                    text: root.browsePage ? "·  " + (root.browsePage.title || "") : ""
                    color: root.textHi; font.pixelSize: 14; font.bold: true
                }
            }
            MouseArea {
                anchors.left: parent.left; anchors.leftMargin: 22; anchors.verticalCenter: parent.verticalCenter
                width: browseBackRow.width; height: parent.height
                cursorShape: Qt.PointingHandCursor
                onClicked: root.navBack()
            }
        }
        Flickable {
            id: browsePane
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 8
            visible: root.browseOpen && !root.artistOpen && !root.settingsOpen && !root.libraryOpen
            clip: true
            contentWidth: width; contentHeight: browseCol.height + 24
            ScrollBar.vertical: ScrollBar {}
            boundsBehavior: Flickable.StopAtBounds
            // A fresh page change (drill-in) starts at the top; a Back that
            // arms pendingRestoreY keeps its scroll instead. The restore is
            // applied in onContentHeightChanged, which fires during the layout
            // pass, BEFORE the frame paints, so Back lands on the saved spot
            // directly, with no visible jump from the top.
            // A Back restore is tagged with the page key it belongs to, so it
            // only ever applies once that page is showing, never against the
            // outgoing page. On the page-key change it applies in the SAME pass
            // (before paint) so Back lands on the saved spot with no jump from
            // the top; a fresh drill-in (no armed restore) starts at the top.
            readonly property string _pageKey: root.browsePageKey
            property real pendingRestoreY: -1
            property string pendingRestoreKey: ""
            function applyRestore() {
                if (pendingRestoreY < 0 || root.browsePageKey !== pendingRestoreKey) return
                var maxY = Math.max(0, contentHeight - height)
                contentY = Math.min(pendingRestoreY, maxY)
                if (maxY >= pendingRestoreY) pendingRestoreY = -1   // reached the target
            }
            on_PageKeyChanged: {
                if (pendingRestoreY >= 0 && root.browsePageKey === pendingRestoreKey) applyRestore()
                else contentY = 0
            }
            onMovingChanged: root.browseMoving = moving
            onPendingRestoreYChanged: if (pendingRestoreY >= 0) restoreGiveUp.restart()
            onContentHeightChanged: { applyRestore(); maybeGrow() }
            Timer { id: restoreGiveUp; interval: 800; onTriggered: browsePane.pendingRestoreY = -1 }
            // Endless scroll on a drilled listing page (a full-listing grid or
            // track list, one section that fills the page). Nearing the bottom
            // fetches the next window. Multi-section pages (genre/mood) are
            // horizontal shelves plus preview rows that each carry their own
            // "show more", so the whole-page vertical scroll doesn't grow them.
            // Guarded by browseGrow's in-flight/exhausted checks, so re-fires
            // while sitting near the bottom are cheap no-ops.
            function maybeGrow() {
                if (root.browsePageKey === "" || !root.browsePage) return
                var secs = root.browsePage.sections || []
                if (secs.length !== 1 || !root.browseCanGrow(secs[0])) return
                if (contentY + height >= contentHeight - 800) root.browseGrow(secs[0])
            }
            // onContentHeightChanged also tops up a page that loads shorter than
            // it can scroll (handled above alongside the scroll restore).
            onContentYChanged: maybeGrow()
            // Fetch on first reveal too (e.g. the user signed in while already
            // on the tab); idempotent thanks to the loading flags here and the
            // in-flight guard backend-side.
            onVisibleChanged: {
                if (visible && waves.loggedIn && root.browseSections.length === 0 && !root.browseLoading) {
                    root.browseLoading = true; root.browseError = false
                    waves.loadBrowse()
                } else if (visible && waves.loggedIn) {
                    waves.refreshBrowse()   // silent, throttled; repaints only on change
                }
            }

            Column {
                id: browseCol
                x: 22; width: browsePane.width - 44; spacing: 8
                // Hidden (but still laid out, so heights resolve) until the
                // highlighted-track scroll has been applied: opening an album from
                // a track then reveals it already on the row, never mid-jump.
                // Landing on a highlighted track: hide the pane INSTANTLY while it
                // lays out and scrolls to the row (entering the state has no
                // transition, so the un-scrolled top never shows, that was the
                // flash), then FADE the reveal in so the album eases into place
                // already scrolled instead of dropping in blank. Only the reveal
                // direction is animated (the Transition's `to: ""`); a Behavior
                // can't do direction here without racing the pending change.
                opacity: 1
                states: State {
                    name: "positioning"; when: root.browseHighlightPending
                    PropertyChanges { target: browseCol; opacity: 0 }
                }
                transitions: Transition {
                    to: ""   // leaving "positioning" == the reveal
                    NumberAnimation { property: "opacity"; duration: 260; easing.type: Easing.OutCubic }
                }

                // Item header (playlist / mix / album page): a full-width hero,
                // the artwork doubles as a dimmed backdrop so the strip above
                // the track list isn't mostly empty panel.
                Rectangle {
                    id: browseItemHeader
                    readonly property var hd: root.browsePage && root.browsePage.header ? root.browsePage.header : null
                    visible: hd !== null
                    width: parent.width; height: visible ? 224 : 0
                    radius: 14; clip: true
                    color: root.surface
                    border.color: root.border1
                    Image {
                        anchors.fill: parent
                        source: browseItemHeader.hd ? (browseItemHeader.hd.art || "") : ""
                        fillMode: Image.PreserveAspectCrop
                        sourceSize.width: 480
                        opacity: 0.30
                        asynchronous: true
                        cache: true
                    }
                    // Left-to-right scrim: keep the caption side readable, let
                    // the backdrop breathe on the right.
                    Rectangle {
                        anchors.fill: parent
                        gradient: Gradient {
                            orientation: Gradient.Horizontal
                            GradientStop { position: 0.0; color: "#e6101318" }
                            GradientStop { position: 0.55; color: "#a0101318" }
                            GradientStop { position: 1.0; color: "#30101318" }
                        }
                    }
                    Row {
                        anchors.fill: parent
                        anchors.margins: 22
                        spacing: 24
                        Art {
                            width: 180; height: 180; radius: 12
                            anchors.verticalCenter: parent.verticalCenter
                            url: browseItemHeader.hd ? (browseItemHeader.hd.art || "") : ""
                        }
                        Column {
                            spacing: 7
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width - 180 - 24
                            Text {
                                textFormat: Text.PlainText
                                text: browseItemHeader.hd
                                      ? (browseItemHeader.hd.kind === "playlist" ? "PLAYLIST"
                                         : browseItemHeader.hd.kind === "mix" ? "MIX" : "ALBUM")
                                      : ""
                                color: root.textDim; font.pixelSize: 11; font.family: root.uiFont
                                font.bold: true; font.letterSpacing: 1.5
                            }
                            Text {
                                textFormat: Text.PlainText
                                text: browseItemHeader.hd ? (browseItemHeader.hd.title || "") : ""
                                color: root.textHi; font.pixelSize: 26; font.bold: true
                                width: parent.width; elide: Text.ElideRight
                            }
                            Text {
                                id: bihSubtitle
                                textFormat: Text.PlainText
                                text: browseItemHeader.hd ? (browseItemHeader.hd.subtitle || "") : ""
                                // Album subtitles carry the artist, make them read (and act) like a link.
                                readonly property bool linked: browseItemHeader.hd ? !!browseItemHeader.hd.artist_id : false
                                color: linked ? root.accentContTx : root.textLo
                                font.pixelSize: 14
                                width: parent.width; elide: Text.ElideRight
                                MouseArea {
                                    anchors.fill: parent
                                    enabled: bihSubtitle.linked
                                    cursorShape: bihSubtitle.linked ? Qt.PointingHandCursor : Qt.ArrowCursor
                                    onClicked: waves.loadArtist(browseItemHeader.hd.artist_id)
                                }
                            }
                            Text {
                                textFormat: Text.PlainText
                                visible: text !== ""
                                text: browseItemHeader.hd ? (browseItemHeader.hd.desc || "") : ""
                                color: root.textLo; font.pixelSize: 12
                                width: parent.width; wrapMode: Text.WordWrap
                                maximumLineCount: 2; elide: Text.ElideRight
                            }
                            Text {
                                textFormat: Text.PlainText
                                visible: text !== ""
                                text: browseItemHeader.hd ? (browseItemHeader.hd.stats || "") : ""
                                color: root.textDim; font.pixelSize: 12; font.family: root.mono
                            }
                            Item { width: 1; height: 3 }
                            DownloadButton {
                                mediaId: browseItemHeader.hd ? (browseItemHeader.hd.id || "") : ""
                                label: browseItemHeader.hd
                                       ? (browseItemHeader.hd.kind === "playlist" ? "Download playlist"
                                          : browseItemHeader.hd.kind === "mix" ? "Download mix" : "Download album")
                                       : ""
                                collectionIds: root.collectionTrackIds(root.browsePage ? root.browsePage.sections : [])
                                onTap: function() {
                                    if (browseItemHeader.hd)
                                        root.browseCardDownload({ kind: browseItemHeader.hd.kind, id: browseItemHeader.hd.id })
                                }
                            }
                        }
                    }
                }

                Text {
                    id: browseHint
                    visible: waves.loggedIn && (root.browsePageKey === "" ? root.browseLoading : root.browsePageLoading)
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: "Reading the wire…"
                    color: root.textLo; font.pixelSize: 22; topPadding: 96
                    // same gentle breathing as the search empty state
                    SequentialAnimation on opacity {
                        running: browseHint.visible; loops: Animation.Infinite
                        NumberAnimation { from: 0.5; to: 1.0; duration: 1500; easing.type: Easing.InOutSine }
                        NumberAnimation { from: 1.0; to: 0.5; duration: 1500; easing.type: Easing.InOutSine }
                    }
                }

                Column {
                    visible: waves.loggedIn && (root.browsePageKey === "" ? root.browseError : root.browsePageError)
                    width: parent.width; spacing: 12
                    Text {
                        width: parent.width; horizontalAlignment: Text.AlignHCenter
                        text: "Browse could not be loaded"
                        color: root.textLo; font.pixelSize: 18; topPadding: 80
                    }
                    Rectangle {
                        anchors.horizontalCenter: parent.horizontalCenter
                        implicitWidth: retryTxt.implicitWidth + root.btnPadH * 2
                        implicitHeight: retryTxt.implicitHeight + root.btnPadV * 2
                        radius: root.btnRad; color: "transparent"; border.color: root.accentDim
                        Text {
                            id: retryTxt; anchors.centerIn: parent; text: "RETRY"
                            color: root.accent; font.family: root.uiFont; font.pixelSize: 12
                            font.bold: true; font.letterSpacing: root.btnTrack
                        }
                        MouseArea {
                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                            onClicked: {
                                if (root.browsePageKey !== "") root.openBrowseLink(root.browsePageKey, "")
                                else { root.browseLoading = true; root.browseError = false; waves.loadBrowse() }
                            }
                        }
                    }
                }

                // Empty state: a drilled page that loaded fine but has nothing
                // Waves can render (e.g. an editorial article page with no
                // music). Better than a blank page below the back bar.
                Text {
                    visible: waves.loggedIn && root.browsePageKey !== "" && root.browsePage
                             && !root.browsePageLoading && !root.browsePageError
                             && ((root.browsePage.sections || []).length === 0)
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    textFormat: Text.PlainText
                    text: "Nothing to show here"
                    color: root.textLo; font.pixelSize: 18; topPadding: 96
                }

                // Landing, console style: the Genres / Moods / Decades chip
                // sets lead the page (the art-first layout renders the same
                // data as colour tiles BELOW the content shelves instead).
                Repeater {
                    model: root.browseStyle === "console" && root.browsePageKey === "" && !root.browseLoading && !root.browseError
                           ? [["GENRES", root.browseChips.genres], ["MOODS & ACTIVITIES", root.browseChips.moods], ["DECADES", root.browseChips.decades]]
                           : []
                    delegate: Column {
                        id: chipGroup
                        required property var modelData
                        width: browseCol.width; spacing: 8
                        visible: modelData[1].length > 0
                        SectionHeader { label: chipGroup.modelData[0]; count: chipGroup.modelData[1].length }
                        Flow {
                            width: parent.width; spacing: 8
                            Repeater {
                                model: chipGroup.modelData[1]
                                delegate: Rectangle {
                                    id: bchip
                                    required property var modelData
                                    radius: 8; implicitHeight: 30; implicitWidth: bcRow.implicitWidth + 26
                                    color: "transparent"; border.color: root.border1
                                    Row {
                                        id: bcRow; anchors.centerIn: parent; spacing: 7
                                        Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter; color: root.textDim }
                                        Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: bchip.modelData.title; color: root.textLo; font.pixelSize: 13 }
                                    }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.openBrowseLink(bchip.modelData.path, bchip.modelData.title) }
                                }
                            }
                        }
                    }
                }

                // Content rows: the landing sections, or the drilled page's
                Repeater {
                    model: root.browsePageKey === "" ? root.browseSections
                         : (root.browsePage ? root.browsePage.sections : [])
                    delegate: Column {
                        id: bsec
                        required property var modelData
                        required property int index
                        // The first landing shelf becomes the art-first hero
                        // row: big covers with the caption on the artwork.
                        readonly property bool artStyle: root.browseStyle === "art"
                        readonly property bool hero: artStyle && index === 0 && root.browsePageKey === ""
                                                     && modelData.rowKind === "cards"
                        // A drilled "show more" page (one lone card section) lays
                        // its cards out as a wrapping grid that scrolls with the
                        // page and re-flows on resize, not a horizontal shelf.
                        readonly property bool grid: modelData.rowKind === "cards"
                                                     && root.browsePageKey !== ""
                                                     && !(root.browsePage && root.browsePage.header)
                                                     && ((root.browsePage && root.browsePage.sections) || []).length === 1
                        // Suppress an empty/generic section headline on a drilled
                        // page, the back bar already names it (avoids a stray
                        // "More" above e.g. the Record Labels grid).
                        readonly property bool showHeadline: ("" + (modelData.title || "")).trim() !== ""
                        // Every card shelf's headline opens the full listing:
                        // TIDAL's own "show more" page when the row has one,
                        // else a local page built from the row's items. Inert
                        // only on an already-drilled grid page (self-link).
                        readonly property bool headlinable: !grid
                            && (!!modelData.more || (modelData.rowKind === "cards" && (modelData.items || []).length > 0))
                        function openListing() {
                            if (modelData.more) root.openBrowseLink(modelData.more, modelData.title || "More")
                            else root.openBrowseSection(modelData)
                        }
                        width: browseCol.width; spacing: 8
                        SectionHeader {
                            visible: !bsec.artStyle && bsec.showHeadline
                            label: (bsec.modelData.title || "More").toUpperCase() + (bsec.headlinable ? "  \u203a" : "")
                            count: (bsec.modelData.items || []).length
                            MouseArea {
                                anchors.fill: parent
                                enabled: bsec.headlinable
                                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: bsec.openListing()
                            }
                        }
                        Text {
                            id: bsecTitle
                            visible: bsec.artStyle && bsec.showHeadline
                            textFormat: Text.PlainText
                            // "\u203a" marks headlines that open the full listing
                            text: (bsec.modelData.title || "More") + (bsec.headlinable ? "  \u203a" : "")
                            color: bsecTitleMa.containsMouse ? "#ffffff" : root.textHi
                            font.pixelSize: 17; font.bold: true
                            width: parent.width; elide: Text.ElideRight
                            topPadding: 6
                            MouseArea {
                                id: bsecTitleMa
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: Math.min(parent.width, parent.implicitWidth)
                                enabled: bsec.headlinable
                                hoverEnabled: enabled
                                cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: bsec.openListing()
                            }
                        }
                        // full-listing wrap grid (drilled "show more" pages).
                        // Width snaps to whole columns and the block centers in
                        // the pane, so a half-column of dead space is split
                        // evenly left/right instead of dumped on the right until
                        // the window grows enough to add another column.
                        Flow {
                            visible: bsec.grid
                            readonly property real cardW: bsec.artStyle ? 200 : 156
                            spacing: bsec.artStyle ? 14 : 12
                            readonly property int cols: root.gridCols(cardW, spacing, (bsec.modelData.items || []).length, parent.width)
                            width: cols * (cardW + spacing) - spacing
                            x: Math.max(0, (parent.width - width) / 2)
                            Repeater {
                                model: bsec.grid ? bsec.modelData.items : []
                                delegate: Loader {
                                    id: bgridLd
                                    required property var modelData
                                    sourceComponent: bsec.artStyle ? bgridArt : bgridConsole
                                    Component {
                                        id: bgridArt
                                        ArtCard { card: bgridLd.modelData }
                                    }
                                    Component {
                                        id: bgridConsole
                                        BrowseCard { card: bgridLd.modelData }
                                    }
                                }
                            }
                        }
                        // Grid footer: tells you whether more is coming or you've
                        // reached the end, so a short editorial listing (e.g. 20
                        // Top Albums) doesn't read as "stuck" the way an endless
                        // genre listing keeps flowing.
                        Item {
                            visible: bsec.grid
                            width: parent.width; height: 46
                            readonly property bool loadingMore: !!(bsec.modelData.data && root.browseGrowing[bsec.modelData.data])
                            Row {
                                anchors.centerIn: parent; spacing: 10
                                visible: parent.loadingMore || !root.browseCanGrow(bsec.modelData)
                                Rectangle {
                                    visible: !parent.parent.loadingMore
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 28; height: 1; color: root.border1
                                }
                                Text {
                                    textFormat: Text.PlainText
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: parent.parent.loadingMore
                                            ? "Loading more…"
                                            : (bsec.modelData.items || []).length + " total"
                                    color: root.textDim; font.family: root.mono; font.pixelSize: 11
                                }
                                Rectangle {
                                    visible: !parent.parent.loadingMore
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 28; height: 1; color: root.border1
                                }
                            }
                        }
                        // horizontal card shelf, console (framed cards)
                        ListView {
                            visible: !bsec.artStyle && !bsec.grid && bsec.modelData.rowKind === "cards"
                            width: parent.width; height: 238
                            orientation: ListView.Horizontal
                            spacing: 12; clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            model: visible ? bsec.modelData.items : []
                            delegate: BrowseCard {
                                required property var modelData
                                card: modelData
                            }
                            ShelfWheelRedirect { pane: browsePane }
                            // endless scroll: reaching the shelf's right edge
                            // pulls the row's next window from TIDAL
                            onAtXEndChanged: if (atXEnd && count > 0) root.browseGrow(bsec.modelData)
                        }
                        // horizontal card shelf, art-first (unframed covers)
                        ListView {
                            visible: bsec.artStyle && !bsec.grid && bsec.modelData.rowKind === "cards"
                            width: parent.width; height: bsec.hero ? 284 : 250
                            orientation: ListView.Horizontal
                            spacing: 14; clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            model: visible ? bsec.modelData.items : []
                            delegate: ArtCard {
                                required property var modelData
                                card: modelData
                                hero: bsec.hero
                            }
                            ShelfWheelRedirect { pane: browsePane }
                            onAtXEndChanged: if (atXEnd && count > 0) root.browseGrow(bsec.modelData)
                        }
                        // vertical track list (e.g. "New Tracks" on a genre page)
                        Column {
                            visible: bsec.modelData.rowKind === "tracks"
                            width: parent.width
                            Repeater {
                                model: bsec.modelData.rowKind === "tracks" ? bsec.modelData.items : []
                                delegate: TrackRow {
                                    id: btr
                                    required property var modelData
                                    width: bsec.width
                                    tId: modelData.id; kind: modelData.kind || "track"
                                    title: modelData.title; artistName: modelData.artist || ""; artistId: modelData.artist_id || ""
                                    album: modelData.album || ""; art: modelData.art || ""; year: "" + (modelData.year || ""); date: modelData.date || ""
                                    duration: modelData.duration || ""; quality: modelData.quality || ""; popularity: modelData.popularity || 0
                                    // Numbers only on item pages, where they're ordered
                                    // (album track #s / playlist positions), editorial
                                    // track shelves would all read "1".
                                    num: (root.browsePage && root.browsePage.header) ? (modelData.num || 0) : 0
                                    albumId: modelData.album_id || ""
                                    hi: root.browseHighlightId !== "" && modelData.id === root.browseHighlightId
                                    // Track click landed here: hide the page while it
                                    // lays out, center this row, then reveal, so the
                                    // scroll into place is never seen. onCompleted
                                    // fires before the first paint, so the page is
                                    // already hidden when this content would show at
                                    // the top.
                                    Component.onCompleted: if (btr.hi) root.browseHighlightPending = true
                                    Timer {
                                        interval: 120; running: btr.hi
                                        onTriggered: {
                                            var y = btr.mapToItem(browseCol, 0, 0).y - (browsePane.height - btr.height) / 2
                                            browsePane.contentY = Math.max(0, Math.min(y, browsePane.contentHeight - browsePane.height))
                                            root.browseHighlightPending = false
                                        }
                                    }
                                }
                            }
                        }
                        // Sub-page links (genre / mood / decade / label tiles).
                        // In art mode these are fixed-size tiles, so they use the
                        // SAME centered, column-snapped, responsive geometry as the
                        // card grid, one presentation across every drilled box
                        // view. Console mode keeps the variable-width chip flow.
                        Flow {
                            visible: bsec.modelData.rowKind === "links"
                            readonly property bool tiled: bsec.artStyle
                            readonly property real cardW: 200
                            spacing: tiled ? 14 : 8
                            readonly property int cols: root.gridCols(cardW, spacing, (bsec.modelData.items || []).length, parent.width)
                            width: tiled ? cols * (cardW + spacing) - spacing : parent.width
                            x: tiled ? Math.max(0, (parent.width - width) / 2) : 0
                            Repeater {
                                model: bsec.modelData.rowKind === "links" ? bsec.modelData.items : []
                                delegate: Loader {
                                    id: blinkLd
                                    required property var modelData
                                    required property int index
                                    sourceComponent: bsec.artStyle ? blinkTile : blinkChip
                                    Component {
                                        id: blinkTile
                                        BrowseTile { title: blinkLd.modelData.title; path: blinkLd.modelData.path; idx: blinkLd.index }
                                    }
                                    Component {
                                        id: blinkChip
                                        Rectangle {
                                            id: blink
                                            radius: 8; implicitHeight: 30; implicitWidth: blRow.implicitWidth + 26
                                            color: "transparent"; border.color: root.border1
                                            Row {
                                                id: blRow; anchors.centerIn: parent; spacing: 7
                                                Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter; color: root.textDim }
                                                Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: blinkLd.modelData.title; color: root.textLo; font.pixelSize: 13 }
                                            }
                                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.openBrowseLink(blinkLd.modelData.path, blinkLd.modelData.title) }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Landing, art-first: genre / mood / decade colour-tile
                // shelves close the page, content first, wayfinding last,
                // the way the streaming services arrange their home pages.
                Repeater {
                    model: root.browseStyle === "art" && root.browsePageKey === "" && !root.browseLoading && !root.browseError
                           ? [["Genres", root.browseChips.genres], ["Moods & Activities", root.browseChips.moods], ["Decades", root.browseChips.decades]]
                           : []
                    delegate: Column {
                        id: tileGroup
                        required property var modelData
                        width: browseCol.width; spacing: 8
                        visible: modelData[1].length > 0
                        Text {
                            id: cloudTitle
                            textFormat: Text.PlainText
                            // "›" marks the headline as openable, click to see
                            // the whole cloud as a wrapping grid, like every
                            // other section headline.
                            text: tileGroup.modelData[0] + "  ›"
                            color: cloudTitleMa.containsMouse ? "#ffffff" : root.textHi
                            font.pixelSize: 17; font.bold: true
                            width: parent.width; elide: Text.ElideRight
                            topPadding: 6
                            MouseArea {
                                id: cloudTitleMa
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: Math.min(parent.width, parent.implicitWidth)
                                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: root.openBrowseCloud(tileGroup.modelData[0], tileGroup.modelData[1])
                            }
                        }
                        ListView {
                            width: parent.width; height: 204
                            orientation: ListView.Horizontal
                            spacing: 14; clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            model: tileGroup.modelData[1]
                            delegate: BrowseTile {
                                required property var modelData
                                required property int index
                                title: modelData.title; path: modelData.path; idx: index
                            }
                            ShelfWheelRedirect { pane: browsePane }
                        }
                    }
                }
            }
        }

        // ---- Search results --------------------------------------------
        Flickable {
            id: results
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 8
            visible: !root.artistOpen && !root.settingsOpen && !root.libraryOpen && !root.browseOpen
            clip: true
            contentWidth: width; contentHeight: contentCol.height + 24
            ScrollBar.vertical: ScrollBar {}
            boundsBehavior: Flickable.StopAtBounds

            Column {
                id: contentCol
                x: 22; width: results.width - 44; spacing: 8

                Text {
                    id: emptyHint
                    visible: waves.loggedIn && !root.hasResults
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: "Search for an artist, album, or track to begin"
                    color: root.textLo; font.pixelSize: 22; topPadding: 96
                    // gentle breathing so the empty state feels alive
                    SequentialAnimation on opacity {
                        running: emptyHint.visible; loops: Animation.Infinite
                        NumberAnimation { from: 0.5; to: 1.0; duration: 1500; easing.type: Easing.InOutSine }
                        NumberAnimation { from: 1.0; to: 0.5; duration: 1500; easing.type: Easing.InOutSine }
                    }
                }

                // ARTISTS
                SectionHeader { visible: root.sectionVisible("artists", artistsModel.count); label: "ARTISTS"; count: artistsModel.count }
                Flow {
                    id: artistFlow
                    visible: root.sectionVisible("artists", artistsModel.count)
                    width: parent.width; spacing: 12
                    property int cols: Math.max(2, Math.floor((width + spacing) / (190 + spacing)))
                    property real cardW: (width - (cols - 1) * spacing) / cols
                    Repeater {
                        model: artistsModel
                        delegate: Rectangle {
                            required property var model
                            width: artistFlow.cardW
                            height: width + 142
                            radius: 12; color: root.surface; border.color: root.border1
                            Column {
                                anchors.fill: parent; anchors.margins: 8; spacing: 8
                                Item {
                                    width: parent.width; height: parent.width
                                    Art { anchors.centerIn: parent; width: Math.min(parent.width, parent.height); height: width; url: model.art }
                                }
                                Text { textFormat: Text.PlainText; text: model.name; color: root.textHi; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; width: parent.width; horizontalAlignment: Text.AlignHCenter }
                                PopMeter { anchors.horizontalCenter: parent.horizontalCenter; value: model.popularity }
                                // Preview + Download live in the card body (never overlaid on the
                                // photo); each has its own MouseArea that consumes the click so the
                                // card-wide open-artist MouseArea (z:-1, below) only fires elsewhere.
                                // Preview plays the artist's top track and doubles as a scrubber.
                                PreviewBar { width: parent.width; pid: model.id }
                                DownloadButton {
                                    width: parent.width
                                    mediaId: model.id; label: "Download artist"
                                    onTap: function(){ waves.downloadArtist(model.id) }
                                }
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; z: -1; onClicked: waves.loadArtist(model.id) }
                        }
                    }
                }

                // ALBUMS
                SectionHeader { visible: root.sectionVisible("albums", albumsModel.count); label: "ALBUMS"; count: albumsModel.count }
                Repeater {
                    model: albumsModel
                    delegate: AlbumBlock {
                        required property var model
                        visible: root.sectionVisible("albums", albumsModel.count)
                        width: contentCol.width
                        albumId: model.id; title: model.title; artistName: model.artist; artistId: model.artist_id
                        art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; quality: model.quality; popularity: model.popularity
                    }
                }

                // TRACKS
                SectionHeader { visible: root.sectionVisible("tracks", tracksModel.count); label: "TRACKS"; count: tracksModel.count }
                Repeater {
                    model: tracksModel
                    delegate: TrackRow {
                        required property var model
                        visible: root.sectionVisible("tracks", tracksModel.count)
                        width: contentCol.width
                        tId: model.id; title: model.title; artistName: model.artist; artistId: model.artist_id
                        album: model.album; art: model.art; year: model.year; date: model.date; duration: model.duration; quality: model.quality; popularity: model.popularity
                        albumId: model.album_id || ""
                    }
                }

                // VIDEOS
                SectionHeader { visible: root.sectionVisible("videos", videosModel.count); label: "VIDEOS"; count: videosModel.count }
                Repeater {
                    model: videosModel
                    delegate: Rectangle {
                        required property var model
                        visible: root.sectionVisible("videos", videosModel.count)
                        width: contentCol.width; height: 50; color: "transparent"
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.divider }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; spacing: 12
                            VideoThumb { url: model.art }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 1
                                Text { textFormat: Text.PlainText; text: model.title; color: root.textHi; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                                ArtistLinks { Layout.fillWidth: true; artists: root.artistsById[model.id] || [] }
                            }
                            Text { textFormat: Text.PlainText; text: model.duration; color: root.textLo; font.pixelSize: 12; Layout.preferredWidth: 42 }
                            QualTag { q: "VIDEO" }
                            DownIcon { mediaId: model.id; onTap: function(){ waves.downloadVideo(model.id) } }
                        }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; z: -1; onClicked: root.openVideo(model.id, model.title, model.artist) }
                    }
                }

                // PLAYLISTS
                SectionHeader { visible: root.sectionVisible("playlists", playlistsModel.count); label: "PLAYLISTS"; count: playlistsModel.count }
                Repeater {
                    model: playlistsModel
                    delegate: Rectangle {
                        required property var model
                        visible: root.sectionVisible("playlists", playlistsModel.count)
                        width: contentCol.width; height: 66; radius: 10; color: root.surface; border.color: root.border1
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 13
                            Art { width: 46; height: 46; url: model.art }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { textFormat: Text.PlainText; text: model.title; color: root.textHi; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText; text: (model.tracks > 0 ? model.tracks + " tracks" : "Playlist") + (model.creator ? "  ·  " + model.creator : ""); color: root.textLo; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            DownloadButton { mediaId: model.id; collectionCheck: true; label: "Download playlist"; onTap: function(){ waves.downloadPlaylist(model.id) } }
                        }
                    }
                }

                // MIXES
                SectionHeader { visible: root.sectionVisible("mixes", mixesModel.count); label: "MIXES"; count: mixesModel.count }
                Repeater {
                    model: mixesModel
                    delegate: Rectangle {
                        required property var model
                        visible: root.sectionVisible("mixes", mixesModel.count)
                        width: contentCol.width; height: 66; radius: 10; color: root.surface; border.color: root.border1
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 13
                            Art { width: 46; height: 46; url: model.art }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { textFormat: Text.PlainText; text: model.title; color: root.textHi; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText; text: model.subtitle ? model.subtitle : "Mix"; color: root.textLo; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            DownloadButton { mediaId: model.id; collectionCheck: true; label: "Download mix"; onTap: function(){ waves.downloadMix(model.id) } }
                        }
                    }
                }
            }
        }

        // ---- Artist page -----------------------------------------------
        ColumnLayout {
            id: artistPane
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 8
            visible: root.artistOpen && !root.settingsOpen && !root.libraryOpen
            spacing: 8

            // Sticky back bar, stays put while the page scrolls
            Item {
                Layout.fillWidth: true; Layout.leftMargin: 22; implicitHeight: 26
                Row {
                    id: backRow; spacing: 8; anchors.verticalCenter: parent.verticalCenter
                    Ico { name: "arrow-left"; color: root.textLo; size: 16; anchors.verticalCenter: parent.verticalCenter }
                    Text { textFormat: Text.PlainText; text: "Back to " + (root.navBackLabel() || "results"); color: root.textLo; font.pixelSize: 14 }
                }
                MouseArea { anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; width: backRow.width; height: parent.height; cursorShape: Qt.PointingHandCursor; onClicked: root.navBack() }
            }

            Flickable {
                id: artistView
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true
                contentWidth: width; contentHeight: artistCol.height + 24
                ScrollBar.vertical: ScrollBar {}
                boundsBehavior: Flickable.StopAtBounds

                Column {
                    id: artistCol
                    x: 22; width: artistView.width - 44; spacing: 12

                    // Artist header, bio sits to the right of the photo, capped
                    // to the photo height, with Read more for the rest.
                Row {
                    width: parent.width; spacing: 20
                    Column {
                        spacing: 10
                        Art { id: artistArt; width: 150; height: 150; url: root.artistData.art || "" }
                        // No idle Preview button on the artist's own page, but if a
                        // preview is already playing (e.g. started from a card), the
                        // scrubber still surfaces here so it stays controllable.
                        PreviewBar {
                            width: 150; pid: root.artistData.id || ""
                            visible: root.pvSt("artist", root.artistData.id || "") !== ""
                        }
                    }
                    Column {
                        width: parent.width - 170; spacing: 8
                        Text { text: "ARTIST"; color: root.accent; font.pixelSize: 12; font.bold: true; font.letterSpacing: 1.9; topPadding: 8 }
                        Text { textFormat: Text.PlainText; text: root.artistData.name || ""; color: root.textHi; font.pixelSize: 30; font.bold: true; width: parent.width; elide: Text.ElideRight }
                        Row {
                            spacing: 12
                            DownloadButton {
                                mediaId: root.artistData.id || ""
                                label: "Download discography"
                                onTap: function(){ waves.downloadArtist(root.artistData.id) }
                            }
                            // A library-scoped artist page (opened from My Tidal)
                            // shows only owned releases; offer a jump to the artist's
                            // full catalogue page. loadArtist() is the full path and
                            // onArtistLoaded swaps the view (Back returns here).
                            Text {
                                visible: !!root.artistData.libraryScoped
                                text: "View full artist page"
                                color: root.accent; font.pixelSize: 13; font.bold: true
                                anchors.verticalCenter: parent.verticalCenter
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.loadArtist(root.artistData.id || "") }
                            }
                            Text {
                                text: "Copy link"; color: root.textLo; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.copyShareUrl("artist", root.artistData.id || "") }
                            }
                        }
                        // Collapsed bio preview (never taller than the photo)
                        Text {
                            visible: (root.artistData.bio || "") !== "" && !root.bioExpanded
                            text: root.artistData.bio || ""
                            textFormat: Text.PlainText  // never interpret remote bio as rich text (no auto <img> fetch)
                            color: root.textLo; font.pixelSize: 13; width: parent.width
                            wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight
                        }
                        Text {
                            textFormat: Text.PlainText
                            visible: (root.artistData.bio || "") !== ""
                            text: root.bioExpanded ? "SHOW LESS" : "READ MORE"
                            color: root.accent; font.pixelSize: 12; font.letterSpacing: 0.8
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.bioExpanded = !root.bioExpanded }
                        }
                    }
                }
                // Full bio appears below the header only when expanded
                Text {
                    visible: (root.artistData.bio || "") !== "" && root.bioExpanded
                    text: root.artistData.bio || ""
                    textFormat: Text.PlainText  // never interpret remote bio as rich text (no auto <img> fetch)
                    color: root.textLo; font.pixelSize: 13; width: parent.width; wrapMode: Text.WordWrap; lineHeight: 1.3
                }

                // Top tracks: first 5 only, SHOW ALL reveals the rest for this
                // visit. Collapsed state persists across artist pages (prefs).
                SectionHeader {
                    visible: artistTracksModel.count > 0
                    label: "TOP TRACKS"; count: artistTracksModel.count
                    collapsible: true; collapsed: root.artistTracksCollapsed
                    onToggled: root.toggleArtistSection("tracks")
                }
                Repeater {
                    // null model while collapsed: no delegates exist at all,
                    // cheaper than count instances with visible: false.
                    model: root.artistTracksCollapsed ? null : artistTracksModel
                    delegate: TrackRow {
                        required property var model
                        required property int index
                        visible: index < 5 || root.topTracksExpanded
                        width: artistCol.width
                        tId: model.id; title: model.title; artistName: model.artist; artistId: ""
                        album: model.album; art: model.art; year: model.year; date: model.date; duration: model.duration; quality: model.quality; popularity: model.popularity
                        albumId: model.album_id || ""
                    }
                }
                Text {
                    textFormat: Text.PlainText
                    visible: !root.artistTracksCollapsed && artistTracksModel.count > 5
                    text: root.topTracksExpanded ? "SHOW LESS" : "SHOW ALL " + artistTracksModel.count
                    color: showAllMa.containsMouse ? root.accent : root.textDim
                    font.pixelSize: 12; font.bold: true; font.letterSpacing: 1.4
                    MouseArea {
                        id: showAllMa
                        anchors.fill: parent; anchors.margins: -4
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: root.topTracksExpanded = !root.topTracksExpanded
                    }
                }

                // Albums (expand inline)
                SectionHeader {
                    visible: artistAlbumsModel.count > 0
                    label: "ALBUMS"; count: artistAlbumsModel.count
                    collapsible: true; collapsed: root.artistAlbumsCollapsed
                    onToggled: root.toggleArtistSection("albums")
                }
                Repeater {
                    model: root.artistAlbumsCollapsed ? null : artistAlbumsModel
                    delegate: AlbumBlock {
                        required property var model
                        width: artistCol.width
                        albumId: model.id; title: model.title; artistName: model.artist; artistId: ""
                        art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; quality: model.quality; popularity: model.popularity
                    }
                }

                // EPs & singles
                SectionHeader {
                    visible: artistEpModel.count > 0
                    label: "EPS & SINGLES"; count: artistEpModel.count
                    collapsible: true; collapsed: root.artistEpsCollapsed
                    onToggled: root.toggleArtistSection("eps")
                }
                Repeater {
                    model: root.artistEpsCollapsed ? null : artistEpModel
                    delegate: AlbumBlock {
                        required property var model
                        width: artistCol.width
                        albumId: model.id; title: model.title; artistName: model.artist; artistId: ""
                        art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; quality: model.quality; popularity: model.popularity
                    }
                }
            }
            }
        }

        // ---- Settings page ---------------------------------------------
        SettingsPage {
            id: settingsPage
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 8
            visible: root.settingsOpen
            active: root.settingsOpen
            ff: appFfmpeg            // share the one app-wide FFmpeg manager
            onClosed: root.settingsOpen = false
        }

        // ---- Library page ----------------------------------------------
        ColumnLayout {
            id: libraryPane
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 8
            visible: root.libraryOpen
            spacing: 8

            // Top bar: title + category tabs on the left, sort inline on the
            // right, all one row (like Settings). No separate back header (you
            // arrive here from the nav) and no separate sort row underneath.
            RowLayout {
                Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22; Layout.topMargin: 2
                spacing: 14
                Text {
                    textFormat: Text.PlainText; text: "My Tidal"; color: root.textHi
                    font.pixelSize: 18; font.bold: true; Layout.alignment: Qt.AlignVCenter
                }
                // Wrap the tab strip in a plain Item that carries Layout.fillWidth,
                // and flow against a DEFINITE width (parent.width). A Flow with
                // Layout.fillWidth directly hits a stale-width feedback loop and
                // wraps spuriously on narrower (but still valid) window sizes,
                // leaving a dead band under the tabs. This is the pattern the other
                // Flows in this file use.
                Item {
                    Layout.fillWidth: true; Layout.alignment: Qt.AlignVCenter
                    implicitHeight: libTabsFlow.implicitHeight
                    Flow {
                        id: libTabsFlow
                        objectName: "libTabsFlow"
                        width: parent.width; spacing: 8
                        Repeater {
                            model: [["home", "Home"], ["albums", "Albums"], ["tracks", "Tracks"], ["artists", "Artists"], ["playlists", "Playlists"], ["mixes", "Mixes"], ["videos", "Videos"]]
                            delegate: Rectangle {
                                id: lchip
                                required property var modelData
                                readonly property bool on: root.libraryCategory === modelData[0]
                                radius: 8; implicitHeight: 30; implicitWidth: lcRow.implicitWidth + 26
                                color: on ? root.accentCont : "transparent"
                                border.color: on ? root.accentDim : root.border1
                                Row {
                                    id: lcRow; anchors.centerIn: parent; spacing: 7
                                    Rectangle { width: 6; height: 6; radius: 3; anchors.verticalCenter: parent.verticalCenter; color: lchip.on ? root.accent : root.textDim }
                                    Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter; text: lchip.modelData[1]; color: lchip.on ? root.accent : root.textLo; font.pixelSize: 13 }
                                }
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.loadLib(lchip.modelData[0]) }
                            }
                        }
                    }
                }
                // Sort (mirrors the Search sort); hidden on the Recent strip,
                // which is already newest-first and merged across kinds.
                ComboBox {
                    id: libSortBox
                    // Kept in the layout on Home too (opacity/enabled, not
                    // visible) so the header keeps the same height and the tabs
                    // keep the same position on every tab. Toggling `visible`
                    // here dropped ~40px and shifted the whole pane vertically
                    // when switching to or from Home.
                    opacity: root.libraryCategory === "home" ? 0 : 1
                    enabled: root.libraryCategory !== "home"
                    Layout.alignment: Qt.AlignVCenter
                    implicitHeight: 40; implicitWidth: 160
                    model: root.libSortLabels(root.libraryCategory)
                    // A Binding element (not an inline currentIndex) so the value
                    // survives the control's own imperative write on selection.
                    Binding {
                        target: libSortBox; property: "currentIndex"
                        value: root.libSortCurrentIndex(root.libraryCategory)
                        restoreMode: Binding.RestoreBindingOrValue
                    }
                    onActivated: {
                        var opts = root.libSortOptions(root.libraryCategory)
                        root.libApplySort(root.libraryCategory, opts[currentIndex][1], root.libSortGet(root.libraryCategory).asc)
                    }
                    background: Rectangle { radius: 8; color: root.surface2; border.color: libSortBox.popup.visible ? root.accent : root.outline }
                    contentItem: Text {
                        textFormat: Text.PlainText
                        text: libSortBox.displayText; color: root.textHi; font.pixelSize: 14
                        leftPadding: 14; rightPadding: 28; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                    }
                    indicator: ExpandChevron {
                        x: libSortBox.width - 26; y: (libSortBox.height - 18) / 2; tile: 18; glyph: 13
                        showTile: false; closedAngle: -90; openAngle: 0
                        stroke: root.libraryOpen ? root.accent : "transparent"
                        open: libSortBox.popup.visible
                    }
                    delegate: ItemDelegate {
                        width: libSortBox.width
                        contentItem: Text { textFormat: Text.PlainText; text: modelData; color: root.textHi; font.pixelSize: 14; verticalAlignment: Text.AlignVCenter }
                        background: Rectangle { color: highlighted ? root.surface3 : root.surface2 }
                        highlighted: libSortBox.highlightedIndex === index
                    }
                    popup: Popup {
                        y: libSortBox.height + 4; width: libSortBox.width; padding: 4
                        implicitHeight: contentItem.implicitHeight + 8
                        background: Rectangle { radius: 8; color: root.surface2; border.color: root.outline }
                        contentItem: ListView {
                            clip: true; implicitHeight: contentHeight
                            model: libSortBox.popup.visible ? libSortBox.delegateModel : null
                            ScrollBar.vertical: ScrollBar {}
                        }
                    }
                }
                Rectangle {
                    // Reserve its space on Home too, matching libSortBox above,
                    // so the header height and tab positions never shift.
                    opacity: root.libraryCategory === "home" ? 0 : 1
                    enabled: root.libraryCategory !== "home"
                    Layout.alignment: Qt.AlignVCenter
                    implicitHeight: 40; implicitWidth: 40; radius: 8
                    color: root.surface2; border.color: root.outline
                    Text {
                        textFormat: Text.PlainText
                        anchors.centerIn: parent; text: root.libSortGet(root.libraryCategory).asc ? "↑" : "↓"
                        color: root.textHi; font.family: root.mono; font.pixelSize: 18
                    }
                    MouseArea {
                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: { var g = root.libSortGet(root.libraryCategory); root.libApplySort(root.libraryCategory, g.key, !g.asc) }
                    }
                }
            }

            Item {
                id: libArea
                Layout.fillWidth: true; Layout.fillHeight: true

                LibList {
                    cat: "albums"; model: libAlbumsModel
                    delegate: AlbumBlock {
                        required property var model
                        width: ListView.view.width
                        albumId: model.id; title: model.title; artistName: model.artist; artistId: ""
                        art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; quality: model.quality; popularity: model.popularity
                    }
                }
                LibList {
                    cat: "tracks"; model: libTracksModel
                    delegate: TrackRow {
                        required property var model
                        width: ListView.view.width
                        tId: model.id; title: model.title; artistName: model.artist; artistId: model.artist_id
                        album: model.album; art: model.art; year: model.year; date: model.date; duration: model.duration; quality: model.quality; popularity: model.popularity
                        albumId: model.album_id || ""
                    }
                }
                // Artists as a compact card grid (the Search artist card, shrunk)
                // rather than tall full-width rows, so more fit on screen. Click
                // opens the artist scoped to the user's library.
                GridView {
                    id: libArtistsGrid
                    visible: root.libraryCategory === "artists"
                    anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22
                    clip: true
                    model: libArtistsModel
                    property int cols: Math.max(3, Math.floor(width / 132))
                    cellWidth: width > 0 ? Math.floor(width / cols) : 132
                    // + name row + the preview row pinned to the card bottom
                    cellHeight: cellWidth + 46
                    cacheBuffer: 800
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}
                    onContentYChanged: root.libMaybeLoadMore(libArtistsGrid, "artists")
                    onContentHeightChanged: root.libMaybeLoadMore(libArtistsGrid, "artists")
                    onHeightChanged: root.libMaybeLoadMore(libArtistsGrid, "artists")
                    delegate: Item {
                        required property var model
                        width: libArtistsGrid.cellWidth; height: libArtistsGrid.cellHeight
                        Rectangle {
                            anchors.fill: parent; anchors.margins: 5; radius: 10
                            color: agMa.containsMouse ? root.surface2 : root.surface; border.color: root.border1
                            Column {
                                anchors.fill: parent; anchors.margins: 8; spacing: 6
                                Item {
                                    width: parent.width; height: width
                                    Art { anchors.centerIn: parent; width: parent.width; height: width; url: model.art }
                                }
                                Text {
                                    textFormat: Text.PlainText; text: model.name
                                    color: root.textHi; font.pixelSize: 12; font.bold: true
                                    elide: Text.ElideRight; width: parent.width; horizontalAlignment: Text.AlignHCenter
                                }
                            }
                            MouseArea { id: agMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: waves.loadArtistLibrary(model.id) }
                            // ---- compact preview row (the Browse card's control
                            // line, shrunk): ▶ PREVIEW -> elapsed + · STOP, playing
                            // this artist's top track via the shared preview
                            // machinery. Declared after agMa so its clicks win
                            // over the open-artist click underneath.
                            Item {
                                id: agPv
                                readonly property string pst: root.pvSt("artist", "" + model.id)
                                anchors.horizontalCenter: parent.horizontalCenter
                                anchors.bottom: parent.bottom; anchors.bottomMargin: 8
                                width: agPvRow.implicitWidth; height: 16
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                    onClicked: root.togglePreview("artist", "" + model.id, 0)
                                }
                                Row {
                                    id: agPvRow
                                    anchors.verticalCenter: parent.verticalCenter; spacing: 4
                                    Ico {
                                        visible: agPv.pst !== "loading"
                                        name: agPv.pst === "playing" ? "pause" : "play"
                                        color: agPv.pst === "error" ? root.red : root.accent
                                        size: 10
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    Text {
                                        textFormat: Text.PlainText
                                        text: agPv.pst === "" ? "PREVIEW"
                                            : agPv.pst === "loading" ? "[buffering]"
                                            : agPv.pst === "error" ? "RETRY"
                                            : root.fmtMs(root.previewPosition)
                                        color: agPv.pst === "error" ? root.red : root.accent
                                        font.family: agPv.pst === "playing" || agPv.pst === "paused" || agPv.pst === "loading" ? root.mono : root.uiFont
                                        font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack
                                        anchors.verticalCenter: parent.verticalCenter
                                        property real breathe: 1
                                        opacity: agPv.pst === "loading" ? breathe : 1
                                        SequentialAnimation on breathe {
                                            running: agPv.pst === "loading"; loops: Animation.Infinite
                                            NumberAnimation { from: 1.0; to: 0.3; duration: 520; easing.type: Easing.InOutSine }
                                            NumberAnimation { from: 0.3; to: 1.0; duration: 520; easing.type: Easing.InOutSine }
                                        }
                                    }
                                    Text {
                                        textFormat: Text.PlainText
                                        visible: agPv.pst === "playing" || agPv.pst === "paused"
                                        text: "· STOP"
                                        color: agStopMa.containsMouse ? root.red : root.textDim
                                        font.family: root.uiFont; font.pixelSize: 9; font.bold: true; font.letterSpacing: root.btnTrack
                                        anchors.verticalCenter: parent.verticalCenter
                                        MouseArea {
                                            id: agStopMa
                                            anchors.fill: parent; anchors.margins: -3
                                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                            onClicked: root.stopPreview()
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                // Home: a Browse-shaped landing scoped to the account. Rendered
                // from homeSections (same shelf shape as Browse), so the app's own
                // art-forward ArtCard / TrackRow shelves render it. Under a
                // "Recently added" header sit two preview shelves, "Recent albums"
                // and "Recent tracks"; each heading drills into that full tab,
                // newest-first (openLibrarySorted).
                Flickable {
                    id: homePane
                    visible: root.libraryCategory === "home"
                    anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22
                    clip: true
                    contentWidth: width; contentHeight: homeCol.height + 24
                    ScrollBar.vertical: ScrollBar {}
                    boundsBehavior: Flickable.StopAtBounds
                    Column {
                        id: homeCol
                        // No topPadding: the favourites lists start flush at the
                        // top of the pane, so Home must too, or the content jumps
                        // vertically when you switch between them.
                        width: homePane.width; spacing: 20
                        Text {
                            visible: root.homeSections.length > 0
                            textFormat: Text.PlainText; text: "Recently added"
                            color: root.textHi; font.pixelSize: 20; font.bold: true
                        }
                        Repeater {
                            model: root.homeSections
                            delegate: Column {
                                id: homeSec
                                required property var modelData
                                readonly property string target: homeSec.modelData.target || ""
                                width: homeCol.width; spacing: 10
                                // Clickable shelf heading: drills into the matching
                                // My Tidal tab, newest-first, showing the full list
                                // this shelf previews. The hit area hugs the text.
                                Item {
                                    implicitWidth: headRow.implicitWidth
                                    implicitHeight: headRow.implicitHeight
                                    Row {
                                        id: headRow; spacing: 6
                                        Text {
                                            id: headText
                                            textFormat: Text.PlainText
                                            text: homeSec.modelData.title || ""
                                            color: (headMouse.containsMouse && homeSec.target !== "") ? root.accent : root.textHi
                                            font.pixelSize: 16; font.bold: true
                                        }
                                        Text {
                                            visible: homeSec.target !== ""
                                            anchors.verticalCenter: headText.verticalCenter
                                            textFormat: Text.PlainText; text: "›"
                                            color: headMouse.containsMouse ? root.accent : root.textLo
                                            font.pixelSize: 18
                                        }
                                    }
                                    MouseArea {
                                        id: headMouse
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        enabled: homeSec.target !== ""
                                        cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                                        onClicked: root.openLibrarySorted(homeSec.target)
                                    }
                                }
                                // Card shelf (Recent albums preview).
                                ListView {
                                    visible: homeSec.modelData.rowKind === "cards"
                                    width: parent.width; height: 250
                                    orientation: ListView.Horizontal
                                    spacing: 14; clip: true
                                    boundsBehavior: Flickable.StopAtBounds
                                    model: visible ? homeSec.modelData.items : []
                                    delegate: ArtCard {
                                        required property var modelData
                                        card: modelData
                                    }
                                    ShelfWheelRedirect { pane: homePane }
                                }
                                // Recent tracks (vertical list, reuses TrackRow).
                                Column {
                                    visible: homeSec.modelData.rowKind === "tracks"
                                    width: parent.width
                                    Repeater {
                                        model: homeSec.modelData.rowKind === "tracks" ? homeSec.modelData.items : []
                                        delegate: TrackRow {
                                            required property var modelData
                                            width: homeCol.width
                                            tId: modelData.id; kind: modelData.kind || "track"
                                            title: modelData.title; artistName: modelData.artist || ""; artistId: modelData.artist_id || ""
                                            album: modelData.album || ""; art: modelData.art || ""; year: "" + (modelData.year || ""); date: modelData.date || ""
                                            duration: modelData.duration || ""; quality: modelData.quality || ""; popularity: modelData.popularity || 0
                                            albumId: modelData.album_id || ""
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                LibList {
                    cat: "playlists"; model: libPlaylistsModel
                    delegate: Rectangle {
                        required property var model
                        width: ListView.view.width; height: 64; radius: 10; color: root.surface; border.color: root.border1
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 13
                            Art { width: 44; height: 44; url: model.art }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { textFormat: Text.PlainText; text: model.title; color: root.textHi; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText; text: (model.tracks > 0 ? model.tracks + " tracks" : "Playlist"); color: root.textLo; font.pixelSize: 12 }
                            }
                            DownloadButton { mediaId: model.id; collectionCheck: true; label: "Download playlist"; onTap: function(){ waves.downloadPlaylist(model.id) } }
                        }
                    }
                }
                LibList {
                    cat: "mixes"; model: libMixesModel
                    delegate: Rectangle {
                        required property var model
                        width: ListView.view.width; height: 64; radius: 10; color: root.surface; border.color: root.border1
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 13
                            Art { width: 44; height: 44; url: model.art }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { textFormat: Text.PlainText; text: model.title; color: root.textHi; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText; text: model.subtitle ? model.subtitle : "Mix"; color: root.textLo; font.pixelSize: 12 }
                            }
                            DownloadButton { mediaId: model.id; collectionCheck: true; label: "Download mix"; onTap: function(){ waves.downloadMix(model.id) } }
                        }
                    }
                }
                LibList {
                    cat: "videos"; model: libVideosModel
                    delegate: Rectangle {
                        required property var model
                        width: ListView.view.width; height: 50; color: "transparent"
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.divider }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; spacing: 12
                            VideoThumb { url: model.art }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 1
                                Text { textFormat: Text.PlainText; text: model.title; color: root.textHi; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText; text: model.artist; color: root.textLo; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                            }
                            Text { textFormat: Text.PlainText; text: model.duration; color: root.textLo; font.pixelSize: 12; Layout.preferredWidth: 42 }
                            DownIcon { mediaId: model.id; onTap: function(){ waves.downloadVideo(model.id) } }
                        }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; z: -1; onClicked: root.openVideo(model.id, model.title, model.artist) }
                    }
                }

                // No placeholder while a category loads or when it is empty. The
                // pane is transparent, so the ambient wave-loop background fills it
                // on its own; a loading or empty category simply shows the moving
                // water, never a card or glyph that flashes in for a beat and fades
                // out. (The waves are the brand presence here, so nothing else needs
                // to stand in.)
            }
        }

        // ---- Status bar -------------------------------------------------
        Rectangle {
            id: statusBar
            Layout.fillWidth: true; implicitHeight: 28; color: root.surface0
            Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: root.border1 }
            // Browse layout switch (art-first vs console), floating over the
            // pane's bottom-right corner so it costs the landing page no row.
            Rectangle {
                visible: waves.loggedIn && root.browseOpen && !root.artistOpen && !root.settingsOpen && !root.libraryOpen && root.browsePageKey === ""
                anchors.right: parent.right; anchors.bottom: parent.top
                anchors.rightMargin: 22; anchors.bottomMargin: 14
                radius: 14; implicitHeight: 28; implicitWidth: styleSeg.implicitWidth + 10
                color: "#e6101418"; border.color: root.border1
                Row {
                    id: styleSeg; anchors.centerIn: parent; spacing: 4
                    Repeater {
                        model: [["art", "ART"], ["console", "CONSOLE"]]
                        delegate: Rectangle {
                            id: schip
                            required property var modelData
                            readonly property bool on: root.browseStyle === modelData[0]
                            radius: 10; implicitHeight: 20; implicitWidth: scText.implicitWidth + 16
                            color: on ? root.accentCont : "transparent"
                            Text {
                                id: scText; textFormat: Text.PlainText; anchors.centerIn: parent
                                text: schip.modelData[1]
                                color: schip.on ? root.accent : (scMa.containsMouse ? root.textLo : root.textDim)
                                font.pixelSize: 10; font.family: root.uiFont; font.bold: true
                                font.letterSpacing: root.btnTrack
                            }
                            MouseArea { id: scMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.setBrowseStyle(schip.modelData[0]) }
                        }
                    }
                }
            }
            RowLayout {
                id: statusRow
                anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22; spacing: 10
                // pulsing LED, bright when busy, dim otherwise
                Rectangle {
                    width: 7; height: 7; radius: 3.5; color: root.accent
                    opacity: waves.busy ? 1 : 0.3
                    SequentialAnimation on opacity {
                        running: waves.busy; loops: Animation.Infinite
                        NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
                        NumberAnimation { to: 1.0; duration: 700; easing.type: Easing.InOutSine }
                    }
                }
                Text {
                    id: statusText; textFormat: Text.PlainText; text: waves.status; color: root.textLo; font.family: root.mono; font.pixelSize: 11
                    // While the mini player is up, elide before its left edge so a
                    // long status line never runs underneath the centered player.
                    elide: Text.ElideRight
                    Layout.maximumWidth: root.previewKind !== "" ? Math.max(60, nowPlaying.x - 51) : statusBar.width - 260
                }
                Item { Layout.fillWidth: true }
                // Update notice: the right slot goes gold when a newer release is
                // waiting. Full line when the bar is idle; compacts to LED +
                // version while the mini player is up so the centre stays clear
                // (npInfo's budget subtracts this slot either way). Click opens
                // Settings, where the updater card carries the install button.
                RowLayout {
                    id: statusUpdate
                    visible: root.appUpdAvailable
                    spacing: 7
                    Rectangle {
                        width: 7; height: 7; radius: 3.5; color: root.gold
                        SequentialAnimation on opacity {
                            running: statusUpdate.visible; loops: Animation.Infinite
                            NumberAnimation { to: 0.35; duration: 700; easing.type: Easing.InOutSine }
                            NumberAnimation { to: 1.0; duration: 700; easing.type: Easing.InOutSine }
                        }
                    }
                    Text {
                        textFormat: Text.PlainText
                        text: root.previewKind !== "" ? "v" + root.appUpdLatest
                                                      : "UPDATE · v" + root.appUpdLatest + " AVAILABLE"
                        color: statusUpdMa.containsMouse ? root.goldContTx : root.gold
                        font.family: root.mono; font.pixelSize: 11; font.letterSpacing: 0.5
                        MouseArea {
                            id: statusUpdMa
                            anchors.fill: parent; anchors.margins: -4
                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            // Drop the user straight onto the Updates card
                            // (already auto-expanded while an update waits);
                            // callLater so the jump lands after the page's
                            // onActiveChanged refresh has run.
                            onClicked: {
                                root.navPush(); root.markNav("settings")
                                root.settingsOpen = true; root.artistOpen = false; root.libraryOpen = false
                                Qt.callLater(function() { settingsPage.jumpToCard("updates") })
                            }
                        }
                    }
                }
                // Decorative, yield the right side to the now-playing bar when it
                // shows -- and to the update notice, which outranks a wordmark.
                Text {
                    visible: !root.appUpdAvailable
                    text: "TIDAL · WAVES CONSOLE"; color: root.textDim; font.family: root.mono; font.pixelSize: 11; font.letterSpacing: 0.5
                    opacity: root.previewKind !== "" ? 0 : 1
                    Behavior on opacity { NumberAnimation { duration: 180 } }
                }
            }
            // Now playing, centered, persists across every view so playback can
            // always be paused/stopped, and clicking the title/artist jumps back to
            // the artist page (and expands the track's album). One shared player.
            // Hovering anywhere on the bottom bar (not just the ✕) reveals the
            // mini player's "[stop]" label while something is playing.
            HoverHandler { id: npBarHover }
            Row {
                id: nowPlaying
                anchors.centerIn: parent
                spacing: 9
                visible: root.previewKind !== ""
                // Fixed box so swapping > / || / … never nudges the art + text. The
                // play caret is drawn larger so it stands at the pause bars' height.
                Item {
                    anchors.verticalCenter: parent.verticalCenter
                    // Fixed 20px normally; widens to fit the transient "[buffering]"
                    // label, then settles back once the stream starts.
                    width: root.previewLoading ? npGlyph.implicitWidth : 20; height: 20
                    Text {
                        id: npGlyph
                        anchors.centerIn: parent
                        textFormat: Text.PlainText
                        text: root.previewLoading ? "[buffering]" : (root.previewPlaying ? "||" : ">")
                        color: root.accent; font.family: root.mono; font.bold: true
                        font.pixelSize: root.previewLoading ? 10 : (root.previewPlaying ? 13 : 18)
                        property real breathe: 1
                        opacity: root.previewLoading ? breathe : 1
                        SequentialAnimation on breathe {
                            running: root.previewLoading; loops: Animation.Infinite
                            NumberAnimation { from: 1.0; to: 0.3; duration: 520; easing.type: Easing.InOutSine }
                            NumberAnimation { from: 0.3; to: 1.0; duration: 520; easing.type: Easing.InOutSine }
                        }
                    }
                    MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: root.nowToggle() }
                }
                Art { anchors.verticalCenter: parent.verticalCenter; width: 18; height: 18; url: root.previewNowArt }
                Item {
                    id: npInfo
                    anchors.verticalCenter: parent.verticalCenter
                    implicitWidth: npInfoRow.implicitWidth; implicitHeight: npInfoRow.implicitHeight
                    // Text budget. The bar is centered in the status Rectangle;
                    // the right "console" caption is hidden while playing but the
                    // update notice (statusUpdate) may hold that corner, so the
                    // row is bounded by the wider of the two sides, then loses
                    // the fixed controls (play +
                    // art + stop + row gaps). Text shows in full when there's room
                    // and elides only when the window is genuinely tight.
                    // Cap the status text's claim at ~a fifth of the bar; it elides
                    // to fit (see statusText), so a long status line no longer
                    // floors the budget and crushes the artist for no reason.
                    readonly property real leftGuard: 22 + 7 + 10 + Math.min(statusText.implicitWidth, statusBar.width * 0.22) + 24
                    // The right slot is no longer guaranteed empty while playing:
                    // the update notice stays up (compacted). Guard on whichever
                    // side claims more so the centred row stays clear of both.
                    readonly property real rightGuard: 22 + (statusUpdate.visible ? statusUpdate.implicitWidth + 24 : 0)
                    readonly property real fixedParts: 20 + 18 + stopMetrics.width + 27
                    readonly property real budget: Math.max(120, statusBar.width - 2 * Math.max(leftGuard, rightGuard) - fixedParts)
                    readonly property real sepW: npSep.implicitWidth + 12
                    readonly property real avail: Math.max(60, budget - sepW)
                    readonly property bool bothFit: (npTitle.implicitWidth + npArtist.implicitWidth) <= avail
                    // When cramped, cap the artist to ~40% so the track title (the
                    // thing you're more likely to want) keeps the larger share.
                    readonly property real artistW: bothFit ? npArtist.implicitWidth : Math.min(npArtist.implicitWidth, avail * 0.42)
                    readonly property real titleW: bothFit ? npTitle.implicitWidth : Math.max(48, avail - artistW)
                    Row {
                        id: npInfoRow
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 6
                        // Track name → its album page, track highlighted (fade).
                        Text {
                            id: npTitle
                            anchors.verticalCenter: parent.verticalCenter
                            textFormat: Text.PlainText; text: root.previewNowTitle
                            color: npTitleMa.containsMouse && root.previewNowAlbumId !== "" ? "#ffffff" : root.textHi
                            font.family: root.mono; font.pixelSize: 11
                            elide: Text.ElideRight; width: Math.min(implicitWidth, npInfo.titleW)
                            font.underline: npTitleMa.containsMouse && root.previewNowAlbumId !== ""
                            MouseArea {
                                id: npTitleMa
                                anchors.fill: parent; anchors.topMargin: -6; anchors.bottomMargin: -6
                                hoverEnabled: true
                                cursorShape: root.previewNowAlbumId !== "" ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: root.nowOpenAlbum()
                            }
                        }
                        Text { id: npSep; anchors.verticalCenter: parent.verticalCenter; textFormat: Text.PlainText; text: "·"; color: root.textDim; font.pixelSize: 11 }
                        // Artist credits, each collaborator its own clickable
                        // name → that artist's page. Clipped (not per-name elided)
                        // when the bar is tight; the whole line still shows in
                        // full whenever there's room (see npInfo's budget).
                        Item {
                            id: npArtist
                            anchors.verticalCenter: parent.verticalCenter
                            readonly property var list: (root.previewNowArtists && root.previewNowArtists.length > 0)
                                ? root.previewNowArtists
                                : (root.previewNowArtist !== "" ? [{ name: root.previewNowArtist, id: root.previewNowArtistId }] : [])
                            implicitWidth: npArtistsRow.implicitWidth; implicitHeight: npArtistsRow.implicitHeight
                            width: Math.min(implicitWidth, npInfo.artistW); clip: true
                            Row {
                                id: npArtistsRow
                                anchors.verticalCenter: parent.verticalCenter
                                Repeater {
                                    model: npArtist.list
                                    delegate: Row {
                                        required property var modelData
                                        required property int index
                                        Text {
                                            id: npArtName
                                            readonly property bool linkable: !!modelData.id && modelData.id !== "" && !root.onArtistPage(modelData.id)
                                            anchors.verticalCenter: parent.verticalCenter
                                            textFormat: Text.PlainText; text: modelData.name
                                            color: npArtMa.containsMouse && linkable ? "#ffffff" : root.accent
                                            font.family: root.mono; font.pixelSize: 11
                                            font.underline: npArtMa.containsMouse && linkable
                                            MouseArea {
                                                id: npArtMa
                                                anchors.fill: parent; anchors.topMargin: -6; anchors.bottomMargin: -6
                                                hoverEnabled: true
                                                cursorShape: npArtName.linkable ? Qt.PointingHandCursor : Qt.ArrowCursor
                                                onClicked: if (npArtName.linkable) waves.loadArtist(modelData.id)
                                            }
                                        }
                                        Text {
                                            visible: index < npArtist.list.length - 1
                                            anchors.verticalCenter: parent.verticalCenter
                                            textFormat: Text.PlainText; text: ", "
                                            color: root.textDim; font.family: root.mono; font.pixelSize: 11
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                // Red at rest; reveals "[stop]" on hover. The box reserves the wider
                // "[stop]" width so the hover swap never reflows the now-playing text.
                Item {
                    anchors.verticalCenter: parent.verticalCenter
                    height: 20; width: stopMetrics.width
                    TextMetrics { id: stopMetrics; font.family: root.mono; font.pixelSize: 11; font.bold: true; text: "[stop]" }
                    Text {
                        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                        textFormat: Text.PlainText
                        text: (npBarHover.hovered || npStopMa.containsMouse) ? "[stop]" : "✕"
                        color: root.red; font.family: root.mono; font.bold: true
                        font.pixelSize: (npBarHover.hovered || npStopMa.containsMouse) ? 11 : 12
                    }
                    MouseArea { id: npStopMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.nowStop() }
                }
            }
            // Thin playback position line along the very bottom edge of the window.
            Rectangle {
                anchors.bottom: parent.bottom; anchors.left: parent.left
                height: 2; color: root.accent
                visible: root.previewKind !== "" && root.previewDuration > 0
                width: parent.width * root.pvFrac(root.previewKind, root.previewId)
            }
        }
    }

    // Clicking anywhere outside the search field while it is focused releases
    // focus (caret stops blinking, accent outline fades back); the press is
    // declined so the underlying control still receives the click.
    // It MUST be `visible:`-gated, not just `enabled:`-gated: a full-window
    // MouseArea sits topmost (z:1000) and, even while disabled, carries the
    // default Arrow cursor, which overrides every button's pointing-hand
    // cursor across the whole app. Hiding it when the field isn't focused
    // removes it from cursor resolution entirely.
    MouseArea {
        anchors.fill: parent; z: 1000
        visible: searchField.activeFocus
        enabled: searchField.activeFocus
        acceptedButtons: Qt.AllButtons
        onPressed: function (mouse) {
            var p = mapToItem(searchBox, mouse.x, mouse.y)
            if (p.x < 0 || p.y < 0 || p.x > searchBox.width || p.y > searchBox.height)
                searchField.focus = false
            mouse.accepted = false
        }
    }

    // Sort the original full search data (not a lossy model copy) so every
    // field, including the full date, survives re-sorting.
    function applySort() {
        var arr = (root.albumsRaw || []).slice()
        var dir = root.sortAsc ? 1 : -1
        if (sortBox.currentIndex === 1) arr.sort(function(a, b){ return dir * ((a.date || a.year || "").localeCompare(b.date || b.year || "")) })
        else if (sortBox.currentIndex === 2) arr.sort(function(a, b){ return dir * a.title.localeCompare(b.title) })
        else arr.sort(function(a, b){ return dir * ((a.popularity || 0) - (b.popularity || 0)) })  // Relevance = popularity
        root.fillMedia(albumsModel, arr)
    }

    // ====================================================================
    // Queue drawer
    // ====================================================================
    Drawer {
        id: queueDrawer
        edge: Qt.RightEdge; width: 340; height: root.height
        background: Rectangle { color: root.surface; border.color: root.line1 }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 16; spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Text { text: "Download queue"; color: root.textHi; font.pixelSize: 16; font.bold: true }
                Item { Layout.fillWidth: true }
                // Pause / resume all
                Rectangle {
                    visible: queueModel.count > 0
                    implicitHeight: prLbl.implicitHeight + root.btnPadV * 2; implicitWidth: prLbl.implicitWidth + root.btnPadH * 2; radius: root.btnRad
                    color: waves.paused ? root.accentCont : "transparent"; border.color: waves.paused ? root.accent : root.border1
                    Text { textFormat: Text.PlainText; id: prLbl; anchors.centerIn: parent; text: waves.paused ? "RESUME" : "PAUSE"; color: waves.paused ? root.accent : root.textLo; font.pixelSize: 12; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.paused ? waves.resumeQueue() : waves.pauseQueue() }
                }
                // Stop everything, abort running downloads + clear the queue
                Rectangle {
                    visible: root.activeQueueCount > 0
                    implicitHeight: stopLbl.implicitHeight + root.btnPadV * 2; implicitWidth: stopLbl.implicitWidth + root.btnPadH * 2; radius: root.btnRad
                    color: "transparent"; border.color: root.red
                    Text { id: stopLbl; anchors.centerIn: parent; text: "STOP"; color: root.red; font.pixelSize: 12; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.stopAll() }
                }
            }
            Text { textFormat: Text.PlainText; text: queueModel.count + " items"; color: root.textLo; font.family: root.mono; font.pixelSize: 12 }
            ListView {
                id: queueList
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 0
                model: queueModel; ScrollBar.vertical: ScrollBar {}

                // Grouped sections: Completed (collapsible) · Downloading · Queued
                section.property: "uiGroup"
                section.criteria: ViewSection.FullString
                section.delegate: Item {
                    id: secItem
                    required property string section
                    width: ListView.view.width
                    implicitHeight: 32
                    RowLayout {
                        anchors.fill: parent; anchors.topMargin: 9; anchors.bottomMargin: 4
                        anchors.leftMargin: 2; anchors.rightMargin: 2; spacing: 8
                        Ico { visible: secItem.section === "completed"; name: "check"; color: root.accent; size: 13; Layout.alignment: Qt.AlignVCenter }
                        Text {
                            textFormat: Text.PlainText
                            id: secLbl
                            text: secItem.section === "completed" ? "COMPLETED · " + root.completedCount
                                : secItem.section === "downloading" ? "DOWNLOADING · " + root.downloadingCount
                                : "QUEUED · " + root.queuedCount
                            color: secItem.section === "completed" ? root.accentContTx : root.textDim
                            font.family: root.mono; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1.4
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Rectangle { Layout.fillWidth: true; Layout.alignment: Qt.AlignVCenter; height: 1; color: root.divider }
                        Text { textFormat: Text.PlainText; visible: secItem.section === "completed"; text: root.completedCollapsed ? "▸" : "▾"; color: root.accent; font.pixelSize: 11; Layout.alignment: Qt.AlignVCenter }
                    }
                    MouseArea { anchors.fill: parent; enabled: secItem.section === "completed"; cursorShape: Qt.PointingHandCursor
                        onClicked: root.completedCollapsed = !root.completedCollapsed }
                    Connections { target: root; function onCompBumpChanged() { if (secItem.section === "completed") secPulse.restart() } }
                    SequentialAnimation {
                        id: secPulse
                        NumberAnimation { target: secLbl; property: "scale"; to: 1.25; duration: 150; easing.type: Easing.OutCubic }
                        NumberAnimation { target: secLbl; property: "scale"; to: 1.0; duration: 180; easing.type: Easing.OutCubic }
                    }
                }

                delegate: Item {
                    id: qrow
                    required property var model
                    readonly property string st: model.status
                    readonly property bool isComp: model.uiGroup === "completed"
                    readonly property bool collapsed: isComp && root.completedCollapsed
                    readonly property bool lingering: st === "done" && !model.moved
                    // Album rows expand in place to an ordered per-track list
                    // (live status/progress). Expansion state lives on root
                    // (keyed by qid) so it survives delegate recycling.
                    readonly property bool expandable: model.type === "album" && model.collection
                    readonly property bool qexp: expandable && root.queueExpanded[model.qid] === true
                    // Hover peek: a collapsed album card dips open ~30px so the
                    // track view's existence is discoverable without a click.
                    readonly property bool peeking: expandable && !qexp && cardHover.containsMouse
                    function qtoggle() {
                        var e = Object.assign({}, root.queueExpanded)
                        if (e[model.qid]) { delete e[model.qid] }
                        else { e[model.qid] = true; waves.loadQueueTracks(model.qid) }
                        root.queueExpanded = e
                    }
                    // Driven by the root lingerClock via the model, so the
                    // fold works with the drawer closed (no delegate) too.
                    readonly property bool leaving: model.leaving === true
                    width: ListView.view.width
                    property real bodyH: actCol.implicitHeight + 18
                    height: (collapsed || leaving) ? 0 : bodyH + 8
                    opacity: leaving ? 0 : 1
                    clip: true
                    // While the peek/expand animation drives the inner list height,
                    // this outer Behavior must idle, otherwise it re-targets every
                    // frame, chasing the moving bodyH, and the motion turns mushy.
                    Behavior on height { enabled: !qtrackAnim.running; NumberAnimation { duration: 280; easing.type: Easing.OutCubic } }
                    Behavior on opacity { NumberAnimation { duration: 240 } }

                    // Finish flow: ✓ DONE chip pops, then the row collapses +
                    // fades in place and is moved into Completed. The timing
                    // (doneAt + 5s) lives on the model row and is driven by
                    // the root lingerClock, not here: a delegate only exists
                    // while the drawer shows it, so per-row timers meant a
                    // closed drawer never folded anything.
                    onStChanged: if (qrow.lingering) chipPop.restart()

                    // ---- queue card (Completed rows use the same card as
                    // Downloading/Queued, art thumb, caret, hover peek and
                    // expand included, just in the quieter completed palette) ----
                    Rectangle {
                        id: activeRect
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        height: actCol.implicitHeight + 18
                        radius: 8; color: qrow.isComp ? root.surface : root.surface2
                        border.color: qrow.isComp ? (cardHover.containsMouse && qrow.expandable ? root.outline : root.line1)
                                      : qrow.st === "done" ? root.greenDim
                                      : cardHover.containsMouse ? root.outline : root.border1
                        clip: true
                        Behavior on border.color { ColorAnimation { duration: 120 } }
                        // Card-wide expand toggle for album rows; declared first so
                        // the retry/cancel MouseAreas (later siblings) stay on top.
                        MouseArea {
                            id: cardHover
                            anchors.fill: parent
                            enabled: qrow.expandable
                            hoverEnabled: qrow.expandable
                            cursorShape: qrow.expandable ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: qrow.qtoggle()
                            // Fetch the track list as soon as the peek starts so the
                            // sliver shows real titles, not just "Loading tracks…".
                            onContainsMouseChanged: {
                                if (containsMouse && qrow.expandable && !qrow.qexp && !(root.queueTracks[model.qid]))
                                    waves.loadQueueTracks(model.qid)
                            }
                        }
                        ColumnLayout {
                            id: actCol
                            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                            anchors.leftMargin: 12; anchors.rightMargin: 10; anchors.topMargin: 9; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true; spacing: 11
                                // Cover thumb with the status dot riding its corner;
                                // falls back to the bare dot when there's no art.
                                Item {
                                    id: qthumb
                                    readonly property bool hasArt: (model.art || "") !== ""
                                    Layout.alignment: Qt.AlignVCenter
                                    implicitWidth: hasArt ? 34 : 8; implicitHeight: hasArt ? 34 : 8
                                    Art { anchors.fill: parent; url: model.art || ""; visible: qthumb.hasArt }
                                    Rectangle {
                                        width: qthumb.hasArt ? 11 : 8; height: width; radius: width / 2
                                        color: root.statusColor(qrow.st)
                                        border.color: root.surface2; border.width: qthumb.hasArt ? 2 : 0
                                        anchors.right: parent.right; anchors.bottom: parent.bottom
                                        anchors.rightMargin: qthumb.hasArt ? -3 : 0
                                        anchors.bottomMargin: qthumb.hasArt ? -3 : 0
                                    }
                                }
                                Text {
                                    visible: qrow.expandable
                                    text: "›"; rotation: qrow.qexp ? 90 : 0
                                    color: (qrow.qexp || cardHover.containsMouse) ? root.accent : root.textDim; font.pixelSize: 13
                                    Layout.alignment: Qt.AlignVCenter; Layout.leftMargin: -5; Layout.rightMargin: -4
                                    Behavior on rotation { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                                    Behavior on color { ColorAnimation { duration: 120 } }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; Layout.alignment: Qt.AlignVCenter; spacing: 1
                                    Text { textFormat: Text.PlainText; text: model.name; color: root.textHi; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text {
                                        textFormat: Text.PlainText  // composed from a remote artist name
                                        text: {
                                            var a = model.artist ? model.artist + " · " : ""
                                            if (qrow.st === "queued") return a + "Queued"
                                            if (qrow.st === "failed") return a + "Failed"
                                            if (model.collection && model.tracks > 0)
                                                // Floor, not round: the roll-up now moves with the in-flight
                                                // track, and 6.4 done of 12 must read "6/12", not "7/12".
                                                // The epsilon absorbs float error at exact completions.
                                                return a + (qrow.st === "done" ? model.tracks : Math.floor(model.progress / 100 * model.tracks + 1e-6)) + "/" + model.tracks + " tracks"
                                            if (qrow.st === "running") return a + Math.round(model.progress) + "%"
                                            return a + (qrow.st === "done" ? "Done" : qrow.st === "cancelled" ? "Cancelled" : qrow.st)
                                        }
                                        color: root.textLo; font.family: root.mono; font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                }
                                Rectangle {
                                    id: doneChip
                                    // Chip pops while the row lingers; in the Completed
                                    // group the status dot on the art carries "done".
                                    Layout.alignment: Qt.AlignVCenter; visible: qrow.st === "done" && !qrow.isComp
                                    radius: 8; color: root.greenCont; border.color: root.greenDim
                                    implicitHeight: 24; implicitWidth: chipRow.implicitWidth + 18
                                    Row {
                                        id: chipRow; anchors.centerIn: parent; spacing: 6
                                        Ico { name: "check"; color: root.accent; size: 13; anchors.verticalCenter: parent.verticalCenter }
                                        Text { text: "DONE"; color: root.accent; font.family: root.mono; font.pixelSize: 11; font.bold: true; font.letterSpacing: 0.8; anchors.verticalCenter: parent.verticalCenter }
                                    }
                                    SequentialAnimation {
                                        id: chipPop
                                        PropertyAction { target: doneChip; property: "scale"; value: 0.5 }
                                        NumberAnimation { target: doneChip; property: "scale"; to: 1.12; duration: 170; easing.type: Easing.OutCubic }
                                        NumberAnimation { target: doneChip; property: "scale"; to: 1.0; duration: 130; easing.type: Easing.OutCubic }
                                    }
                                }
                                RetryMark {
                                    Layout.alignment: Qt.AlignVCenter; visible: qrow.st === "failed"
                                    color: root.accent; box: 16
                                    MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: waves.retryQueueItem(model.qid) }
                                }
                                Ico {
                                    Layout.alignment: Qt.AlignVCenter
                                    readonly property bool active: qrow.st === "running" || qrow.st === "queued"
                                    name: "close"; size: 14; bold: active ? 8 : 0   // heavier while cancellable, matching the old font.bold: active
                                    color: cancMa.containsMouse ? root.red : (active ? root.textLo : root.textDim)
                                    MouseArea {
                                        id: cancMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: parent.active ? waves.cancelQueueItem(model.qid) : waves.removeQueueItem(model.qid)
                                    }
                                }
                            }
                            Item {
                                Layout.fillWidth: true
                                Layout.preferredHeight: qrow.st === "running" ? 12 : 0
                                opacity: qrow.st === "running" ? 1 : 0
                                clip: true
                                Behavior on Layout.preferredHeight { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
                                Behavior on opacity { NumberAnimation { duration: 240 } }
                                DotMatrix {
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    rows: 2; dot: 4; gap: 4; maxCols: 0
                                    pulse: qrow.st === "running"
                                    pct: qrow.st === "done" ? 100 : model.progress
                                    // 100% but still running = the final steps (merge,
                                    // decrypt, tag) are in flight: twinkle, don't freeze.
                                    finishing: qrow.st === "running" && model.progress >= 99.9
                                    onColor: qrow.st === "failed" ? root.red : qrow.st === "queued" ? root.cyanDim : root.accent
                                }
                            }
                            // ---- expanded per-track list (album order, live state) ----
                            // Hovering a collapsed album card "peeks" the top of this
                            // list, the card bottom bounces down just far enough to
                            // show the track view exists, and retracts on hover-out.
                            Item {
                                id: qtrackClip
                                readonly property bool shown: qrow.qexp || qrow.peeking
                                visible: shown || implicitHeight > 0.5
                                clip: true
                                Layout.fillWidth: true; Layout.bottomMargin: shown ? 2 : 0
                                implicitHeight: qrow.qexp ? qtrackCol.implicitHeight : (qrow.peeking ? 30 : 0)
                                Behavior on implicitHeight {
                                    NumberAnimation {
                                        id: qtrackAnim
                                        duration: 220; easing.type: Easing.OutBack; easing.overshoot: 1.6
                                    }
                                }
                                ColumnLayout {
                                    id: qtrackCol
                                    anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                                    spacing: 3
                                    Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.line1 }
                                    Text {
                                        textFormat: Text.PlainText
                                        visible: qtrackClip.shown && (root.queueTracks[qrow.model.qid] || []).length === 0
                                        text: "Loading tracks…"; color: root.textDim; font.family: root.mono; font.pixelSize: 10
                                    }
                                Repeater {
                                    model: qtrackClip.shown ? (root.queueTracks[qrow.model.qid] || []) : []
                                    delegate: RowLayout {
                                        required property var modelData
                                        Layout.fillWidth: true; spacing: 8
                                        Text {
                                            textFormat: Text.PlainText
                                            text: modelData.num
                                            color: root.textDim; font.family: root.mono; font.pixelSize: 10
                                            Layout.preferredWidth: 16; horizontalAlignment: Text.AlignRight
                                        }
                                        Text {
                                            textFormat: Text.PlainText
                                            text: modelData.title
                                            color: modelData.status === "running" ? root.textHi
                                                 : modelData.status === "done" ? root.textLo
                                                 : modelData.status === "failed" ? root.textHi : root.textLo
                                            font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true
                                        }
                                        Text {
                                            textFormat: Text.PlainText
                                            text: modelData.status === "done" ? "✓"
                                                : modelData.status === "running" ? Math.round(modelData.pct) + "%"
                                                : modelData.status === "failed" ? "✕"
                                                : modelData.status === "skipped" ? "HAVE"
                                                : modelData.status === "cancelled" ? "-" : "·"
                                            color: modelData.status === "done" || modelData.status === "running" ? root.accent
                                                 : modelData.status === "failed" ? root.red
                                                 : modelData.status === "skipped" ? root.green : root.textDim
                                            font.family: root.mono; font.pixelSize: 10
                                            font.bold: modelData.status === "running"
                                            Layout.preferredWidth: 34; horizontalAlignment: Text.AlignRight
                                        }
                                    }
                                }
                                }
                            }
                        }
                        // 5-second countdown bar, drains while the ✓ DONE chip
                        // lingers. Sized from doneAt so a delegate created
                        // mid-linger (drawer opened late) starts at the true
                        // remaining fraction, not a fresh full bar.
                        Rectangle {
                            anchors.left: parent.left; anchors.bottom: parent.bottom
                            height: 2; radius: 1; color: root.accent; opacity: 0.6
                            visible: qrow.lingering && !qrow.leaving
                            NumberAnimation on width {
                                running: qrow.lingering && !qrow.leaving
                                from: activeRect.width * Math.max(0, 1 - (Date.now() - model.doneAt) / 5000)
                                to: 0
                                duration: Math.max(1, 5000 - (Date.now() - model.doneAt))
                                easing.type: Easing.Linear
                            }
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 32; radius: root.btnRad; color: root.surface; border.color: root.border1
                    Text { anchors.centerIn: parent; text: "CLEAR FINISHED"; color: root.textLo; font.pixelSize: 13; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.clearFinished() }
                }
                Rectangle {
                    Layout.fillWidth: true; implicitHeight: 32; radius: root.btnRad; color: root.surface; border.color: root.border1
                    Text { anchors.centerIn: parent; text: "CLEAR ALL"; color: root.textLo; font.pixelSize: 13; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.clearQueue() }
                }
            }
        }
    }

    // ====================================================================
    // Login overlay
    // ====================================================================
    Rectangle {
        id: loginPanel
        property bool urlOpened: false
        anchors.fill: parent
        // sessionResolved gates the overlay so an already-signed-in launch
        // doesn't flash the logged-out screen while the cached-token network
        // check is still in flight.
        visible: waves.sessionResolved && !waves.loggedIn
        color: "#06070ed6"
        MouseArea { anchors.fill: parent }
        Rectangle {
            anchors.centerIn: parent; width: 460; radius: 14; color: root.surface2; border.color: root.outline
            implicitHeight: loginCol.implicitHeight + 40
            ColumnLayout {
                id: loginCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 13
                WelcomeBanner { Layout.fillWidth: true; Layout.preferredHeight: 75 }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Text { text: "1"; color: root.accent; font.family: root.mono; font.pixelSize: 12; font.bold: true; Layout.alignment: Qt.AlignTop }
                    Text {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                        text: "Open the TIDAL login in your browser and sign in."
                        color: root.textLo; font.pixelSize: 13
                    }
                }
                GateAction {
                    label: loginPanel.urlOpened ? "REOPEN BROWSER LOGIN" : "OPEN BROWSER LOGIN"
                    onClicked: waves.beginLogin()
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    visible: loginPanel.urlOpened
                    Text { text: "2"; color: root.accent; font.family: root.mono; font.pixelSize: 12; font.bold: true; Layout.alignment: Qt.AlignTop }
                    Text {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                        text: "Paste the URL you land on back here."
                        color: root.textLo; font.pixelSize: 13
                    }
                }
                // Same matrix-decrypt paste field as the search bar; a pasted redirect
                // URL auto-attempts sign-in once it has decoded in.
                Rectangle {
                    id: redirectBox
                    Layout.fillWidth: true; implicitHeight: 44; radius: 8; color: root.surface
                    visible: loginPanel.urlOpened
                    border.color: (redirectField.activeFocus || loginDecoder.decoding) ? root.accent : root.outline
                    Behavior on border.color { ColorAnimation { duration: 160; easing.type: Easing.OutQuad } }
                    DecodeController {
                        id: loginDecoder; field: redirectField; glyph: loginPaste
                        onDecoded: function(text) { waves.completeLogin(text) }
                    }
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 6; spacing: 8
                        TextField {
                            id: redirectField
                            Layout.fillWidth: true
                            placeholderText: "Paste redirect URL here…"
                            color: loginDecoder.decoding ? root.accent : root.textHi
                            placeholderTextColor: root.textLo; font.pixelSize: 13; font.family: root.mono
                            background: Rectangle { color: "transparent" }
                            onAccepted: waves.completeLogin(text)
                            onTextChanged: loginDecoder.noteTextChanged()
                        }
                        PasteGlyph {
                            id: loginPaste
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: { redirectField.forceActiveFocus(); redirectField.clear(); redirectField.paste() }
                        }
                    }
                }
                GateAction {
                    visible: loginPanel.urlOpened
                    label: "COMPLETE SIGN-IN"
                    onClicked: waves.completeLogin(redirectField.text)
                }
            }
        }
    }

    // ====================================================================
    // FFmpeg setup gate, shown after sign-in. FFmpeg powers several core
    // features, so we nudge users toward the one-click managed install (the same
    // flow as Settings) while letting power-users map their own. It appears in
    // two cases:
    //   • first run, once, before the usage agreement (persisted via
    //     ffmpegSetupDone); and
    //   • a returning user whose FFmpeg has gone missing, re-prompted on launch
    //     so they're not silently left without it. "Later" snoozes that
    //     re-prompt for the session only (sessionSnoozed), so it's never naggy
    //     within a session but does check again next launch.
    // ====================================================================
    Settings {
        id: setupSettings; category: "setup"
        property bool ffmpegSetupDone: false
        // "Don't show this again at launch": suppresses the ffmpeg-missing
        // re-prompt permanently for users who don't want FFmpeg at all.
        property bool ffmpegPromptDismissed: false
        // Update toast: the version the user last dismissed (✕) or acted on,
        // so that version stops toasting at launch; a NEWER release toasts.
        property string updateToastDismissed: ""
    }
    FfmpegManager { id: appFfmpeg; objectName: "appFfmpeg" }

    // Download-folder gate: no folder is set at all (fresh install). Blocks, the
    // download did not start; the CTA jumps straight to the Downloads setting.
    Rectangle {
        id: folderGate
        objectName: "folderGate"
        anchors.fill: parent
        visible: root.folderGateBlocking
        color: "#06070ef4"
        MouseArea { anchors.fill: parent; hoverEnabled: true }   // eat clicks behind the card
        Rectangle {
            anchors.centerIn: parent; width: 440
            implicitHeight: fgCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            ColumnLayout {
                id: fgCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text { textFormat: Text.PlainText; Layout.fillWidth: true; color: root.textHi; font.pixelSize: 18; font.bold: true; text: "Choose a download folder" }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: "Waves doesn't have a folder to save downloads to yet. Pick where your music should go, then start the download again."
                }
                GateAction { label: "Open download settings"; onClicked: { root.folderGateBlocking = false; root.openDownloadSetting() } }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    textFormat: Text.PlainText; text: "Not now"; color: root.textLo; font.pixelSize: 13
                    MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor; onClicked: root.folderGateBlocking = false }
                }
            }
        }
    }

    // Download-folder nudge: still on the old "~/download" default. Blocking: the
    // download is held in the backend until the user decides. "Keep" continues it;
    // "Choose a new location" abandons it (they re-initiate after picking a folder),
    // so the download button never changes state until the decision is made.
    Rectangle {
        id: folderNudge
        objectName: "folderNudge"
        anchors.fill: parent
        visible: root.folderNudge
        color: "#06070ecc"
        MouseArea { anchors.fill: parent; onClicked: { root.folderNudge = false; waves.dismissDownloadFolderNudge() } }   // click-away cancels (no download)
        Rectangle {
            anchors.centerIn: parent; width: 460
            implicitHeight: fnCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: fnCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text { textFormat: Text.PlainText; Layout.fillWidth: true; color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap; text: "Would you like to update your download location?" }
                Text { // guard:deliberate-richtext download-nudge-body
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    textFormat: Text.StyledText; linkColor: "#e6ebf0"
                    // The path is a link: clicking it opens the OS file manager at that
                    // folder (backend expands ~ and falls back to the nearest real dir).
                    text: "Currently downloads are going to the default path <a href=\"reveal\"><tt>~/download</tt></a> the application shipped in v0.1.0, and that caused some issues with users being unable to locate their files, or not realizing they could update the default location for downloads.<br><br>Would you like to keep the current default, or choose a new location?"
                    onLinkActivated: function(link) { waves.revealDownloadPath() }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.topMargin: 4; spacing: 12
                    GateAction { showArrow: false; label: "Choose a new location"; onClicked: { root.folderNudge = false; waves.dismissDownloadFolderNudge(); root.openDownloadSetting() } }
                    GateAction { showArrow: false; neutral: true; label: "Keep the default location"; onClicked: { root.folderNudge = false; waves.keepDownloadFolder() } }
                }
            }
        }
    }

    // Download folder set but unreachable (NAS asleep after lid close, drive
    // unplugged, stale mount). The download is held backend-side; "Try again"
    // re-runs it through the full gate (which also auto-heals a share that
    // remounted under a new /Volumes name).
    Rectangle {
        id: folderUnreachableGate
        objectName: "folderUnreachableGate"
        anchors.fill: parent
        visible: root.folderUnreachable
        color: "#06070ecc"
        MouseArea { anchors.fill: parent; onClicked: { root.folderUnreachable = false; waves.dismissDownloadFolderNudge() } }   // click-away cancels (no download)
        Rectangle {
            anchors.centerIn: parent; width: 460
            implicitHeight: fuCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: fuCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text { textFormat: Text.PlainText; Layout.fillWidth: true; color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap; text: "Download folder isn't reachable" }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WrapAnywhere
                    color: root.textLo; font.family: root.mono; font.pixelSize: 12
                    text: root.folderUnreachablePath
                }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: "The folder is set, but writing to it failed just now. If it lives on a network drive or NAS, it may have disconnected while the computer slept. Reconnect it and try again, or choose a different folder. The download is held and will start once this is resolved."
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.topMargin: 4; spacing: 12
                    GateAction { showArrow: false; label: "Try again"; onClicked: { root.folderUnreachable = false; waves.retryDownloadFolder() } }
                    GateAction { showArrow: false; neutral: true; label: "Choose a new location"; onClicked: { root.folderUnreachable = false; waves.dismissDownloadFolderNudge(); root.openDownloadSetting() } }
                }
            }
        }
    }

    Rectangle {
        id: ffmpegGate
        objectName: "ffmpegGate"
        anchors.fill: parent
        // First-run step (before terms) OR a returning user whose ffmpeg is now
        // missing (after terms, this session, not yet snoozed). The two branches
        // are mutually exclusive on termsAccepted, so this never stacks with
        // termsGate.
        visible: waves.loggedIn && (
            (!setupSettings.ffmpegSetupDone && !legalSettings.termsAccepted)
            || (setupSettings.ffmpegSetupDone && legalSettings.termsAccepted
                && appFfmpeg.stateKey === "missing" && !ffmpegGate.sessionSnoozed
                && !setupSettings.ffmpegPromptDismissed)
        )
        color: "#06070ef4"
        // Eat every click; the only way past is the Continue / "later" choice.
        MouseArea { anchors.fill: parent; hoverEnabled: true }

        // Session-only snooze for the returning-user re-prompt: set by "later"
        // so the gate doesn't immediately re-show, but reset next launch.
        property bool sessionSnoozed: false

        // True once an install actually completes while this step is open, which
        // distinguishes "just installed" from "was already present" for the
        // title wording, without depending on FFmpeg-status load timing.
        property bool installedHere: false
        Connections {
            target: appFfmpeg
            function onLifeStateChanged() { if (appFfmpeg.lifeState === "done") ffmpegGate.installedHere = true }
        }

        Rectangle {
            anchors.centerIn: parent; width: 460
            implicitHeight: ffCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline

            ColumnLayout {
                id: ffCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 13

                Text {
                    textFormat: Text.PlainText
                    Layout.fillWidth: true
                    color: root.textHi; font.pixelSize: 18; font.bold: true
                    text: appFfmpeg.ready
                        ? (ffmpegGate.installedHere ? "Awesome, FFmpeg is installed!" : "Awesome, FFmpeg is already installed!")
                        : "Set up FFmpeg"
                }
                Text {
                    textFormat: Text.PlainText
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: appFfmpeg.stateKey === "managed"
                            ? "Installed and managed by Waves. Video conversion, FLAC extraction, and downsampling are all available. You can manage or replace FFmpeg anytime in Settings."
                          : appFfmpeg.stateKey === "path"
                            ? "FFmpeg converts videos, extracts FLAC, and downsamples hi-res audio. Waves found a copy on your system and can use it as is."
                          : "FFmpeg converts videos, extracts FLAC, and downsamples hi-res audio. Without it, those steps are skipped."
                }

                // Status readout: dot + FFMPEG + state, grouped on one line.
                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Rectangle {
                        width: 7; height: 7; radius: 4; Layout.alignment: Qt.AlignVCenter
                        color: appFfmpeg.busy ? root.gold
                             : appFfmpeg.stateKey === "managed" ? root.green
                             : appFfmpeg.stateKey === "path" ? root.gold : root.red
                    }
                    Text { text: "FFMPEG"; color: root.textHi; font.pixelSize: 12; font.bold: true; font.letterSpacing: 1.4 }
                    Text {
                        textFormat: Text.PlainText
                        text: appFfmpeg.busy ? "INSTALLING"
                            : appFfmpeg.stateKey === "managed" ? ("MANAGED" + (appFfmpeg.status.version ? " · " + appFfmpeg.status.version : ""))
                            : appFfmpeg.stateKey === "path" ? ("SYSTEM" + (appFfmpeg.status.version ? " · " + appFfmpeg.status.version : ""))
                            : "NOT INSTALLED"
                        color: appFfmpeg.busy ? root.gold
                             : appFfmpeg.stateKey === "managed" ? root.green
                             : appFfmpeg.stateKey === "path" ? root.gold : root.textDim
                        font.family: root.mono; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.4
                    }
                    Item { Layout.fillWidth: true }
                }

                // Progress (while downloading/installing): the same LED
                // dot-matrix pill the Settings updater card uses.
                LedBar {
                    visible: appFfmpeg.busy; Layout.fillWidth: true
                    radius: root.btnRad; mono: root.mono
                    pct: appFfmpeg.pct
                    label: (appFfmpeg.message || "Working…") + " · " + Math.round(appFfmpeg.pct) + "%"
                }
                Text {
                    textFormat: Text.PlainText
                    visible: appFfmpeg.lifeState === "failed"
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    text: "Install failed: " + appFfmpeg.message; color: root.red; font.pixelSize: 12
                }

                // Missing: the choice is two tap cards, no separate confirm.
                GateCard {
                    visible: appFfmpeg.stateKey === "missing" && !appFfmpeg.busy
                    highlight: true
                    title: "Install a managed copy"; chip: "RECOMMENDED"
                    desc: "Private to Waves, updated in one click from Settings."
                    onClicked: appFfmpeg.install()
                }
                GateCard {
                    visible: appFfmpeg.stateKey === "missing" && !appFfmpeg.busy
                    title: "Set it up myself later"
                    desc: "Point Waves at your own FFmpeg from Settings."
                    onClicked: { setupSettings.ffmpegSetupDone = true; ffmpegGate.sessionSnoozed = true }
                }
                Item {
                    visible: appFfmpeg.stateKey === "missing" && !appFfmpeg.busy
                    Layout.fillWidth: true; implicitHeight: ffNoAskTxt.implicitHeight + 2
                    Text {
                        id: ffNoAskTxt; anchors.centerIn: parent
                        text: "I don't need FFmpeg. Stop showing this at launch."
                        color: ffNoAskMa.containsMouse ? root.textHi : root.textLo
                        font.pixelSize: 12; font.underline: true
                    }
                    MouseArea {
                        id: ffNoAskMa; anchors.fill: ffNoAskTxt; hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            setupSettings.ffmpegPromptDismissed = true
                            setupSettings.ffmpegSetupDone = true
                            ffmpegGate.sessionSnoozed = true
                        }
                    }
                }

                // Detected on the system: keep it (continues) or switch to managed.
                GateCard {
                    visible: appFfmpeg.stateKey === "path" && !appFfmpeg.busy
                    highlight: true
                    title: "Keep my system FFmpeg"
                    chip: appFfmpeg.status.version ? "DETECTED · " + appFfmpeg.status.version : "DETECTED"
                    desc: appFfmpeg.status.path ? "Use the copy already installed at " + appFfmpeg.status.path + "." : "Use the copy already installed on this system."
                    onClicked: { setupSettings.ffmpegSetupDone = true; ffmpegGate.sessionSnoozed = true }
                }
                GateCard {
                    visible: appFfmpeg.stateKey === "path" && !appFfmpeg.busy
                    title: "Switch to a managed copy"
                    desc: "Private to Waves, one-click updates from Settings."
                    onClicked: appFfmpeg.install()
                }

                // Cancel (while installing)
                GateAction {
                    visible: appFfmpeg.busy
                    label: "CANCEL"; danger: true
                    onClicked: appFfmpeg.cancel()
                }

                // Managed: continue past the gate.
                GateAction {
                    visible: appFfmpeg.stateKey === "managed" && !appFfmpeg.busy
                    label: "CONTINUE"
                    onClicked: { setupSettings.ffmpegSetupDone = true; ffmpegGate.sessionSnoozed = true }
                }

                // Source attribution, crediting the build maintainers.
                Text { // guard:deliberate-richtext ffmpeg-attribution
                    visible: appFfmpeg.status.source ? true : false
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    textFormat: Text.StyledText; linkColor: root.cyan
                    color: root.textDim; font.pixelSize: 11
                    text: "Managed builds for " + (appFfmpeg.status.os || "") + "/" + (appFfmpeg.status.arch || "")
                          + " come from <a href=\"" + (appFfmpeg.status.source_url || "") + "\">"
                          + (appFfmpeg.status.source || "") + "</a>"
                          + (appFfmpeg.status.source_license ? " · " + appFfmpeg.status.source_license : "")
                          + ". Thank you to the maintainers. FFmpeg © the FFmpeg project (ffmpeg.org)."
                    onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                }
            }
        }
    }

    // Update toast: a newer Waves release was detected (launch check or a
    // manual one), and the WHOLE update flow runs inline here:
    //   offer      [gold dot] Waves vX is available     INSTALL   ✕
    //   installing [LedBar: download / verify / stage]  CANCEL
    //   ready      [green dot] vX installed             RESTART NOW   LATER
    //   failed     [red dot] Install failed: <reason>   RETRY   ✕
    // Shows on detection and again at every launch until the user dismisses
    // or acts on it (remembered per version); the gold status-bar notice
    // stays up regardless. Deliberately NO auto-hide: an update notice waits
    // until it is acted on in some way. The Settings updater card keeps its
    // own full controls; both listen to the same backend signals.
    Rectangle {
        id: updateToast
        objectName: "updateToast"
        // phase: "" (hidden) | "offer" | "installing" | "ready" | "failed"
        property string phase: ""
        // face: what the pill RENDERS. Tracks phase while shown but keeps the
        // last state during the fade-out, so dismissing never rebinds the
        // texts (RESTART NOW must not snap back to INSTALL mid-fade) and the
        // width never re-measures while collapsing.
        property string face: "offer"
        onPhaseChanged: if (phase !== "") face = phase
        property string version: ""
        property real pct: 0
        property string stage: ""
        property string error: ""
        // INSTALL works for a normal self-install AND for a package-manager
        // copy whose manager the app can run (brew upgrade). Only channels
        // without a runnable upgrade (Snap, Flatpak, an unknown sentinel)
        // fall back to VIEW (releases page). Snapshotted at offer time.
        property bool selfInstall: true
        function offer(v) {
            if (setupSettings.updateToastDismissed === v) return
            var st = waves.appUpdateStatus()
            selfInstall = st && (st.can_self_install === true || st.can_managed_install === true)
            version = v; pct = 0; phase = "offer"
        }
        function dismiss() {   // remembered for this version
            setupSettings.updateToastDismissed = version
            phase = ""
        }

        // Real updater lifecycle. Progress/stage bind only while this toast
        // drives the install; "done" flips any visible toast to the restart
        // prompt (even one still on "offer" while Settings ran the install).
        Connections {
            target: waves
            function onAppUpdateProgress(p) { if (updateToast.phase === "installing") updateToast.pct = p }
            function onAppUpdateStateChanged(state, message) {
                if (updateToast.phase === "") return
                if (state === "downloading") {
                    if (updateToast.phase === "installing") updateToast.stage = message
                } else if (state === "done") {
                    updateToast.pct = 100; updateToast.phase = "ready"
                } else if (state === "failed" && updateToast.phase === "installing") {
                    updateToast.error = message; updateToast.phase = "failed"
                } else if (state === "cancelled" && updateToast.phase === "installing") {
                    updateToast.pct = 0; updateToast.phase = "offer"
                }
            }
        }

        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 46
        // The pill widens while the LED bar is up, then narrows again.
        width: face === "installing" ? Math.min(560, parent.width - 40)
                                     : Math.min(utRow.implicitWidth + 28, parent.width - 40)
        Behavior on width { enabled: updateToast.phase !== ""; NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        height: 40; radius: 10
        color: root.surface2; border.color: root.outline; border.width: 1
        opacity: phase !== "" ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        // gentle settle down and away as it fades
        transform: Translate {
            y: updateToast.phase !== "" ? 0 : 10
            Behavior on y { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        }

        RowLayout {
            id: utRow
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14
            spacing: 12

            // status dot (offer: gold / ready: green / failed: red)
            Rectangle {
                visible: updateToast.face !== "installing"
                width: 7; height: 7; radius: 4; Layout.alignment: Qt.AlignVCenter
                color: updateToast.face === "ready" ? root.green
                     : updateToast.face === "failed" ? root.red : root.gold
            }

            // main line (offer / ready / failed)
            Text {
                visible: updateToast.face !== "installing"
                Layout.alignment: Qt.AlignBaseline
                textFormat: Text.PlainText
                text: updateToast.face === "ready" ? "Waves v" + updateToast.version + " installed"
                    : updateToast.face === "failed" ? "Install failed: " + updateToast.error
                    : "Waves v" + updateToast.version + " is available"
                color: updateToast.face === "failed" ? root.red : root.textHi
                font.pixelSize: 12; elide: Text.ElideRight
                Layout.maximumWidth: root.width - 300
            }

            // the LED pill while installing (same bar as the Settings card)
            LedBar {
                visible: updateToast.face === "installing"
                Layout.fillWidth: true; Layout.preferredHeight: 22
                Layout.alignment: Qt.AlignVCenter
                radius: 8; mono: root.mono
                pct: updateToast.pct
                label: updateToast.stage + " · " + Math.round(updateToast.pct) + "%"
            }

            // primary action per phase: green. On hover the label runs a
            // console "decode": every glyph scrambles, then characters lock
            // in left to right while the tail keeps churning, with a bright
            // flash that fades as the word resolves. Deliberate, not a bug.
            // CANCEL (installing) stays plain grey: no glitch on it.
            Text {
                id: utAct
                Layout.alignment: Qt.AlignBaseline
                textFormat: Text.PlainText
                readonly property string realLabel: updateToast.face === "offer"
                      ? (updateToast.selfInstall ? "INSTALL" : "VIEW")
                    : updateToast.face === "installing" ? "CANCEL"
                    : updateToast.face === "ready" ? "RESTART NOW"
                    : "RETRY"
                property string scr: ""
                text: scr !== "" ? scr : realLabel
                color: updateToast.face === "installing" ? root.textDim
                     : utGlitch.running ? Qt.lighter(root.green, 1.0 + 0.45 * (1 - utAct._gt / utGlitch.ticks))
                     : root.green
                font.family: root.mono; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.8
                property int _gt: 0
                readonly property string _glyphs: "ABCDEF0123456789/:#@$%&*+=<>"
                Timer {
                    id: utGlitch; interval: 30; repeat: true
                    readonly property int ticks: 12
                    onTriggered: {
                        utAct._gt++
                        if (utAct._gt > ticks) { utGlitch.stop(); utAct.scr = ""; return }
                        // characters resolve left to right; the unresolved
                        // tail keeps scrambling every tick
                        var locked = Math.floor(utAct.realLabel.length * utAct._gt / ticks)
                        var out = ""
                        for (var i = 0; i < utAct.realLabel.length; i++)
                            out += (i < locked || utAct.realLabel.charAt(i) === " ")
                                 ? utAct.realLabel.charAt(i)
                                 : utAct._glyphs.charAt(Math.floor(Math.random() * utAct._glyphs.length))
                        utAct.scr = out
                    }
                }
                MouseArea {
                    id: utGo; anchors.fill: parent; anchors.margins: -8
                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onEntered: if (updateToast.phase !== "installing") { utAct._gt = 0; utGlitch.restart() }
                    onClicked: {
                        if ((updateToast.phase === "offer" || updateToast.phase === "failed")
                                && !updateToast.selfInstall) {
                            // Package-manager-owned install: hand off to the
                            // releases page, never write over the managed copy.
                            waves.openReleasesPage()
                            updateToast.dismiss()
                        } else if (updateToast.phase === "offer" || updateToast.phase === "failed") {
                            // acted on: stop re-showing at launch
                            setupSettings.updateToastDismissed = updateToast.version
                            updateToast.pct = 0; updateToast.error = ""; updateToast.stage = "Downloading update…"
                            updateToast.phase = "installing"
                            waves.installAppUpdate()
                        } else if (updateToast.phase === "installing") {
                            waves.cancelAppUpdate()   // backend answers with state "cancelled"
                        } else {
                            waves.restartForUpdate()
                        }
                    }
                }
            }

            // secondary: ✕ (offer/failed) or LATER (ready); nothing mid-install
            Text {
                visible: updateToast.face !== "installing"
                Layout.leftMargin: 2
                Layout.alignment: Qt.AlignBaseline
                textFormat: Text.PlainText
                text: updateToast.face === "ready" ? "LATER" : "✕"
                color: utX.containsMouse ? root.textHi : root.textDim
                font.family: updateToast.face === "ready" ? root.mono : root.uiFont
                font.pixelSize: updateToast.face === "ready" ? 10 : 11
                font.bold: updateToast.face === "ready"; font.letterSpacing: updateToast.face === "ready" ? 0.8 : 0
                MouseArea {
                    id: utX; anchors.fill: parent; anchors.margins: -8
                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: updateToast.dismiss()
                }
            }
        }
    }

    // FFmpeg-missing download gate: the download is HELD backend-side before
    // anything is queued, because without FFmpeg the files come out degraded
    // (no FLAC extraction, no video conversion, no track-length repair, so
    // strict players can read 0:00). Fix it first, or knowingly continue.
    Rectangle {
        id: ffmpegBlockGate
        objectName: "ffmpegBlockGate"
        anchors.fill: parent
        visible: root.ffmpegBlocked
        color: "#06070ecc"
        MouseArea { anchors.fill: parent; onClicked: { root.ffmpegBlocked = false; waves.dismissDownloadFolderNudge() } }   // click-away cancels (no download)
        Rectangle {
            anchors.centerIn: parent; width: 460
            implicitHeight: fbCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: fbCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text { textFormat: Text.PlainText; Layout.fillWidth: true; color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap; text: "This download needs FFmpeg" }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: "FFmpeg isn't set up, so files would save degraded: FLAC stays wrapped in its stream container, videos aren't converted, and track lengths aren't repaired (strict players can show 0:00). Set it up in Settings with one click, then start the download again, or continue anyway with these limitations."
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.topMargin: 4; spacing: 12
                    GateAction { showArrow: false; label: "Set up FFmpeg"; onClicked: { root.ffmpegBlocked = false; waves.dismissDownloadFolderNudge(); root.openFfmpegSetting() } }
                    GateAction { showArrow: false; neutral: true; label: "Continue anyway"; onClicked: { root.ffmpegBlocked = false; waves.bypassFfmpegGate() } }
                }
            }
        }
    }

    // ====================================================================
    // Usage agreement, a non-dismissible gate shown the first time a user is
    // signed in. The only way past it is to acknowledge; the acceptance is
    // persisted (QSettings) so it appears once, not on every launch.
    // ====================================================================
    Settings { id: legalSettings; category: "legal"; property bool termsAccepted: false }

    Rectangle {
        id: termsGate
        anchors.fill: parent
        visible: waves.loggedIn && setupSettings.ffmpegSetupDone && !legalSettings.termsAccepted
        color: "#06070ef4"
        // Eat every click so nothing behind the gate is reachable; with no close
        // control and no outside-click handler, the gate cannot be dismissed.
        MouseArea { anchors.fill: parent; hoverEnabled: true }

        Rectangle {
            anchors.centerIn: parent; width: 540
            implicitHeight: termsCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline

            ColumnLayout {
                id: termsCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 13

                Text {
                    Layout.fillWidth: true
                    text: "Before you continue"; color: root.textHi; font.pixelSize: 18; font.bold: true
                }
                Text {
                    textFormat: Text.PlainText
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: "Waves is a personal, educational tool for accessing your own TIDAL account. By continuing, you agree that:\n\n"
                        + "•  You will use Waves only for lawful, personal, non-commercial purposes, and only with content you are authorized to access.\n"
                        + "•  You will not use Waves to infringe copyright or to reproduce, distribute, or pirate any content. Respect the rights of artists and rights-holders.\n"
                        + "•  You are solely responsible for your use of Waves and for complying with TIDAL's Terms of Service and all laws that apply to you.\n"
                        + "•  Waves is provided \"as is\", without warranty of any kind. Its developers and contributors accept no liability for how it is used.\n\n"
                        + "Waves is not affiliated with, endorsed by, or sponsored by TIDAL."
                }
                // Privacy promise, emphasized, the closing note of the preamble.
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: root.border1 }
                Text { // guard:deliberate-richtext privacy-promise
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter
                    textFormat: Text.StyledText
                    text: "Waves does not collect any information from its users and has no way of knowing how the application is used. "
                        + "<font color=\"#3dff6e\">Privacy is the foundation of this application.</font>"
                    color: root.textHi; font.pixelSize: 13; font.bold: true; lineHeight: 1.3
                }
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    Rectangle {
                        id: ackChk
                        property bool checked: false
                        Layout.alignment: Qt.AlignTop
                        implicitWidth: 20; implicitHeight: 20; radius: 5
                        color: checked ? root.accent : "transparent"
                        border.color: checked ? root.accent : root.outline; border.width: 1.5
                        Ico { anchors.centerIn: parent; visible: ackChk.checked; name: "check"; color: root.accentText; size: 13; bold: 8 }
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: ackChk.checked = !ackChk.checked }
                    }
                    Text {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                        text: "I have read and agree to these terms."
                        color: root.textHi; font.pixelSize: 13
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: ackChk.checked = !ackChk.checked }
                    }
                }
                GateAction {
                    label: "ACKNOWLEDGE & AGREE"
                    enabled: ackChk.checked
                    opacity: ackChk.checked ? 1 : 0.4
                    onClicked: if (ackChk.checked) legalSettings.termsAccepted = true
                }
            }
        }
    }
}
