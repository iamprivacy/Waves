import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Effects
import QtQuick.Shapes
import QtCore
import QtMultimedia

ApplicationWindow {
    id: root
    // Starts hidden and is shown from Component.onCompleted once the saved
    // frame (issue #6) has been applied, so a remembered size/position takes
    // effect BEFORE the first present: the window opens where it was left with
    // no jump from the default frame. On a fresh install there is nothing to
    // restore and it shows at the default (4:3, 1040x780), centered on the
    // screen by onCompleted.
    visible: false
    width: 1040
    height: 780
    // Never allow a width that clips the header: the top bar's content
    // (logo, wordmark, nav tabs, queue, connection pill, sign out) sets the
    // real floor. headerRow reports 0 until it is laid out, hence the max.
    minimumWidth: Math.max(880, Math.ceil(headerRow.implicitWidth) + 44)
    minimumHeight: 560
    title: "Waves"
    color: bg

    // ---- Album-art hover tilt knobs (iteration; final home = settings) --
    // Variant: none | tilt | tilt_gloss | tilt_shadow. Tilt is BGT's value.
    // Lift is BGT's 1.04 swell cut by a fifth (0.04 -> 0.032): the cover read
    // as jumping toward you too eagerly at BGT's number.
    // Settings > Advanced > "Cover art tilts on hover". "none" is the same
    // switch the variants already had, so one binding turns the effect off
    // everywhere it reaches (Art, the track discs, the Browse hero cards).
    property bool artHoverTilt: waves.wavesPref("art_hover_tilt") !== false
    // Settings > Advanced > "Videos preview on hover". Off gates peekOpen, so
    // pointing at a thumbnail no longer grows the sound-on preview card; a
    // click still plays the video in full. An open peek closes on the flip.
    property bool videoHoverPeek: waves.wavesPref("video_hover_peek") !== false
    // Whether the library scan is on (saved state). The green done-state
    // button reads IN LIBRARY instead of DOWNLOADED while it is: with a
    // library in the picture "you have this" is the claim that matters, and
    // DOWNLOADED is just how the app happens to know it.
    property bool libraryOn: waves.wavesPref("library_enabled") === true
    // Whether fresh downloads land INSIDE the library folder. IN LIBRARY on a
    // done face is only honest when the library will actually contain the
    // file: either downloads land in the library (this flag) or the scan has
    // proven the copy present (libPresent per button). With a separate
    // download folder the face says DOWNLOADED, and moving the files into
    // the library flips it through the normal rescan.
    property bool dlInLibrary: waves.downloadsInsideLibrary() === true
    Connections {
        target: waves
        function onLibrarySourceChanged() {
            root.libraryOn = waves.wavesPref("library_enabled") === true
            root.dlInLibrary = waves.downloadsInsideLibrary() === true
        }
        function onArtHoverTiltChanged() {
            root.artHoverTilt = waves.wavesPref("art_hover_tilt") !== false
        }
        function onVideoHoverPeekChanged() {
            root.videoHoverPeek = waves.wavesPref("video_hover_peek") !== false
            if (!root.videoHoverPeek && root.peekNow) root.peekClose()
        }
    }
    property string artFxVariant: root.artHoverTilt ? "tilt" : "none"
    property real artFxTilt: 8
    property real artFxLift: 1.032
    // Whether the tilt springs or eases. OutBack overshoots at the end of
    // every move, which on the way back reads as the cover bouncing as it
    // lands flat; OutCubic settles straight into place. One knob, so the
    // covers and the track discs always move the same way.
    property int artFxEase: Easing.OutCubic
    // ---- Artwork depth: hover shadow, and a resting raise while playing --
    // Every cover that answers the pointer casts a shadow while it is
    // hovered, and artwork whose own preview is running stops answering the
    // pointer and settles slightly raised over that same shadow, so "what is
    // playing" reads from across the page without a badge or a colour
    // change. One shadow language for both states: a cover that starts
    // playing under the pointer deepens what is already there instead of
    // growing one out of nothing.
    // Held for the whole session on that cover, not just while sound is
    // coming out: a pause keeps the card up and sets the shadow breathing
    // (artPlayBreath) rather than dimming it, so a held cover reads as
    // resting, not as switched off.
    // Raise and shadow are deliberately small; this is a hierarchy cue, not
    // an animation. Values chosen in scratchpad/playing_raise_lab against
    // the real Browse page.
    property real artPlayLift: 1.02          // resting swell while it plays
    property real artPlayShadowY: 10         // shadow drop while raised, px
    property real artPlayShadowBlur: 0.75    // MultiEffect blur, 0..1
    property real artPlayShadowA: 0.55       // shadow alpha at full depth
    property real artPlayBreath: 0.22        // paused: how far the breath relaxes
    property int  artPlayBreathMs: 2400      // paused: one half-breath

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
    // "In your library" ownership badge: amber, deliberately distinct from the
    // green on-disk owned tag so the two ownership signals never blur.
    readonly property color libAccent:   "#e5a00d"
    readonly property color libDim:      "#9c6e0a"
    readonly property color libCont:     "#241a05"
    readonly property color libContTx:   "#f2c766"
    readonly property color cyan:         "#56c8d8"   // HIGH tier + queued
    readonly property color cyanDim:      "#3a8d99"
    readonly property color cyanCont:     "#07232b"   // healthy-lossy pill container
    readonly property color cyanContTx:   "#a6e7f1"
    readonly property color red:          "#ff5a52"   // failed / peak / heart
    readonly property color redCont:      "#2a0e0c"
    readonly property color redDim:       "#b23f3a"   // small-lossy pill border
    readonly property color redContTx:    "#ff9d97"
    readonly property string mono:        monoFont    // bundled JetBrains Mono (see app.py)
    // ---- Console button spec (chosen in the Button Lab, 2026-07-02) -----
    // One voice for every button/tab label: the native system sans, Bold,
    // UPPERCASE (nav tabs sentence case), lit-cell primaries; mono stays the
    // "data voice" (badges, numbers, ASCII art).
    readonly property string uiFont:   uiFontFamily   // native system sans (see app.py)
    readonly property real   btnTrack: 0              // label letter-spacing
    readonly property int    btnRad:   8              // button corner radius
    // Outline weight for the download/preview controls (and the divider inside
    // the hover strip). A hair over 1px so the controls hold their edge against
    // busy artwork without reading as a heavy frame.
    readonly property real   btnBorderW: 1.5
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
    property var playlistTrackCache: ({}) // playlistId -> [rows], filled by playlistTracksLoaded
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

    // ---- Window geometry persistence (issue #6) ------------------------
    // Remember the window's size, position and maximized state across launches.
    // Saves route through the bridge's waves.json store (the channel every
    // other pref uses; it writes synchronously so it survives the standalone
    // os._exit teardown) and are debounced: a drag/resize fires a change per
    // pixel, so one settled write per gesture is enough. The NORMAL
    // (non-maximized) frame is tracked separately from the maximized flag so
    // un-maximizing lands on a sane size, and the backend clamps a restored
    // frame back onto a live screen. _geomReady gates saves so the restore
    // itself never triggers one, and so the initial show is not saved as a
    // change.
    property int  _winNormalX: x
    property int  _winNormalY: y
    property int  _winNormalW: width
    property int  _winNormalH: height
    property bool _geomReady: false
    function _winIsMax() {
        return visibility === Window.Maximized || visibility === Window.FullScreen
    }
    function _winCaptureNormalIfWindowed() {
        // Capture the normal frame only from a SETTLED windowed state, and only
        // at persist time (not on every raw geometry signal). On X11 a maximize
        // arrives as two independent, unordered events (the maximized
        // ConfigureNotify and the _NET_WM_STATE change); capturing eagerly could
        // record the transitional maximized geometry as the "normal" frame if
        // the geometry event landed first. By the time the debounce fires the
        // state has settled, so visibility here is coherent with the geometry.
        if (visibility === Window.Windowed) {
            _winNormalX = x; _winNormalY = y
            _winNormalW = width; _winNormalH = height
        }
    }
    function _winPersist() {
        if (!_geomReady) return
        // Don't persist while minimized/hidden: visibility collapses the
        // maximized substate to Minimized there, so a save would wrongly record
        // max=false, and there is no fresh frame to capture. The last
        // visible-state save stands, so maximize then minimize then quit still
        // restores maximized.
        if (visibility === Window.Minimized || visibility === Window.Hidden) return
        // Maximized keeps the last known normal frame (capture is skipped); only
        // the flag changes. windowSaveGeometry ignores a 0x0 teardown frame.
        _winCaptureNormalIfWindowed()
        waves.windowSaveGeometry(_winNormalX, _winNormalY, _winNormalW, _winNormalH, _winIsMax())
    }
    Timer { id: winGeomSaveTimer; interval: 600; onTriggered: root._winPersist() }
    onXChanged:          if (_geomReady) winGeomSaveTimer.restart()
    onYChanged:          if (_geomReady) winGeomSaveTimer.restart()
    onWidthChanged:      if (_geomReady) winGeomSaveTimer.restart()
    onHeightChanged:     if (_geomReady) winGeomSaveTimer.restart()
    onVisibilityChanged: if (_geomReady) winGeomSaveTimer.restart()
    onClosing: function (close) {
        _winPersist()   // best-effort final flush; not the only save path
        // The drawer's width rides its own debounce, so a drag settled inside
        // the last interval would leave with the timer that never fired. Same
        // best-effort flush as the frame above, and the same guards behind it:
        // the bridge drops a save that changes nothing, and drops a zero.
        waves.queueSaveWidth(Math.round(root.queueWidth))
        // Downloads still running: veto the close and ask first. EXIT ANYWAY
        // re-closes with confirmed set, so this runs at most once per attempt.
        // Qt.quit() paths (factory reset) bypass by design: nothing to save.
        if (root.activeQueueCount > 0 && !setupSettings.exitWarnMuted && !exitGate.confirmed) {
            close.accepted = false
            exitGate.open = true
        }
    }

    Component.onCompleted: {
        // Restore the saved window frame BEFORE the first present (see the
        // block above): apply the frame, seed the normal-frame trackers, then
        // show. The try/catch guarantees the window is shown even if the
        // restore hiccups, so a geometry glitch can never leave it hidden.
        var showMax = false
        var restored = false
        try {
            var g = waves.windowRestoreGeometry()
            if (g && g.w > 0 && g.h > 0) {
                root.x = g.x; root.y = g.y
                root.width = g.w; root.height = g.h
                root._winNormalX = g.x; root._winNormalY = g.y
                root._winNormalW = g.w; root._winNormalH = g.h
                showMax = g.maximized === true
                restored = true
            }
        } catch (e) {
            // fall through to a plain show
        }
        if (!restored) {
            // Nothing remembered yet: center the default 4:3 frame on the
            // screen instead of taking the OS's corner placement.
            root.x = Math.round(Screen.virtualX + (Screen.width - root.width) / 2)
            root.y = Math.round(Screen.virtualY + (Screen.height - root.height) / 2)
            root._winNormalX = root.x; root._winNormalY = root.y
        }
        if (showMax) root.visibility = Window.Maximized
        else root.visible = true
        root._geomReady = true

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
    property var tracksRaw: []            // same, for tracksModel
    property var videosRaw: []            // same, for videosModel
    // Default "home": the first My Tidal press of a session lands there, and
    // later presses return to whichever category this last held (openLibrary).
    property string libraryCategory: "home"
    // Album expand state lives here (keyed by album id) rather than inside each
    // AlbumBlock, so it survives ListView delegate recycling in the virtualised
    // My Tidal lists.
    property var expandedAlbums: ({})
    // Same, for the search PLAYLISTS rows (PlaylistBlock).
    property var expandedPlaylists: ({})
    // Expanding a row scrolls it up to this fraction of the viewport height
    // (a sliver of padding kept above), so the tracks it reveals are on
    // screen instead of below the fold. Scrolls down only: a row already
    // higher than the anchor line stays put.
    readonly property real expandAnchor: 0.10
    function scrollExpandedIntoView(item) {
        var f = item.parent
        while (f && f.contentY === undefined) f = f.parent
        if (!f) return
        var y = item.mapToItem(f.contentItem, 0, 0).y
        var target = y - f.height * expandAnchor
        target = Math.max(0, Math.min(target, Math.max(0, f.contentHeight - f.height)))
        if (target <= f.contentY + 1) return
        expandScrollAnim.stop()
        expandScrollAnim.target = f
        expandScrollAnim.from = f.contentY
        expandScrollAnim.to = target
        expandScrollAnim.start()
    }
    NumberAnimation { id: expandScrollAnim; property: "contentY"; duration: 300; easing.type: Easing.OutCubic }
    // Collapsing a SHOW ALL section from deep inside it would otherwise leave
    // the view clamped to the page bottom, a random spot with no relation to
    // what was clicked. Land the view back at the section's header instead
    // (same padding sliver as expandAnchor), so SHOW LESS reads as returning
    // to the section you were browsing. Measured before the rows disappear
    // (the header sits above them, its y is unchanged by the collapse) and
    // applied via callLater, after the Column has relaid out, so the clamp
    // sees the new contentHeight; that still lands pre-paint (no visible
    // scroll). A view already above the section stays put.
    function scrollCollapsedToSection(item) {
        var f = item.parent
        while (f && f.contentY === undefined) f = f.parent
        if (!f) return
        var target = Math.max(0, item.mapToItem(f.contentItem, 0, 0).y - f.height * expandAnchor)
        if (f.contentY <= target + 1) return
        expandScrollAnim.stop()
        Qt.callLater(function() {
            f.contentY = Math.min(target, Math.max(0, f.contentHeight - f.height))
        })
    }
    // Scroll-edge dressing plus the "back to top" badge, one window-level
    // instance serving whichever view is on screen. Three pieces:
    //   1. LIP edge fades: a short dense darkening at the top and bottom of
    //      the scroll viewport so rows fade in and out of frame instead of
    //      being cut off hard at the chrome edge.
    //   2. Rolodex roll: rows whose center enters the edge band tilt subtly
    //      around their horizontal axis (real perspective), so the list reads
    //      like a conveyor curling over a drum edge.
    //   3. The INLINE crest pill: appears once the page has been scrolled
    //      about a viewport down, riding the top edge; one click glides back.
    component BackToTop: Item {
        id: btt
        property var flick: null
        anchors.fill: parent
        readonly property bool on: flick !== null && flick.visible
                                   && flick.contentY > flick.height * 0.75
        NumberAnimation { id: bttAnim; property: "contentY"; to: 0; duration: 450; easing.type: Easing.OutCubic }
        // Viewport geometry in window coordinates. The leading terms in the
        // sequence expressions are binding dependencies: mapToItem alone is
        // not notifiable, so re-evaluate when the window or view resizes.
        readonly property bool live: flick !== null && flick.visible
        readonly property real fTop: live ? (btt.height, flick.height, flick.mapToItem(btt, 0, 0).y) : 0
        readonly property real fX: live ? (btt.width, flick.width, flick.mapToItem(btt, 0, 0).x) : 0
        readonly property real fW: live ? flick.width : 0
        readonly property real fBottom: live ? fTop + flick.height : 0

        // ---- LIP edge fades ------------------------------------------------
        // Scroll-gated: a fade exists to soften rows being cut off by the
        // viewport edge, so when nothing is cut off there must be no fade.
        // At the very top of a page (contentY 0) the top fade is fully
        // transparent, so heroes, artist art and the back bar are never
        // dimmed for no reason; it ramps in over the first fadeH pixels of
        // scroll. Mirrored at the bottom: the fade lifts as the end of the
        // page arrives, and a page too short to scroll shows no fades at all.
        readonly property real fadeH: 34
        Rectangle {
            x: btt.fX; y: btt.fTop; width: btt.fW; height: btt.fadeH
            visible: btt.live && opacity > 0
            opacity: btt.live ? Math.min(1, Math.max(0, btt.flick.contentY) / btt.fadeH) : 0
            gradient: Gradient {
                GradientStop { position: 0; color: "#0d0f12" }
                GradientStop { position: 0.25; color: Qt.alpha("#0d0f12", 0.82) }
                GradientStop { position: 0.6; color: Qt.alpha("#0d0f12", 0.28) }
                GradientStop { position: 1; color: Qt.alpha("#0d0f12", 0) }
            }
        }
        Rectangle {
            x: btt.fX; y: btt.fBottom - btt.fadeH; width: btt.fW; height: btt.fadeH
            visible: btt.live && opacity > 0
            opacity: btt.live ? Math.min(1, Math.max(0,
                         btt.flick.contentHeight - btt.flick.height - btt.flick.contentY) / btt.fadeH) : 0
            rotation: 180
            gradient: Gradient {
                GradientStop { position: 0; color: "#0d0f12" }
                GradientStop { position: 0.25; color: Qt.alpha("#0d0f12", 0.82) }
                GradientStop { position: 0.6; color: Qt.alpha("#0d0f12", 0.28) }
                GradientStop { position: 1; color: Qt.alpha("#0d0f12", 0) }
            }
        }

        // ---- rolodex roll ----------------------------------------------------
        // Each row gets a Rotation bound to its own view (captured at creation,
        // so rows on backgrounded pages keep working when the user returns).
        readonly property real rollMax: 9
        readonly property real rollBand: 56
        // The tilt angle below reads row.mapToItem(fl), which is NOT reactive, so
        // the binding only re-evaluates on its one live dependency: fl.contentY
        // (a scroll). A tab switch or a filter change repositions rows without
        // moving contentY, which would leave every angle frozen at its old value
        // until the next scroll. Bumping rollTick on those events (see armRoll and
        // onFlickChanged) is the dependency that forces a recompute on demand.
        property int rollTick: 0
        Component {
            id: bttRollComp
            Rotation {
                property Item row
                property Item fl
                origin.x: row ? row.width / 2 : 0
                origin.y: row ? row.height / 2 : 0
                axis { x: 1; y: 0; z: 0 }
                angle: {
                    var _t = btt.rollTick  // re-eval on tab switch / filter / reflow, not only scroll
                    if (!row || !fl) return 0
                    // Only row-sized items may tilt. Anything taller (a shelf
                    // section, an expanded album panel) projects far outside
                    // the edge band when rotated and shears the whole page.
                    if (row.height > 160) return 0
                    var cy = fl.contentY   // binding dependency: re-evaluate on scroll
                    var c = row.mapToItem(fl, 0, 0).y + row.height / 2
                    if (c < btt.rollBand) return (1 - Math.max(0, c) / btt.rollBand) * btt.rollMax
                    var vh = fl.height
                    if (c > vh - btt.rollBand) return -Math.min(1, (c - (vh - btt.rollBand)) / btt.rollBand) * btt.rollMax
                    return 0
                }
            }
        }
        function armRow(row, fl) {
            // Rows with an existing transform are either already armed or own
            // one of the app's few bespoke transforms; leave both alone.
            if (row.transform.length > 0) return
            row.transform = bttRollComp.createObject(row, { row: row, fl: fl })
        }
        function armRoll() {
            var fl = flick
            if (!fl || !fl.contentItem) return
            var ci = fl.contentItem
            for (var i = 0; i < ci.children.length; i++) {
                var it = ci.children[i]
                if (it.width < 100 || it.height < 25) continue
                // List views parent delegates directly to contentItem; the
                // column-based pages nest rows one level down in a page-sized
                // content column, which must not tilt as a whole. In both
                // cases only row-sized items are armed: shelf sections and
                // grid cells stay flat (the angle binding also re-checks the
                // cap live, for rows that grow after arming).
                if (it.height <= 160) { armRow(it, fl); continue }
                if (it.height < fl.height * 0.9) continue
                for (var j = 0; j < it.children.length; j++) {
                    var row = it.children[j]
                    if (row.width > 100 && row.height > 24 && row.height <= 160) armRow(row, fl)
                }
            }
            // Rows may have moved (a fresh arm, a reflow, a returning tab), so
            // force every armed angle to recompute now, not on the next scroll.
            rollTick++
        }
        // Bump synchronously too: on a tab switch the returning pane is already
        // laid out, so its rows can re-tilt correctly in the same frame it
        // appears (no stale-then-correct flash); armRoll then re-checks post-layout.
        onFlickChanged: { btt.rollTick++; Qt.callLater(armRoll) }
        Component.onCompleted: Qt.callLater(armRoll)
        // Delegates recycle and result columns repopulate; contentHeight moves
        // in both cases, so it doubles as the re-arm signal.
        Connections {
            target: btt.flick
            function onContentHeightChanged() { Qt.callLater(btt.armRoll) }
        }

        // ---- the INLINE crest pill ------------------------------------------
        // The clipping band pins the pill's travel to the scroll viewport, so
        // it slides up UNDER the chrome edge when hiding, the same way rows
        // disappear when scrolled, instead of passing over the navbar.
        Item {
            x: btt.fX; y: btt.fTop; width: btt.fW; height: 40
            clip: true
            visible: btt.live
        Rectangle {
            anchors.horizontalCenter: parent.horizontalCenter
            y: btt.on ? 6 : -30
            width: bttRow.implicitWidth + 20; height: 24; radius: 12
            color: "#e6060810"
            border.color: bttMa.containsMouse ? root.accent : root.accentDim
            opacity: btt.on ? (bttMa.containsMouse ? 1 : 0.6) : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 160 } }
            Behavior on y { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            Row {
                id: bttRow
                anchors.centerIn: parent; spacing: 7
                Text {
                    id: bttCrest
                    textFormat: Text.PlainText
                    property int ph: 0
                    text: ("_.-~^~-._.~^'~._").substring(bttCrest.ph) + ("_.-~^~-._.~^'~._").substring(0, bttCrest.ph)
                    anchors.verticalCenter: parent.verticalCenter
                    color: root.accent; font.family: root.mono; font.pixelSize: 9; font.bold: true; font.letterSpacing: -1
                    Timer { running: btt.on; interval: 120; repeat: true; onTriggered: bttCrest.ph = (bttCrest.ph + 1) % 16 }
                }
                Text {
                    textFormat: Text.PlainText; text: "TOP"
                    anchors.verticalCenter: parent.verticalCenter
                    color: root.textHi; font.pixelSize: 9; font.bold: true; font.letterSpacing: root.btnTrack
                }
            }
            MouseArea {
                id: bttMa
                anchors.fill: parent
                enabled: btt.on
                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: {
                    bttAnim.stop()
                    bttAnim.target = btt.flick
                    bttAnim.from = btt.flick.contentY
                    bttAnim.start()
                }
            }
        }
        }
    }
    // My Tidal infinite scroll: whether more pages exist PER CATEGORY (the
    // category panes are keep-alive now, so each keeps its own pagination
    // truth while hidden), and whether a page is in flight for the active one
    // (to avoid firing duplicate page requests while scrolling). The map is
    // read only from functions, never bindings, so plain mutation is fine.
    property var libCatHasMore: ({})
    property bool libLoadingMore: false
    // Armed when a quiet revalidate of the active category is issued: if it
    // does land (rows actually changed), the refill pins the user's scroll
    // spot instead of snapping to the top. Sort changes and fresh loads
    // disarm it: those genuinely restart the list from the top.
    property bool libPinRefill: false
    // My Tidal per-category sort, {cat: {key, asc}}. Kept in step with the
    // backend's own per-category sort (both mutate only via libApplySort).
    property var libSort: ({})
    // Download-folder gate dialogs: the blocking "no folder set" gate and the
    // one-time soft nudge for users still on the old default. Driven by the
    // downloadFolderMissing / downloadFolderDefault signals.
    property bool folderGateBlocking: false
    property bool folderNudge: false
    // Advanced-settings reset confirmations. Both actions are destructive, so
    // nothing happens until the user confirms in these dialogs.
    property bool confirmSettingsReset: false
    property bool confirmFactoryReset: false
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
    property bool artistVideosCollapsed: waves.wavesPref("artist_sec_videos_collapsed") === true
    function toggleArtistSection(which) {
        var v
        if (which === "tracks") { v = artistTracksCollapsed = !artistTracksCollapsed }
        else if (which === "albums") { v = artistAlbumsCollapsed = !artistAlbumsCollapsed }
        else if (which === "videos") { v = artistVideosCollapsed = !artistVideosCollapsed }
        else { v = artistEpsCollapsed = !artistEpsCollapsed }
        waves.setWavesPref("artist_sec_" + which + "_collapsed", v)
    }
    // Every artist-page section shows its first 5 (videos: whole rows) with a
    // SHOW ALL beneath it, exactly like the search page's mixed view, and a
    // section the user expands is remembered (prefs) across artists and
    // launches, per section, alongside the fold state the headers already
    // persist. These used to be per-visit only for tracks/videos and absent
    // for albums/EPs.
    property bool topTracksExpanded: waves.wavesPref("artist_sec_tracks_expanded") === true
    property bool artistAlbumsExpanded: waves.wavesPref("artist_sec_albums_expanded") === true
    property bool artistEpsExpanded: waves.wavesPref("artist_sec_eps_expanded") === true
    property bool artistVideosExpanded: waves.wavesPref("artist_sec_videos_expanded") === true
    function toggleArtistExpand(which) {
        var v
        if (which === "tracks") v = topTracksExpanded = !topTracksExpanded
        else if (which === "albums") v = artistAlbumsExpanded = !artistAlbumsExpanded
        else if (which === "eps") v = artistEpsExpanded = !artistEpsExpanded
        else v = artistVideosExpanded = !artistVideosExpanded
        waves.setWavesPref("artist_sec_" + which + "_expanded", v)
    }
    // The search page (mixed All view) shows each section's first 5 results with
    // a SHOW ALL beneath it, so the page reads as a quick overview instead of a
    // wall. A section the user expands is remembered (prefs) and stays expanded
    // on the next search, per section, the same way the artist page remembers a
    // folded section. A specific section filter always shows everything (no cap).
    //
    // ARTISTS is the exception to the layout: its collapsed view is a horizontal
    // scroll strip of fixed-size cards (a resize reveals more cards, never
    // re-fits the ones on screen, which is what keeps it smooth) and SHOW ALL
    // expands it to a fill grid. stripMode drives which layout is live and gates
    // the two layouts' Loaders so exactly one set is active, which keeps the
    // build veil's one-tick-per-artist accounting balanced. Its expanded state
    // persists the same as the list sections.
    property bool searchArtistsExpanded: waves.wavesPref("search_sec_artists_expanded") === true
    readonly property bool searchArtistsStripMode: root.filterType === "all" && !root.searchArtistsExpanded
    property bool searchAlbumsExpanded: waves.wavesPref("search_sec_albums_expanded") === true
    property bool searchTracksExpanded: waves.wavesPref("search_sec_tracks_expanded") === true
    property bool searchVideosExpanded: waves.wavesPref("search_sec_videos_expanded") === true
    property bool searchPlaylistsExpanded: waves.wavesPref("search_sec_playlists_expanded") === true
    property bool searchMixesExpanded: waves.wavesPref("search_sec_mixes_expanded") === true
    function toggleSearchSection(which) {
        var v
        if (which === "artists") v = searchArtistsExpanded = !searchArtistsExpanded
        else if (which === "albums") v = searchAlbumsExpanded = !searchAlbumsExpanded
        else if (which === "tracks") v = searchTracksExpanded = !searchTracksExpanded
        else if (which === "videos") v = searchVideosExpanded = !searchVideosExpanded
        else if (which === "playlists") v = searchPlaylistsExpanded = !searchPlaylistsExpanded
        else v = searchMixesExpanded = !searchMixesExpanded
        waves.setWavesPref("search_sec_" + which + "_expanded", v)
    }
    // A per-section cap for the mixed All view: the section's first 5 rows, or
    // everything once it is expanded; a specific section filter is never capped.
    function searchRowVisible(name, count, index, expanded) {
        return sectionVisible(name, count) && (filterType !== "all" || expanded || index < 5)
    }
    // ---- Download state (mirrors the bridge) ----------------------------
    // mediaId -> a small reactive holder { real pct; string st }, created lazily
    // when a download for that id first reports. dlPct()/dlSt() read the holder;
    // the downloadProgress/downloadState handlers set exactly one holder's
    // property, so a progress tick re-binds only the controls showing THAT id.
    // (The previous whole-map reassignment invalidated the pct/state binding of
    // every instantiated download control on every tick, which under a burst of
    // per-segment ticks stole GUI-thread frames from scrolling and cover art.)
    property var dlHolders: ({})
    Component { id: dlHolderComp; QtObject { property real pct: -1; property string st: "" } }
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
    // Failed rows get their own section (between Completed and Downloading)
    // whose header carries RETRY ALL; the count drives both.
    property int failedCount: 0
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
    // Queue drawer width, dragged by the handle on its own edge, and remembered
    // across launches the way the window frame is (0 is the never-saved
    // sentinel). 420 is the floor as well as the default: below it the quality
    // a row states and the title it states it for start fighting for the same
    // pixels. Read through the clamp in the Drawer below, so shrinking the
    // window pulls an over-wide drawer in rather than letting it hang off the
    // side, while what is STORED stays the width that was asked for.
    property real queueWidth: {
        var w = waves.queueRestoreWidth()
        return w > 0 ? w : 420
    }
    // One settled write per drag rather than one per pixel, same as the window
    // frame above. Restoring fires this too; the bridge drops a no-op save.
    Timer { id: queueWidthSaveTimer; interval: 600; onTriggered: waves.queueSaveWidth(Math.round(root.queueWidth)) }
    onQueueWidthChanged: queueWidthSaveTimer.restart()
    // True while the drawer's resize edge is held. DotMatrix reads it to hold
    // its column rebuild until the hand has let go AND the release animation
    // has finished, so the one rebuild per gesture lands on a static screen.
    property bool queueEdgeHeld: false

    // ---- Browse (TIDAL editorial pages) --------------------------------
    // The landing payload (content rows + the genre/mood/decade chip sets)
    // arrives via onBrowseLoaded; drilling a chip loads that page into
    // browsePage, keyed by its TIDAL api path so a slow load for a chip the
    // user has already left is ignored (see onBrowsePageLoaded).
    property var browseSections: []
    // The clicked card's own title, kept while its page payload is in flight,
    // so the breadcrumb (and a snapshot of a page left mid-load) can name the
    // page immediately instead of flashing the "Browse" fallback until
    // onBrowsePageLoaded fills browsePage.title in. Empty when the opener had
    // no title at hand; the trail then holds the crumb back entirely.
    property string browseTitleHint: ""
    // --- Browse landing build veil ---------------------------------------
    // Fresh landing shelves incubate through asynchronous Loaders so the GUI
    // thread never freezes mid tab-strike, but the page must never be WATCHED
    // assembling: while building, the shelf loaders render at opacity 0
    // (heights still resolve) and the "Reading the wire…" hint stays up; when
    // the last loader reports ready the page appears complete in one paint,
    // exactly the pre-async look. Revalidations of a landing the user is
    // looking at, and endless-scroll growth, rebuild synchronously instead
    // (an in-place swap, no loading flash), so _browseAsyncBuild is set per
    // assignment by onBrowseLoaded / browseGrew.
    property bool _browseAsyncBuild: false
    property int _browseBuildTotal: 0
    property int _browseBuildReady: 0
    property bool browseBuilding: false
    function _browseBuildStart(n) {
        _browseBuildTotal = n; _browseBuildReady = 0
        browseBuilding = n > 0
        if (browseBuilding) browseBuildGuard.restart(); else browseBuildGuard.stop()
    }
    // A landing payload that arrived mid-handover, waiting for the reveal to
    // finish (see onBrowseLoaded and the end of bootHandover).
    property var _browseParked: null
    function applyBrowseLanding(p) {
        root.browseError = !!p.error
        var secs = p.sections || []
        // Fresh build (nothing on screen): async + veil. Refresh of a landing
        // already showing: synchronous, swaps in place. While the launch
        // overlay is still up nothing is "showing" yet, and the boot-time
        // revalidate re-emit fires almost every launch now that the landing
        // embeds For You rows (their ordering shifts between sessions), so
        // build that one asynchronously too: the synchronous shelf rebuild
        // froze the GUI thread mid boot sequence and the water/nav animations
        // visibly hitched (reported from livetesting).
        root._browseAsyncBuild = root.browseSections.length === 0 || !bootOverlay.done
        // +4 = the wayfinding groups (Playlists / Genres / Moods / Decades)
        // rendered as async shelves alongside the content sections.
        root._browseBuildStart(root._browseAsyncBuild && !p.error ? secs.length + 4 : 0)
        root.browseArtistsSideMap(secs)
        // Refresh of a landing already built: hold the spot across the
        // shelf rebuild (see holdScroll). The landing pane is alive even
        // behind a drilled page, and the clamp does not care that it is
        // hidden, so the hold arms regardless of which pane is showing.
        if (root.browseSections.length > 0) browseLanding.holdScroll()
        root.browseSections = secs
        root.browseChips = { genres: p.genres || [], moods: p.moods || [], decades: p.decades || [] }
        // An open (or stacked) local: row page snapshotted a row this payload
        // may have just refreshed; bring it in line.
        root.refreshLocalBrowsePages(p.sections || [])
    }
    function _browseBuildTick() {
        if (!browseBuilding) return
        browseBuildGuard.restart()   // progress: the guard watches for a STALL
        if (++_browseBuildReady >= _browseBuildTotal) { browseBuilding = false; browseBuildGuard.stop() }
    }
    // A loader that errors (or a miscount) must never pin the veil. Re-armed
    // by every loader that reports, so this is an inactivity timeout, not a
    // budget for the whole build: a landing with a dozen shelves used to blow
    // through a fixed 800ms and drop the veil mid-incubation, and the rest of
    // the page was then watched arriving shelf by shelf.
    Timer { id: browseBuildGuard; interval: 800; onTriggered: root.browseBuilding = false }
    // Always-on freshness: revalidate-on-tab-return alone lets the landing
    // freeze for a user who parks on Browse. The backend's 60s throttle is a
    // floor (don't re-hit the API more often than this), not a ceiling, so
    // nothing forces a refresh while the tab just sits there. This timer is
    // that ceiling: while the Browse landing is the on-screen view, it re-pokes
    // the (throttled) revalidation on a cadence so New / For You track TIDAL
    // instead of staying pinned to the first load of a weeks-long session.
    // refreshBrowse repaints only on an actual change, so a quiet landing costs
    // one background request per tick and no visible flash. Bound to run only
    // when the landing is visible (browsePageKey === "" and no overlay), so
    // drill-in pages, other tabs and a backgrounded app make no requests.
    Timer {
        id: browseLandingFreshTimer
        interval: 5 * 60 * 1000    // max-age: editorial rows move a few times a day
        repeat: true
        running: waves.loggedIn && root.browseOpen && root.browsePageKey === ""
                 && !root.settingsOpen && !root.libraryOpen && !root.artistOpen
        onTriggered: waves.refreshBrowse()   // silent, throttled; repaints only on change
    }
    // --- Search results build veil ----------------------------------------
    // Same treatment for a fresh search: every result card (artists, albums,
    // tracks, videos, playlists, mixes) incubates through an asynchronous
    // Loader while searchBuilding holds the pane at opacity 0 behind the
    // "Reading the wire…" hint, then the finished page appears in one paint.
    // The Loaders' asynchronous flag reads searchBuilding itself, so refills
    // outside a fresh search (the albums sort control) stay synchronous, an
    // in-place swap with no loading flash.
    property int _searchBuildTotal: 0
    property int _searchBuildReady: 0
    property bool searchBuilding: false
    // The library has not answered yet, so the badges the finished cards will
    // wear are not knowable and every pill would resolve to "not present".
    // Revealing here is what made a search during the first scan render bare
    // and then light every badge at once one frame later; the veil is already
    // up, so waiting costs nothing but the wait, and searchBuildGuard is still
    // the ceiling that ends it whatever the library is doing.
    property bool _searchAwaitingLibrary: false
    function _searchBuildStart(n) {
        _searchBuildTotal = n; _searchBuildReady = 0
        _searchAwaitingLibrary = n > 0 && !waves.libraryIndexReady()
        searchBuilding = n > 0
        // A pending release from the PREVIOUS build would clear this one's wait
        // a pass after it started, revealing the new page unanswered.
        searchLibraryReveal.stop()
        if (searchBuilding) searchBuildGuard.restart(); else searchBuildGuard.stop()
    }
    function _searchBuildTick() {
        if (!searchBuilding) return
        searchBuildGuard.restart()   // progress: the guard watches for a STALL
        if (++_searchBuildReady >= _searchBuildTotal) _searchBuildMaybeReveal()
    }
    // Both conditions, from either side: the last card can finish before the
    // index publishes or after it, and whichever lands second drops the veil.
    function _searchBuildMaybeReveal() {
        if (!searchBuilding) return
        if (_searchBuildReady < _searchBuildTotal || _searchAwaitingLibrary) return
        searchBuilding = false
        searchBuildGuard.stop()
    }
    // A loader that errors (or a miscount) must never pin the veil, and neither
    // must a library that never answers. Re-armed by every loader that reports,
    // so this is an inactivity timeout rather than a budget for the whole
    // build: a full result set is well over a hundred async Loaders and used to
    // blow through a fixed 800ms, dropping the veil mid-incubation so the rest
    // of the page (its badges included) was watched arriving card by card.
    Timer {
        id: searchBuildGuard; interval: 800
        onTriggered: { root._searchAwaitingLibrary = false; root.searchBuilding = false }
    }
    // One event-loop pass between the library answering and the veil dropping,
    // so every badge on the page has re-resolved first (see the presence
    // handler for why the order is not free).
    Timer {
        id: searchLibraryReveal; interval: 0
        onTriggered: { root._searchAwaitingLibrary = false; root._searchBuildMaybeReveal() }
    }
    // The veil's visual: every veiled element binds its opacity to this one
    // animated value instead of the raw building flag, so when the veil drops
    // the whole page fades in quickly (over the ambient water, which stays
    // visible behind the "Reading the wire…" hint) rather than snapping in
    // as one hard paint. The animation runs in ONE direction only: raising
    // the veil for a new build snaps the value to 0 in the same frame. A
    // two-way Behavior here let the freshly refilled headers and SHOW ALL
    // links ghost-fade for 180ms over the loading hint on every new search,
    // a glitchy flash of half-transparent furniture between pages.
    property real searchReveal: 1
    onSearchBuildingChanged: {
        if (searchBuilding) { searchRevealRise.stop(); searchReveal = 0 }
        else searchRevealRise.restart()
    }
    NumberAnimation { id: searchRevealRise; target: root; property: "searchReveal"; to: 1; duration: 180; easing.type: Easing.OutQuad }
    property real browseReveal: 1
    onBrowseBuildingChanged: {
        if (browseBuilding) { browseRevealRise.stop(); browseReveal = 0 }
        else browseRevealRise.restart()
    }
    NumberAnimation { id: browseRevealRise; target: root; property: "browseReveal"; to: 1; duration: 180; easing.type: Easing.OutQuad }
    property var browseChips: ({ genres: [], moods: [], decades: [] })
    property bool browseLoading: false
    property bool browseError: false
    property var browsePage: null          // {key, title, sections} when drilled in
    property string browsePageKey: ""      // "" = the Browse landing page
    property bool browsePageLoading: false
    property bool browsePageError: false
    // "Is this album already in my local library?" for the album page on
    // screen, resolved synchronously from the scan index (null = no match or
    // index not built, badge hidden). Re-resolved whenever the page changes
    // and whenever the library scan publishes (onLibraryPresenceChanged).
    property var libraryPresence: null
    // browsePage is assigned from a dozen places (a fresh load, Back, forward,
    // a tab restore, a revalidate). Resolving presence only where the page is
    // LOADED left Back and tab-restore showing the previous album's badge on
    // this album, pointing at the wrong folder, so hang it off the property
    // itself: every assignment re-resolves, and nothing has to remember to.
    onBrowsePageChanged: root._resolveLibraryPresence()
    function _resolveLibraryPresence() {
        var ph = (root.browsePage && root.browsePage.header) ? root.browsePage.header : null
        root.libraryPresence = (ph && ph.kind === "album")
            ? waves.libraryAlbumPresence(ph.artist || "", ph.title || "", "" + (ph.year || ""), ph.num_tracks || 0, ph.duration_sec || 0)
            : null
    }
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

    // ---- Open-water launch dials -----------------------------------------
    // Driven by bootOverlay (end of file). The scrim starts light so the
    // water shows under the WAVES wordmark, and the interface starts hidden;
    // the launch sequence animates both to their resting values exactly once.
    property real bootScrimLevel: 0.55
    property real bootContentShown: 0
    // What the reveal actually paints. The dial above is the launch sequence's
    // own progress; this is the interface's share of it, and a pending terms
    // gate takes the whole frame instead: nothing of the app is painted or
    // reachable until the agreement is answered. Revealing the interface first
    // and dropping the card on top of it left the app fully readable, and
    // usable, by someone who had agreed to nothing (reported from
    // livetesting). The gate rides the same dial, so the wordmark's zoom fades
    // straight into the card.
    readonly property real uiShown: termsGate.wanted ? 0 : bootContentShown
    // Paint the interface, invisibly, before the reveal needs it. The scene
    // graph skips a subtree whose opacity is 0 outright, so the first frame
    // that shows the interface pays for the whole page at once: every texture
    // upload, every glyph rastered, every material built. That bill landed
    // halfway through the wordmark zoom (the reveal starts 350ms into a 700ms
    // scale), which is the stutter that was seen, always at the same point
    // because the reveal always begins at the same point.
    //
    // Warming holds the interface a hair above the skip threshold (0.001)
    // during the version drain instead: far enough below perception to be
    // invisible over the scrim, far enough above zero for the renderer to do
    // the work. The drain is a text readout ticking on its own timer, so a
    // frame spent there costs nothing anyone can see, and by the time the
    // zoom starts every one of those caches is already warm.
    //
    // NOT wired to bootContentShown: that dial also ungates input, and the
    // interface must stay inert under the launch screen (issue #13).
    property bool bootWarming: false

    // ---- Ambient wave-loop background -----------------------------------
    // A muted, seamlessly looping ocean video (public-domain loop, re-encoded
    // 720p) sits behind every page under a heavy scrim so the Console palette
    // and text contrast survive. z:-1 keeps it below all content; playback
    // pauses via presentStalled (see onPresentStalledChanged below) once
    // frames stop reaching the glass, which covers hidden/minimised too.
    Video {
        id: bgWave
        anchors.fill: parent
        z: -1
        // Settings > Advanced > "Motion background". An empty source (off)
        // tears down the whole decode pipeline, so disabled means zero cost.
        property bool motionOn: waves.wavesPref("motion_background") !== false
        visible: motionOn
        // Launch opens on the flat dark frame (wordmark and version only) and
        // the water fades in slowly underneath during the opening hold, timed
        // to be fully present as the zoom begins; the pipeline's first-frame
        // readiness gates it so there is never a pop. After boot (the motion
        // toggle, a stream restart) the fade is short.
        // (Video aliases hasVideo but not mediaStatus; hasVideo flips true
        // once the media is loaded, just before the first frame paints.)
        opacity: hasVideo ? 1 : 0
        Behavior on opacity {
            NumberAnimation {
                // InCubic keeps the opening frame essentially dark for the
                // first third of the fade, so the wordmark screen registers
                // before the water builds under it.
                duration: root.bootContentShown === 0 ? 1200 : 350
                easing.type: root.bootContentShown === 0 ? Easing.InCubic : Easing.InOutSine
            }
        }
        source: motionOn ? Qt.resolvedUrl("assets/wave_loop.mp4") : ""
        loops: MediaPlayer.Infinite
        muted: true
        fillMode: VideoOutput.PreserveAspectCrop
        autoPlay: true
        onErrorOccurred: visible = false   // missing/undecodable asset: fall back to flat bg
        // Presentation continuity: the position of the last frame that
        // actually reached the glass (recorded per swap by onFrameSwapped).
        // While macOS holds presentation, the media clock keeps running, so
        // whenever frames return after a hold the loop seeks back here and
        // the water continues from the exact frame the user last saw.
        property int shownPos: 0
        function noteShown() {
            if (playbackState === MediaPlayer.PlayingState) shownPos = position
        }
        function seekShown() {
            if (!seekable || duration <= 0) return
            position = Math.min(Math.max(0, shownPos), duration - 40)
        }
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
            // One mechanism for every "nobody can see us" case: minimised,
            // hidden AND fully occluded (the old visibility branch missed
            // occlusion, so the loop played on under the covering window and
            // re-expose jumped to a different frame). The clock has already
            // run for the detector's threshold by the time the stall trips,
            // so pausing alone would park the loop past the last visible
            // frame: pause, then seek back to that frame, and re-expose
            // presents it immediately before playback resumes.
            function onPresentStalledChanged() {
                if (!bgWave.motionOn) return
                if (root.presentStalled) {
                    bgWave.pause()
                    bgWave.seekShown()
                } else {
                    bgWave.play()
                }
            }
        }
    }
    Rectangle {   // scrim: keeps the CRT-dark reading surface over the moving water
        id: bgScrim
        anchors.fill: parent
        z: -1
        visible: bgWave.motionOn
        color: root.bg
        // 0.93 is the resting value; the launch sequence holds it lighter so
        // the water reads through, then settles it here (see bootOverlay).
        opacity: root.bootScrimLevel
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
    // Stepped tick counter for sequenced LED animations (the queued stack's
    // walking highlight): consumers derive their step from a base tick they
    // capture when they appear, so every instance starts its sequence from
    // the beginning. Same one-write-per-tick discipline as ledPulse; when
    // nothing is queued nothing binds it.
    property int marchTick: 0
    // Presentation stall detector. When the window is fully covered (or
    // minimised/hidden), macOS stops asking it to render: frameSwapped goes
    // quiet while the media clock and every Timer keep running, so on
    // re-expose the water video and the ASCII clocks snapped to "now", a
    // visible jump to a completely different frame (livetest, confirmed by
    // the reactivation position jump in the diag trace). Watch presentation
    // itself: no swap for ~½s means nobody can see us, pause the decorative
    // motion, and the first swap after re-expose resumes it from the exact
    // frame the user last saw. Any real repaint (input, progress, an
    // animation) keeps the swaps flowing, so a visible-but-idle window
    // cannot false-positive its own clocks off: those clocks only stop
    // once frames already stopped.
    property bool presentStalled: false
    // Seeded with "now", not 0: the detector below runs from t=0 and a zero
    // seed reads as an hours-old swap, latching a spurious stall during boot
    // before the first frameSwapped has been delivered.
    property double _lastSwap: Date.now()
    onFrameSwapped: {
        _lastSwap = Date.now()
        bgWave.noteShown()
        if (presentStalled) presentStalled = false
    }
    // True while frames are demonstrably reaching the glass. Only meaningful
    // with the wave video playing (its sink keeps swaps flowing whenever the
    // window is presented); video off, scenes legitimately go idle between
    // repaints and swap age says nothing, so the gate stands open.
    function presentFresh() {
        return bgWave.playbackState !== MediaPlayer.PlayingState
            || Date.now() - _lastSwap <= 120
    }
    Timer {
        interval: 400; repeat: true; running: true
        // 1.1s: comfortably past the ~0.6s presentation hiccup macOS causes
        // on every app-switch transaction, so only a genuinely covered or
        // minimised window trips the pause; the switch hiccup must ride
        // through undisturbed.
        onTriggered: if (Date.now() - root._lastSwap > 1100) root.presentStalled = true
    }
    // On screen = not hidden or minimised, and actually being presented.
    // Decorative clocks (this one, the WaveMark water, the browse ticker,
    // the login phrases) pause on THIS, never on window focus: an unfocused
    // window usually stays fully visible next to whatever stole focus, and
    // the water freezing the instant the app loses focus (then lurching
    // back on refocus) reads as a glitch (livetest report).
    readonly property bool onScreen: visibility !== Window.Hidden && visibility !== Window.Minimized
                                     && !presentStalled
    Timer {
        running: root.onScreen
        interval: 50; repeat: true
        property real phase: 0
        onTriggered: {
            // Hold the step while presentation is stalled (app-switch hold,
            // covered window): stepped state advancing unseen is exactly the
            // "jumps forward when frames return" glitch the water video had.
            if (!root.presentFresh()) return
            phase = (phase + 0.05 / 1.04) % 1   // 1.04s breathe = 2 x 520ms
            root.ledPulse = 0.28 + 0.57 * (0.5 + 0.5 * Math.cos(2 * Math.PI * phase))
            root.shimmerPhase = (root.shimmerPhase + 0.05 / 1.6) % 1   // 1.6s twinkle cycle
            root.marchTick = (root.marchTick + 1) % 100000
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
    // Every user navigation flows through markNav, so it doubles as the bump
    // point for _navSeq: a counter that lets late search results detect the
    // user has moved on (see onSearchResults).
    property int _navSeq: 0
    // _navSeq's value at the moment the current search was issued.
    property int _searchSeq: -1
    function markNav(label) {
        _navSeq++
        markRender(label)
    }
    // A payload ARRIVING is not a navigation: it stamps the perf timer but must
    // never bump _navSeq. The Browse landing re-emits on every background
    // revalidate (near enough every launch, since the landing embeds the home
    // rows), and bumping there made onSearchResults discard the results the
    // user was waiting for: the status bar read "n results" while the pane
    // still showed the empty-state hint. Same for a late browse sub-page
    // payload, and for a results render (a second search typed right behind
    // the first must not look stale).
    function markRender(label) {
        _navLabel = label; _navT0 = Date.now(); _navPending = true; navTimer.restart()
    }
    Timer {
        id: navTimer; interval: 0; repeat: false
        onTriggered: {
            if (root._navPending) {
                root._navPending = false
                waves.uiLog("nav", root._navLabel, Date.now() - root._navT0)
            }
        }
    }

    // Create-or-get the reactive holder for a media id (writers only). Cloning
    // dlHolders on creation forces a change notification so any control already
    // bound to dlPct(id)/dlSt(id) starts tracking the new holder; that O(N)
    // rebind happens once per download start, not once per progress tick.
    function dlHolder(id) {
        var h = dlHolders[id]
        if (h === undefined) {
            h = dlHolderComp.createObject(root, { pct: -1, st: "" })
            if (h === null) return null
            var m = Object.assign({}, dlHolders); m[id] = h; dlHolders = m
        }
        return h
    }
    function dlPct(id) { var h = dlHolders[id]; return h !== undefined ? h.pct : -1 }
    function dlSt(id) { var h = dlHolders[id]; return h !== undefined ? h.st : "" }
    // The queue row a media id is waiting in, so a download button can cancel
    // its own wait without the queue drawer being opened. Walked on the click,
    // never bound: a queue of any size costs nothing until someone presses the
    // X. -1 = nothing of that id is waiting (it already started, or it is gone).
    function queuedQid(id) {
        if (!id) return -1
        for (var i = 0; i < queueModel.count; ++i) {
            var row = queueModel.get(i)
            if (row.media_id === id && row.status === "queued") return row.qid
        }
        return -1
    }
    function cancelQueuedMedia(id) {
        var qid = queuedQid(id)
        if (qid >= 0) waves.cancelQueueItem(qid)
    }

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
    // The overlay can be driven by EITHER player: its own (a cold open from a
    // row) or the hover peek's, adopted mid-playback when the card is clicked
    // (see promoteVideo). Every control and readout in the overlay reads
    // vPlayer so it never has to care which pipeline is sounding.
    property bool videoFromPeek: false   // the overlay is showing the adopted peek pipeline
    property bool videoUpgrading: false  // real-quality stream loading behind that picture
    property bool videoOnB: false        // which of the two stacked sinks is on stage
    readonly property MediaPlayer vPlayer: videoFromPeek ? peekPlayer : videoPlayer
    function openVideo(id, title, artist) {
        if (!id) return
        peekClose()     // the hover peek yields to the real player
        stopPreview()   // one thing plays at a time
        videoPlayer.stop(); videoPlayer.source = ""
        _videoStageReset()
        videoNow = { id: "" + id, title: title || "", artist: artist || "" }
        videoLoading = true; videoError = false; videoSwitching = false
        _videoSwapDone()
        waves.playVideo("" + id)
    }
    function closeVideo() {
        videoPlayer.stop(); videoPlayer.source = ""
        videoUpgradeRetry.stop(); videoUpgrading = false
        if (videoFromPeek) { peekPlayer.stop(); peekPlayer.source = ""; peekReady = false }
        videoFromPeek = false
        _videoStageReset()
        videoNow = null; videoLoading = false; videoError = false
        videoSwitching = false
        _videoSwapDone()
        vqMenu.visible = false
    }
    // Cold-open sink arrangement: the overlay's own player paints stage A,
    // the peek player owns the card's surface again.
    function _videoStageReset() {
        peekPlayer.videoOutput = peekSurface
        videoPlayer.videoOutput = vsA
        videoOnB = false
        videoOut.muted = false   // an abandoned upgrade must never leave it silent
    }
    // Switch the running video to a newly chosen resolution as seamlessly as
    // Qt allows: the current stream KEEPS PLAYING while the new variant URL
    // resolves in the background; only once it arrives do we swap the source
    // and jump back to the live position (a sub-second hiccup, not a restart).
    // The choice persists app-wide (setVideoQuality writes the same setting
    // the Settings page does).
    function changeVideoQuality(h) {
        // A promotion upgrade is already a resolve in flight on this video;
        // a second one would race it (the menu is hidden while it runs).
        if (!videoNow || videoSwitching || videoUpgrading || videoFromPeek) return
        waves.setVideoQuality(h)
        videoSwitching = true
        waves.playVideo(videoNow.id)
    }

    // --- Peek to full player, without a gap ------------------------------
    // The peek's pipeline is ALREADY decoding this video, so the click never
    // opens a second stream and never restarts: the running player simply
    // hands its picture to the overlay's stage and keeps sounding. A frame
    // grabbed from the card covers the one-frame sink move. Behind that live
    // picture, the real-quality stream loads into the second (invisible)
    // stage and takes over once both are at the same moment, so the step up
    // in resolution costs nothing visible either (see _videoCut).
    function promoteVideo() {
        var p = peekNow
        if (!p || videoNow) return
        peekLinger.stop()
        // The grab lands on the next render (~a frame), while the card is
        // still showing the video, so the handover has something to hide
        // behind. If the platform refuses the grab, go without it.
        if (!peekSurface.grabToImage(function(result){ root._videoAdopt(p, result) }))
            _videoAdopt(p, null)
    }
    function _videoAdopt(p, grab) {
        if (!peekNow || peekNow.id !== p.id) return   // closed or retargeted mid-grab
        stopPreview()
        if (grab) {
            _videoGrab = grab
            videoFreeze.source = grab.url
            videoFreeze.visible = true
            videoFreezeLift.restart()
        }
        videoNow = { id: p.id, title: p.title, artist: p.artist }
        videoLoading = false; videoError = false; videoSwitching = false
        videoPendingSeek = -1; _videoSeekTarget = -1; videoSeekRetry.stop()
        videoFromPeek = true; videoUpgrading = true
        // Sinks trade places: the live peek takes the stage, the overlay's
        // own player parks on the invisible one to warm the upgrade there.
        videoOnB = false
        videoPlayer.videoOutput = vsB
        peekPlayer.videoOutput = vsA
        peekNow = null   // the card goes away, its player does not
        waves.playVideo(p.id)
    }
    // Both pipelines are at the same moment: swap which one is on stage and
    // which one is audible, in a single frame, then retire the peek.
    function _videoCut() {
        videoUpgradeRetry.stop()
        videoUpgrading = false
        _videoUpPhase = 0
        if (!videoFromPeek) return
        var wasPaused = peekPlayer.playbackState !== MediaPlayer.PlayingState
        // The upgrade is parked on the very frame the peek is showing, so the
        // stage flip reveals a picture that is already correct. Retire the
        // glance stream first so the two never sound at once.
        videoOnB = true
        peekPlayer.stop(); peekPlayer.source = ""
        peekPlayer.videoOutput = peekSurface
        videoOut.muted = false
        if (!wasPaused) videoPlayer.play()
        videoFromPeek = false
        peekReady = false
    }
    // The upgrade could not happen (no stream, or it failed): stay on the
    // peek's pipeline, which is playing perfectly well, just at glance
    // resolution. Nothing user-visible happens here.
    function _videoUpgradeGiveUp() {
        videoUpgradeRetry.stop()
        videoUpgrading = false
        _videoUpPhase = 0
        videoPlayer.stop(); videoPlayer.source = ""
        videoOut.muted = false
    }
    // The peek's own pipeline died while it was driving the overlay. Take
    // the upgrade if it is ready, else surface the failure like a cold open.
    function _videoPromotionLost() {
        if (videoUpgrading && (videoPlayer.mediaStatus === MediaPlayer.LoadedMedia
                || videoPlayer.mediaStatus === MediaPlayer.BufferedMedia)) {
            _videoCut()
            return
        }
        videoUpgradeRetry.stop(); videoUpgrading = false
        videoFromPeek = false; peekReady = false
        _videoStageReset()
        videoLoading = false
        videoError = true
    }

    // --- Hover video peek (floating card, sound but no controls) ---------
    // peekNow: {id, title, artist, art} while the card is up, else null. A
    // short dwell on a video thumbnail grows the card out of the thumb's
    // rect; the backend resolves a low variant (waves.peekVideo) and it
    // plays with sound until the pointer leaves. A click opens the full
    // player. This is a glance surface: no scrubber, no buttons, no chrome.
    property var peekNow: null
    property bool peekReady: false       // first frames are on the surface
    property bool peekThumbHover: false  // pointer is over the source thumb
    function peekOpen(anchor, id, title, artist, art) {
        if (!videoHoverPeek) return   // Settings > Advanced switched previews off
        if (!id || videoNow) return
        if (peekNow && peekNow.id === ("" + id)) return
        peekPlayer.stop(); peekPlayer.source = ""
        peekReady = false
        var p = anchor.mapToItem(peekCard.parent, 0, 0)
        peekNow = { id: "" + id, title: title || "", artist: artist || "", art: art || "" }
        peekCard.growFrom(p.x, p.y, anchor.width, anchor.height)
        peekLinger.stop()
        waves.peekVideo("" + id)
    }
    function peekClose() {
        peekLinger.stop()
        peekPlayer.stop(); peekPlayer.source = ""
        if (peekNow) { peekCooldown = true; peekCooldownTimer.restart() }
        peekNow = null; peekReady = false
    }
    // Brief rest after a peek ends before the next may start earning its
    // dwell, so glances across a grid of large thumbnails cannot chain-fire
    // stream requests (see BigVideoThumb).
    property bool peekCooldown: false
    Timer { id: peekCooldownTimer; interval: 400; onTriggered: root.peekCooldown = false }
    // The card lives while the pointer is over the thumb OR the card; when
    // both are gone a short grace covers the travel between them (and the
    // moment the growing card slides under the cursor).
    function peekHoverCheck() {
        if (!peekNow) return
        if (peekThumbHover || peekCardHover.hovered) { peekLinger.stop(); return }
        peekLinger.restart()
    }
    Timer { id: peekLinger; interval: 260; onTriggered: root.peekClose() }

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
        peekClose()   // a deliberate track preview outranks a hover glance
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

    // Back/forward navigation (triggered by the back bar, the native swipe
    // gesture, or the mouse side buttons, detected app-side in
    // WavesBridge.eventFilter; the swipe gesture stays back-only).
    // ---- Navigation history --------------------------------------------
    // Swipe-back / back bars return to where you actually WERE (search page,
    // a genre page, an artist), not to a fixed hierarchy. Each view change
    // pushes a snapshot of the view being left; navBack() pops and restores
    // it, pushing the left view onto navForwardHistory so navForward() can
    // return to it.
    property var navHistory: []
    property var navForwardHistory: []
    property bool _navRestoring: false
    // Armed by navBack when the target artist must be re-loaded: onArtistLoaded
    // resets the expansion state on every full load, so a Back re-applies the
    // snapshot's state ({id, ex, bio}) from here instead. The matching
    // scroll restore is armed on artistView (pendingRestoreKey/Y), pre-paint.
    property var _artistRestoreState: null
    // Which top-level section the user is "in" for the nav tabs: drilling into
    // an artist or album page keeps the tab of the section it was opened from
    // lit (to the user they never left Browse/Search/My Tidal). Only explicit
    // section switches (tab clicks, a new search, Back/Forward across sections)
    // move it.
    property string navOrigin: "browse"
    function navSig(s) { return s.v + "|" + (s.key || "") + "|" + (s.id || "") + "|" + (s.cat || "") }
    function navSnapshot() {
        if (settingsOpen) return { v: "settings", label: "Settings" }
        if (libraryOpen) return { v: "library", cat: libraryCategory, label: "My Tidal" }
        if (artistOpen) return { v: "artist", id: artistData ? "" + artistData.id : "",
                                 label: artistData ? (artistData.name || "Artist") : "Artist",
                                 scrollY: artistView.contentY, ex: expandedAlbums,
                                 bio: bioExpanded }
        if (browseOpen) return { v: "browse", key: browsePageKey, page: browsePage,
                                 stack: browseStack.slice(), hi: browseHighlightId,
                                 scrollY: browsePageKey === "" ? browseLanding.contentY : browseDrill.contentY,
                                 label: browsePageKey === "" ? "Browse"
                                       : browsePage ? (browsePage.title || "Browse")
                                       : (browseTitleHint !== "" ? browseTitleHint : "Browse") }
        return { v: "search", label: "Search" }
    }
    function navPush() {
        if (_navRestoring) return
        _navRestored = false     // a genuine navigation: the trim guard must not carry over
        navForwardHistory = []   // a genuine new navigation invalidates any redo history
        var s = navSnapshot()
        s.o = navOrigin   // restore the lit tab along with the view
        if (navHistory.length > 0 && navSig(navHistory[navHistory.length - 1]) === navSig(s)) return
        navHistory = navHistory.concat([s]).slice(-50)
    }
    function navBackLabel() { return navHistory.length > 0 ? navHistory[navHistory.length - 1].label : "" }
    function navForwardLabel() { return navForwardHistory.length > 0 ? navForwardHistory[navForwardHistory.length - 1].label : "" }
    function navBack() {
        var levelUp = navHistory.length === 0
        // A Back press that lands nowhere (nothing recorded, no surface open:
        // the search root) must stay a complete no-op. Marking it would bump
        // _navSeq and make an in-flight search's results get discarded, and
        // recording a forward entry would cost one dead Forward press per
        // over-press. navForward() already returns before its markNav.
        if (levelUp && !settingsOpen && !libraryOpen && !artistOpen && !browseOpen) return
        markNav("back")
        saveSearchView()   // leaving Search via Back must also keep its drill-in restorable
        var fwd = navSnapshot()
        fwd.o = navOrigin
        navForwardHistory = navForwardHistory.concat([fwd]).slice(-50)
        if (levelUp) {
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
        _navRestore(s)
    }
    function navForward() {
        if (navForwardHistory.length === 0) return
        markNav("forward")
        saveSearchView()   // leaving Search via Forward must also keep its drill-in restorable
        var s = navSnapshot()
        s.o = navOrigin
        navHistory = navHistory.concat([s]).slice(-50)
        var t = navForwardHistory[navForwardHistory.length - 1]
        navForwardHistory = navForwardHistory.slice(0, navForwardHistory.length - 1)
        _navRestore(t)
    }
    function _navRestore(s) {
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
            if (artistData && ("" + artistData.id) === s.id) {
                artistOpen = true; settingsOpen = false; libraryOpen = false
                // The page's content is still loaded; put back the state other
                // tabs may have clobbered (loadLib clears expandedAlbums), and
                // land on the saved spot same-frame, pre-paint (the pane only
                // becomes visible this frame, same rule as openSearch).
                expandedAlbums = s.ex || ({})
                bioExpanded = !!s.bio
                // Section expansion is a persisted per-section pref now (like
                // the search sections), so snapshots neither carry nor restore
                // it: the pref would just be overwritten with stale state.
                if (s.scrollY !== undefined)
                    artistView.contentY = Math.min(s.scrollY, Math.max(0, artistView.contentHeight - artistView.height))
            } else if (s.id) {
                // The artist must be re-loaded (usually a warm backend cache).
                // Arm the pre-paint scroll restore, tagged with the artist id,
                // and stash the expansion state for onArtistLoaded to re-apply.
                artistView.pendingRestoreKey = s.id
                artistView.pendingRestoreY = (s.scrollY !== undefined ? s.scrollY : -1)
                _artistRestoreState = { id: s.id, ex: s.ex, bio: s.bio }
                waves.loadArtist(s.id); return   // flag cleared in onArtistLoaded
            }
        } else if (s.v === "browse") {
            browseOpen = true; settingsOpen = false; artistOpen = false; libraryOpen = false
            browseStack = s.stack || []
            browseHighlightId = s.hi || ""
            var bkey = s.key || ""
            if (bkey === "") {
                // The landing pane is alive and still positioned; showing it
                // is the whole restore. Its own contentY is the truth (a
                // revalidate may have reshaped the page since the snapshot),
                // so the recorded scrollY is deliberately not re-applied.
                // browsePage deliberately stays set: the drill pane stays
                // alive behind the landing, so Forward (or reopening the
                // same page) costs nothing and Back pays no teardown.
                browsePageKey = ""
                browseTitleHint = ""
                browsePageLoading = false; browsePageError = false
            } else if (bkey === browsePageKey && browsePage) {
                // The drilled page snapshotted is the one still loaded in
                // the drill pane (a round trip through another section):
                // alive, positioned, possibly fresher than the snapshot.
                // Reassigning browsePage here would rebuild it for nothing.
            } else {
                // A different drilled page: this is a real rebuild, so arm
                // the scroll restore BEFORE changing the page key. It's
                // tagged with the destination key so the pane applies it
                // (rather than jumping to the top) once that page is
                // showing, pre-paint, and Back lands where you left off.
                browseDrill.pendingRestoreKey = bkey
                browseDrill.pendingRestoreY = (s.scrollY !== undefined ? s.scrollY : -1)
                browsePageKey = bkey
                browsePage = s.page || null
                // A page snapshotted mid-load has no payload; its label is
                // the best (and only) name for it until the user reloads it.
                browseTitleHint = !s.page ? (s.label || "") : ""
                browsePageLoading = false; browsePageError = false
            }
            if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
                browseLoading = true; browseError = false; waves.loadBrowse()
            } else if (waves.loggedIn) {
                waves.refreshBrowse()   // silent, throttled; repaints only on change
            }
        } else {   // search
            settingsOpen = false; artistOpen = false; libraryOpen = false; browseOpen = false
        }
        _navRestoring = false
        _navRestored = true   // survives the 0ms crumb-trim debounce (see crumbTrimTimer)
    }
    // ---- Breadcrumb trail over the history -------------------------------
    // The artist and browse sub-page back bars render the whole navHistory as
    // a crumb trail (NavCrumbTrail) with the current page as the last, lit
    // pill. These mirror navSnapshot()'s label and navSig() WITHOUT reading
    // scroll positions, so the bindings don't re-evaluate on every scrolled
    // frame.
    // A keyed browse page still loading with no title hint yields "": the
    // trail holds the crumb back rather than flash "Browse" and then swap in
    // the real name when the payload arrives (reported from livetesting).
    readonly property string currentNavLabel: settingsOpen ? "Settings"
        : libraryOpen ? "My Tidal"
        : artistOpen ? (artistData ? (artistData.name || "Artist") : "Artist")
        : browseOpen ? (browsePageKey === "" ? "Browse"
                        : browsePage ? (browsePage.title || "Browse")
                        : browseTitleHint)
        : "Search"
    // The page is open but cannot be named yet (opened without a title hint,
    // payload not in). The trail holds its crumb back rather than flash
    // "Browse", which leaves the PARENT as the last pill: without this, the
    // parent would be lit as though it were the page you are on, and its click
    // disabled for the same reason, so the one crumb that could get you back
    // stops working. Reached from the now-playing title and the video player,
    // which know an album id but no album name, and it lasts until the payload
    // arrives (indefinitely, if the load errors).
    readonly property bool crumbTailPending: currentNavLabel === ""
    function currentNavSig() {
        var v = settingsOpen ? "settings" : libraryOpen ? "library"
              : artistOpen ? "artist" : browseOpen ? "browse" : "search"
        return v + "|" + (v === "browse" ? browsePageKey : "")
                 + "|" + (v === "artist" ? (artistData ? "" + artistData.id : "") : "")
                 + "|" + (v === "library" ? libraryCategory : "")
    }
    // SECTION-SCOPED TRAIL. The trail shows the drill-in you are actually
    // inside, never the tabs you passed through to get here: switching tab
    // starts a fresh trail, drilling deeper extends it. Every snapshot records
    // the section it was taken in (s.o, the lit tab), so the trail is the run
    // of entries at the tail of the history whose section is the current one;
    // the first entry from another section ends it. Back and Forward keep
    // working ACROSS sections (they walk the whole navHistory), they just stop
    // spelling the crossing out in crumbs.
    // This is also what bounds the trail: its depth is how deep you drilled,
    // not how much you browsed. Tab-flipping cannot stack it.
    readonly property int crumbBase: {
        var k = navHistory.length
        while (k > 0 && (navHistory[k - 1].o || "") === navOrigin) k--
        return k
    }
    // SYNTHETIC SECTION-ROOT CRUMB. A drilled page can be the whole trail:
    // returning to a section via its nav tab lands on the page it was left on
    // (keep-alive), but the tab switch also started a fresh section-scoped
    // trail, so the crumb row held a single, unclickable pill and the only
    // way up was the tab button or the Back gesture (reported from
    // livetesting: "stuck in a playlist with no breadcrumbs"). Whenever the
    // trail of a drilled page does not begin at a section root, a synthetic
    // root pill ("Browse", "Search", "My Tidal") leads it, and clicking it
    // climbs out of the drill within the section (the keep-alive panes make
    // that a pure flip). It is display-only: history is untouched until it
    // is clicked, which records the drilled page like any navigation.
    readonly property bool crumbSynth: {
        var drilled = artistOpen || (browseOpen && !settingsOpen && !libraryOpen && browsePageKey !== "")
        if (!drilled || settingsOpen) return false
        if (crumbBase >= navHistory.length) return true
        return !navIsRoot(navHistory[crumbBase])
    }
    readonly property string crumbSynthLabel:
        navOrigin === "library" ? "My Tidal" : navOrigin === "browse" ? "Browse" : "Search"
    function crumbSynthGo() {
        navPush()
        markNav("crumb root")
        if (navOrigin === "browse") {
            // Up to the Browse landing: the drilled page idles behind it
            // (keep-alive), its lineage is done (same as the tab's home
            // strike), but the landing keeps its own scroll.
            browseStack = []
            browseHighlightId = ""
            browseTitleHint = ""
            browsePageLoading = false; browsePageError = false
            browsePageKey = ""
            artistOpen = false; settingsOpen = false; libraryOpen = false
            browseOpen = true
        } else if (navOrigin === "library") {
            artistOpen = false; settingsOpen = false; browseOpen = false
            libraryOpen = true
        } else {
            // Search: uncover the results pane exactly as it stands.
            artistOpen = false; settingsOpen = false; libraryOpen = false
            browseOpen = false
        }
    }
    readonly property var crumbLabels: {
        var out = []
        if (crumbSynth) out.push(crumbSynthLabel)
        for (var i = crumbBase; i < navHistory.length; i++) out.push(navHistory[i].label || "")
        if (currentNavLabel !== "") out.push(currentNavLabel)   // "" = name not known yet
        return out
    }
    // TRIM-ON-REVISIT: arriving at a SECTION ROOT (Search, My Tidal, Browse
    // home, Settings) that is already in the history cuts the history back
    // to just before it, so flipping between two tabs bounces between two
    // crumbs instead of stacking Search > My Tidal > Search > ... twenty
    // deep (and Back stops replaying the oscillation). Deep pages (artist,
    // browse sub-pages) are deliberately NOT trimmed: reopening a playlist
    // page from inside a folder must keep the folder in the history or Back
    // would skip it (the exact bug test_folder_back_navigation fences off).
    // Debounced through a 0ms timer so it runs once the navigation functions
    // have finished flipping flags, never against a half-switched state.
    // _navRestoring is already back to false by the time the 0ms timer fires,
    // so that guard alone never fired: a Back or Forward landing on a section
    // root would trim away the very history it had just walked into, and the
    // next Back press fell through to the level-up fallback. Latch the restore
    // across the debounce instead (cleared by the timer, and by navPush so a
    // genuine navigation can never inherit it).
    property bool _navRestored: false
    onCrumbLabelsChanged: crumbTrimTimer.restart()
    Timer {
        id: crumbTrimTimer; interval: 0
        onTriggered: {
            var restored = root._navRestored
            root._navRestored = false
            if (!restored) root.crumbTrimRevisit()
        }
    }
    // A section root: a tab's own landing, nothing drilled into. Only these
    // are disposable (see crumbTrimRevisit).
    function navIsRoot(s) {
        return s.v === "search" || s.v === "library" || s.v === "settings"
            || (s.v === "browse" && (s.key || "") === "")
    }
    function crumbTrimRevisit() {
        if (_navRestoring) return
        if (artistOpen || (browseOpen && browsePageKey !== "")) return
        var sig = currentNavSig()
        for (var k = 0; k < navHistory.length; k++) {
            if (navSig(navHistory[k]) !== sig) continue
            // Everything from here on is about to be discarded, so it may only
            // be the oscillation itself. A real page in the way (an artist, a
            // browse sub-page) means this is not a tab flip but a journey, and
            // dropping it would make Back skip a page the user actually opened:
            // Browse > artist > Search tab > Browse tab used to lose the artist
            // from Back entirely. Leave the history alone; the trail stays
            // short on its own, because it only shows this section (crumbBase).
            for (var j = k; j < navHistory.length; j++)
                if (!navIsRoot(navHistory[j])) return
            navHistory = navHistory.slice(0, k)
            return
        }
    }
    // Crumb click: jump straight back to history entry i. Dropping everything
    // after it and letting navBack pop-and-restore entry i itself reuses the
    // whole restore path (scroll spot, expanded albums, pre-paint landing).
    function navTo(i) {
        if (i < 0 || i >= navHistory.length) return
        navHistory = navHistory.slice(0, i + 1)
        navBack()
    }
    // Set the target view true BEFORE clearing the others: the Search tab's
    // `active` is `!artistOpen && !libraryOpen && !settingsOpen`, so clearing the
    // old view first would transiently make Search active and fire its power-on
    // animation mid-switch. Target-first keeps Search inactive throughout.
    function openLibrary() {
        saveSearchView()
        // Second press, My Tidal already active: land on Home, mirroring the
        // Browse tab's second press landing on its main page.
        var alreadyActive = libraryOpen && !artistOpen && !settingsOpen && navOrigin === "library"
        if (alreadyActive) { navPush(); markNav("library home"); loadLib("home"); return }
        navPush(); markNav("library"); navOrigin = "library"
        libraryOpen = true; settingsOpen = false; artistOpen = false
        // RETURN to the category the user left, not always Home. loadLib is
        // keep-alive: a category whose rows are already on screen keeps them
        // (and its scroll) untouched and only revalidates quietly.
        loadLib(libraryCategory)
    }

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
                bio: bioExpanded,
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
                    bioExpanded = !!s.bio
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
        playlistTrackCache = ({}); expandedPlaylists = ({})
        _searchBuildStart(0)   // a mid-build blank must drop the veil with the cards
        artistsModel.clear(); albumsRaw = []; tracksRaw = []; videosRaw = []; applySort()
        playlistsModel.clear(); mixesModel.clear()
        searchField.forceActiveFocus()
    }
    function loadLib(cat) {
        libraryCategory = cat
        libLoadingMore = false
        // "Home" is a self-contained, Browse-shaped landing kept on screen:
        // re-opening My Tidal shows the shelves it already has, instantly. The
        // backend serves the first load from its disk snapshot and every visit
        // triggers a quiet, throttled revalidation (repainting only when the
        // favourites changed), so an app left running still stays current. The
        // placeholder glyph shows only on the very first run (nothing cached
        // yet). Logout drops the cache (onLoggedInChanged), so a different
        // account still refetches.
        if (cat === "home") {
            waves.loadHome(root.homeSections.length > 0)
            return
        }
        // Every category pane is keep-alive: switching only changes which one
        // is visible, and each keeps its rows, expansion and scroll for its
        // whole life. A category that already has rows shows them as-is and
        // revalidates quietly (the backend repaints only on change, and the
        // refill pins the scroll spot); only a still-empty category does a
        // visible first load.
        var m = libModelFor(cat)
        if (m && m.count > 0) {
            libPinRefill = true
            waves.loadLibrary(cat, true)
            return
        }
        libPinRefill = false
        libCatHasMore[cat] = false
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
        // sub-page (genre / playlist / album) and its stack. The landing
        // pane is alive; this only flips it visible.
        browseStack = []
        browsePageKey = ""
        browseTitleHint = ""   // the landing is "Browse", never the page we left
        browsePageLoading = false
        browsePageError = false
        browseHighlightId = ""
        browseOpen = true
        settingsOpen = false; artistOpen = false; libraryOpen = false
        // The tab button is an explicit "take me to the top of Browse":
        // cancel any armed hold and reset the landing's scroll directly
        // (the pane is alive, so a plain set is exact).
        browseLanding.pendingRestoreY = -1
        browseLanding.contentY = 0
        if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
            browseLoading = true; browseError = false
            waves.loadBrowse()
        } else if (waves.loggedIn) {
            waves.refreshBrowse()   // silent, throttled; repaints only on change
        }
    }
    function openBrowseLink(path, title) {
        navPush()
        // browsePage stays set while the landing shows (the drill pane
        // is kept alive for an instant return), so "there is a page" is
        // browsePageKey, not browsePage: pushing the stale object here
        // would make Back detour through a page the user already left.
        if (browsePageKey !== "" && browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = ""
        browsePageKey = path
        browsePage = null
        browseTitleHint = title || ""   // names the crumb until the payload lands
        browsePageError = false
        browsePageLoading = true
        waves.openBrowsePage(path, title)
    }
    // Re-issue the current drilled page's fetch after an error. The key alone
    // says which backend entry built the page: openBrowsePage only answers
    // pages/… paths, so routing a pl:… (filtered playlists grid) or item:…
    // (playlist/mix/album page) key through it makes no request at all and
    // the loading hint never clears. No navPush: a retry is not a navigation.
    function retryBrowsePage() {
        var key = "" + browsePageKey
        browsePageError = false
        browsePageLoading = true
        if (key.indexOf("pl:") === 0) {
            waves.openBrowsePlaylists(key.substring(3), browseTitleHint)
        } else if (key.indexOf("item:") === 0) {
            var rest = key.substring(5)
            var cut = rest.indexOf(":")
            waves.openBrowseItem(rest.substring(0, cut), rest.substring(cut + 1))
        } else {
            waves.openBrowsePage(key, browseTitleHint)
        }
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
        // browsePage stays set while the landing shows (the drill pane
        // is kept alive for an instant return), so "there is a page" is
        // browsePageKey, not browsePage: pushing the stale object here
        // would make Back detour through a page the user already left.
        if (browsePageKey !== "" && browsePage) browseStack = browseStack.concat([browsePage])
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
    // Browse's folder-style Playlists view. Level 1 (openPlaylistsRoot) is a
    // local page of the wayfinding clouds re-cast as playlist folders (moods
    // first, they are the most playlist-dense); level 2 (openPlaylistsFolder)
    // fetches that category's page filtered to just its playlists. Items
    // carry pl: true so the shared links/tile delegates route back here
    // instead of to the unfiltered page.
    function plChips(chips) {
        return (chips || []).map(function(c) { return { title: c.title, path: c.path, pl: true } })
    }
    function openPlaylistsFolder(path, title) {
        var key = "pl:" + path
        if (browsePageKey === key) return
        navPush()
        // browsePage stays set while the landing shows (the drill pane
        // is kept alive for an instant return), so "there is a page" is
        // browsePageKey, not browsePage: pushing the stale object here
        // would make Back detour through a page the user already left.
        if (browsePageKey !== "" && browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = ""
        browsePageKey = key
        browsePage = null
        browseTitleHint = title || ""   // names the crumb until the payload lands
        browsePageError = false
        browsePageLoading = true
        waves.openBrowsePlaylists(path, title)
    }
    function openPlaylistsRoot() {
        var key = "cloud:All Playlists"
        if (browsePageKey === key) return
        navPush()
        // browsePage stays set while the landing shows (the drill pane
        // is kept alive for an instant return), so "there is a page" is
        // browsePageKey, not browsePage: pushing the stale object here
        // would make Back detour through a page the user already left.
        if (browsePageKey !== "" && browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = ""
        browsePageKey = key
        browsePageError = false
        browsePageLoading = false
        browsePage = { key: key, title: "All Playlists",
                       sections: [{ rowKind: "links", title: "Moods & Activities", items: plChips(browseChips.moods) },
                                  { rowKind: "links", title: "Genres", items: plChips(browseChips.genres) },
                                  { rowKind: "links", title: "Decades", items: plChips(browseChips.decades) }] }
    }
    function openBrowseCloud(title, chips) {
        var key = "cloud:" + title
        if (browsePageKey === key) return
        navPush()
        // browsePage stays set while the landing shows (the drill pane
        // is kept alive for an instant return), so "there is a page" is
        // browsePageKey, not browsePage: pushing the stale object here
        // would make Back detour through a page the user already left.
        if (browsePageKey !== "" && browsePage) browseStack = browseStack.concat([browsePage])
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
        _browseAsyncBuild = false   // growth re-lays the landing in place, never streamed
        var s = grown(browseSections)
        var ps = (browsePage && browsePage.sections) ? grown(browsePage.sections) : null
        // Growth rebuilds a column in place; hold the user's spot across it,
        // on whichever pane actually re-lays.
        if (s) browseLanding.holdScroll()
        if (ps) browseDrill.holdScroll()
        if (s) browseSections = s
        if (ps) browsePage = Object.assign({}, browsePage, { sections: ps })
    }
    // Open one playlist / mix / album as its own page inside Browse (art
    // header + track list). Drilling from an editorial page pushes it onto
    // browseStack so Back walks up one level at a time.
    function openBrowseItem(kind, id, highlight, title) {
        if (browsePageKey === "item:" + kind + ":" + id) {
            // Already keyed to this page, nothing to fetch. But the key
            // survives leaving Browse via the nav tabs, so "already there"
            // is only true when Browse is the active surface. Arriving from
            // another surface (a folder row, a My Tidal shelf) must still
            // record where the user came from: this guard used to return
            // without pushing, so Back skipped the folder entirely and fell
            // through to whatever was under it in the history (Search).
            if (!browseOpen || artistOpen || settingsOpen || libraryOpen) navPush()
            browseHighlightId = highlight || ""
            browseHighlightPending = false
            return
        }
        navPush()
        // browsePage stays set while the landing shows (the drill pane
        // is kept alive for an instant return), so "there is a page" is
        // browsePageKey, not browsePage: pushing the stale object here
        // would make Back detour through a page the user already left.
        if (browsePageKey !== "" && browsePage) browseStack = browseStack.concat([browsePage])
        browseHighlightId = highlight || ""
        browseHighlightPending = false   // the highlighted row re-arms this on layout
        browsePageKey = "item:" + kind + ":" + id
        browsePage = null
        browseTitleHint = title || ""   // names the crumb until the payload lands
        browsePageError = false
        browsePageLoading = true
        waves.openBrowseItem(kind, id)
    }
    function browseBack() {
        browsePageLoading = false
        browsePageError = false
        browseHighlightId = ""
        browseTitleHint = ""
        if (browseStack.length > 0) {
            var s = browseStack.slice()
            var prev = s.pop()
            browseStack = s
            browsePage = prev
            browsePageKey = prev.key
        } else {
            // Home: flip the landing visible. browsePage stays set so the
            // page just left survives behind it (see _navRestore).
            browsePageKey = ""
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
    function openAlbumPage(albumId, highlight, title) {
        if (!albumId || onAlbumPage(albumId)) return
        markNav("browse")
        openBrowseItem("album", albumId, highlight || "", title || "")   // snapshots the view being left
        settingsOpen = false; artistOpen = false; libraryOpen = false
        browseOpen = true
        if (waves.loggedIn && browseSections.length === 0 && !browseLoading) {
            browseLoading = true; browseError = false
            waves.loadBrowse()
        }
    }
    // Same page for a playlist from anywhere (My Tidal rows included): the
    // synthesized art-header + track-list browse page, switching the Browse
    // surface in just like openAlbumPage does for albums.
    function openPlaylistPage(playlistId, title) {
        if (!playlistId) return
        if (browseOpen && !artistOpen && !settingsOpen && !libraryOpen
            && browsePageKey === "item:playlist:" + playlistId) return
        markNav("browse")
        openBrowseItem("playlist", playlistId, "", title || "")
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
            openBrowseItem(kind, card.id, "", card.title || "")
        } else if (kind === "track") {
            if (card.album_id) openBrowseItem("album", card.album_id, card.id, card.album || "")
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
    // Lifts the frame grabbed from the peek card once the adopted pipeline is
    // painting the overlay's stage (the sink move takes a frame or two).
    Timer {
        id: videoFreezeLift
        interval: 260
        onTriggered: { videoFreeze.visible = false; root._videoGrab = null }
    }
    // Promotion upgrade, park and meet.
    //
    // Cutting on POSITION alone is not enough, and livetesting showed exactly
    // why: position reports a seek target the instant it is set, while the
    // sink still holds whatever frame it had before (for a stream that just
    // started, frame one). The swap then flashed the first frame for a beat
    // before the seeked picture arrived. So the upgrade does not chase: it
    // parks PAUSED a couple of seconds AHEAD of the live clock, which makes
    // the backend decode and present that exact frame into the off-stage
    // sink, and the cut waits until the peek's own clock reaches the parked
    // frame. Both sides are then showing the same moment, and the swap is a
    // true continuation with nothing stale to reveal.
    property int _videoUpTries: 0
    property int _videoUpAims: 0
    property int _videoUpPhase: 0     // 0 opening, 1 seeking to the park, 2 parked and waiting
    property real _videoUpTarget: -1
    function _videoUpAimAt(t) {
        if (++_videoUpAims > 6) { _videoUpgradeGiveUp(); return }
        // Not enough runway left to stage a handover before the video ends.
        if (videoPlayer.duration > 0 && t > videoPlayer.duration - 800) { _videoUpgradeGiveUp(); return }
        _videoUpTarget = Math.max(0, t)
        _videoUpPhase = 1
        videoPlayer.pause()
        videoPlayer.position = _videoUpTarget
    }
    Timer {
        id: videoUpgradeRetry
        interval: 60; repeat: true
        onTriggered: {
            if (!root.videoUpgrading || !root.videoFromPeek || !root.videoNow) { root._videoUpgradeGiveUp(); return }
            if (++root._videoUpTries > 250) { root._videoUpgradeGiveUp(); return }   // ~15s, stay on the glance stream
            if (videoPlayer.mediaStatus !== MediaPlayer.LoadedMedia
                    && videoPlayer.mediaStatus !== MediaPlayer.BufferedMedia) return
            var live = peekPlayer.position
            var playing = peekPlayer.playbackState === MediaPlayer.PlayingState
            if (root._videoUpPhase === 0) {
                // First moment the new stream is usable: park it ahead of the
                // live clock (or right on it, when the peek is paused).
                root._videoUpAimAt(playing ? live + 2000 : live)
                return
            }
            if (root._videoUpPhase === 1) {
                // Parked once the seek has actually landed, which is also when
                // the sink is holding the frame we are going to reveal.
                if (Math.abs(videoPlayer.position - root._videoUpTarget) <= 250) { root._videoUpPhase = 2; return }
                // The live clock overtook the park before the seek landed
                // (slow seek, or the user jumped forward): aim further ahead.
                if (playing && live > root._videoUpTarget - 250)
                    root._videoUpAimAt(live + 2000 * (root._videoUpAims + 1))
                return
            }
            // Parked and holding: meet the live clock at that exact frame.
            if (!playing) {
                if (Math.abs(videoPlayer.position - live) <= 250) { root._videoCut(); return }
                root._videoUpAimAt(live)   // paused somewhere else, re-park on the spot
                return
            }
            if (live >= root._videoUpTarget - 40) root._videoCut()
        }
    }
    MediaPlayer {
        id: videoPlayer
        videoOutput: vsA
        audioOutput: AudioOutput { id: videoOut }
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
            // A failed UPGRADE is invisible: the peek's pipeline is on stage
            // and playing, so the video just stays at glance resolution.
            if (root.videoUpgrading) { root._videoUpgradeGiveUp(); return }
            if (root.videoNow) { root.videoError = true; root.videoLoading = false }
        }
    }
    // ==== Hover video peek card =========================================
    // Grows out of the hovered thumbnail's mapped rect into a 16:9 card
    // centered on it (clamped inside the window). The thumbnail's own art
    // holds the surface until frames arrive so the growth never reveals a
    // black panel; sound eases in rather than blasting. Sits under the full
    // player overlay (z 999) so opening the real player covers it.
    MediaPlayer {
        id: peekPlayer
        videoOutput: peekSurface
        audioOutput: AudioOutput {
            volume: root.peekReady ? 1 : 0
            Behavior on volume { NumberAnimation { duration: 420 } }
        }
        onPlaybackStateChanged: {
            if (playbackState === MediaPlayer.PlayingState) {
                root.peekReady = true
                // One thing sounds at a time (same rule as the full player),
                // but a hover glance only pauses a running track preview, it
                // never tears the preview down.
                if (previewPlayer.playbackState === MediaPlayer.PlayingState) previewPlayer.pause()
            }
        }
        onMediaStatusChanged: {
            if (mediaStatus !== MediaPlayer.EndOfMedia) return
            // Once promoted this player IS the overlay, so its end is the
            // video's end, not a hover ending.
            if (root.videoFromPeek) root.closeVideo(); else root.peekClose()
        }
        onErrorOccurred: function(err, errStr) {
            // A failed glance just goes away, no error chrome on a hover.
            waves.uiLog("video", "peek player error " + err + ": " + errStr, -1)
            if (root.videoFromPeek) root._videoPromotionLost(); else root.peekClose()
        }
    }
    Rectangle {
        id: peekCard
        z: 990
        visible: root.peekNow !== null
        color: "#0b0d10"; radius: 8
        border.color: root.border1; border.width: 1
        clip: true
        function growFrom(ax, ay, aw, ah) {
            peekGrow.stop()
            x = ax; y = ay; width = aw; height = ah
            // Grow relative to the source: a 88px row thumb keeps the old
            // 360 card, a search-grid thumb steps up from its own size so the
            // peek still reads as an expansion rather than a shrink.
            var tw = Math.min(Math.max(360, aw + 80), root.width - 24)
            var th = Math.round(tw * 9 / 16)
            gx.to = Math.max(12, Math.min(ax + aw / 2 - tw / 2, root.width - tw - 12))
            gy.to = Math.max(12, Math.min(ay + ah / 2 - th / 2, root.height - th - 12))
            gw.to = tw; gh.to = th
            peekGrow.start()
        }
        ParallelAnimation {
            id: peekGrow
            NumberAnimation { id: gx; target: peekCard; property: "x"; duration: 240; easing.type: Easing.OutCubic }
            NumberAnimation { id: gy; target: peekCard; property: "y"; duration: 240; easing.type: Easing.OutCubic }
            NumberAnimation { id: gw; target: peekCard; property: "width"; duration: 240; easing.type: Easing.OutCubic }
            NumberAnimation { id: gh; target: peekCard; property: "height"; duration: 240; easing.type: Easing.OutCubic }
        }
        VideoOutput {
            id: peekSurface
            anchors.fill: parent; anchors.margins: 1
            fillMode: VideoOutput.PreserveAspectCrop
            opacity: root.peekReady ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 180 } }
        }
        // Waiting surface: rather than blowing the thumbnail up to a size it
        // was never made for, the card waits on its own with a game of snake
        // running quietly behind the label.
        Item {
            anchors.fill: parent; anchors.margins: 1
            visible: !root.peekReady
            SnakeField {
                anchors.fill: parent
                running: parent.visible && root.peekNow !== null
                plateW: peekWaitPlate.width
                plateH: peekWaitPlate.height
                opacity: 0.85
            }
            Rectangle {
                id: peekWaitPlate
                anchors.centerIn: parent
                radius: 6
                color: "#cc06090c"
                border.color: root.border1; border.width: 1
                // Sized for the label with all its dots, so the plate (and
                // the snake's ring around it) never shifts as they march.
                implicitWidth: peekWaitTm.width + 26
                implicitHeight: 30
                TextMetrics { id: peekWaitTm; font: peekWaitTx.font; text: "LOADING VIDEO..." }
                Text {
                    id: peekWaitTx
                    anchors.left: parent.left; anchors.leftMargin: 13
                    anchors.verticalCenter: parent.verticalCenter
                    // The dots march instead of the label pulsing: one clock,
                    // no fade to fight the snake behind it.
                    textFormat: Text.PlainText
                    text: "LOADING VIDEO" + ".".repeat(peekWaitDots.tick)
                    color: root.textLo
                    font.family: root.mono; font.pixelSize: 11; font.bold: true
                }
                Timer {
                    id: peekWaitDots
                    property int tick: 0
                    running: parent.visible
                    interval: 380; repeat: true
                    onTriggered: tick = (tick + 1) % 4
                }
            }
        }
        HoverHandler { id: peekCardHover; onHoveredChanged: root.peekHoverCheck() }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: {
                // Playing: hand the live pipeline over, no gap, no restart.
                // Still resolving: nothing to adopt, so open cold.
                if (root.peekReady) { root.promoteVideo(); return }
                var p = root.peekNow
                root.peekClose()
                if (p) root.openVideo(p.id, p.title, p.artist)
            }
            // A scroll is page navigation, not a peek interaction. The card
            // grows centred on the thumb, i.e. right under the cursor, so
            // swallowing the wheel froze scrolling wherever a peek was open.
            // Let the glance go and hand the event on to the page.
            onWheel: function(w){ root.peekClose(); w.accepted = false }
        }
    }
    Shortcut { sequence: "Esc"; enabled: root.peekNow !== null && root.videoNow === null; onActivated: root.peekClose() }
    Shortcut { sequence: "Esc"; enabled: root.videoNow !== null; onActivated: root.closeVideo() }
    Shortcut {
        sequence: "Space"; enabled: root.videoNow !== null && !root.videoLoading && !root.videoError
        onActivated: root.vPlayer.playbackState === MediaPlayer.PlayingState ? root.vPlayer.pause() : root.vPlayer.play()
    }
    Rectangle {
        anchors.fill: parent
        z: 999
        visible: root.videoNow !== null
        color: "#e0000000"
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
            // Two stacked sinks, one on stage. A single player uses A and B
            // never renders; a promoted peek plays A while the upgrade warms
            // up on B, and the cut is one opacity flip (see _videoCut). Both
            // always exist as real sinks so the warming stream has somewhere
            // to decode into, it just is not painted.
            Item {
                id: videoStage
                anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                anchors.margins: 1
                height: parent.height - 46
                VideoOutput { id: vsA; anchors.fill: parent; opacity: root.videoOnB ? 0 : 1 }
                VideoOutput { id: vsB; anchors.fill: parent; opacity: root.videoOnB ? 1 : 0 }
            }
            // Held over the stage whenever a swap could show a blank panel: a
            // quality switch (last frame of the outgoing stream) or the peek
            // handover (last frame of the card).
            Image {
                id: videoFreeze
                visible: false
                anchors.fill: videoStage
                fillMode: Image.PreserveAspectFit
                cache: false
            }
            // Click anywhere on the picture to toggle play/pause.
            MouseArea {
                anchors.fill: videoStage
                onClicked: root.vPlayer.playbackState === MediaPlayer.PlayingState ? root.vPlayer.pause() : root.vPlayer.play()
            }
            // GET / ERR states, in the terminal voice of the Art placeholders.
            Text {
                anchors.centerIn: videoStage
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
                        text: root.vPlayer.playbackState === MediaPlayer.PlayingState ? "[||]" : "[>]"
                        color: root.accent; font.family: root.mono; font.pixelSize: 14
                        MouseArea {
                            anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor
                            enabled: !root.videoLoading && !root.videoError
                            onClicked: root.vPlayer.playbackState === MediaPlayer.PlayingState ? root.vPlayer.pause() : root.vPlayer.play()
                        }
                    }
                    // Seek bar: click or drag anywhere on the track.
                    Rectangle {
                        Layout.fillWidth: true; height: 5; radius: 2; color: root.surface3
                        Rectangle {
                            width: root.vPlayer.duration > 0 ? parent.width * root.vPlayer.position / root.vPlayer.duration : 0
                            height: parent.height; radius: 2; color: root.accent
                        }
                        MouseArea {
                            anchors.fill: parent; anchors.margins: -8
                            enabled: root.vPlayer.seekable
                            function seekTo(x) {
                                var w = width - 16
                                if (w > 0 && root.vPlayer.duration > 0)
                                    root.vPlayer.position = Math.max(0, Math.min(1, (x - 8) / w)) * root.vPlayer.duration
                            }
                            onPressed: function(m){ seekTo(m.x) }
                            onPositionChanged: function(m){ if (pressed) seekTo(m.x) }
                        }
                    }
                    Text {
                        textFormat: Text.PlainText
                        text: root.fmtMs(root.vPlayer.position) + " / " + root.fmtMs(root.vPlayer.duration)
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
                        // Hidden while a promotion upgrade is in flight: the
                        // resolution is about to change on its own, and a
                        // manual pick would race that resolve.
                        visible: root.videoNow !== null && !root.videoUpgrading && !root.videoFromPeek
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
        // Never fetch a cover the user has never been shown. Rows hidden
        // behind a section cap (search's first-5) still instantiate, and
        // their fetches used to queue ahead of the on-screen art on the six
        // shared connections, so a big search left visible covers hanging
        // while invisible ones downloaded. `visible` here reads EFFECTIVE
        // visibility (ancestors included), so the latch flips the first time
        // the element is actually shown and the fetch starts then. Once
        // latched it never drops: a tab switch or filter change must keep
        // repainting instantly from the cache, never unload.
        property bool everShown: false
        onVisibleChanged: if (visible) everShown = true
        Timer {
            interval: 250
            // everShown gates the fetch itself (below), so a hidden row is
            // not "waiting" yet; its grace window starts when it first shows.
            running: artRoot.artState === "loading" && artRoot.everShown
            onTriggered: artRoot.artWaited = true
        }
        // Fixed decode size for the cover. The art is only for viewing, so it is
        // decoded ONCE at (roughly) its first display size and then just scaled to
        // fit. Binding the decode size to the live element width made a window
        // resize re-request the Image on every step, and since opacity follows the
        // load state (below) that blinked the cover to the grey placeholder. With a
        // fixed size a resize never touches the Image, so it simply stays put.
        // Seeded at first layout; ×2 keeps it crisp on HiDPI screens.
        property int decodeW: 96
        property bool _decodeSeeded: false
        function _seedDecode() {
            if (!_decodeSeeded && width > 0) {
                _decodeSeeded = true
                decodeW = Math.round(width * 2)
            }
        }
        onWidthChanged: _seedDecode()
        Component.onCompleted: { _seedDecode(); if (visible) everShown = true }
        color: surface3
        radius: 6
        border.color: border1
        border.width: 1
        clip: true
        // Album-art hover tilt (from BGT's CoverArt.qml): opt-in per call
        // site. The whole cover (art, border, anything anchored on it)
        // tilts toward the cursor and lifts slightly, springing back on
        // exit. Pure render-thread transforms: Image geometry, decode size
        // and clip untouched, so hoverFx: false sites are pixel-identical
        // and cost nothing.
        property bool hoverFx: false
        readonly property string fxVariant: root.artFxVariant
        property real fxRx: 0
        property real fxRy: 0
        property real fxLift: 1.0
        property real fxGlossX: width / 2
        property real fxGlossY: height / 2
        // What this cover IS, so the resting raise can follow the shared
        // player. The call site declares it; sites that leave fxId empty keep
        // exactly the old behaviour, every binding below folds away.
        // Matching is literal (this cover's own preview), the same rule
        // pvActive already applies everywhere else: a cover that lifts without
        // being asked reads as a glitch, and one album can sit on three
        // shelves at once.
        property string fxKind: ""
        property string fxId: ""
        // Gated on the same Settings switch as the tilt: "Cover art tilts on
        // hover" is the app's only "artwork moves" control, and someone who
        // turned motion off should not be handed new motion.
        readonly property string fxPlaySt: (hoverFx && fxVariant !== "none" && fxId !== "")
                                           ? root.pvSt(fxKind, fxId) : ""
        // Same three live states PreviewArt calls "active"; "error" is a
        // transient flash, not something to raise the cover for.
        readonly property bool fxPlayRaised: fxPlaySt === "playing" || fxPlaySt === "paused"
                                             || fxPlaySt === "loading"
        // A preview usually ends with the pointer still on the cover, on the
        // very button that stopped it. Dropping there is wrong twice over: the
        // cover is still being pointed at, so it has no business lying flat,
        // and the hover tilt would take it straight back up, which reads as
        // the drop bouncing. So it stays up, and comes down when the pointer
        // leaves: one movement, and the pointer is gone before it starts.
        property bool fxHoldRaise: false
        onFxPlayRaisedChanged: fxHoldRaise = fxPlayRaised ? false : fxHover.hovered
        readonly property bool fxRaised: fxPlayRaised || fxHoldRaise
        readonly property bool fxPaused: fxPlaySt === "paused"
        // `&& !fxRaised` is the whole "stop tilting" half of this: the
        // onFxOnChanged handler below already lays a mid-tilt cover flat when
        // this goes false, which is exactly the hand-off from tilt to raise.
        readonly property bool fxOn: hoverFx && fxVariant !== "none" && !fxRaised
        // The effect waits for the pointer to REST on the cover. A scrolling
        // list slides its rows under a stationary cursor, and every row that
        // passed under it used to arm the tilt on the way through: dozens of
        // 280 ms lift and tilt springs at once (and, on tilt_shadow, a layer
        // FBO per row), for covers nobody pointed at. That was half of the
        // playlist scroll jank reported from livetesting. 90 ms of unbroken
        // hover is below notice when you mean it, and far longer than a row
        // spends under the cursor mid-scroll. Disarms the instant the pointer
        // leaves, so springing back is never delayed.
        property bool fxArmed: false
        readonly property bool fxHovering: fxOn && fxArmed && fxHover.hovered
        function _fxAim(p) {
            var cx = width / 2, cy = height / 2
            fxRy =  ((p.x - cx) / cx) * root.artFxTilt
            fxRx = -((p.y - cy) / cy) * root.artFxTilt
            fxGlossX = p.x
            fxGlossY = p.y
        }
        Timer {
            id: fxArm; interval: 90
            // Take the lift AND the current aim on arming: a cursor that
            // lands and holds still must tilt without needing a nudge.
            onTriggered: {
                if (!fxHover.hovered || !artRoot.fxOn) return
                artRoot.fxArmed = true
                artRoot.fxLift = root.artFxLift
                artRoot._fxAim(fxHover.point.position)
            }
        }
        Behavior on fxRx   { NumberAnimation { duration: 280; easing.type: root.artFxEase } }
        Behavior on fxRy   { NumberAnimation { duration: 280; easing.type: root.artFxEase } }
        Behavior on fxLift { NumberAnimation { duration: 280; easing.type: root.artFxEase } }
        // Turning the effect off in Settings while a cover is mid-tilt must
        // lay it flat, not freeze it there (the same guard the track discs
        // carry for a preview starting under an armed tilt).
        onFxOnChanged: if (!fxOn) { fxArm.stop(); fxArmed = false; fxRx = 0; fxRy = 0; fxLift = 1.0 }
        // ONE dial for "raised", 0 to 1. The resting swell and the shadow's
        // shape both read it, so they cannot drift apart mid-transition: the
        // card rises and its shadow settles under it on the same curve, in
        // the same 260 ms. Its own value, never the hover lift's, which snaps
        // back to 1.0 on every pointer exit. Eases, never springs: OutBack
        // overshoots, and a cover meant to settle and stay must not bounce.
        property real fxRaiseT: fxRaised ? 1 : 0
        Behavior on fxRaiseT { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        readonly property real fxRaiseScale: 1 + (root.artPlayLift - 1) * fxRaiseT
        // How present the shadow is, 0 to 1. Hovering casts one too, so a
        // cover that starts playing under the pointer deepens a shadow that
        // is already there. Animated rather than switched, and layer.enabled
        // follows it, so the layer comes up while the shadow is still
        // invisible and goes away only after it has faded out.
        property real fxShadow: (fxRaised || fxHovering) ? 1 : 0
        Behavior on fxShadow { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        // Paused: a slow, shallow breath, not a dimmed shadow. 0 = full depth,
        // 1 = the bottom of the exhale.
        property real fxBreath: 0
        SequentialAnimation {
            running: artRoot.fxPaused
            loops: Animation.Infinite
            NumberAnimation { target: artRoot; property: "fxBreath"; to: 1
                              duration: root.artPlayBreathMs; easing.type: Easing.InOutSine }
            NumberAnimation { target: artRoot; property: "fxBreath"; to: 0
                              duration: root.artPlayBreathMs; easing.type: Easing.InOutSine }
        }
        // Resuming stops the loop wherever it happens to be; the amplitude is
        // shallow enough that settling back to full depth is not a visible step.
        onFxPausedChanged: if (!fxPaused) fxBreath = 0
        readonly property real fxBreathK: 1 - root.artPlayBreath * fxBreath
        readonly property real fxShadowDepth: fxShadow * fxBreathK
        // A raised cover sits above its neighbours, so neither the swell nor
        // the shadow is sliced by the next sibling's background.
        // Held for the whole descent, not just while raised: dropping back
        // behind the next sibling's background at the instant the preview ends
        // clips the last of the swell and the shadow, which reads as a snap.
        // Keyed on shadow depth, not on the raise: a merely hovered cover
        // casts a shadow too, and at z 0 the next sibling's background paints
        // over it, so the hover shadow never showed until preview raised the
        // card.
        z: (fxRaiseT > 0.001 || fxShadowDepth > 0.004) ? 1 : 0
        transform: [
            Rotation { origin.x: artRoot.width / 2; origin.y: artRoot.height / 2; axis: Qt.vector3d(1, 0, 0); angle: artRoot.fxRx },
            Rotation { origin.x: artRoot.width / 2; origin.y: artRoot.height / 2; axis: Qt.vector3d(0, 1, 0); angle: artRoot.fxRy },
            Scale    { origin.x: artRoot.width / 2; origin.y: artRoot.height / 2; xScale: artRoot.fxLift; yScale: artRoot.fxLift },
            Scale    { origin.x: artRoot.width / 2; origin.y: artRoot.height / 2; xScale: artRoot.fxRaiseScale; yScale: artRoot.fxRaiseScale }
        ]
        // HoverHandler, not a MouseArea: call sites' own MouseAreas (open
        // the page, hover controls) keep every click and hover.
        HoverHandler {
            id: fxHover
            // Stays enabled through the raise, where the tilt is off: something
            // has to know the pointer is still on the cover when the preview
            // ends. fxOn gates the movement instead, below.
            enabled: artRoot.hoverFx && artRoot.fxVariant !== "none"
            onHoveredChanged: {
                if (hovered) fxArm.restart()
                else {
                    fxArm.stop(); artRoot.fxArmed = false
                    artRoot.fxHoldRaise = false
                    artRoot.fxRx = 0; artRoot.fxRy = 0; artRoot.fxLift = 1.0
                }
            }
            // fxArmed implies hovered and fxOn (both clear it), so this is the
            // whole guard: an unarmed pass-through moves nothing.
            onPointChanged: if (artRoot.fxArmed) artRoot._fxAim(point.position)
        }
        Image {
            id: artImg
            anchors.fill: parent
            source: artRoot.everShown ? artRoot.url : ""
            fillMode: Image.PreserveAspectCrop
            asynchronous: true
            cache: true
            // Art fades in over the placeholder instead of popping.
            opacity: artRoot.artState === "ready" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { enabled: artRoot.artWaited; NumberAnimation { duration: 220; easing.type: Easing.OutQuad } }
            // Decode at (roughly) display resolution instead of the full 320-480px
            // source (each tiny thumbnail would otherwise keep a full-size bitmap and
            // burn decode time). Art is square, so one decode dimension covers both,
            // and decodeW is fixed once (see above) so a resize never re-requests it.
            sourceSize.width: artRoot.decodeW
            sourceSize.height: artRoot.decodeW
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
                            running: artRoot.artState === "loading" && artTerm.visible && root.onScreen
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
        // Gloss variant (from BGT): a cursor-following radial highlight.
        // Behind a Loader: the Shape + gradient exist only for tilt_gloss
        // instances, every other Art (there are hundreds in list delegates)
        // creates nothing here.
        Loader {
            anchors.fill: parent
            z: 10   // above any call-site children riding the art
            active: artRoot.hoverFx && artRoot.fxVariant === "tilt_gloss"
            sourceComponent: Item {
                clip: true
                opacity: artRoot.fxHovering ? 1 : 0
                Behavior on opacity { NumberAnimation { duration: 140 } }
                Shape {
                    anchors.fill: parent
                    antialiasing: true
                    ShapePath {
                        strokeColor: "transparent"
                        fillGradient: RadialGradient {
                            centerX: artRoot.fxGlossX; centerY: artRoot.fxGlossY
                            centerRadius: Math.max(artRoot.width, artRoot.height) * 0.55
                            focalX: artRoot.fxGlossX; focalY: artRoot.fxGlossY
                            GradientStop { position: 0.0;  color: Qt.rgba(1, 1, 1, 0.42) }
                            GradientStop { position: 0.45; color: Qt.rgba(1, 1, 1, 0.0) }
                        }
                        startX: 0; startY: 0
                        PathLine { x: artRoot.width; y: 0 }
                        PathLine { x: artRoot.width; y: artRoot.height }
                        PathLine { x: 0;             y: artRoot.height }
                        PathLine { x: 0;             y: 0 }
                    }
                }
            }
        }
        // Offset depth shadow (BGT tilt_shadow: sx=-px*14, sy=-py*14+6). While
        // hovered it leans away from the tilt; while raised it sits straight
        // down at artPlayShadowY, and fxRaiseT crosses between the two, so the
        // shadow never jumps as a hovered cover becomes a playing one. Gated
        // on fxShadowDepth, not on a state boolean: the layer exists slightly
        // before and after the shadow is visible, which is what makes the fade
        // seamless. Idle covers still never pay the layer FBO, and the 90 ms
        // rest-arming means a scrolling list cannot mint one per row.
        layer.enabled: artRoot.fxShadowDepth > 0.004
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Qt.rgba(0, 0, 0, root.artPlayShadowA * artRoot.fxShadowDepth)
            shadowBlur: root.artPlayShadowBlur
            shadowHorizontalOffset: -(artRoot.fxRy / root.artFxTilt) * 14 * (1 - artRoot.fxRaiseT)
            shadowVerticalOffset: ((artRoot.fxRx / root.artFxTilt) * 14 + 6) * (1 - artRoot.fxRaiseT)
                                  + root.artPlayShadowY * artRoot.fxRaiseT * artRoot.fxBreathK
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

    // The tier one line of an expanded album states: what the file landed at
    // once it has landed, and until then what the job is ASKING for, which the
    // caller renders faded.
    //
    // "pending" has to be in that second group and was not: it is the state a
    // fetched track list starts in (_merge_queue_tracks defaults to it) and so
    // it is every track of a queued album and every not-yet-started track of a
    // running one, which is precisely the stretch where the only thing to say
    // is the request. Without it the column sat blank until each track's turn
    // came. A track that is skipped, cancelled or failed says nothing: no tier
    // was ever delivered for it, and the request is not news about a file that
    // is not coming.
    function queueTrackTier(delivered, status, target) {
        if (delivered !== "") return delivered
        return (status === "pending" || status === "queued" || status === "running") ? target : ""
    }

    component QualTag: Row {
        id: qt
        property string q: ""
        // Set (via qualMixList) when the album mixes tiers, replaces the
        // single-tier pill with MIXED + per-tier counts (/ sep).
        property var mix: []
        // Real per-item spec when we know it (a video's actual resolution);
        // empty falls back to the tier's standard spec.
        property string spec: ""
        // Narrow surfaces (the queue drawer) show the tier word alone: the
        // spec is what makes the pill wide enough to crush a title.
        property bool compact: false
        readonly property string specText: compact ? "" : (spec !== "" ? spec : root.qualSpec(qt.q))
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
                    visible: qt.specText !== ""
                    text: qt.specText; color: root.qualSpecFg(qt.q); font.family: root.mono; font.pixelSize: 9
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
                    // The per-tier counts are the widest thing a pill can
                    // carry; on a narrow surface the dots say "not uniform"
                    // and the expanded ledger says exactly how.
                    model: qt.compact ? [] : qt.mix
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

    // The "in your library" presence pill: amber identity, compact and STATIC.
    // The pill is color-coded by the QUALITY of the local copy (gold hi-res,
    // green lossless, cyan healthy lossy, red small lossy, amber when unknown),
    // and that colour is the whole quality story: no hover reveal, nothing
    // resizes. It reports presence (a partial copy spells out N OF M); the
    // prevention lives in the DownloadButton itself, which renders a full
    // local copy as the inert DOWNLOADED state (see its lib* properties). A
    // click opens the matched local album folder.
    component LibraryTag: Rectangle {
        id: pxt
        property int have: 0
        property int total: 0
        property string albumId: ""        // the matched album's folder path
        property string qclass: ""         // quality class (presence local_class)
        // Any copy short of the full count spells out N OF M. The strict bar
        // for the inert DOWNLOADED claim is local >= total with no slack
        // (matching.decide_presence), and this must agree with it: an earlier
        // "total - 1" here let a 9-of-10 copy wear an unqualified IN LIBRARY
        // pill beside a still-live DOWNLOAD button.
        // What the LOCAL release says it holds, which is not always what the
        // album on screen holds. N OF M counts against whichever total the copy
        // falls short of: normally the album being viewed, but a folder holding
        // 14 files of a 63-track set beside a 14-track edition is short by its
        // OWN reckoning, and that is the number explaining the still-live
        // download button. Reading a flat IN LIBRARY over it is the one thing
        // the pill must not do.
        property int declared: 0
        readonly property int want: pxt.declared > pxt.have ? pxt.declared : pxt.total
        readonly property bool partial: pxt.have > 0 && pxt.want > 0 && pxt.have < pxt.want
        // True when the match's IDENTITY is proven (exact edition title, both
        // years agreeing): the "?" is reserved for matches the matcher could
        // not prove are the same album. Coverage is a separate axis, already
        // spelled out as N OF M.
        property bool proven: false
        // A badge the page was BUILT with is simply there; a badge the scan
        // finds while you are already looking ARRIVES. News in this app fades
        // in, over the same 180ms the reveal itself uses, instead of snapping
        // into place a second after you started reading.
        //
        // The build flags cannot tell those apart on their own: they only cover
        // a fresh search or Browse build, so navigating BACK to a page, or a
        // row recycling under a scroll, animated pills that were never news and
        // left the page assembling itself in front of the reader. _settled is
        // the real question. It flips one event-loop pass after this pill is
        // created, which is late enough that the pill's first state is already
        // decided and early enough to catch anything the scan publishes after.
        // A Timer rather than Component.onCompleted, because call sites declare
        // their own onCompleted and would silently replace it.
        // Gated on visible, not replacing it: an invisible pill must still take
        // no layout width, which opacity alone would not do.
        //
        // Creation is only half of it. These pills outlive the thing they
        // describe: a list delegate is RECYCLED onto the next album as you
        // scroll, and the album header's single pill is rebound on every page
        // you open. Neither is news, and a latch that only ever flipped once
        // faded both. settleKey is whatever this pill is currently about, so
        // changing it re-arms the latch and the next answer is simply there.
        property string settleKey: ""
        property bool _settled: false
        onSettleKeyChanged: { pxt._settled = false; pxtSettle.restart() }
        Timer { id: pxtSettle; interval: 0; running: true; onTriggered: pxt._settled = true }
        opacity: pxt.visible ? 1 : 0
        Behavior on opacity {
            enabled: pxt._settled && !root.searchBuilding && !root.browseBuilding
            NumberAnimation { duration: 180; easing.type: Easing.OutQuad }
        }
        radius: 4; color: root.pillClassCont(pxt.qclass)
        border.color: root.pillClassDim(pxt.qclass); border.width: 1
        implicitHeight: 18
        implicitWidth: (pxt.proven ? 14 : 24) + pxBase.implicitWidth
        // A question mark, not the quality dot it replaces: every one of these
        // pills is a TAG MATCH, an inference from what the files say about
        // themselves, and nothing here was watched being downloaded. The mark
        // says so at a glance and costs no width, sitting exactly where the eye
        // already lands. Quality is still the whole colour story (the mark
        // takes the same class colour the dot did); this only separates
        // "recognised on disk" from the app's own records, which never wear it.
        Text {
            id: pxMark
            textFormat: Text.PlainText
            x: 7; anchors.verticalCenter: parent.verticalCenter
            width: 5; horizontalAlignment: Text.AlignHCenter
            visible: !pxt.proven
            text: "?"
            color: root.pillClassFg(pxt.qclass)
            font.family: root.mono; font.pixelSize: 10; font.bold: true
        }
        Text {
            id: pxBase
            textFormat: Text.PlainText
            x: pxt.proven ? 7 : 17; anchors.verticalCenter: parent.verticalCenter
            text: pxt.partial ? (pxt.have + " OF " + pxt.want + " IN LIBRARY") : "IN LIBRARY"
            color: root.pillClassTx(pxt.qclass); font.family: root.mono; font.pixelSize: 9; font.bold: true
        }
        MouseArea {
            anchors.fill: parent
            // Gated by VISIBILITY, not enabled: a disabled MouseArea still
            // owns its cursorShape in Qt, so an explicit ArrowCursor here sat
            // on top of whatever cursor the row underneath was showing. An
            // invisible one claims nothing.
            visible: pxt.albumId !== ""
            cursorShape: Qt.PointingHandCursor
            onClicked: waves.revealLibraryAlbum(pxt.albumId)  // open the album folder
        }
    }
    // The library pill is color-coded by the QUALITY you hold, so low, middle
    // and high read at a glance: gold = hi-res, green = lossless, cyan =
    // healthy lossy, red = small lossy. "" (quality unknown) keeps the amber
    // "in library" identity. The class comes from the backend (local_class),
    // one source of truth shared with the download engine.
    // Open the library claim gate: explain the match, name the matched folder,
    // and leave DOWNLOAD ANYWAY one click away. One definition, because every
    // surface that can show a claim (the full download button, a track row's
    // button, the browse card's hover strip) has to open the SAME conversation.
    // `kind` is "album" (the default) or "track", and only picks the wording
    // and which download the ANYWAY click makes.
    function openLibraryClaim(albumId, albumTitle, folder, kind) {
        libraryClaimGate.mode = "claim"
        libraryClaimGate.card = null
        libraryClaimGate.albumId = albumId
        libraryClaimGate.albumTitle = albumTitle
        libraryClaimGate.folder = folder
        libraryClaimGate.kind = kind === "track" ? "track" : "album"
        libraryClaimGate.shown = true
    }
    // The owned twin of the claim gate, same dialog in its other mode: this
    // is a RECORD of a download Waves made, not a tag guess, so the question
    // is not "is the match right" but "fetch it again?". REDOWNLOAD forces
    // the whole job (the ownership gate would otherwise skip every track and
    // the job would fetch nothing). Takes the card so the confirm can start
    // the right kind of download (album, playlist or mix).
    function openRedownloadGate(card) {
        libraryClaimGate.mode = "owned"
        libraryClaimGate.card = card
        libraryClaimGate.albumId = "" + (card.id || "")
        libraryClaimGate.albumTitle = "" + (card.title || "")
        libraryClaimGate.folder = ""
        libraryClaimGate.kind = "album"
        libraryClaimGate.shown = true
    }
    function pillClassFg(c) {
        return c === "hires" ? root.gold : c === "lossless" ? root.green
             : c === "high" ? root.cyan : c === "low" ? root.red : root.libAccent
    }
    function pillClassDim(c) {
        return c === "hires" ? root.goldDim : c === "lossless" ? root.greenDim
             : c === "high" ? root.cyanDim : c === "low" ? root.redDim : root.libDim
    }
    function pillClassCont(c) {
        return c === "hires" ? root.goldCont : c === "lossless" ? root.greenCont
             : c === "high" ? root.cyanCont : c === "low" ? root.redCont : root.libCont
    }
    function pillClassTx(c) {
        return c === "hires" ? root.goldContTx : c === "lossless" ? root.greenContTx
             : c === "high" ? root.cyanContTx : c === "low" ? root.redContTx : root.libContTx
    }

    // The album presence pill, self-resolving against the local scan index,
    // placed right beside the album title. Static IN LIBRARY (or N OF M) with
    // a quality-coded dot; the colour class is the whole quality story.
    component AlbumPresencePill: LibraryTag {
        id: appl
        // The album's identity as ONE object ({artist, title, year, tracks}),
        // not four properties. Each property carried its own change handler, so
        // filling them in cost one QML->Python presence call each plus a fifth
        // from onCompleted: measured at 15 calls per album row and 750 for a
        // 50-row page, 22us apiece, ~16ms of GUI thread per page build. One
        // property is one binding evaluation and one call. Null (or no title)
        // means no album to ask about, and the pill stays hidden.
        property var album: null
        property var presence: null
        readonly property string albumTitle: (album && album.title) ? ("" + album.title) : ""
        // A recycled row is a new album, not news about this one.
        settleKey: appl.albumTitle
        property bool _resolved: false
        function _resolvePresence() {
            _resolved = true
            var a = appl.album
            presence = (a && a.title)
                ? waves.libraryAlbumPresence("" + (a.artist || ""), "" + a.title,
                                             "" + (a.year || ""), a.tracks || 0, a.duration_sec || 0)
                : null
        }
        onAlbumChanged: _resolvePresence()
        // Only if the binding above has not already answered: an unconditional
        // resolve here is the fifth call this component exists to avoid.
        Component.onCompleted: if (!_resolved) _resolvePresence()
        Connections {
            target: waves
            function onLibraryPresenceChanged() { appl._resolvePresence() }
        }
        visible: !!(presence && presence.present)
        have: presence ? (presence.local_tracks || 0) : 0
        total: (album && album.tracks) ? album.tracks : 0
        declared: presence ? (presence.local_declared || 0) : 0
        albumId: presence ? (presence.local_album_id || "") : ""
        qclass: presence ? (presence.local_class || "") : ""
        // The identity axis alone ("sure"): the "?" asks "is this really the
        // same album", so a proven match drops it even when the copy is short
        // on tracks (coverage is already spelled out as N OF M). Track pills
        // and the artist rollup have no identity proofs and keep theirs.
        proven: !!(presence && presence.present && presence.sure === true)
    }

    // The track twin of AlbumPresencePill: the same LibraryTag, resolved
    // against the per-track index, so a track row can say the exact song is on
    // disk (an incomplete album's tracks included). One identity object for
    // the same one-call-per-row economy as the album pill. Never shown for
    // videos: the library scan only ever holds audio.
    component TrackPresencePill: LibraryTag {
        id: tppl
        // {artist, title, album, year}; null (or no title) means nothing to ask
        // about. album/year name the release this track belongs to: they are
        // what the match can be PROVEN against, since a track carries no year
        // of its own and its title matches every edition that shares it. A row
        // that cannot name its album still gets a pill, it just keeps the "?".
        property var track: null
        property var presence: null
        // A recycled row is a new track, not news about this one.
        settleKey: (tppl.track && tppl.track.title) ? ("" + tppl.track.title) : ""
        property bool _resolved: false
        function _resolvePresence() {
            _resolved = true
            var t = tppl.track
            presence = (t && t.title)
                ? waves.libraryTrackPresence("" + (t.artist || ""), "" + t.title,
                                             "" + (t.album || ""), "" + (t.year || ""), t.duration_sec || 0)
                : null
        }
        onTrackChanged: _resolvePresence()
        Component.onCompleted: if (!_resolved) _resolvePresence()
        Connections {
            target: waves
            function onLibraryPresenceChanged() { tppl._resolvePresence() }
        }
        visible: !!(presence && presence.present)
        albumId: presence ? (presence.local_album_id || "") : ""
        qclass: presence ? (presence.local_class || "") : ""
        // The same identity axis the album pill reads, inherited from the
        // folder the copy was found in: a track sitting in an album this
        // library can prove is the release drops the "?" with it.
        proven: !!(presence && presence.present && presence.sure === true)
    }

    // The artist badge: what this artist holds in your library, fed by the
    // artist-level rollup. On artwork it is a full-width STRIP across the
    // cover's lower edge rather than a chip in a corner, so it never sits on
    // top of a face, and it is drawn on the same gradient the hero card
    // already uses to carry a caption over a photograph rather than on a new
    // material invented for it.
    //
    // Two voices only: "IN LIBRARY" in the owned green says what this is, and
    // the counts in plain text say how much. No "?" anywhere. The mark belongs
    // on an album pill, where it separates a proven identity from a name match;
    // a rollup is a count, and a count has nothing to doubt.
    //
    // Green and not a quality class on purpose: the green is the one the
    // download button and the browse card already turn when a release is in the
    // library, so one page never says "owned" in two colours. Quality colour is
    // the album pill's story, and an artist spanning FLAC and MP3 has no single
    // class to tell anyway.
    component ArtistBadges: Item {
        id: arb
        property string artistName: ""
        property var presence: null
        // Draw as the strip on artwork. The caller sets the width and parents
        // it inside the Art, which clips, so the strip takes the cover's tilt
        // and rounded corners with it. The page form sizes itself to its text.
        property bool bar: false
        // Two lines, the shape the hero card's caption already uses over
        // artwork: a spaced cap line over counts set big enough to survive a
        // photograph, which one rail-height line could never give them.
        //
        // Sized to the cover it is on, not to one cover. A search card's is
        // 184px and a browse card's 200, but the followed-artists grid packs
        // its covers to about 116, where a 12px count line runs 150px wide and
        // an Art that clips simply sliced the ends off both numbers. The type
        // is mono, so the width is arithmetic rather than a guess: 0.6em per
        // character plus the two gaps, floored at 8px so it stops shrinking
        // before it stops being readable.
        readonly property int countChars: arb.albumLine.length + arb.songLine.length + 1
        readonly property real barCountPx:
            Math.max(8, Math.min(12, (arb.width - 12 - 14) / Math.max(1, arb.countChars) / 0.6))
        readonly property real barLabelPx: Math.max(8, Math.min(10, arb.barCountPx))
        // The words' own band, plus the room the smaller type gives back.
        readonly property real barHeight: Math.max(24, Math.min(34, arb.barCountPx * 2.8))
        implicitWidth: arb.bar ? 0 : abPlate.implicitWidth
        implicitHeight: arb.bar ? arb.barHeight : abPlate.implicitHeight
        width: implicitWidth
        height: implicitHeight
        // The album pill's economy, for the same reason: this now rides every
        // artist card on a search page, and an unconditional resolve here
        // would be a second QML->Python call per card on top of the binding's.
        property bool _resolved: false
        // False until this badge has answered for the name it currently shows.
        // The fade below reads it, so the badge a page is BUILT with (or a
        // recycled row rebound to a new artist) is simply there, and only a
        // badge the running scan turns up while you are looking at it animates.
        property bool _settled: false
        function _resolvePresence() {
            _resolved = true
            presence = artistName !== "" ? waves.artistLibraryPresence(artistName) : null
            _settled = true   // after the answer, never before: see the Behavior
        }
        onArtistNameChanged: { _settled = false; _resolvePresence() }
        Component.onCompleted: if (!_resolved) _resolvePresence()
        Connections {
            target: waves
            // A browse shelf builds this strip on every card and names only the
            // artists, so the ones that will never show must not run a handler
            // per card per committed batch of a running scan.
            enabled: arb.artistName !== ""
            function onLibraryPresenceChanged() { arb._resolvePresence() }
        }
        visible: !!(presence && presence.present)
        // The badge hides at the top, so nothing inside it ever sees a
        // visibility change of its own.
        opacity: arb.visible ? 1 : 0
        // Arriving is animated; being there is not. The build flags alone were
        // not enough: they only cover a fresh search or Browse build, so coming
        // BACK to a page (or a row recycling under a scroll) faded every badge
        // in a beat behind its artwork, which read as the page assembling
        // itself in front of you. _settled is the real question, and it is
        // false for exactly as long as this badge has not yet answered for the
        // artist it is showing.
        Behavior on opacity {
            enabled: arb._settled && !root.searchBuilding && !root.browseBuilding
            NumberAnimation { duration: 180; easing.type: Easing.OutQuad }
        }
        readonly property int albums: presence ? (presence.albums || 0) : 0
        readonly property int tracks: presence ? (presence.tracks || 0) : 0
        readonly property string albumLine: arb.albums + (arb.albums === 1 ? " ALBUM" : " ALBUMS")
        readonly property string songLine: arb.tracks + (arb.tracks === 1 ? " SONG" : " SONGS")

        // THE STRIP. It sits ON the cover's lower edge over the same gradient
        // the hero card already uses to carry a caption over artwork, so the
        // words have a floor without the photograph gaining an edge: nothing to
        // align, it melts into whatever is behind it.
        Item {
            visible: arb.bar
            anchors.fill: parent

            Rectangle {
                anchors.left: parent.left; anchors.right: parent.right
                anchors.bottom: parent.bottom
                // Taller than the words it carries, and by a lot: a scrim the
                // height of its own text is a dark bar with soft edges, not a
                // fade. The overflow is drawn inside the Art, which clips, so
                // it can reach as far up the cover as it needs to.
                height: parent.height * 2.1
                // Weighted so the words' own band is already dark and only the
                // reach above it is doing the fading: a linear ramp puts its
                // midpoint under the text, which is where a bright cover wins.
                gradient: Gradient {
                    GradientStop { position: 0; color: "transparent" }
                    GradientStop { position: 0.42; color: "#8f0b0d10" }
                    GradientStop { position: 0.62; color: "#e00b0d10" }
                    GradientStop { position: 1; color: "#fa0b0d10" }
                }
            }

            // THE STRIP'S WORDS. Every one of them carries a one-pixel black
            // outline: not a shadow and not a plate, it costs no space and
            // changes no colour, and it is the difference between text that
            // survives a blown-out white studio cover and text that has to be
            // hunted for. Centred, because a strip that spans the cover has no
            // side to favour.
            Column {
                anchors.centerIn: parent
                spacing: 2
                Text {
                    anchors.horizontalCenter: parent.horizontalCenter
                    textFormat: Text.PlainText; text: "IN LIBRARY"
                    color: root.green
                    style: Text.Outline; styleColor: "#000000"
                    font.family: root.mono; font.pixelSize: arb.barLabelPx
                    font.bold: true; font.letterSpacing: 1.4
                }
                Row {
                    anchors.horizontalCenter: parent.horizontalCenter
                    spacing: 7
                    Text {
                        textFormat: Text.PlainText; text: arb.albumLine
                        color: root.textHi
                        style: Text.Outline; styleColor: "#000000"
                        font.family: root.mono; font.pixelSize: arb.barCountPx; font.bold: true
                    }
                    Text {
                        textFormat: Text.PlainText; text: "\u00b7"
                        color: root.textLo
                        style: Text.Outline; styleColor: "#000000"
                        font.family: root.mono; font.pixelSize: arb.barCountPx
                    }
                    Text {
                        textFormat: Text.PlainText; text: arb.songLine
                        color: root.textHi
                        style: Text.Outline; styleColor: "#000000"
                        font.family: root.mono; font.pixelSize: arb.barCountPx; font.bold: true
                    }
                }
            }
        }

        // THE PAGE FORM. The artist page header has no cover for a strip to sit
        // on, and nothing there should be riding the photograph anyway. So it
        // is the strip's own typography with the strip taken away: the spaced
        // green label over the counts, left aligned, standing on the page.
        // No plate, no border, no fill, and no outline either: on a page the
        // background is known, so there is nothing for the words to survive.
        //
        // It sits under the artist name and above the button that would add to
        // what it counts.
        Column {
            id: abPlate
            visible: !arb.bar
            spacing: 3
            Text {
                textFormat: Text.PlainText; text: "IN LIBRARY"
                color: root.green
                font.family: root.mono; font.pixelSize: 10
                font.bold: true; font.letterSpacing: 1.4
            }
            Row {
                spacing: 8
                Text {
                    textFormat: Text.PlainText; text: arb.albumLine
                    color: root.textHi
                    font.family: root.mono; font.pixelSize: 15; font.bold: true
                }
                Text {
                    textFormat: Text.PlainText; text: "\u00b7"
                    color: root.textDim; font.family: root.mono; font.pixelSize: 15
                }
                Text {
                    textFormat: Text.PlainText; text: arb.songLine
                    color: root.textHi
                    font.family: root.mono; font.pixelSize: 15; font.bold: true
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

    // Queued glyph (chosen in the queued-descent lab): a 3-bar list, your
    // item is the bottom bar. The accent highlight ping-pongs down and back
    // up the stack (350ms per step, never pausing), phase-aligned via
    // baseTick so it always starts at the top bar the moment the queued
    // state appears. Steps off root.marchTick, no per-frame animation.
    component QueueStack: Column {
        id: qs
        property real barW: 12
        property int baseTick: 0
        Component.onCompleted: baseTick = root.marchTick
        onVisibleChanged: if (visible) baseTick = root.marchTick
        // Gated on visible so an idle button doesn't re-evaluate this on every
        // 50ms tick: hidden, the binding's only dependency is `visible` itself.
        readonly property int step: visible ? Math.floor(((root.marchTick - baseTick + 100000) % 100000) / 7) % 4 : 0
        readonly property int walk: step === 3 ? 1 : step   // 0,1,2,1 bounce
        spacing: 2.5
        Repeater {
            model: 3
            delegate: Rectangle {
                required property int index
                width: qs.barW; height: 2.5; radius: 0   // sharp LED bars
                anchors.horizontalCenter: parent.horizontalCenter
                color: index === qs.walk ? root.accent : root.accentDim
                opacity: index === qs.walk ? 1.0 : 0.45
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
        property var _ownIds: null
        function refreshOwned() {
            if (collectionCheck) {
                var ids = di.mediaId !== "" ? waves.collectionMemberIds(di.mediaId) : null
                _ownIds = ids
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
                if (di.collectionCheck) {
                    if (tid === "" || (di._ownIds && di._ownIds.indexOf(tid) !== -1)) di.refreshOwned()
                }
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
        border.width: root.btnBorderW
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
            anchors.fill: parent; anchors.margins: root.btnBorderW   // sit inside the border
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
            anchors.fill: parent; anchors.margins: root.btnBorderW
            visible: false
            Rectangle { anchors.fill: parent; radius: root.btnRad - root.btnBorderW; color: "#ffffff" }
        }
        Text {
            textFormat: Text.PlainText
            anchors.centerIn: parent; visible: di.st === "running"
            text: di.pct >= 0 ? Math.round(di.pct) + "%" : "…"
            color: root.textHi; font.family: root.mono; font.pixelSize: 9; font.bold: true
            style: Text.Outline; styleColor: root.bg
        }
        // "preparing" (parked behind a re-fetch or a folder-tree warm) reads as
        // queued here too: see DownloadButton.waiting.
        readonly property bool waiting: di.st === "queued" || di.st === "preparing"
        Ico {
            anchors.centerIn: parent; visible: di.st !== "running" && di.st !== "failed" && !di.waiting
            name: di.st === "done" ? "check" : "arrow-down"
            color: di.st === "done" ? root.green : root.accent
            size: 15; bold: di.st === "done" ? 0 : 10
        }
        // Queued: click acknowledged, waiting for a download slot; the stack
        // glyph, your item is the bottom bar.
        QueueStack { visible: di.waiting; barW: 13; anchors.centerIn: parent }
        RetryMark { anchors.centerIn: parent; visible: di.st === "failed"; color: root.red; box: 15 }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            onPressed: di.scale = 0.85
            onReleased: di.scale = 1.0
            onCanceled: di.scale = 1.0
            onClicked: { if (di.st === "running" || di.st === "done" || di.waiting) return; di.onTap() }
        }
    }

    // Track preview: the album art IS the play button. Idle it's just artwork;
    // hover reveals a ▶ over a dark scrim; while previewing, a smooth arc
    // hugging the art fills clockwise with playback position (⏸ over the scrim)
    // and doubles as the seek surface: hovering the band around the art shows
    // a ghost marker + time readout, click or drag seeks (seek-lab ghost
    // cursor + segment-lab solid arc). Only the active track shows its ring,
    // one shared player.
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
        // Seek aim state: aimFrac is the track fraction under the mouse angle,
        // 12 o'clock = 0, clockwise (the arc's fill direction).
        property real aimFrac: 0
        function angFrac(mx, my) {
            var a = Math.atan2(my - height / 2, mx - width / 2)
            return ((a + Math.PI / 2) / (2 * Math.PI) + 1) % 1
        }
        implicitWidth: 48; implicitHeight: 48
        // Same lazy-fetch latch as Art: rows hidden behind the search page's
        // first-5 cap must not spend a connection on a disc nobody can see.
        // Latches on first real show, never unloads after that.
        property bool everShown: false
        Component.onCompleted: if (visible) everShown = true
        onVisibleChanged: if (visible) everShown = true

        // The same rest-armed hover tilt as Art (identical knobs, timings and
        // rest-before-tilt arming), so the track discs no longer sit flat in
        // lists whose every other cover tilts (reported from livetesting on
        // a mix page). Held OFF while this disc's preview is active: the
        // seek ring aims by pointer angle and needs a stable target.
        property real fxRx: 0
        property real fxRy: 0
        property real fxLift: 1.0
        readonly property bool fxOn: root.artFxVariant !== "none" && !pa.active
        property bool fxArmed: false
        function _fxAim(p) {
            var cx = width / 2, cy = height / 2
            fxRy =  ((p.x - cx) / cx) * root.artFxTilt
            fxRx = -((p.y - cy) / cy) * root.artFxTilt
        }
        Timer {
            id: paFxArm; interval: 90
            onTriggered: {
                if (!paFxHover.hovered) return
                pa.fxArmed = true
                pa.fxLift = root.artFxLift
                pa._fxAim(paFxHover.point.position)
            }
        }
        // A preview starting under an armed tilt (click) must not leave the
        // disc frozen mid-tilt while the ring takes over.
        onFxOnChanged: if (!fxOn) { paFxArm.stop(); fxArmed = false; fxRx = 0; fxRy = 0; fxLift = 1.0 }
        Behavior on fxRx   { NumberAnimation { duration: 280; easing.type: root.artFxEase } }
        Behavior on fxRy   { NumberAnimation { duration: 280; easing.type: root.artFxEase } }
        Behavior on fxLift { NumberAnimation { duration: 280; easing.type: root.artFxEase } }
        transform: [
            Rotation { origin.x: pa.width / 2; origin.y: pa.height / 2; axis: Qt.vector3d(1, 0, 0); angle: pa.fxRx },
            Rotation { origin.x: pa.width / 2; origin.y: pa.height / 2; axis: Qt.vector3d(0, 1, 0); angle: pa.fxRy },
            Scale    { origin.x: pa.width / 2; origin.y: pa.height / 2; xScale: pa.fxLift; yScale: pa.fxLift }
        ]
        HoverHandler {
            id: paFxHover
            enabled: pa.fxOn
            onHoveredChanged: {
                if (hovered) paFxArm.restart()
                else {
                    paFxArm.stop(); pa.fxArmed = false
                    pa.fxRx = 0; pa.fxRy = 0; pa.fxLift = 1.0
                }
            }
            onPointChanged: if (pa.fxArmed) pa._fxAim(point.position)
        }

        // Circular cover. A Rectangle's clip ignores its radius (Qt draws children
        // to the square bounds), so the square crop is clipped to a real circle
        // with a MultiEffect mask (the same pattern the download grid uses above).
        // This rides Qt's Image lifecycle (async + cache + warm-pool + an
        // observable error state) instead of a hand-painted Canvas: the Canvas
        // had only a success signal and no retry, so any fetch that failed during
        // the search build's concurrent load burst left the disc grey forever, and
        // some rows lost that lottery at random. The progress ring and glyph are
        // separate siblings below and paint on top of this art.
        Item {
            id: coverWrap
            anchors.centerIn: parent
            width: 34; height: 34   // artR * 2: a 34px circle centred in the 48px box
            rotation: paVinyl.rot   // buffering vinyl spins the cover itself
            // Grey disc under the art: shows while a cover is still decoding and
            // stays when there is no url, matching the old surface3 fill.
            Rectangle { anchors.fill: parent; radius: width / 2; color: root.surface3 }
            Image {
                id: paImg
                anchors.fill: parent
                source: pa.everShown ? pa.url : ""
                fillMode: Image.PreserveAspectCrop
                asynchronous: true
                cache: true
                visible: status === Image.Ready
                // Decode near display size (x2 for HiDPI crispness on the disc).
                sourceSize.width: 68
                sourceSize.height: 68
                // Pin decoded pixels in the warm pool so a rebuilt row (tab switch,
                // filter change, SHOW ALL) repaints without a re-decode. On a
                // transient error, re-request a bounded number of times (the Canvas
                // never did, hence the permanent grey): a cache/warm-pool hit makes
                // the retry instant. Qt.binding keeps the `source: pa.url` binding
                // alive across the reload so a later url change still propagates.
                property int retries: 0
                onStatusChanged: {
                    if (status === Image.Ready)
                        root.warmArt("" + source, sourceSize.width, sourceSize.height)
                    else if (status === Image.Error && retries < 3)
                        paRetry.restart()
                }
                Timer {
                    id: paRetry
                    interval: 600
                    onTriggered: {
                        paImg.retries += 1
                        paImg.source = ""
                        paImg.source = Qt.binding(function () { return pa.everShown ? pa.url : "" })
                    }
                }
                Connections {
                    target: pa
                    function onUrlChanged() { paImg.retries = 0 }
                }
                // Clip the square crop to a real circle: MultiEffect keeps only
                // what the white mask disc covers.
                layer.enabled: true
                layer.effect: MultiEffect {
                    maskEnabled: true
                    maskSource: ShaderEffectSource { sourceItem: paMask; hideSource: false }
                }
            }
            Item {
                id: paMask
                anchors.fill: parent
                visible: false
                Rectangle { anchors.fill: parent; radius: width / 2; color: "#ffffff" }
            }
        }
        // Playback ring: one continuous round-cap arc over a dim track
        // (segment-lab round 2 winner, "solid arc"), drawn with the same
        // CurveRenderer as the Ico glyphs so the curve is genuinely smooth,
        // no rotated-rectangle corners. Fills clockwise from 12 o'clock.
        // Outer edge 19.5 + 1.6 stays within 22 of centre, so the ring can
        // never poke past the 44px thumb box and get clipped by neighbours.
        Item {
            id: ledRing
            visible: pa.active
            anchors.fill: parent
            readonly property real ringR: 19.5
            // Aim UI waits for the pointer to REST over the band (or a press):
            // the straight path to the disc's play/pause crosses the ring, and
            // the readout flashing on every pass-through read as jarring
            // (livetest report). Scrubbing always aims instantly.
            readonly property bool aiming: (paZone.ringHover && paZone.aimArmed) || paZone.scrubbing
            Shape {
                anchors.fill: parent
                antialiasing: true
                preferredRendererType: Shape.CurveRenderer
                // dim track (full circle)
                ShapePath {
                    strokeColor: "#293dff6e"
                    strokeWidth: 3.2; fillColor: "transparent"
                    capStyle: ShapePath.RoundCap
                    PathAngleArc {
                        centerX: ledRing.width / 2; centerY: ledRing.height / 2
                        radiusX: ledRing.ringR; radiusY: ledRing.ringR
                        startAngle: -90; sweepAngle: 360
                    }
                }
                // lit arc (parked at zero while the buffering vinyl settles)
                ShapePath {
                    strokeColor: root.accent
                    strokeWidth: 3.2; fillColor: "transparent"
                    capStyle: ShapePath.RoundCap
                    PathAngleArc {
                        centerX: ledRing.width / 2; centerY: ledRing.height / 2
                        radiusX: ledRing.ringR; radiusY: ledRing.ringR
                        startAngle: -90
                        sweepAngle: paVinyl.busy ? 0 : 360 * Math.max(0, Math.min(1, pa.frac))
                    }
                }
            }
            // Buffering vinyl (lab round 2 winner): while the stream resolves,
            // the cover itself spins up like a record, accelerating through
            // the first turn then cruising, while the ring stays calm. The
            // moment the song is ready the disc eases to rest at its original
            // orientation (always completing the current turn forward) and
            // the playhead sparks in at 12 o'clock, where the progress arc
            // then rises. Smooth and continuous by design: no dots, no
            // segments (livetest direction).
            Item {
                id: paVinyl
                anchors.fill: parent
                readonly property bool loadingNow: pa.st === "loading"
                property bool settling: false
                readonly property bool busy: loadingNow || settling
                property real rot: 0
                onLoadingNowChanged: {
                    if (loadingNow) {
                        vinylSettle.stop(); settling = false
                        rot = 0; vinylSpin.restart()
                    } else if (pa.st === "playing" || pa.st === "paused") {
                        vinylSpin.stop(); settling = true; vinylSettle.restart()
                    } else {
                        vinylSpin.stop(); settling = false; rot = 0
                    }
                }
                SequentialAnimation {
                    id: vinylSpin
                    // spin-up through the first turn, then a steady cruise;
                    // the long tail outlasts any realistic load (4+ minutes)
                    NumberAnimation { target: paVinyl; property: "rot"; from: 0; to: 360; duration: 1300; easing.type: Easing.InQuad }
                    NumberAnimation { target: paVinyl; property: "rot"; from: 360; to: 360 * 400; duration: 400 * 650 }
                }
                Rectangle {
                    id: vinylSpark
                    x: ledRing.width / 2 - width / 2
                    y: ledRing.height / 2 - ledRing.ringR - height / 2
                    width: 5; height: 5; radius: 2.5; color: root.accentSoft
                    antialiasing: true; opacity: 0
                }
                SequentialAnimation {
                    id: vinylSparkPop
                    ParallelAnimation {
                        NumberAnimation { target: vinylSpark; property: "opacity"; from: 0; to: 1; duration: 110 }
                        NumberAnimation { target: vinylSpark; property: "scale"; from: 2.2; to: 1; duration: 260; easing.type: Easing.OutBack }
                    }
                    NumberAnimation { target: vinylSpark; property: "opacity"; to: 0; duration: 200 }
                }
                SequentialAnimation {
                    id: vinylSettle
                    // The settle target is snapshotted here, not bound on the
                    // NumberAnimation: a `to:` binding reading `rot` re-evaluates
                    // on every frame the animation itself writes, which is one
                    // easing overshoot away from a ratcheting runaway.
                    ScriptAction {
                        script: {
                            vinylSparkPop.restart()
                            vinylSettleAnim.to = Math.ceil(paVinyl.rot / 360) * 360
                        }
                    }
                    NumberAnimation {
                        id: vinylSettleAnim
                        target: paVinyl; property: "rot"
                        duration: 520; easing.type: Easing.OutCubic
                    }
                    ScriptAction { script: { paVinyl.rot = 0; paVinyl.settling = false } }
                }
            }
            // Ghost cursor (seek lab): a marker dot at the aim angle, bright
            // phosphor over the unplayed side, a dark notch punched into the
            // arc over the played side, so it stands out on both sides of
            // the playhead.
            Rectangle {
                opacity: ledRing.aiming ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 110 } }
                readonly property real ang: -Math.PI / 2 + pa.aimFrac * 2 * Math.PI
                readonly property bool played: pa.aimFrac <= pa.frac
                antialiasing: true
                x: ledRing.width / 2 + ledRing.ringR * Math.cos(ang) - width / 2
                y: ledRing.height / 2 + ledRing.ringR * Math.sin(ang) - height / 2
                width: 6; height: 6; radius: 3
                color: played ? root.bg : root.accentSoft
                border.width: played ? 1 : 0
                border.color: root.accentDim
            }
        }
        // Only a whisper of a scrim, and only when the glyph is up.
        Rectangle {
            anchors.centerIn: parent; width: 34; height: 34; radius: 17; color: "#000000"
            opacity: pa.showGlyph ? 0.34 : 0
            Behavior on opacity { NumberAnimation { duration: 150 } }
        }
        Item {
            anchors.centerIn: parent; width: 30; height: 30
            // The glyph eases in and out with the scrim (fade plus a slight
            // settle) instead of popping with the hover flag (livetest report).
            opacity: pa.showGlyph ? 1 : 0
            scale: pa.showGlyph ? 1 : 0.8
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 150 } }
            Behavior on scale { NumberAnimation { duration: 190; easing.type: Easing.OutCubic } }
            // Play/pause/error as a vector glyph. Loading shows no glyph:
            // the buffering animation (paVinyl spins the cover) is the activity
            // indicator, over the lightly scrimmed art. A soft dark disc
            // behind the glyph gives the same legibility over art the
            // Text.Outline used to.
            Item {
                anchors.fill: parent
                opacity: pa.st !== "loading" ? 1 : 0
                visible: opacity > 0
                Behavior on opacity { NumberAnimation { duration: 150 } }
                Rectangle {
                    anchors.centerIn: parent; width: 26; height: 26; radius: 13
                    color: "#55000000"
                }
                Ico {
                    anchors.centerIn: parent
                    name: pa.st === "playing" ? "pause" : (pa.st === "error" ? "close" : "play")
                    color: pa.st === "error" ? root.red : root.accent
                    size: 17
                }
            }
        }
        // The would-seek time, floating above the art while aiming at the ring.
        Rectangle {
            opacity: ledRing.aiming ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: 120 } }
            anchors.horizontalCenter: parent.horizontalCenter
            y: -18
            width: paAimTxt.implicitWidth + 12; height: 16; radius: 4
            color: root.surfaceHi; border.color: root.border1
            Text {
                id: paAimTxt
                anchors.centerIn: parent; textFormat: Text.PlainText
                text: root.fmtMs(pa.aimFrac * root.previewDuration)
                color: root.accentContTx; font.family: root.mono; font.pixelSize: 9
            }
        }
        // Pointer zone (seek-lab ghost cursor). Idle, the whole box starts the
        // preview exactly as the old full-box MouseArea did. While this track
        // is active, the disc toggles play/pause and the band around it is the
        // seek surface: a resting hover aims the ghost marker, press/drag scrubs the
        // fill locally, the single real seek fires on release (same discipline
        // as the PreviewBar scrubber, no mid-gesture flush pop).
        MouseArea {
            id: paZone
            anchors.fill: parent
            anchors.margins: -6
            hoverEnabled: true
            preventStealing: true
            property bool scrubbing: false
            property bool ringHover: false
            // Rest-armed aim (same idiom as the art tilt): the readout and
            // ghost marker appear only once the pointer stops moving over the
            // band, so crossing the ring on the way to play/pause never
            // flashes them. The arm timer restarts on every move while
            // unarmed; a press (scrub) bypasses arming entirely.
            property bool aimArmed: false
            onRingHoverChanged: if (!ringHover) { paAimArm.stop(); aimArmed = false }
            Timer {
                id: paAimArm; interval: 160
                onTriggered: paZone.aimArmed = true
            }
            readonly property real discR: 17
            readonly property real bandR: 28
            readonly property real idleR: 24
            function dist(mx, my) {
                var dx = mx - width / 2; var dy = my - height / 2
                return Math.sqrt(dx * dx + dy * dy)
            }
            cursorShape: (ringHover || dist(mouseX, mouseY) <= (pa.active ? discR : idleR))
                         ? Qt.PointingHandCursor : Qt.ArrowCursor
            onPositionChanged: function(m) {
                var d = dist(m.x, m.y)
                pa.hovered = pa.active ? d <= discR : d <= idleR
                ringHover = pa.active && d > discR && d <= bandR
                if (ringHover && !aimArmed) paAimArm.restart()
                if (ringHover || scrubbing) {
                    pa.aimFrac = pa.angFrac(m.x + x, m.y + y)
                    if (scrubbing) root.scrubPreviewVisual(pa.aimFrac)
                }
            }
            onEntered: pa.hovered = !pa.active
            onExited: { pa.hovered = false; ringHover = false }
            onPressed: function(m) {
                var d = dist(m.x, m.y)
                if (pa.active && d > discR && d <= bandR) {
                    scrubbing = true; root.previewScrubbing = true
                    pa.aimFrac = pa.angFrac(m.x + x, m.y + y)
                    root.scrubPreviewVisual(pa.aimFrac)
                    m.accepted = true
                } else if (d <= (pa.active ? discR : idleR)) {
                    m.accepted = true
                } else {
                    m.accepted = false
                }
            }
            onReleased: function(m) {
                if (scrubbing) {
                    scrubbing = false; root.previewScrubbing = false
                    root.seekPreview(pa.aimFrac)
                } else if (dist(m.x, m.y) <= (pa.active ? discR : idleR)) {
                    root.togglePreview(pa.kind, pa.pid, 0)
                }
            }
            onCanceled: { scrubbing = false; root.previewScrubbing = false }
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
        // Inside a RollSwap the outline is the wrapper's pill, and drawing a
        // second one here would double every edge. The fill stays: it is what
        // makes the control read over artwork.
        property bool bare: false
        width: 140; height: 30; radius: root.btnRad; clip: true
        // natural (content) size per the shared button rules, callers may
        // still set explicit width/height (the artist page's fixed bar does)
        implicitWidth: pbIdleRow.implicitWidth + root.btnPadH * 2
        implicitHeight: pbIdleRow.implicitHeight + root.btnPadV * 2
        color: st === "error" ? root.redCont : "transparent"
        border.width: bare ? 0 : root.btnBorderW
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
    // Hover swell (edge-lab round 3 winner): the control's outline turns
    // bright accent and breathes while the pointer is on it. Light only, no
    // travel: the line keeps its resting weight, exit is a fade in place.
    // Scoped to the clickable region, so on the two-up pills each half lights
    // by itself (outer corners rounded, square edge on the divider side).
    component HoverSwell: Rectangle {
        id: hs
        property bool on: false
        property real radL: root.btnRad     // 0 on the side that meets a divider
        property real radR: root.btnRad
        property real pulse: 0              // 0..1, the slow breath
        property color tone: root.accent    // the frame colour family (gold on the library claim)
        color: "transparent"
        border.width: root.btnBorderW
        border.color: Qt.lighter(hs.tone, 1.04 + 0.14 * pulse)
        topLeftRadius: radL; bottomLeftRadius: radL
        topRightRadius: radR; bottomRightRadius: radR
        // In fast and flat, out slow and eased, so crossing the PREVIEW |
        // DOWNLOAD divider hands the light over without a gap.
        // States, not a Behavior: a Behavior whose duration binding also reads
        // hs.on captures the OLD value when the flip triggers it, which swapped
        // the two durations (measured: 262ms in, 98ms out, exactly reversed).
        // A transition is picked by direction, so it cannot race the flag.
        opacity: 0
        states: State { name: "lit"; when: hs.on
            PropertyChanges { target: hs; opacity: 1 } }
        transitions: [
            Transition { to: "lit"
                NumberAnimation { property: "opacity"; duration: 90; easing.type: Easing.Linear } },
            Transition { from: "lit"
                NumberAnimation { property: "opacity"; duration: 260; easing.type: Easing.OutQuad } }
        ]
        // Slow and symmetric, so it reads as the control being alive rather
        // than as an animation starting.
        SequentialAnimation {
            running: hs.on
            loops: Animation.Infinite
            alwaysRunToEnd: true
            NumberAnimation { target: hs; property: "pulse"; from: 0; to: 1; duration: 1100; easing.type: Easing.InOutSine }
            NumberAnimation { target: hs; property: "pulse"; from: 1; to: 0; duration: 1100; easing.type: Easing.InOutSine }
        }
    }

    component DownloadButton: Rectangle {
        id: db
        property string mediaId: ""
        property string label: "Download"
        // The media word the resolved label leads with ("TRACK IN LIBRARY",
        // "ALBUM DOWNLOADED"), so a done button says what it is talking about
        // instead of a bare IN LIBRARY. Derived from the idle label's own
        // noun; set `noun` where the label is generic ("Download" on a video
        // cell). Empty means the plain label, for buttons like "Download all"
        // whose scope has no one-word name.
        property string noun: ""
        readonly property string doneNoun: {
            if (noun !== "") return noun.toUpperCase()
            var l = label.toLowerCase()
            if (l.indexOf("videos") !== -1) return "VIDEOS"
            if (l.indexOf("video") !== -1) return "VIDEO"
            if (l.indexOf("track") !== -1) return "TRACK"
            if (l.indexOf("album") !== -1) return "ALBUM"
            if (l.indexOf("playlist") !== -1) return "PLAYLIST"
            if (l.indexOf("mix") !== -1) return "MIX"
            if (l.indexOf("artist") !== -1 || l.indexOf("discography") !== -1) return "DISCOGRAPHY"
            return ""
        }
        property var onTap: (function(){})
        // Opt-in for track-scoped buttons only: the ownership store is keyed by
        // exact track id, so a plain album/playlist/artist mediaId must not be
        // looked up as if it were one.
        property bool ownedCheck: false
        // Collection rollup: when set (non-null array of member track/video
        // ids), DOWNLOADED means every one of those ids is individually owned.
        // Set where the caller already has the member ids in hand (an opened
        // album/playlist/mix page, an expanded album panel), never triggers
        // a fetch itself.
        property var collectionIds: null
        // Collection rollup, discovered locally: mediaId is a collection id
        // (album/playlist/mix) and its member ids are looked up from what
        // Waves has already LEARNED locally (see collectionMemberIds): no
        // caller-supplied list needed, so this also covers collapsed rows and
        // shelf cards that have never had their track list fetched. A
        // collection Waves has genuinely never opened or downloaded reads as
        // unknown (not owned) until the first time it is: this is a local
        // cache, not a live query, so it can never require a network call.
        property bool collectionCheck: false
        property bool owned: false
        // Optional album-level library check: when the caller supplies the
        // album's identity facts, a FULL local copy renders this button in the
        // same inert DOWNLOADED state the ownership rollup uses, so an album
        // you already have is prevented at the button itself. A PARTIAL copy
        // keeps a live Download (completing an album is not a duplicate), and
        // no facts or no scan index means no check. Display and this button's
        // own state are the ONLY consumers of the answer: the engine never
        // sees it, and every claim, album or track, keeps its click routed to
        // the gate, so a wrong tag match is always one click from downloading
        // anyway.
        //
        // The album's identity arrives as ONE object ({artist, title, year,
        // tracks}), not four properties. Four properties meant four change
        // handlers and so four QML->Python presence calls to fill one button
        // in, plus a fifth from onCompleted: measured at 15 calls per album
        // row, 750 for a 50-row page at 22us apiece, ~16ms of GUI thread spent
        // asking the same question. One property is one binding evaluation and
        // one call.
        property var libAlbum: null
        // The track twin of libAlbum ({artist, title, album, year}), for buttons
        // that download ONE song. Same economy, same one object per button.
        //
        // A track has no coverage axis: it is on disk or it is not, so a match
        // is always the done shape and libPartial can never be true here. What
        // it keeps from the album side is the part that matters, the click
        // still opens the gate, because a track match is the most brittle
        // guess this app makes (the key is the exact normalised title and
        // artist, edition qualifiers included) and a guess must never be the
        // end of the conversation. Never set for a video: the scan only ever
        // holds audio.
        property var libTrack: null
        // Kept as a derived read for the Connections guard below (and so a test
        // can find a button by the album or track it is showing).
        readonly property string libTitle: {
            if (libAlbum && libAlbum.title) return "" + libAlbum.title
            if (libTrack && libTrack.title) return "" + libTrack.title
            return ""
        }
        property bool libPresent: false
        // The scan matched this album but not all of it (some tracks are on
        // disk, some are not): the button stays an action, in cyan, and a
        // click downloads normally, which with the bulk claim gate on fetches
        // only the missing tracks.
        property bool libPartial: false
        // The identity axis of the verdict (presence "sure"): the match was
        // proven by year and edition title, not just the key. A sure claim
        // wears the same green IN LIBRARY as the fact tier (the state is the
        // colour); gold MAYBE is reserved for unproven matches. What green
        // claim keeps that owned green lacks: the click still opens the gate.
        property bool libSure: false
        // The matched local folder, carried so the click-through can name it
        // and reveal it (see libraryClaimGate).
        property string libPath: ""
        property bool _libResolved: false
        function refreshLibPresent() {
            _libResolved = true
            var a = db.libAlbum
            if (!a || !a.title) {
                var t = db.libTrack
                if (!t || !t.title) { libPresent = false; libPartial = false; libSure = false; libPath = ""; return }
                var tp = waves.libraryTrackPresence("" + (t.artist || ""), "" + t.title,
                                                    "" + (t.album || ""), "" + (t.year || ""),
                                                    t.duration_sec || 0)
                // No completeness bar to apply: presence alone is the done
                // shape, and identity alone picks green from gold.
                libPresent = !!(tp && tp.present === true)
                libPartial = false
                libSure = !!(tp && tp.sure === true)
                libPath = libPresent ? ("" + (tp.local_album_id || "")) : ""
                return
            }
            var p = waves.libraryAlbumPresence("" + (a.artist || ""), "" + a.title,
                                               "" + (a.year || ""), a.tracks || 0, a.duration_sec || 0)
            // Coverage picks the shape (gold claim vs cyan partial), identity
            // picks the gold wording: an apparently complete but unproven
            // match is exactly what MAYBE is for, while a copy short on
            // tracks is partial no matter how proven.
            libPresent = !!(p && p.present === true && p.full === true)
            libPartial = !!(p && p.present === true && p.full !== true)
            libSure = !!(p && p.sure === true)
            libPath = libPresent ? ("" + (p.local_album_id || "")) : ""
        }
        // DOWNLOADED purely because the library scan matched something on disk:
        // a GUESS from tags, not a record of a download Waves made. Ownership
        // (`owned`) is a fact and stays inert; this one keeps a way through,
        // because a wrong match must never be the end of the conversation.
        readonly property bool libClaim: liveSt === "" && !owned && libPresent
        // The gold face of a claim: only the UNPROVEN ones. A sure claim falls
        // through to the green done styling below (state is the colour) while
        // libClaim keeps routing its click to the gate.
        readonly property bool libGuess: libClaim && !libSure
        // The partial twin: informative colour on a still-live button. Never
        // true alongside libClaim (present is either full or partial).
        readonly property bool libPartialClaim: liveSt === "" && !owned && libPartial
        // The click-through itself: explain the claim, show where the copy is,
        // and leave DOWNLOAD ANYWAY one click away. A function rather than
        // inline in the MouseArea so the scenario test can drive it.
        function openLibraryClaim() {
            root.openLibraryClaim(db.mediaId, db.libTitle, db.libPath,
                                  db.libAlbum ? "album" : "track")
        }
        // "pending" = at least one member's ownership has never been answered
        // this session (the cache is cold and a worker is fetching); a single
        // firm "not owned" settles the rollup to false no matter what is
        // still in flight. The button holds its roll animation on pending so
        // the answer landing a beat later snaps in instead of visibly
        // flipping a face that was never true.
        function _rollup(ids) {
            if (!ids || ids.length === 0) return false
            var pending = false
            for (var i = 0; i < ids.length; ++i) {
                var oi = waves.ownershipOf(ids[i])
                if (oi.pending === true) { pending = true; continue }
                if (!(oi.owned === true && oi.up_to_date === true)) return false
            }
            return pending ? "pending" : true
        }
        property bool ownPending: false
        // collectionCheck mode's member ids, cached so the ownership listener
        // can ignore other collections' answers (see the ArtCard twin).
        property var _ownIds: null
        function refreshOwned() {
            var r
            if (collectionIds !== null) r = _rollup(collectionIds)
            else if (collectionCheck && mediaId !== "") {
                _ownIds = waves.collectionMemberIds(mediaId)
                r = _rollup(_ownIds)
            }
            else {
                var o = ownedCheck && mediaId !== "" ? waves.ownershipOf(mediaId) : ({})
                r = o.pending === true ? "pending" : (o.owned === true && o.up_to_date === true)
            }
            ownPending = (r === "pending")
            owned = (r === true)
        }
        onOwnPendingChanged: if (!ownPending && !rollReady) { syncGhost(); dbSettle.restart() }
        // Only if the libAlbum binding has not already answered: an
        // unconditional resolve here is exactly the extra call this shape
        // exists to avoid.
        // rollReady only once the ownership answer is real: a cold cache
        // answers "pending" and the truth lands a beat later; arming the roll
        // then would animate a flip the user reads as every button changing
        // its mind (reported from livetesting an owned album page).
        Component.onCompleted: { refreshOwned(); if (!_libResolved) refreshLibPresent(); syncGhost(); if (!ownPending) rollReady = true }
        onMediaIdChanged: { if (rollReady) { rollReady = false; dbSettle.restart() } refreshOwned() }
        onOwnedCheckChanged: refreshOwned()
        onCollectionIdsChanged: refreshOwned()
        onCollectionCheckChanged: refreshOwned()
        onLibAlbumChanged: refreshLibPresent()
        onLibTrackChanged: refreshLibPresent()
        Connections {
            target: waves
            enabled: db.libTitle !== ""
            function onLibraryPresenceChanged() { db.refreshLibPresent() }
        }
        Connections {
            target: waves
            enabled: db.ownedCheck || db.collectionIds !== null || db.collectionCheck
            // Empty id = broadcast (the quality setting changed).
            function onOwnershipChanged(tid) {
                if (db.collectionIds !== null) {
                    if (tid === "" || db.collectionIds.indexOf(tid) !== -1) db.refreshOwned()
                } else if (db.collectionCheck) {
                    if (tid === "" || (db._ownIds && db._ownIds.indexOf(tid) !== -1)) db.refreshOwned()
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
        readonly property string st: liveSt !== "" ? liveSt : ((owned || libPresent) ? "done" : "")
        // "preparing" = the click is parked behind a prerequisite (a metadata
        // re-fetch, a folder-tree warm, an edition scan) and has not reached the
        // queue yet. It draws exactly like queued, so the hand-over to a real
        // queue row is the cancel ✕ fading in and nothing else moving. It used
        // to draw as "running", which flashed a progress bar for a download that
        // had not started and then snapped to the queued pill.
        readonly property bool waiting: st === "queued" || st === "preparing"
        implicitHeight: dbRow.implicitHeight + root.btnPadV * 2
        // Width is pinned to the idle label ("⭳ DOWNLOAD …") so the button
        // doesn't shrink when the state text changes to DONE/RETRY, keeps
        // row columns aligned and avoids layout jumps mid-download.
        implicitWidth: Math.max(dbRow.implicitWidth, dbMetric.implicitWidth,
                                dbMetricDone.implicitWidth, dbMetricQueued.implicitWidth,
                                db.libAlbum ? dbMetricLib.implicitWidth
                                            : db.libTrack ? dbMetricLibTrack.implicitWidth : 0) + root.btnPadH * 2
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
            Text { textFormat: Text.PlainText; text: (db.doneNoun !== "" ? db.doneNoun + " " : "") + (root.libraryOn && (db.libPresent || root.dlInLibrary) ? "IN LIBRARY" : "DOWNLOADED"); font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack }
        }
        // The longest library-claim label ("PARTIALLY IN LIBRARY" beats "MAYBE
        // IN LIBRARY") is measured for the same reason DOWNLOADED is: the
        // width must not move when a claim resolves, or one matched album in a
        // list would shunt its own row's button out of line with every
        // neighbour. Counted ONLY for album-capable buttons (libAlbum set),
        // since a track button can never reach either state and must not
        // reserve width for them.
        Row {
            id: dbMetricLib
            visible: false; spacing: 7
            Ico { name: "check"; color: root.accent; size: 14 }
            Text { textFormat: Text.PlainText; text: "PARTIALLY IN LIBRARY"; font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack }
        }
        // The track twin. A track can reach only two claim faces, MAYBE IN
        // LIBRARY and the done label, and the done label is already measured
        // above (dbMetricDone spells the noun out), so the hedge is the one
        // width left to reserve. Kept separate from dbMetricLib because
        // PARTIALLY is a word a track button can never say, and reserving for
        // it would pad every track row in the app.
        Row {
            id: dbMetricLibTrack
            visible: false; spacing: 7
            Ico { name: "check"; color: root.accent; size: 14 }
            Text { textFormat: Text.PlainText; text: "MAYBE IN LIBRARY"; font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack }
        }
        // Queued measures too, now that it carries a cancel X: without it the
        // button grew the moment a click was queued, and in a track list every
        // row's button is aligned against its neighbours'. Boxes, not the real
        // glyphs: a QueueStack and an Ico per idle row would be three bars and
        // a vector path built for a state the row is almost never in.
        Row {
            id: dbMetricQueued
            visible: false; spacing: 7
            Item { width: 12; height: 12 }      // QueueStack, default barW
            Text { textFormat: Text.PlainText; text: db.queuedLabel; font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack }
            Item { width: 13; height: 13 }      // the cancel X
        }
        radius: root.btnRad
        // Inside a RollSwap the outline is the wrapper's; see PreviewBar.bare.
        property bool bare: false
        // Filled like DOWNLOAD SELECTED so every download button reads primary.
        // A library claim wears gold, not green: green DOWNLOADED is a fact
        // (Waves wrote that exact file), gold IN LIBRARY is a tag match, a
        // guess that stays clickable to explain itself.
        // A partial claim wears cyan: still a live download button (a click
        // fetches what's missing), coloured to say part of this is on disk.
        readonly property color fill: libGuess ? root.goldCont : libPartialClaim ? root.cyanCont : st === "done" ? root.greenCont : st === "failed" ? root.redCont : root.accentCont
        readonly property color edge: libGuess ? root.goldDim : libPartialClaim ? root.cyanDim : st === "done" ? root.greenDim : st === "failed" ? root.red : root.accentDim
        color: fill
        border.width: bare ? 0 : root.btnBorderW
        border.color: edge
        // State changes FADE between colours instead of snapping: queued ->
        // running -> done reads as one button changing its mind, not three
        // buttons taking turns. 420ms InOutQuad, paced to land just after
        // the 340ms state roll below so the hue settles as the new face
        // does; symmetric easing because OutCubic spends its travel in the
        // first frames and reads as a snap on these near-black fills.
        // Instant with motion off, same gate as the other control animation.
        Behavior on color { ColorAnimation { duration: root.hoverMotion ? 420 : 0; easing.type: Easing.InOutQuad } }
        Behavior on border.color { ColorAnimation { duration: root.hoverMotion ? 420 : 0; easing.type: Easing.InOutQuad } }
        clip: true
        scale: 1
        Behavior on scale { NumberAnimation { duration: 130; easing.type: Easing.OutBack } }

        // RUNNING, dot matrix fills the full button width; % pinned to the right.
        // Behind a Loader rather than plain `visible: false`: an invisible
        // subtree is still BUILT, and this one is a 92-dot matrix. A row that
        // is not downloading has no progress to draw, but every row of a
        // 500-track playlist was paying for the matrix anyway: 92 of the 232
        // items a TrackRow creates, measured. The Loader has a size, so the
        // loaded Item is sized to it (no anchors needed inside).
        Loader {
            // The matrix rides the same state roll as the label faces: it
            // arrives from below when a run starts (while the queued face
            // exits above) and leaves through the top when the run ends.
            // Kept active until its own exit lands, or the end of a run would
            // unload 92 bright dots in a single frame.
            active: db.st === "running" || opacity > 0
            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
            // The exit belt is taken ONLY when this roll is the matrix's own
            // exit (matrixRollOut, set when the state leaves "running"). Every
            // other roll is a label swap between two text faces, and riding
            // rollOut unconditionally put a progress matrix on the way out of
            // rolls that never ran: a click from idle to queued flashed one,
            // and so did every button whose library claim resolved late.
            opacity: db.st === "running" ? db.rollIn : (db.matrixRollOut ? db.rollOut : 0)
            transform: Translate { y: db.st === "running" ? 10 * (1 - db.rollIn) : -10 * (1 - db.rollOut) }
            sourceComponent: Item {
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
                    // Only loaded while st === "running", so 100% here means the
                    // final steps are still in flight: twinkle in step with the
                    // queue row for the same item.
                    finishing: db.pct >= 99.9
                }
            }
        }
        // IDLE / QUEUED / DONE / FAILED, centered glyph + label
        // Queued: the click is in the queue but no download slot has picked
        // it up yet; the stack glyph's walking highlight says the wait is
        // alive, and the label keeps the media noun ("QUEUED ALBUM").
        readonly property string queuedLabel: "QUEUED" + label.toUpperCase().replace("DOWNLOAD", "")
        // --- The state roll: RollSwap's belt, transplanted onto the button's
        // own face changes. When the label text changes (the one reliable
        // tell that the face did), the OLD face rides out the top while the
        // new one arrives from the bottom, on RollSwap's no-overlap curve
        // (exit ends at 0.44, entry starts there). The ghost row shows the
        // last COMMITTED face (icon + label snapshot, resynced when the roll
        // lands), so mid-roll both faces are real pixels, not a text pop.
        property real rollT: 1
        readonly property real rollOut: 1 - Math.min(1, rollT / 0.44)
        readonly property real rollIn: Math.max(0, (rollT - 0.44) / 0.56)
        property string ghostIco: ""
        property color ghostIcoColor: root.accent
        property string ghostText: ""
        property color ghostTextColor: root.accent
        property bool rollReady: false
        // Rebinding is not news: a recycled list delegate (reuseItems) hands
        // this button the next row's identity, and the face swap that follows
        // must land settled, not ride the belt, exactly the pills' settleKey
        // rule (see LibraryTag). mediaId is what the button is currently
        // about, so its change drops the latch; the 0-interval re-arm
        // restores it once the rebind's own property changes have landed.
        Timer { id: dbSettle; interval: 0; onTriggered: db.rollReady = true }
        // Whether THIS roll is the matrix's own exit (a run just ended). It
        // picks which of the two outgoing faces rides the belt: the matrix
        // takes it and the ghost sits out, since two things leaving through
        // the top would double up. False for every label-only swap, or an
        // idle button would flash a progress matrix it never earned.
        property bool matrixRollOut: false
        property string _prevSt: ""
        onStChanged: {
            var was = _prevSt
            _prevSt = st
            if (!rollReady) return
            if (st === "running") { matrixRollOut = false; startRoll() }
            else { matrixRollOut = (was === "running"); if (matrixRollOut) startRoll() }
        }
        function syncGhost() {
            ghostIco = (db.st !== "failed" && !db.waiting && db.st !== "running") ? dbFaceIco.name : ""
            ghostIcoColor = dbFaceIco.color
            ghostText = dbFaceText.text
            ghostTextColor = dbFaceText.color
        }
        function startRoll() {
            rollAnim.stop()
            rollT = 0
            rollAnim.start()
        }
        NumberAnimation {
            id: rollAnim
            target: db; property: "rollT"; to: 1
            duration: root.hoverMotion ? 340 : 0
            easing.type: Easing.OutCubic
            onStopped: db.syncGhost()
        }
        // The outgoing face. No input, no loaders: a still image of what the
        // button just stopped saying, on its way out through the top.
        Row {
            id: dbGhost; spacing: 7
            anchors.centerIn: parent
            anchors.verticalCenterOffset: -10 * (1 - db.rollOut)
            opacity: db.rollOut
            // The matrix takes the exit belt itself both into and out of a
            // run; the ghost would ride alongside showing a stale face.
            visible: opacity > 0 && db.st !== "running" && !db.matrixRollOut
            z: 1
            Ico {
                visible: db.ghostIco !== ""
                name: db.ghostIco || "arrow-down"; color: db.ghostIcoColor
                size: 14; bold: db.ghostIco === "check" ? 0 : 10
                anchors.verticalCenter: parent.verticalCenter
            }
            Text {
                textFormat: Text.PlainText
                text: db.ghostText; color: db.ghostTextColor
                font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack
                anchors.verticalCenter: parent.verticalCenter
            }
        }
        Row {
            id: dbRow; anchors.centerIn: parent; spacing: 7
            // Normally the incoming face (from below, as the ghost leaves
            // above). While a run starts it swaps roles: the frozen queued
            // face is the OUTGOING one, exiting above as the matrix arrives.
            anchors.verticalCenterOffset: db.st === "running" ? -10 * (1 - db.rollOut) : 10 * (1 - db.rollIn)
            opacity: db.st === "running" ? db.rollOut : db.rollIn
            visible: opacity > 0
            // Above the button-wide MouseArea declared below, which would
            // otherwise swallow every press aimed at the cancel X. Nothing
            // else in here takes input, so raising the row costs no clicks.
            z: 1
            Ico {
                id: dbFaceIco
                visible: db.st !== "failed" && !db.waiting && db.st !== "running"
                name: db.st === "done" ? "check" : "arrow-down"
                color: db.libGuess ? root.gold : db.libPartialClaim ? root.cyan : root.accent
                Behavior on color { ColorAnimation { duration: root.hoverMotion ? 420 : 0; easing.type: Easing.InOutQuad } }
                size: 14; bold: db.st === "done" ? 0 : 10; anchors.verticalCenter: parent.verticalCenter
            }
            // Both draw through a Canvas, and both belong to a state a row is
            // almost never in. Same reason as the dot matrix above: gate them
            // on `active`, not `visible`, so an idle row builds neither.
            // `visible: active` matters: a Row skips invisible children but
            // still spaces around a zero-width one, so an inactive Loader left
            // visible would pad the idle button by two gaps.
            Loader {
                // Held through "running" too: the queued face rides the exit
                // belt as the matrix arrives, and unloading the stack glyph at
                // the waiting flip would snap it out one frame into that roll.
                active: db.waiting || db.st === "running"; visible: active
                anchors.verticalCenter: parent.verticalCenter
                sourceComponent: QueueStack {}
            }
            Loader {
                active: db.st === "failed"; visible: active
                anchors.verticalCenter: parent.verticalCenter
                sourceComponent: RetryMark { color: root.red; box: 16 }
            }
            Text {
                id: dbFaceText
                // Named so the scenario test can read the WORDS the user sees,
                // not just the properties that are supposed to pick them.
                objectName: "dbFaceText"
                // The face's one reliable change tell: every state lands with
                // its own words, so a text change IS a face change, and the
                // roll rides it. Guarded until the first commit so creation
                // does not roll.
                //
                // Skipped while a roll is already running, because then the
                // state handler started it and owns matrixRollOut: without the
                // guard, a state change that also changes the words (every run
                // ending) restarted its own roll here and cancelled the
                // matrix's exit. When no roll is in flight the change is a
                // label-only swap, which clears the flag rather than inheriting
                // whatever the last state change left in it.
                onTextChanged: if (db.rollReady && !rollAnim.running) { db.matrixRollOut = false; db.startRoll() }
                textFormat: Text.PlainText  // db.label carries a remote artist name
                // The claim says what it knows, never what the fact-state says
                // ("DOWNLOADED"): the scan matched tags on disk, it did not
                // watch Waves write the file. It says MAYBE out loud, because
                // this button is where a guess PREVENTS an action, and the
                // hedge is the difference between "we recorded this" and "we
                // recognised this". The gold stays: colour separates it from
                // green DOWNLOADED, the word explains why they differ.
                // With the library scan on, the done state reads IN LIBRARY
                // instead of DOWNLOADED, but only when the library actually
                // holds the copy: downloads land inside the library root
                // (dlInLibrary) or the scan proved this one present
                // (libPresent). A download to a folder OUTSIDE the library
                // stays DOWNLOADED; move the files in and the rescan flips
                // the word. Green still marks a recorded fact (gold MAYBE
                // and cyan PARTIALLY are the guesses).
                // "running" keeps the queued face frozen: the row is riding the
                // exit belt as the matrix arrives, and letting the text fall
                // through to the idle label would restart the roll mid-exit.
                text: db.libGuess ? "MAYBE IN LIBRARY" : db.st === "done" ? ((db.doneNoun !== "" ? db.doneNoun + " " : "") + (root.libraryOn && (db.libPresent || root.dlInLibrary) ? "IN LIBRARY" : "DOWNLOADED")) : db.st === "failed" ? "RETRY" : (db.waiting || db.st === "running") ? db.queuedLabel : db.libPartialClaim ? "PARTIALLY IN LIBRARY" : db.label.toUpperCase()
                color: db.libGuess ? root.gold : db.st === "done" ? root.accent : db.st === "failed" ? root.red : (db.waiting || db.st === "running") ? root.accentDim : db.libPartialClaim ? root.cyan : root.accent
                Behavior on color { ColorAnimation { duration: root.hoverMotion ? 420 : 0; easing.type: Easing.InOutQuad } }
                font.family: root.uiFont; font.pixelSize: 11; font.bold: true; font.letterSpacing: root.btnTrack
                anchors.verticalCenter: parent.verticalCenter
            }
            // Queued only: give up the wait right here, instead of opening the
            // queue drawer to find the same row. Its own click zone, because
            // the button around it is deliberately inert while queued.
            // Loaded for "preparing" too, holding its space blank and inert:
            // there is no queue row to cancel yet, and reserving the width means
            // the label doesn't shift sideways when the row lands and the ✕
            // fades in.
            Loader {
                // Same hold as the stack glyph: alive through the queued face's
                // exit under the matrix (the X itself already fades via its
                // own opacity the moment st leaves "queued").
                active: db.waiting || db.st === "running"; visible: active
                anchors.verticalCenter: parent.verticalCenter
                sourceComponent: Ico {
                    // Red at rest, brighter on hover: the same read as the
                    // player's stop glyph, and the only way out of a queued
                    // click should not have to be hunted for.
                    name: "close"; size: 13; bold: 8
                    color: dbCancelMa.containsMouse ? "#ff7d76" : root.red
                    opacity: db.st === "queued" ? 1 : 0
                    Behavior on opacity { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                    MouseArea {
                        id: dbCancelMa
                        anchors.fill: parent; anchors.margins: -5
                        enabled: db.st === "queued"
                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: function(m) { m.accepted = true; root.cancelQueuedMedia(db.mediaId) }
                    }
                }
            }
        }
        MouseArea {
            // Named so the scenario test can drive the REAL tap area rather
            // than the functions behind it (the wiring is the thing at risk).
            objectName: "dbTapArea"
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
            onPressed: db.scale = 0.96
            onReleased: db.scale = 1.0
            onCanceled: db.scale = 1.0
            onClicked: {
                // A library claim is a guess, so it answers instead of ignoring.
                if (db.libClaim) { db.openLibraryClaim(); return }
                if (db.st === "running" || db.st === "done" || db.waiting) return
                db.onTap()
            }
        }
        // Always enabled: a HoverHandler whose enabled flips false UNDER the
        // pointer can latch hovered true (and its cursor claim) until the
        // pointer re-enters, so the state gating lives where the value is
        // consumed instead. Idle, plus the library claim, is what swells:
        // running/queued and a real DOWNLOADED are not clickable, and failed
        // keeps its red frame instead of a green breath.
        HoverHandler { id: dbHover }
        HoverSwell {
            anchors.fill: parent
            // The claim's swell is GOLD like its label and check: the green
            // breath is the download-action language, and this button is
            // currently declining to be one.
            tone: db.libGuess ? root.gold : db.libPartialClaim ? root.cyan : root.accent
            on: dbHover.hovered && (db.st === "" || db.libClaim)
        }
    }

    // Playlist-folder tile (issue #11): folders draw their own glyph, they
    // have no artwork.
    component FolderTile: Rectangle {
        width: 44; height: 44; radius: 6
        color: root.surface3; border.color: root.border1; border.width: 1
        Item {
            anchors.centerIn: parent
            width: 22; height: 17
            Rectangle { width: 9; height: 4; radius: 1; color: "transparent"; border.width: 1.2; border.color: root.accentDim }
            Rectangle { y: 3; width: 22; height: 14; radius: 2; color: "transparent"; border.width: 1.2; border.color: root.accentDim }
        }
    }

    // Odometer digit for the folder badge: the next value drops in from above
    // while the old one falls away, clipped to the badge (220ms, OutQuad).
    // The final tick rolls in a checkmark instead of 0.
    component OdoDigit: Item {
        id: od
        property string value: "0"
        property string shown: "0"
        property string _pending: "0"
        implicitWidth: Math.max(odA.implicitWidth, odB.implicitWidth)
        implicitHeight: odA.implicitHeight
        clip: true
        Component.onCompleted: shown = value
        onValueChanged: {
            // Land any roll in flight BEFORE the no-op check. Returning early
            // on value === shown used to leave the animation running, and its
            // ScriptAction then latched the abandoned _pending: the digit stuck
            // on a number the badge no longer holds. LibList pools delegates
            // (reuseItems), so a fast flick rebinds one A -> B -> A well inside
            // the 220ms roll and a folder ends up wearing another one's count.
            if (odAnim.running) {
                odAnim.stop()
                od.shown = od._pending
                odA.y = 0; odB.y = -od.height
            }
            if (value === od.shown) return
            od._pending = value
            odB.text = value
            odAnim.restart()
        }
        Text {
            id: odA
            textFormat: Text.PlainText; text: od.shown
            color: root.accent; font.family: root.mono; font.pixelSize: 12; font.bold: true
        }
        Text {
            id: odB
            textFormat: Text.PlainText; text: ""
            color: root.accent; font.family: root.mono; font.pixelSize: 12; font.bold: true
            y: -od.height
        }
        SequentialAnimation {
            id: odAnim
            ParallelAnimation {
                NumberAnimation { target: odA; property: "y"; from: 0; to: od.height; duration: 220; easing.type: Easing.OutQuad }
                NumberAnimation { target: odB; property: "y"; from: -od.height; to: 0; duration: 220; easing.type: Easing.OutQuad }
            }
            ScriptAction { script: { od.shown = od._pending; odA.y = 0; odB.y = -od.height } }
        }
    }

    // Count badge on a folder's "Download all" button. Idle: the folder's
    // playlist total. Running: playlists remaining, rolling down one tick per
    // completed playlist; the last tick rolls in a checkmark, holds a beat,
    // then the badge shrinks and fades away with the button flipped to done.
    component FolderBadge: Rectangle {
        id: fb
        property string folderId: ""
        property int total: 0
        property string st: ""   // the folder DownloadButton's st
        readonly property string value: {
            // "failed" reads as idle again: _bump_folder_group emits
            // folderRemaining(fid, 0, total) for a failed member as well as a
            // done one, so a tick here would paint green success straight over
            // the red RETRY button, and it would never clear (the dismissal
            // Timer only arms for "done"). The total is what RETRY re-queues.
            var m = root.folderRemainMap
            var known = m[folderId] !== undefined
            if (st === "" || st === "queued" || st === "preparing" || st === "failed")
                return total > 0 ? "" + total : (known ? "" + m[folderId] : "")
            var r = known ? m[folderId] : total
            // A count nobody has published yet is not zero. The Browse category
            // badge has no total to fall back on (a category's size is only
            // known once it resolves), so it read "0" and odometer-rolled up to
            // N. Blank until there is a real number to show.
            if (!known && total <= 0) return ""
            return r > 0 ? "" + r : "✓"
        }
        property bool gone: false
        onStChanged: if (st !== "done") gone = false
        Timer {
            running: fb.st === "done" && !fb.gone
            interval: 700
            onTriggered: fb.gone = true
        }
        implicitHeight: 21
        implicitWidth: Math.max(21, digit.implicitWidth + 12)
        radius: height / 2
        color: root.accentCont
        border.width: 1.4; border.color: root.accent
        opacity: gone ? 0 : 1
        scale: gone ? 0.3 : 1
        visible: opacity > 0.01 && value !== ""   // an empty pill says nothing
        Behavior on opacity { NumberAnimation { duration: 420; easing.type: Easing.InQuad } }
        Behavior on scale { NumberAnimation { duration: 420; easing.type: Easing.InBack } }
        OdoDigit { id: digit; anchors.centerIn: parent; value: fb.value }
    }

    // One "My Tidal > Playlists" row: a playlist, or a playlist folder that
    // drills in like a file manager. Shared by the root list and the
    // drilled-in folder list.
    component LibPlaylistRow: Rectangle {
        id: plRow
        required property var model
        width: ListView.view.width; height: 64; radius: 10; color: root.surface; border.color: root.border1
        readonly property bool isFolder: model.kind === "folder"
        RowLayout {
            anchors.fill: parent; anchors.margins: 10; spacing: 13
            FolderTile { visible: plRow.isFolder }
            Art {
                visible: !plRow.isFolder; width: 44; height: 44; hoverFx: true
                fxKind: "playlist"; fxId: plRow.isFolder ? "" : ("" + (model.id || ""))
                url: plRow.isFolder ? "" : model.art
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                Text { textFormat: Text.PlainText; text: model.title; color: root.textHi; font.pixelSize: 15; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true }
                Text {
                    textFormat: Text.PlainText
                    text: plRow.isFolder ? model.sub : (model.tracks > 0 ? model.tracks + " tracks" : "Playlist")
                    color: root.textLo; font.pixelSize: 12
                }
            }
            DownloadButton {
                visible: !plRow.isFolder
                mediaId: plRow.isFolder ? "" : model.id
                collectionCheck: !plRow.isFolder
                label: "Download playlist"
                onTap: function(){ waves.downloadPlaylist(model.id) }
            }
            Item {
                visible: plRow.isFolder
                implicitWidth: folderBtn.implicitWidth; implicitHeight: folderBtn.implicitHeight
                // A folder rollup has no ownership record, so the button shows
                // live download state only (the discography precedent).
                DownloadButton {
                    id: folderBtn
                    mediaId: plRow.isFolder ? plRow.model.id : ""
                    label: "Download all"
                    onTap: function(){ waves.downloadFolder(folderBtn.mediaId) }
                }
                FolderBadge {
                    anchors.right: folderBtn.right; anchors.rightMargin: -8
                    anchors.top: folderBtn.top; anchors.topMargin: -9
                    folderId: folderBtn.mediaId
                    total: plRow.isFolder ? (plRow.model.plCount || 0) : 0
                    st: folderBtn.st
                }
            }
        }
        MouseArea {
            anchors.fill: parent; z: -1
            cursorShape: Qt.PointingHandCursor
            onClicked: plRow.isFolder ? root.openPlFolder(plRow.model.id, plRow.model.title)
                                      : root.openPlaylistPage(plRow.model.id, plRow.model.title)
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
        // Idle-lightning clock: its own slow loop (the storyboard below spans
        // 20s, longer than the water's ~6.9s loop, so strikes drift across
        // different wave positions instead of always hitting the same crest).
        readonly property real idleLoopSecs: 20
        property real idleT: 0
        Timer {
            // Pause while the window is off screen (hidden/minimised). Focus
            // is deliberately NOT part of the gate: an unfocused window is
            // still on screen, and the water freezing on every focus change
            // looked like a rendering glitch (livetest report).
            running: wm.visible && root.onScreen
            interval: 50; repeat: true                // 20 Hz step (see delegate note)
            onTriggered: {
                if (!root.presentFresh()) return   // no step while unpresented (see the LED clock)
                wm.t = (wm.t + 0.05 / wm.loopSecs) % 1
                wm.idleT = (wm.idleT + 0.05 / wm.idleLoopSecs) % 1
            }
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

            // Idle lightning: a fixed 20-second storm storyboard that loops
            // identically, seven strikes spread far apart. Big bolts frame the
            // box left and right, smaller ones scatter between, and the tall
            // centre strike lights the whole box with a brief sheet flash.
            // Every strike lives the same ~280ms flicker (near-full pre-flash,
            // dark beat, full flash, dim afterglow, the cadence read from the
            // README banner). Deliberately sparse: the free-running random
            // storm above stays a hover-only reward, and these hide while it
            // runs. `at` is milliseconds into the loop.
            Rectangle {
                // Sheet flash for the big centre strike: the whole clip blinks
                // faintly white during that bolt's full-flash beat.
                anchors.fill: parent
                color: "#eafff1"
                readonly property real ms: wm.idleT * wm.idleLoopSecs * 1000 - 9600
                opacity: wm.storm || ms < 140 || ms >= 280 ? 0 : ms < 210 ? 0.13 : 0.05
            }
            Repeater {
                // Every bolt hangs from the box's top edge (yf 0): with no
                // clouds drawn, the frame itself is the sky, so a bolt starting
                // mid-air would read as coming from nowhere. Short bolts are
                // distant strikes; the tall ones reach down toward the swell.
                model: [
                    { at: 1400,  shape: "\\\n \\\n /\n/",          xf: 0.06, yf: 0, px: 9 },   // big, far left
                    { at: 4200,  shape: "\\\n \\",                 xf: 0.56, yf: 0, px: 5 },   // small
                    { at: 6800,  shape: " /\n/\n\\",               xf: 0.30, yf: 0, px: 5.5 }, // small
                    { at: 9600,  shape: "\\\n \\\n  \\/\n  /\n /", xf: 0.45, yf: 0, px: 10 },  // big centre + flash
                    { at: 13000, shape: "\\\n/\n\\\n \\",          xf: 0.74, yf: 0, px: 5 },   // small
                    { at: 15800, shape: "/\n \\\n  \\\n  /",       xf: 0.88, yf: 0, px: 9 },   // big, far right
                    { at: 18200, shape: " /\n/\n\\",               xf: 0.18, yf: 0, px: 4.5 }  // small
                ]
                delegate: Text {
                    required property var modelData
                    readonly property real ms: wm.idleT * wm.idleLoopSecs * 1000 - modelData.at
                    textFormat: Text.PlainText
                    text: modelData.shape
                    x: Math.round((wm.boxW - 2 * wm.inset) * modelData.xf)
                    y: Math.round((wm.boxH - 2 * wm.inset) * modelData.yf)
                    color: "#eafff1"
                    font.family: root.mono
                    font.bold: true
                    font.pixelSize: Math.max(4, Math.round(modelData.px * wm.uscale))
                    lineHeight: 0.78
                    opacity: wm.storm || ms < 0 || ms >= 280 ? 0
                        : ms < 70 ? 0.85
                        : ms < 140 ? 0
                        : ms < 210 ? 1 : 0.45
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

    // Console-spec button: the app's ordinary hugging button (label + btnPadH*2
    // wide, btnRad corners, uiFont 13 bold UPPERCASE) as a reusable component.
    // For dialogs it is the pair shape: a SINGLE stacked CTA stays a full-width
    // GateAction, but two actions side by side hug their labels in a centred
    // row instead of stretching half-empty across the card. primary = lit cell
    // (accentCont/accentDim, hover lightens like GateAction), secondary =
    // outlined with the label warming textLo -> textHi on hover.
    component SpecBtn: Rectangle {
        id: sb
        property string label: ""
        property bool primary: false
        // GateAction's red recipe (red on redCont, translucent red border) on
        // the hugging shape, for the destructive half of a button pair.
        property bool danger: false
        // The same recipe in the palette's gold, for an action that interrupts
        // without discarding anything (pausing the queue). Not danger: nothing
        // is lost, and not primary: it is not the way forward either.
        property bool warn: false
        // Chip scale, for a button riding a section header or another dense
        // row: same fill/border/hover recipe, mono caps at the label size those
        // headers already use, so it reads as part of the header rather than a
        // dialog button that wandered in.
        property bool compact: false
        signal clicked()
        readonly property color bg: danger ? root.redCont
                                  : warn ? root.goldCont
                                  : (primary ? root.accentCont : "transparent")
        implicitWidth: sbTxt.implicitWidth + (compact ? 9 : root.btnPadH) * 2
        implicitHeight: sbTxt.implicitHeight + (compact ? 3 : root.btnPadV) * 2
        radius: compact ? 5 : root.btnRad
        color: (sbMa.containsMouse && (sb.primary || sb.danger || sb.warn)) ? Qt.lighter(sb.bg, 1.35) : sb.bg
        border.width: 1
        border.color: danger ? Qt.alpha(root.red, 0.55)
                    : warn ? Qt.alpha(root.gold, 0.55)
                    : (primary ? root.accentDim : root.border1)
        Behavior on color { ColorAnimation { duration: 110 } }
        Text {
            id: sbTxt; anchors.centerIn: parent
            textFormat: Text.PlainText; text: sb.label
            color: sb.danger ? root.red
                 : sb.warn ? root.gold
                 : (sb.primary ? root.accent : (sbMa.containsMouse ? root.textHi : root.textLo))
            font.family: sb.compact ? root.mono : root.uiFont
            font.pixelSize: sb.compact ? 10 : 13; font.bold: true
            font.letterSpacing: sb.compact ? 1.4 : root.btnTrack
        }
        MouseArea {
            id: sbMa; anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor; onClicked: sb.clicked()
        }
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
        signal begun()
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
            begun()
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
                        running: wtile.width > 0 && !waves.loggedIn && root.onScreen
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
                        running: phraseM.advanceWidth > 0 && !waves.loggedIn && root.onScreen
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
        // Choreography rule: the tube must MOVE on the click frame. The static
        // burst and flash are overlays that ride on top of the strike/collapse,
        // never gates in front of it; a sequential chain here once meant 130 ms
        // of dead flicker before anything lit, which read as input lag.
        transitions: [
            Transition { to: "on"
                ParallelAnimation {
                    SequentialAnimation {   // static burst rides the strike
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.7; duration: 24 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.2; duration: 24 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.6; duration: 21 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.0; duration: 63 } }
                    SequentialAnimation {
                        PropertyAction { target: navAfter; property: "opacity"; value: 0 }
                        PropertyAction { target: navLit; property: "opacity"; value: 1 }
                        NumberAnimation { target: navSc; property: "xScale"; from: 0.02; to: 1; duration: 83; easing.type: Easing.OutCubic }
                        ParallelAnimation {
                            NumberAnimation { target: navSc; property: "yScale"; from: 0.02; to: 1; duration: 111; easing.type: Easing.OutBack }
                            SequentialAnimation {
                                NumberAnimation { target: navFlash; property: "opacity"; to: 0.65; duration: 38 }
                                NumberAnimation { target: navFlash; property: "opacity"; to: 0.0; duration: 128 } } } } } },
            Transition { from: "on"
                ParallelAnimation {
                    SequentialAnimation {   // static crackle rides the collapse
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.75; duration: 24 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.25; duration: 28 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.6; duration: 24 }
                        NumberAnimation { target: navStat; property: "opacity"; to: 0.0; duration: 77 } }
                    SequentialAnimation {
                        NumberAnimation { target: navSc; property: "yScale"; to: 0.05; duration: 90; easing.type: Easing.InCubic }
                        PropertyAction { target: navLit; property: "opacity"; value: 0 }
                        ParallelAnimation {
                            NumberAnimation { target: navSc; property: "xScale"; to: 0.02; duration: 83; easing.type: Easing.InCubic }
                            NumberAnimation { target: navFlash; property: "opacity"; to: 0.85; duration: 83 } }
                        ParallelAnimation {
                            NumberAnimation { target: navFlash; property: "opacity"; to: 0.0; duration: 63 }
                            SequentialAnimation {
                                PropertyAction { target: navAfter; property: "opacity"; value: 0.55 }
                                NumberAnimation { target: navAfter; property: "opacity"; to: 0.0; duration: 306; easing.type: Easing.InQuad } } } } } }
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
        // Openable mode: the headline itself is a link to the whole set, marked
        // with the same "›" the art-style cloud headlines use. Only the label
        // is the target, never the rule that runs to the count badge.
        property bool openable: false
        signal opened()
        // Optional trailing control (the VIDEOS download-all button), sits
        // between the rule and the count badge. The row takes z 1 so the
        // control's own MouseArea receives clicks above the whole-header
        // collapse target; nothing else in the row accepts mouse events (the
        // openable label's MouseArea is disabled outside openable mode), so
        // header and label clicks fall through to secMa exactly as before.
        property Component trailing: null
        anchors.left: parent ? parent.left : undefined
        anchors.right: parent ? parent.right : undefined
        implicitHeight: 36
        RowLayout {
            z: 1   // lets the trailing control sit above secMa
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
            Text {
                id: secLabel
                textFormat: Text.PlainText
                text: secHead.openable ? secHead.label + "  ›" : secHead.label
                color: (secHead.collapsible && secMa.containsMouse) || (secHead.openable && secOpenMa.containsMouse)
                       ? root.textHi : root.textLo
                font.pixelSize: 12; font.bold: true; font.letterSpacing: 1.9
                MouseArea {
                    id: secOpenMa
                    anchors.fill: parent
                    enabled: secHead.openable; hoverEnabled: secHead.openable
                    cursorShape: secHead.openable ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: secHead.opened()
                }
            }
            Rectangle { Layout.fillWidth: true; height: 1; color: root.divider }
            Loader {
                active: secHead.trailing !== null
                visible: active
                sourceComponent: secHead.trailing
                Layout.alignment: Qt.AlignVCenter
            }
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

    // SHOW ALL / SHOW LESS label used beneath every capped list (top tracks,
    // search sections, artist strip). Mint green at rest (accentContTx, the
    // soft container green) so it reads as clickable without shouting; hover
    // brightens to full accent. Static by user choice: the sheen sweep from
    // scratchpad/showall_design_lab2.qml was tried and pulled as distracting.
    component ShowAllLabel: Item {
        id: sa
        property bool expanded: false
        property int count: 0
        // The section's header item: SHOW LESS scrolls the view back to it
        // (root.scrollCollapsedToSection) instead of leaving the page clamped
        // to the bottom after the rows vanish.
        property Item sectionTop: null
        signal toggled()
        implicitWidth: saText.implicitWidth
        implicitHeight: saText.implicitHeight
        width: implicitWidth; height: implicitHeight
        Text {
            id: saText
            textFormat: Text.PlainText
            text: sa.expanded ? "SHOW LESS" : "SHOW ALL " + sa.count
            color: saMa.containsMouse ? root.accent : root.accentContTx
            font.pixelSize: 12; font.bold: true; font.letterSpacing: 1.4
            Behavior on color { ColorAnimation { duration: 90 } }
        }
        MouseArea {
            id: saMa
            anchors.fill: parent; anchors.margins: -4
            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
            onClicked: {
                // Capture before toggled() flips the bound expanded state.
                var collapsing = sa.expanded
                sa.toggled()
                if (collapsing && sa.sectionTop) root.scrollCollapsedToSection(sa.sectionTop)
            }
        }
    }

    // SHOW ALL / SHOW LESS toggle for a search-page list section (Albums,
    // Tracks, Videos, Playlists, Mixes). Shows only in the mixed All view and
    // only when the section has more than the 5 rows shown by default; clicking
    // flips (and persists) that section's expanded flag via toggleSearchSection.
    component SearchSectionMore: ShowAllLabel {
        property string section: ""
        opacity: root.searchReveal
        visible: root.filterType === "all" && count > 5
        onToggled: root.toggleSearchSection(section)
    }

    // Video thumbnail: 16:9-ish art with a scanline play strip.
    // The one video-play affordance: a green data-strip along the bottom edge
    // of the art with an ink triangle and PLAY label, like a terminal status
    // bar. `lit` brightens and thickens it while the row/thumb is hovered.
    // Fills its parent (the thumb): the strip anchors itself to the bottom.
    // Play affordance on video artwork: the least ink that still reads. A
    // shadow rises out of the bottom edge so the mark stays legible over any
    // frame, and the mark is a small triangle in the corner. This replaces a
    // full-width green strip with "PLAY" set into it, which ate a quarter of
    // an 88x50 thumb; the picture is the point, the more so now that resting
    // on one plays it. `radius` follows the art the badge sits on (6 on the
    // rounded browse thumbs, 0 on the square track-row ones).
    component PlayBadge: Item {
        id: pb
        property bool lit: false
        property real radius: 6
        anchors.fill: parent
        Rectangle {
            anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
            height: Math.round(parent.height * 0.42)
            radius: pb.radius
            // Eased, and with no band squaring off the top edge: the scrim is
            // fully transparent up there, so its rounded top corners never
            // showed anyway, while the band's own step from transparent to
            // #33 drew a hard line straight across a grid-sized thumbnail.
            gradient: Gradient {
                GradientStop { position: 0.0; color: "#0006090c" }
                GradientStop { position: 0.45; color: "#4406090c" }
                GradientStop { position: 1.0; color: pb.lit ? "#dd06090c" : "#cc06090c" }
            }
        }
        Canvas {
            anchors.left: parent.left; anchors.bottom: parent.bottom
            anchors.leftMargin: 6; anchors.bottomMargin: 6
            width: pb.lit ? 9 : 8; height: pb.lit ? 10 : 9
            readonly property color ink: pb.lit ? root.accentSoft : root.accent
            // Repaint on every input to the paint, none of which Canvas
            // watches by itself.
            onInkChanged: requestPaint()
            onWidthChanged: requestPaint()
            onHeightChanged: requestPaint()
            onPaint: {
                var c = getContext("2d")
                c.reset()
                c.fillStyle = "" + ink
                c.beginPath(); c.moveTo(0, 0); c.lineTo(0, height); c.lineTo(width, height / 2)
                c.closePath(); c.fill()
            }
        }
    }
    component VideoThumb: Item {
        id: vt
        property string url: ""
        property bool lit: false
        // Hover peek wiring: with a videoId set, the thumb swells right away
        // for instant feedback and a short dwell grows the floating peek
        // card out of its rect (root.peekOpen). Thumbs without an id (none
        // today) stay inert.
        property string videoId: ""
        property string vTitle: ""
        property string vArtist: ""
        readonly property bool peekable: videoId !== ""
        width: 88; height: 50
        scale: peekable && vtHover.hovered ? 1.08 : 1
        Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        z: vtHover.hovered ? 4 : 0
        Art { anchors.fill: parent; radius: 6; url: vt.url }
        PlayBadge { lit: vt.lit || vtHover.hovered }
        HoverHandler {
            id: vtHover
            enabled: vt.peekable
            onHoveredChanged: {
                if (hovered) { root.peekThumbHover = true; vtDwell.restart() }
                else { vtDwell.stop(); root.peekThumbHover = false; root.peekHoverCheck() }
            }
        }
        Timer {
            id: vtDwell
            interval: 450
            onTriggered: if (vtHover.hovered) root.peekOpen(vt, vt.videoId, vt.vTitle, vt.vArtist, vt.url)
        }
    }

    // Search-results video thumbnail: the same peek behaviour as VideoThumb at
    // 16:9 and several hundred pixels wide, with the duration in the corner.
    // At this size a glance costs a real stream, so the dwell is longer and
    // guarded: any movement inside the thumb restarts it (only a resting
    // pointer counts) and a closed peek holds a brief cooldown, so a pointer
    // crossing the grid never chain-fires previews.
    component BigVideoThumb: Item {
        id: bvt
        property string url: ""
        property string urlBig: ""
        property string videoId: ""
        property string vTitle: ""
        property string vArtist: ""
        property string vDuration: ""
        property string spec: ""
        readonly property bool peekable: videoId !== ""
        scale: peekable && bvtHover.hovered ? 1.03 : 1
        Behavior on scale { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
        z: bvtHover.hovered ? 4 : 0
        Art { anchors.fill: parent; radius: 8; url: bvt.urlBig !== "" ? bvt.urlBig : bvt.url }
        PlayBadge { radius: 8; lit: bvtHover.hovered }
        // "You already downloaded this" rides the top-LEFT corner, across from
        // the resolution badge: same plate, same size, green (the on-disk
        // owned colour). Videos never appear in the library scan (it only
        // walks audio), so the one truthful signal here is Waves' own
        // ownership record for this exact video id.
        property bool owned: false
        function _refreshOwned() {
            var o = bvt.videoId !== "" ? waves.ownershipOf(bvt.videoId) : ({})
            owned = o.owned === true
        }
        onVideoIdChanged: _refreshOwned()
        Component.onCompleted: _refreshOwned()
        Connections {
            target: waves
            // Empty id = broadcast (the quality setting changed).
            function onOwnershipChanged(tid) { if (tid === bvt.videoId || tid === "") bvt._refreshOwned() }
        }
        Rectangle {
            visible: bvt.owned
            anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 6
            radius: 4; color: "#cc06090c"
            implicitHeight: 18; implicitWidth: bvtOwn.implicitWidth + 10
            Text {
                id: bvtOwn
                anchors.centerIn: parent
                // The same wording rule as the cell's own DownloadButton done
                // face (whose noun is "video"): one cell must never say
                // DOWNLOADED on the art and VIDEO IN LIBRARY on the button
                // for the same ownership record. A video is never in the
                // scan (audio only), so its libPresent is always false and
                // both faces reduce to libraryOn && dlInLibrary.
                textFormat: Text.PlainText; text: root.libraryOn && root.dlInLibrary ? "IN LIBRARY" : "DOWNLOADED"
                color: root.green; font.family: root.mono; font.pixelSize: 10
            }
        }
        // Resolution rides the thumbnail's top-right corner (gold, the video
        // tier's colour), same plate as the duration below it. On a 16:9
        // thumbnail "video" goes without saying, so the badge is the spec
        // alone and the title below keeps its full width.
        Rectangle {
            visible: bvt.spec !== ""
            anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 6
            radius: 4; color: "#cc06090c"
            implicitHeight: 18; implicitWidth: bvtSpec.implicitWidth + 10
            Text {
                id: bvtSpec
                anchors.centerIn: parent
                textFormat: Text.PlainText; text: bvt.spec
                color: root.gold; font.family: root.mono; font.pixelSize: 10
            }
        }
        Rectangle {
            visible: bvt.vDuration !== ""
            anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 6
            radius: 4; color: "#cc06090c"
            implicitHeight: 18; implicitWidth: bvtDur.implicitWidth + 10
            Text {
                id: bvtDur
                anchors.centerIn: parent
                textFormat: Text.PlainText; text: bvt.vDuration
                color: root.textHi; font.family: root.mono; font.pixelSize: 10
            }
        }
        // Rest detection needs the pointer's own travel, not raw point
        // updates: art hover effects move the thumb under a still cursor,
        // which re-delivers hover events every frame. Restarting on those
        // would hold the dwell off forever, so only real movement past a
        // few pixels counts as "not resting yet".
        property real _restX: -1
        property real _restY: -1
        HoverHandler {
            id: bvtHover
            enabled: bvt.peekable
            onHoveredChanged: {
                if (hovered) {
                    // Landing on a neighbour drops the open card at once:
                    // peekThumbHover is shared, so without this a sideways
                    // slide across the grid would pin the card open.
                    if (root.peekNow && root.peekNow.id !== bvt.videoId) root.peekClose()
                    root.peekThumbHover = true
                    bvt._restX = point.scenePosition.x; bvt._restY = point.scenePosition.y
                    bvtDwell.restart()
                } else {
                    bvtDwell.stop(); root.peekThumbHover = false; root.peekHoverCheck()
                }
            }
            onPointChanged: {
                if (!hovered || !bvtDwell.running) return
                var dx = point.scenePosition.x - bvt._restX
                var dy = point.scenePosition.y - bvt._restY
                if (dx * dx + dy * dy < 16) return   // within 4px: still resting
                bvt._restX = point.scenePosition.x; bvt._restY = point.scenePosition.y
                bvtDwell.restart()
            }
        }
        Timer {
            id: bvtDwell
            interval: 700
            onTriggered: {
                if (!bvtHover.hovered) return
                if (root.peekCooldown) { bvtDwell.restart(); return }
                root.peekOpen(bvt, bvt.videoId, bvt.vTitle, bvt.vArtist, bvt.urlBig !== "" ? bvt.urlBig : bvt.url)
            }
        }
        MouseArea {
            anchors.fill: parent; cursorShape: Qt.PointingHandCursor; z: -1
            onClicked: root.openVideo(bvt.videoId, bvt.vTitle, bvt.vArtist)
        }
    }

    // One art-first video result cell: the 16:9 thumbnail with the title,
    // quality tag riding the title's end, artist and release date baseline-
    // aligned below, and the download button centred on the text block. The
    // search VIDEOS grid and the artist page's VIDEOS section are the same
    // cell at whatever width their grid hands it.
    component VideoCell: Column {
        id: vcell
        property string vid: ""
        property string vcTitle: ""
        property string vcArtist: ""
        property string artUrl: ""
        property string artBigUrl: ""
        property string vcDuration: ""
        property bool vcExplicit: false
        property string vcSpec: ""
        property string vcDate: ""
        spacing: 8
        BigVideoThumb {
            width: vcell.width
            height: Math.round(vcell.width * 9 / 16)
            url: vcell.artUrl; urlBig: vcell.artBigUrl !== "" ? vcell.artBigUrl : vcell.artUrl
            videoId: vcell.vid; vTitle: vcell.vcTitle; vArtist: vcell.vcArtist
            vDuration: vcell.vcDuration
            // The resolution lives on the thumbnail, not beside the title:
            // a tag there ate most of the title on typical song names.
            spec: vcell.vcSpec !== "" ? vcell.vcSpec : root.qualSpec("VIDEO")
        }
        Item {
            width: vcell.width
            height: vMetaCol.height
            Column {
                id: vMetaCol
                anchors.left: parent.left; anchors.top: parent.top
                width: parent.width - vDl.width - 12
                spacing: 4
                Item {
                    width: parent.width
                    height: 22
                    readonly property real avail: width
                        - (vExp.visible ? vExp.width + 6 : 0)
                    Text {
                        id: vTitleTx
                        anchors.verticalCenter: parent.verticalCenter
                        width: Math.max(0, Math.min(implicitWidth, parent.avail))
                        textFormat: Text.PlainText; text: vcell.vcTitle
                        color: root.textHi; font.pixelSize: 15; font.bold: true
                        elide: Text.ElideRight; maximumLineCount: 1
                    }
                    ExplicitMark {
                        id: vExp
                        visible: vcell.vcExplicit
                        anchors.left: vTitleTx.right; anchors.leftMargin: 6
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                // Baseline-aligned, not centred: the mono date and the UI-font
                // artist have different descents, so matching line boxes still
                // reads crooked.
                Item {
                    width: parent.width
                    // Never collapse: a video with no artist credits still
                    // shows its date.
                    height: Math.max(vArtists.height, 18)
                    // The dot and date reserve their (short, fixed) width
                    // first and the artist list clips to what remains, so a
                    // long credit line cannot push the date out of the meta
                    // column and under the download button.
                    // Clamped to the column width so an extreme narrowing can
                    // never push the dot + date past the meta column and under
                    // the download button.
                    readonly property real dateW: vDateTx.visible
                        ? Math.min(width, vDot.implicitWidth + vDateTx.implicitWidth + 16) : 0
                    ArtistLinks {
                        id: vArtists
                        anchors.left: parent.left; anchors.top: parent.top
                        width: Math.max(0, Math.min(implicitWidth, parent.width - parent.dateW))
                        artists: root.artistsById[vcell.vid] || []
                    }
                    Text {
                        id: vDot
                        visible: vcell.vcDate !== ""
                        anchors.left: vArtists.right; anchors.leftMargin: 8
                        anchors.baseline: vArtists.baseline
                        text: "·"; color: root.textDim
                        font.family: root.mono; font.pixelSize: 11
                    }
                    Text {
                        id: vDateTx
                        visible: vcell.vcDate !== ""
                        anchors.left: vDot.right; anchors.leftMargin: 8
                        anchors.baseline: vArtists.baseline
                        textFormat: Text.PlainText; text: vcell.vcDate
                        color: root.textLo; font.family: root.mono; font.pixelSize: 11
                    }
                }
            }
            DownloadButton {
                id: vDl
                mediaId: vcell.vid; ownedCheck: true; label: "Download"; noun: "video"
                anchors.right: parent.right
                anchors.verticalCenter: vMetaCol.verticalCenter
                onTap: function(){ waves.downloadVideo(vcell.vid) }
            }
        }
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
        // The column count follows a SETTLED width, not the live one. Cols
        // riding width directly meant a drawer-edge drag (which resizes per
        // mouse move) changed `total` on every pixel, and the Repeater tore
        // down and rebuilt every dot each time: thousands of object churns
        // per drag, a GUI event backlog, and a pointer that stayed wedged
        // for a beat after letting go. The bar tolerating a stale column
        // count for a beat is invisible; the churn was not.
        // 300ms, and never while the drawer edge is held: at 120ms the one
        // remaining rebuild landed mid-way through the grip's 220ms release
        // animation and visibly froze it (a plain click, changing nothing,
        // was smooth). Letting the release animation finish first moves the
        // same rebuild onto a static screen, where it cannot be seen.
        // Declared with a binding for a correct first paint, then the
        // imperative assignment below BREAKS that binding on completion so
        // a drag can no longer ride it; from then on only the timer writes.
        property real _settledWidth: width
        Component.onCompleted: _settledWidth = width
        onWidthChanged: dmSettle.restart()
        Timer {
            id: dmSettle; interval: 300
            onTriggered: if (root.queueEdgeHeld) dmSettle.restart(); else dm._settledWidth = dm.width
        }
        readonly property int cols: {
            var c = Math.max(1, Math.floor((_settledWidth + gap) / (dot + gap)))
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

    // A word that "decrypts" in: every glyph starts scrambled and locks left
    // to right. Carried over from the queue design lab for the ledger's
    // status cell; any target change while live replays the decode, so a
    // state flip (QUEUED -> DOWNLOADING -> COMPLETED) announces itself.
    component DecryptText: Text {
        id: dt
        property string target: ""
        property int _step: 0
        property bool _live: false
        readonly property string glyphs: "#%&*+=<>/\\~"
        textFormat: Text.PlainText
        font.family: root.mono
        Component.onCompleted: { text = target; _live = true }
        onTargetChanged: if (_live) replay(); else text = target
        function replay() { _step = 0; dtTimer.restart(); _frame() }
        function _frame() {
            var out = ""
            for (var i = 0; i < target.length; ++i)
                out += i < _step ? target.charAt(i) : glyphs.charAt(Math.floor(Math.random() * glyphs.length))
            text = out
        }
        Timer {
            id: dtTimer
            interval: 28; repeat: true
            onTriggered: {
                dt._step++
                if (dt._step > dt.target.length) { dt.text = dt.target; stop() }
                else dt._frame()
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
        property int durationSec: 0      // raw seconds, the presence matcher's duration witness
        property string quality: ""
        property int popularity: 0
        // Expand state is held globally (keyed by album id) so it survives
        // ListView delegate recycling in the virtualised My Tidal lists.
        readonly property bool expanded: root.expandedAlbums[albumId] === true
        property var sel: ({})
        // sel holds raw track ids, so a recycled delegate rebinding to a new
        // album (LibList sets reuseItems) would download the PREVIOUS album's
        // selection; expanded/trackList live in global maps for that reason,
        // the per-delegate selection resets instead.
        onAlbumIdChanged: sel = ({})
        readonly property var trackList: root.trackCache[albumId] || []
        // Tier split once the tracks are known (expand fetches them), flips
        // the quality badge to MIXED when the album spans tiers.
        readonly property var qualMix: root.qualMixList(trackList)
        readonly property int selCount: Object.keys(sel).length
        readonly property bool allSelected: trackList.length > 0 && selCount === trackList.length
        // The artists this block should link: the side-map array when the
        // payload carried one, else a single entry from artistName/artistId
        // (same fallback as cardLeadArtists, so search albums always show a
        // clickable artist like the track rows do).
        readonly property var leadArtists: {
            var a = root.artistsById[albumId] || []
            if (a.length > 0) return a
            return artistName !== "" ? [{ id: artistId, name: artistName }] : []
        }
        spacing: 6

        function toggle() {
            var e = Object.assign({}, root.expandedAlbums)
            if (e[albumId]) { delete e[albumId] }
            else { e[albumId] = true; if (!root.trackCache[albumId]) waves.loadAlbumTracks(albumId) }
            root.expandedAlbums = e
            // After the panel exists (next event loop turn, so its height is
            // laid out), bring the row up to the expand anchor line.
            if (e[albumId]) Qt.callLater(function() { if (ab.expanded) root.scrollExpandedIntoView(ab) })
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
                Art { width: 46; height: 46; hoverFx: true; fxKind: "album"; fxId: albumId; url: art }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 2
                    // Title with the presence pill floating right beside the
                    // rendered text (static, quality told by its colour class).
                    // The pill lives INSIDE the full-width Text so the title
                    // keeps its layout size and elides only for real overflow.
                    Text {
                        id: abRowTitle
                        textFormat: Text.PlainText; text: title
                        color: abRowTitleMa.containsMouse ? "#ffffff" : root.textHi
                        font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true
                        rightPadding: abRowPill.visible ? abRowPill.width + 8 : 0
                        // Title -> the album's dedicated page (row click still expands)
                        MouseArea {
                            id: abRowTitleMa
                            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                            width: Math.min(parent.width, parent.implicitWidth)
                            enabled: !root.onAlbumPage(albumId)
                            hoverEnabled: enabled
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: root.openAlbumPage(albumId, "", title)
                        }
                        AlbumPresencePill {
                            id: abRowPill
                            anchors.verticalCenter: parent.verticalCenter
                            x: Math.min(abRowTitle.contentWidth + 8, abRowTitle.width - width)
                            album: ({ artist: ab.artistName, title: ab.title,
                                      year: ab.year, tracks: ab.trackCount,
                                      duration_sec: ab.durationSec })
                        }
                    }
                    ArtistLinks {
                        Layout.fillWidth: true
                        artists: ab.leadArtists
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
                DownloadButton {
                    Layout.alignment: Qt.AlignVCenter; mediaId: albumId; collectionCheck: true; label: "Download album"
                    libAlbum: ({ artist: ab.artistName, title: ab.title, year: ab.year, tracks: ab.trackCount, duration_sec: ab.durationSec })
                    onTap: function(){ waves.downloadAlbum(albumId) }
                }
            }
            MouseArea { id: rowMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; z: -1; onClicked: toggle() }
        }

        // --- Expanded rich panel ---
        Rectangle {
            width: parent.width
            visible: expanded
            height: visible ? expandedCol.height + 24 : 0
            Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            color: root.surface0
            border.color: root.border1
            radius: 10
            Column {
                id: expandedCol
                x: 16; y: 12; width: parent.width - 32; spacing: 12
                Row {
                    width: parent.width; spacing: 16
                    Art { width: 116; height: 116; hoverFx: true; fxKind: "album"; fxId: albumId; url: art }
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
                                onClicked: root.openAlbumPage(albumId, "", title)
                            }
                        }
                        ArtistLinks {
                            width: parent.width
                            px: 14
                            artists: ab.leadArtists
                            suffix: (releaseDate !== "" ? releaseDate : year) + (trackCount > 0 ? " · " + trackCount + " tracks" : "")
                        }
                        Row {
                            spacing: 10; topPadding: 6
                            // With the merge preference on the backend runs the
                            // best-of-both scan behind this same button; no
                            // separate action.
                            DownloadButton {
                                mediaId: albumId; label: "Download album"
                                collectionIds: ab.trackList.length > 0 ? ab.trackList.map(function(t){ return t.id }) : []
                                libAlbum: ({ artist: ab.artistName, title: ab.title, year: ab.year, tracks: ab.trackCount, duration_sec: ab.durationSec })
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

    // Playlist row + inline expand: the playlist counterpart of AlbumBlock,
    // same interaction grammar (row click expands the track list in place,
    // the title opens the dedicated page). Unlike an album a playlist
    // mutates, so every expand refetches its rows instead of caching them
    // for the session; video entries keep their kind so preview and
    // download route as videos.
    component PlaylistBlock: Column {
        id: pb
        property string plId: ""
        property string title: ""
        property string creator: ""
        property string art: ""
        property int trackCount: 0
        readonly property bool expanded: root.expandedPlaylists[plId] === true
        // sel maps ROW INDEX -> kind ("track"/"video") so Download selected
        // routes each pick to the right slot. Keyed by index, not by track id:
        // a playlist may legitimately list the same track twice, and an id key
        // would tie those rows to one checkbox (and leave allSelected forever
        // false). Same recycling rationale as AlbumBlock: expand state global,
        // selection per delegate.
        property var sel: ({})
        onPlIdChanged: sel = ({})
        readonly property var trackList: root.playlistTrackCache[plId] || []
        // Row indices only mean something against the list they were picked
        // from, and a re-expand refetches (playlists mutate). A replaced list
        // must not point the old indices at other tracks, but wholesale
        // dropping the selection eats ticks made during the refetch window
        // (the stale rows stay on screen while the fetch runs), so remap by
        // track id instead: a tick survives if its track is still in the
        // list, and vanishes with the track if the playlist lost it.
        // Plain [] on purpose: a binding here would re-evaluate to the NEW
        // list before the handler below could read the old one.
        property var _selList: []
        onTrackListChanged: {
            var old = _selList, byId = {}, s = {}
            for (var i = 0; i < trackList.length; ++i) byId[trackList[i].id] = i
            for (var k in sel) {
                var t = old[k]
                if (t && byId[t.id] !== undefined) s[byId[t.id]] = sel[k]
            }
            sel = s
            _selList = trackList
        }
        readonly property int selCount: Object.keys(sel).length
        readonly property bool allSelected: trackList.length > 0 && selCount === trackList.length
        readonly property string subLabel: (trackCount > 0 ? trackCount + " tracks" : "Playlist") + (creator ? "  ·  " + creator : "")
        spacing: 6

        function toggle() {
            var e = Object.assign({}, root.expandedPlaylists)
            if (e[plId]) { delete e[plId] }
            else { e[plId] = true; waves.loadPlaylistTracks(plId) }
            root.expandedPlaylists = e
            if (e[plId]) Qt.callLater(function() { if (pb.expanded) root.scrollExpandedIntoView(pb) })
        }
        function setSel(row, kind, v) { var s = Object.assign({}, sel); if (v) s[row] = kind; else delete s[row]; sel = s }
        function toggleAll() {
            if (allSelected) { sel = ({}) }
            else { var s = {}; for (var i = 0; i < trackList.length; ++i) s[i] = trackList[i].kind; sel = s }
        }
        function downloadSelected() {
            for (var k in sel) {
                var row = trackList[k]
                if (!row) continue
                if (sel[k] === "video") waves.downloadVideo(row.id); else waves.downloadTrack(row.id)
            }
        }

        // --- Row ---
        Rectangle {
            width: parent.width
            height: 64
            radius: 10
            color: pbRowMa.containsMouse ? root.surface2 : root.surface
            border.color: expanded ? root.outline : root.border1
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 14; spacing: 12
                Text {
                    text: "›"; rotation: expanded ? 90 : 0
                    color: expanded ? root.accent : root.textDim; font.pixelSize: 16
                    Layout.preferredWidth: 12; horizontalAlignment: Text.AlignHCenter
                    Behavior on rotation { NumberAnimation { duration: 160; easing.type: Easing.OutCubic } }
                }
                Art { width: 46; height: 46; hoverFx: true; fxKind: "playlist"; fxId: plId; url: art }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 2
                    Text {
                        textFormat: Text.PlainText; text: title
                        color: pbRowTitleMa.containsMouse ? "#ffffff" : root.textHi
                        font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; Layout.fillWidth: true
                        // Title -> the playlist's dedicated page (row click still expands)
                        MouseArea {
                            id: pbRowTitleMa
                            anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                            width: Math.min(parent.width, parent.implicitWidth)
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.openPlaylistPage(plId, title)
                        }
                    }
                    Text { textFormat: Text.PlainText; text: pb.subLabel; color: root.textLo; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true }
                }
                DownloadButton { Layout.alignment: Qt.AlignVCenter; mediaId: plId; collectionCheck: true; label: "Download playlist"; onTap: function(){ waves.downloadPlaylist(plId) } }
            }
            MouseArea { id: pbRowMa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; z: -1; onClicked: toggle() }
        }

        // --- Expanded rich panel ---
        Rectangle {
            width: parent.width
            visible: expanded
            height: visible ? pbExpandedCol.height + 24 : 0
            Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            color: root.surface0
            border.color: root.border1
            radius: 10
            Column {
                id: pbExpandedCol
                x: 16; y: 12; width: parent.width - 32; spacing: 12
                Row {
                    width: parent.width; spacing: 16
                    Art { width: 116; height: 116; hoverFx: true; fxKind: "playlist"; fxId: plId; url: art }
                    Column {
                        width: parent.width - 132; spacing: 6
                        Text { text: "PLAYLIST"; color: root.textDim; font.pixelSize: 11 }
                        Text {
                            textFormat: Text.PlainText; text: title
                            color: pbPanelTitleMa.containsMouse ? "#ffffff" : root.textHi
                            font.pixelSize: 21; font.bold: true; width: parent.width; elide: Text.ElideRight
                            MouseArea {
                                id: pbPanelTitleMa
                                anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                                width: Math.min(parent.width, parent.implicitWidth)
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: root.openPlaylistPage(plId, title)
                            }
                        }
                        Text { textFormat: Text.PlainText; text: pb.subLabel; color: root.textLo; font.pixelSize: 14; width: parent.width; elide: Text.ElideRight }
                        Row {
                            spacing: 10; topPadding: 6
                            DownloadButton {
                                mediaId: plId; label: "Download playlist"; collectionCheck: true
                                onTap: function(){ waves.downloadPlaylist(plId) }
                            }
                            Text {
                                text: "Copy link"; color: root.textLo; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter
                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.copyShareUrl("playlist", plId) }
                            }
                        }
                    }
                }
                Text { visible: !root.playlistTrackCache[plId]; text: "Loading tracks…"; color: root.textLo; font.pixelSize: 13 }
                Column {
                    width: parent.width; spacing: 0
                    // Select-all header
                    RowLayout {
                        visible: pb.trackList.length > 0
                        width: parent.width; height: 40; spacing: 12
                        Check { Layout.alignment: Qt.AlignVCenter; checked: pb.allSelected; onToggled: pb.toggleAll() }
                        Text { text: "Select all"; color: root.textLo; font.pixelSize: 13 }
                        Text { textFormat: Text.PlainText; text: "· " + pb.selCount + " of " + pb.trackList.length + " selected"; color: root.textDim; font.pixelSize: 12 }
                        Item { Layout.fillWidth: true }
                        Rectangle {
                            Layout.alignment: Qt.AlignVCenter
                            opacity: pb.selCount > 0 ? 1 : 0.4
                            // Sized like DownloadButton so it matches DOWNLOAD PLAYLIST above.
                            radius: root.btnRad; color: root.accentCont; border.color: root.accentDim; border.width: 1
                            implicitHeight: pbDsRow.implicitHeight + root.btnPadV * 2; implicitWidth: pbDsRow.implicitWidth + root.btnPadH * 2
                            Row { id: pbDsRow; anchors.centerIn: parent; spacing: 7
                                Ico { name: "arrow-down"; color: root.accent; size: 14; bold: 10; anchors.verticalCenter: parent.verticalCenter }
                                Text { text: "DOWNLOAD SELECTED"; color: root.accent; font.pixelSize: 11; font.family: root.uiFont; font.bold: true; font.letterSpacing: root.btnTrack; anchors.verticalCenter: parent.verticalCenter }
                            }
                            MouseArea { anchors.fill: parent; enabled: pb.selCount > 0; cursorShape: pb.selCount > 0 ? Qt.PointingHandCursor : Qt.ArrowCursor; onClicked: pb.downloadSelected() }
                        }
                    }
                    // Tracks (playlist order; a video entry previews and
                    // downloads as a video)
                    Repeater {
                        model: pb.trackList
                        delegate: Rectangle {
                            required property var modelData
                            required property int index
                            width: parent.width; height: 40; color: "transparent"
                            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.divider }
                            RowLayout {
                                anchors.fill: parent; anchors.leftMargin: 4; anchors.rightMargin: 4; spacing: 12
                                Check { Layout.alignment: Qt.AlignVCenter; checked: pb.sel[index] !== undefined; onToggled: pb.setSel(index, modelData.kind, pb.sel[index] === undefined) }
                                Text { textFormat: Text.PlainText; text: modelData.num; color: root.textDim; font.family: root.mono; font.pixelSize: 15; font.bold: true; Layout.preferredWidth: 24; Layout.leftMargin: -4; horizontalAlignment: Text.AlignLeft }
                                TrackPreview { kind: modelData.kind; pid: modelData.id; Layout.alignment: Qt.AlignVCenter }
                                Text { textFormat: Text.PlainText; text: modelData.title; color: root.textHi; font.pixelSize: 13; elide: Text.ElideRight; Layout.fillWidth: true }
                                Text { textFormat: Text.PlainText; text: modelData.artist; color: root.textLo; font.pixelSize: 12; elide: Text.ElideRight; Layout.maximumWidth: 220 }
                                PopMeter { value: modelData.popularity; showNum: false }
                                Text { textFormat: Text.PlainText; text: modelData.duration; color: root.textLo; font.family: root.mono; font.pixelSize: 12; Layout.preferredWidth: 42 }
                                DownIcon { mediaId: modelData.id; onTap: function(){ if (modelData.kind === "video") waves.downloadVideo(modelData.id); else waves.downloadTrack(modelData.id) } }
                            }
                        }
                    }
                }
            }
        }
    }

    // A small snake circling the loading label, used as the waiting surface
    // of the peek card. It laps a ring laid around the plate, eating the
    // bites dropped on the path and growing a little with each one; only a
    // wait long enough to fill most of the ring (minutes, i.e. genuinely bad
    // internet) makes the run start over. The body is just (head, length)
    // along the ring, so the card resizing mid-grow re-lays the path without
    // ever scattering segments. The clock only runs while the field is both
    // visible and armed, so a closed peek costs nothing.
    component SnakeField: Item {
        id: sf
        property bool running: false
        property real cell: 9
        property real gap: 12
        property real plateW: 0
        property real plateH: 0
        // Pixel top-left corners of the ring cells, walked clockwise.
        property var ring: []
        property int head: 0
        property int len: 5
        property int food: -1

        function _buildRing() {
            var w = plateW + gap * 2 + cell
            var h = plateH + gap * 2 + cell
            if (plateW <= 0 || plateH <= 0 || width < w + cell || height < h + cell) { ring = []; cv.requestPaint(); return }
            var x0 = (width - w) / 2
            var y0 = (height - h) / 2
            var nx = Math.max(4, Math.round(w / cell))
            var ny = Math.max(3, Math.round(h / cell))
            var sx = w / nx
            var sy = h / ny
            var r = []
            var i
            for (i = 0; i < nx; i++) r.push({ x: x0 + i * sx, y: y0 })
            for (i = 1; i < ny; i++) r.push({ x: x0 + (nx - 1) * sx, y: y0 + i * sy })
            for (i = nx - 2; i >= 0; i--) r.push({ x: x0 + i * sx, y: y0 + (ny - 1) * sy })
            for (i = ny - 2; i >= 1; i--) r.push({ x: x0, y: y0 + i * sy })
            var wasEmpty = ring.length === 0
            ring = r
            // The card opens smaller than the ring needs and grows into it,
            // so the run truly starts here, on the frame the ring first fits
            // (a reset against the empty ring parked food at -1 for good).
            if (wasEmpty && running) { reset(); return }
            if (head >= r.length) head = head % r.length
            if (len > r.length - 4) len = Math.max(3, r.length - 4)
            if (food < 0 || food >= r.length) _placeFood()
            cv.requestPaint()
        }
        function _occupied(idx) {
            var n = ring.length
            for (var i = 0; i < len; i++) if ((head - i + n * 2) % n === idx) return true
            return false
        }
        function _placeFood() {
            var n = ring.length
            if (n === 0) { food = -1; return }
            // Drop the bite somewhere ahead on the lap, never on the snake.
            for (var t = 0; t < 30; t++) {
                var idx = (head + 3 + Math.floor(Math.random() * Math.max(1, n - len - 4))) % n
                if (!_occupied(idx)) { food = idx; return }
            }
            food = -1
        }
        function reset() {
            len = 5
            var n = ring.length
            if (n === 0) { food = -1; cv.requestPaint(); return }
            // Random start, so two peeks in a row are not the same run.
            head = Math.floor(Math.random() * n)
            // The first bite lands just ahead of the head (3 to 6 cells, well
            // under a second away), so even a quick load shows the snake eat
            // at least once. Clamped clear of the tail on a tiny ring.
            var off = 3 + Math.floor(Math.random() * 4)
            if (off > n - len - 1) off = Math.max(1, n - len - 1)
            food = (head + off) % n
            cv.requestPaint()
        }
        function step() {
            var n = ring.length
            if (n === 0) return
            head = (head + 1) % n
            if (head === food) {
                len += 1
                // Filling most of the lap takes minutes of eating: shed the
                // weight and keep going rather than catching the tail.
                if (len >= n - 4) len = 5
                _placeFood()
            }
            cv.requestPaint()
        }
        onRunningChanged: if (running) { _buildRing(); reset() }
        onWidthChanged: _buildRing()
        onHeightChanged: _buildRing()
        onPlateWChanged: _buildRing()
        onPlateHChanged: _buildRing()
        Timer {
            running: sf.running && sf.visible
            interval: 110; repeat: true
            onTriggered: sf.step()
        }
        Canvas {
            id: cv
            anchors.fill: parent
            onPaint: {
                var c = getContext("2d")
                c.reset()
                var n = sf.ring.length
                if (n === 0) return
                var s = sf.cell
                var pad = Math.max(1, Math.round(s * 0.18))
                if (sf.food >= 0 && sf.food < n) {
                    var f = sf.ring[sf.food]
                    c.fillStyle = "" + root.goldDim
                    c.fillRect(f.x + pad, f.y + pad, s - pad * 2, s - pad * 2)
                }
                for (var i = 0; i < sf.len; i++) {
                    var seg = sf.ring[(sf.head - i + n * 2) % n]
                    // Head brightest, fading down the tail: reads as motion
                    // even in a still frame.
                    var a = Math.max(0.14, 0.55 - i * 0.55 / Math.max(1, sf.len))
                    c.fillStyle = "" + Qt.alpha(root.accent, a)
                    c.fillRect(seg.x + pad, seg.y + pad, s - pad * 2, s - pad * 2)
                }
            }
        }
    }

    // Explicit-content mark, in the app's mono data voice.
    component ExplicitMark: Rectangle {
        radius: 3; color: "transparent"
        border.color: root.textDim; border.width: 1
        implicitWidth: 14; implicitHeight: 14
        Text {
            anchors.centerIn: parent; text: "E"; color: root.textDim
            font.family: root.mono; font.pixelSize: 9; font.bold: true
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
        // A Row has no baseline of its own; publish the name text's so callers
        // can baseline-align a date or tag sitting beside it (centring two
        // fonts with different descents reads crooked).
        FontMetrics { id: alFm; font.pixelSize: al.px }
        baselineOffset: alFm.ascent
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
                onClicked: root.openAlbumPage(al.albumId, "", al.suffix)
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
        property int durationSec: 0      // raw seconds, the presence matcher's duration witness
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
            // Blank space anywhere on the row navigates like the title does
            // (the track's album page, or the video player): most of the row
            // is clickable, while the artist/album links, meters and buttons
            // stacked above still take their own clicks first.
            MouseArea {
                id: trowMa
                anchors.fill: parent; hoverEnabled: true
                readonly property bool linkable: trow.kind === "video"
                                                 || (trow.albumId !== "" && !root.onAlbumPage(trow.albumId))
                acceptedButtons: linkable ? Qt.LeftButton : Qt.NoButton
                cursorShape: linkable ? Qt.PointingHandCursor : Qt.ArrowCursor
                onClicked: trow.kind === "video" ? root.openVideo(trow.tId, trow.title, trow.artistName)
                                                 : root.openAlbumPage(trow.albumId, trow.tId, trow.album)
            }
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
                    // textHi to match the title's resting colour (set to the same
                    // value, not bound to the title, which whitens on hover).
                    color: root.textHi
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
                    PlayBadge { visible: trow.kind === "video"; lit: thumbMa.containsMouse; radius: 0 }
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
                        // A step above the 12px subtitle in both size and weight
                        // so the title leads the row (the green artist link used
                        // to outshine it).
                        font.pixelSize: 14; font.weight: Font.Medium
                        elide: Text.ElideRight; Layout.fillWidth: true
                        // The presence pill floats beside the rendered text,
                        // inside the full-width Text, exactly like the album
                        // row's pill: the title keeps its layout size and
                        // elides only for real overflow.
                        rightPadding: trPill.visible ? trPill.width + 8 : 0
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
                                                             : root.openAlbumPage(albumId, tId, trow.album)
                        }
                        // Declared after the title's MouseArea so the pill sits
                        // on top of it and takes its own click (reveal folder).
                        TrackPresencePill {
                            id: trPill
                            anchors.verticalCenter: parent.verticalCenter
                            x: Math.min(trTitle.contentWidth + 8, trTitle.width - width)
                            track: trow.kind === "video" ? null
                                   : ({ artist: trow.artistName, title: trow.title,
                                        album: trow.album, year: trow.year,
                                        duration_sec: trow.durationSec })
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
                    // The same identity the pill beside the title resolves, so
                    // the row says one thing in two places: the pill answers at
                    // a glance, the button answers what a click will do. Videos
                    // pass null, the scan holds no video.
                    libTrack: trow.kind === "video" ? null
                              : ({ artist: trow.artistName, title: trow.title,
                                   album: trow.album, year: trow.year,
                                   duration_sec: trow.durationSec })
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

    // The horizontal counterpart of BackToTop's LIP edge fades: a short dense
    // darkening at the left and right of a shelf's viewport so cards fade in
    // and out of frame instead of being cut off hard at the shelf edge. Same
    // gating rules as the vertical pair: fully transparent at the very start
    // (contentX 0), ramping in over the first fadeW pixels of travel; the
    // right fade lifts as the end of the shelf arrives; a shelf too short to
    // scroll shows no fades at all. Declared inside the shelf it dresses: a
    // Flickable reparents declared children onto its contentItem (they would
    // scroll away with the cards), so onCompleted walks up to the shelf and
    // hoists the overlay back onto it, viewport-fixed above the delegates.
    // The gradient runs top-to-bottom, so each band is built sideways and
    // rotated about its center: -90 points the dense edge left, 90 right.
    component ShelfEdgeFades: Item {
        id: sef
        property Flickable shelf: null
        readonly property real fadeW: 34
        anchors.fill: shelf
        z: 10
        Component.onCompleted: {
            var p = parent
            while (p && p.contentX === undefined) p = p.parent
            // Parent first, shelf second: anchors.fill binds to shelf, and it
            // must never see a shelf that is not yet the parent (warning).
            if (p) { sef.parent = p; sef.shelf = p }
        }
        Rectangle {
            width: sef.height; height: sef.fadeW
            x: (sef.fadeW - sef.height) / 2; y: (sef.height - sef.fadeW) / 2
            rotation: -90
            visible: sef.shelf !== null && opacity > 0
            opacity: sef.shelf ? Math.min(1, Math.max(0, sef.shelf.contentX) / sef.fadeW) : 0
            gradient: Gradient {
                GradientStop { position: 0; color: "#0d0f12" }
                GradientStop { position: 0.25; color: Qt.alpha("#0d0f12", 0.82) }
                GradientStop { position: 0.6; color: Qt.alpha("#0d0f12", 0.28) }
                GradientStop { position: 1; color: Qt.alpha("#0d0f12", 0) }
            }
        }
        Rectangle {
            width: sef.height; height: sef.fadeW
            x: sef.width - (sef.fadeW + sef.height) / 2; y: (sef.height - sef.fadeW) / 2
            rotation: 90
            visible: sef.shelf !== null && opacity > 0
            opacity: sef.shelf ? Math.min(1, Math.max(0,
                         sef.shelf.contentWidth - sef.shelf.width - sef.shelf.contentX) / sef.fadeW) : 0
            gradient: Gradient {
                GradientStop { position: 0; color: "#0d0f12" }
                GradientStop { position: 0.25; color: Qt.alpha("#0d0f12", 0.82) }
                GradientStop { position: 0.6; color: Qt.alpha("#0d0f12", 0.28) }
                GradientStop { position: 1; color: Qt.alpha("#0d0f12", 0) }
            }
        }
    }

    // Shared artist search-result card. Used by both the collapsed horizontal
    // strip and the expanded fill grid, so its data comes in as plain properties
    // (not model roles): a top-level component sits outside the Repeater delegate
    // scope, so the delegate wires model.art/name/popularity/id into these.
    component ArtistSearchCard: Rectangle {
        property string aArt: ""
        property string aName: ""
        property real aPop: 0
        property string aId: ""
        radius: 12; color: root.surface; border.color: root.border1
        // Size to the content (plus the 8px top and bottom margins) so the card
        // height matches art + name + meter + preview + download exactly.
        implicitHeight: cardCol.implicitHeight + 16
        Column {
            id: cardCol
            anchors.fill: parent; anchors.margins: 8; spacing: 8
            Art {
                width: parent.width; height: parent.width
                hoverFx: true; fxKind: "artist"; fxId: aId
                url: aArt
                // What you already hold by this artist, said as a strip across
                // the cover's lower edge rather than a chip dropped in a
                // corner: the one row of a search page that stayed silent about
                // the library now answers, and no face is covered to say it. A
                // child of the Art, so it takes the cover's tilt, hover lift
                // and rounded clip for free.
                ArtistBadges {
                    bar: true
                    width: parent.width
                    anchors.bottom: parent.bottom
                    artistName: aName
                }
            }
            Text { textFormat: Text.PlainText; text: aName; color: root.textHi; font.pixelSize: 14; font.bold: true; elide: Text.ElideRight; width: parent.width; horizontalAlignment: Text.AlignHCenter }
            PopMeter { anchors.horizontalCenter: parent.horizontalCenter; value: aPop }
            // Preview + Download live in the card body (never overlaid on the
            // photo); each has its own MouseArea that consumes the click so the
            // card-wide open-artist MouseArea (z:-1, below) only fires elsewhere.
            // Preview plays the artist's top track and doubles as a scrubber.
            PreviewBar { width: parent.width; pid: aId }
            DownloadButton {
                width: parent.width
                mediaId: aId; label: "Download artist"
                onTap: function(){ waves.downloadArtist(aId) }
            }
        }
        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; z: -1; onClicked: waves.loadArtist(aId) }
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
                width: parent.width; height: parent.width; hoverFx: true
                // previewable mirrors the strip's own gate, so a video card
                // (no audio preview path) can never claim the raise.
                fxKind: bc.previewable ? bc.kind : ""
                fxId: bc.previewable ? ("" + (bc.card.id || "")) : ""
                url: bc.card.art || ""
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
                id: bcDlBox
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                width: Math.max(bcDlIdle.implicitWidth, bcDlRun.implicitWidth, bcDlQueued.implicitWidth); height: 16
                // "preparing" reads as queued here too: see DownloadButton.waiting.
                readonly property bool waiting: bc.dlSt === "queued" || bc.dlSt === "preparing"
                Text {
                    id: bcDlIdle
                    textFormat: Text.PlainText
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    visible: bc.dlSt !== "running" && !bcDlBox.waiting
                    text: bc.dlSt === "done" ? "DONE" : bc.dlSt === "failed" ? "RETRY" : "DOWNLOAD"
                    color: bc.dlSt === "done" ? root.green : bc.dlSt === "failed" ? root.red : root.accent
                    font.family: root.uiFont; font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack
                }
                // Queued: the stack glyph + the media noun, same language as
                // the full DownloadButton's queued state.
                Row {
                    id: bcDlQueued
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    visible: bcDlBox.waiting; spacing: 5
                    QueueStack { barW: 9; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        textFormat: Text.PlainText
                        text: "QUEUED" + (bc.kind ? " " + bc.kind.toUpperCase() : "")
                        color: root.accentDim
                        font.family: root.uiFont; font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack
                        anchors.verticalCenter: parent.verticalCenter
                    }
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
                    onClicked: { if (bc.dlSt === "running" || bc.dlSt === "done" || bcDlBox.waiting) return; root.browseCardDownload(bc.card) }
                }
            }
        }
    }

    // How every hover-revealed control enters and leaves (picked in the
    // animation lab, scratchpad/hover_anim_lab.qml, 2026-07-26): it rises from
    // below the art's bottom edge with a soft overshoot and settles, fading in
    // as it climbs, then drops back out. The fade runs on its own short curve
    // so the overshoot never dims or flickers the control. `visible` follows
    // opacity, which gates the children's MouseAreas: a control on its way out
    // can never swallow a click. Place it where the control used to be
    // anchored and put the control inside.
    // Settings > Advanced > "Hover controls slide in". Off keeps the plain
    // fade the controls used before the motion landed, for anyone who finds
    // the movement distracting.
    property bool hoverMotion: waves.wavesPref("hover_control_motion") !== false
    Connections {
        target: waves
        function onHoverMotionChanged() {
            root.hoverMotion = waves.wavesPref("hover_control_motion") !== false
        }
    }
    component RiseIn: Item {
        id: riser
        property bool on: false
        // Far enough to clear the bottom edge of any art tile (clip: true), so
        // the control is genuinely offscreen rather than peeking. With motion
        // off it rests at 0 and only the opacity animates.
        property real travel: 44
        readonly property bool motion: root.hoverMotion
        property real yShift: motion ? travel : 0
        opacity: 0
        visible: opacity > 0
        transform: Translate { y: riser.yShift }
        states: State {
            name: "on"; when: riser.on
            PropertyChanges { target: riser; yShift: 0; opacity: 1 }
        }
        transitions: [
            Transition {
                to: "on"
                NumberAnimation { property: "yShift"; duration: riser.motion ? 240 : 0; easing.type: Easing.OutBack; easing.overshoot: 1.2 }
                NumberAnimation { property: "opacity"; duration: riser.motion ? 180 : 160; easing.type: Easing.OutQuad }
            },
            Transition {
                from: "on"
                NumberAnimation { property: "yShift"; duration: riser.motion ? 150 : 0; easing.type: Easing.InQuad }
                NumberAnimation { property: "opacity"; duration: riser.motion ? 150 : 160; easing.type: Easing.InQuad }
            }
        ]
    }

    // The hover controls' idle-to-live swap: one pill whose contents ride a
    // short belt. The outgoing set leaves through the top exactly as the
    // incoming arrives from the bottom, while the pill itself stretches
    // between their two natural sizes and its fill and outline cross to the
    // arriving control's. Replaces what used to be a bare `visible:` flip in
    // both directions.
    // Shape picked in scratchpad/pv_swap_lab.qml round 2 ("ROLL snap"): a
    // 16px belt over 230ms with NO overlap, so the swap reads as a flip
    // rather than a dissolve.
    // The pill belongs to this wrapper, not to the two controls, which is why
    // both load `bare`: mid-belt a control is half out of frame, and only a
    // visible outline makes that read as sliding behind an edge instead of as
    // something being clipped off.
    // Gated on hoverMotion like RiseIn: with motion off the swap is instant.
    component RollSwap: Item {
        id: rsw
        property bool live: false
        // Assigned inline by the call site; reparented into the pill so each
        // side can keep its own bindings and its own natural size.
        property Item idleItem: null
        property Item liveItem: null
        property color idleColor: "#d90d0f12"
        property color idleBorder: root.accentDim
        property color liveColor: idleColor
        property color liveBorder: idleBorder
        readonly property bool motion: root.hoverMotion
        readonly property real travel: 16
        // Centred horizontally by anchor, placed vertically by the belt below:
        // centerIn would own y and fight it.
        onIdleItemChanged: if (idleItem) {
            idleItem.parent = rswPill
            idleItem.anchors.horizontalCenter = rswPill.horizontalCenter
        }
        onLiveItemChanged: if (liveItem) {
            liveItem.parent = rswPill
            liveItem.anchors.horizontalCenter = rswPill.horizontalCenter
        }

        property real t: live ? 1 : 0
        Behavior on t { NumberAnimation { duration: rsw.motion ? 230 : 0; easing.type: Easing.OutCubic } }
        // 0.44 is both the end of the exit and the start of the entry: no
        // overlap, so the two are never on screen together.
        readonly property real outFrac: 1 - Math.min(1, t / 0.44)
        readonly property real inFrac: Math.max(0, (t - 0.44) / 0.56)
        function _mix(a, b, k) {
            return Qt.rgba(a.r + (b.r - a.r) * k, a.g + (b.g - a.g) * k,
                           a.b + (b.b - a.b) * k, a.a + (b.a - a.a) * k)
        }

        readonly property real wIdle: idleItem ? idleItem.width : 0
        readonly property real hIdle: idleItem ? idleItem.height : 0
        // The live side's size is LATCHED, not read raw. The state that ends
        // the live side usually also empties it in the same binding pass (a
        // stopped preview hides the scrubber, and its Column relayouts to 0
        // before t has moved), so reading the raw height collapsed the pill to
        // nothing in one frame instead of rolling the outgoing control away.
        // Holding the last real size keeps the roll on screen; a size change
        // WHILE live (a download joining a preview) eases instead of snapping.
        readonly property real _wLiveRaw: liveItem ? liveItem.width : 0
        readonly property real _hLiveRaw: liveItem ? liveItem.height : 0
        property real wLive: 0
        property real hLive: 0
        on_WLiveRawChanged: if (live || _wLiveRaw > 0) wLive = _wLiveRaw
        on_HLiveRawChanged: if (live || _hLiveRaw > 0) hLive = _hLiveRaw
        Behavior on wLive { enabled: rsw.live; NumberAnimation { duration: rsw.motion ? 180 : 0; easing.type: Easing.OutCubic } }
        Behavior on hLive { enabled: rsw.live; NumberAnimation { duration: rsw.motion ? 180 : 0; easing.type: Easing.OutCubic } }
        implicitWidth: wIdle + (wLive - wIdle) * t
        implicitHeight: hIdle + (hLive - hIdle) * t
        width: implicitWidth
        height: implicitHeight

        Rectangle {
            id: rswPill
            anchors.fill: parent
            radius: root.btnRad
            color: rsw._mix(rsw.idleColor, rsw.liveColor, rsw.t)
            border.width: root.btnBorderW
            border.color: rsw._mix(rsw.idleBorder, rsw.liveBorder, rsw.t)
            clip: true
            // The two contents are reparented in above; their belt offset and
            // presence are driven from here so neither call site repeats it.
            Binding { target: rsw.idleItem; property: "opacity"; value: rsw.outFrac }
            Binding { target: rsw.idleItem; property: "visible"; value: rsw.outFrac > 0.004 }
            Binding { target: rsw.idleItem; property: "y"
                      value: (rswPill.height - rsw.hIdle) / 2 - rsw.travel * (1 - rsw.outFrac) }
            Binding { target: rsw.liveItem; property: "opacity"; value: rsw.inFrac }
            Binding { target: rsw.liveItem; property: "visible"; value: rsw.inFrac > 0.004 }
            Binding { target: rsw.liveItem; property: "y"
                      value: (rswPill.height - rsw.hLive) / 2 + rsw.travel * (1 - rsw.inFrac) }
        }
    }

    // The app-wide breadcrumb trail (folder-strip pills over navHistory +
    // the current page as the last, lit pill; see crumbBase for why the trail
    // is only this section's slice of it). Crumb click = navTo(crumbBase + ord).
    // Long trails fold the middle behind a "…" pill that expands in place
    // and morphs into "›‹" to fold back; navigating deeper auto-refolds.
    // Motion: slot widths glide 220ms OutCubic while new crumbs rise in
    // with the control bounce (the picked M3, gated on hoverMotion like
    // RiseIn). The trail reconciles against crumbLabels instead of being
    // rebuilt, so crumbs animate in and out rather than popping.
    component NavCrumbTrail: Item {
        id: nct
        property int keepTail: 2
        property bool unfolded: false
        property int liveCount: 0
        implicitHeight: 26
        implicitWidth: rowNct.width
        readonly property bool foldable: liveCount > keepTail + 2
        readonly property bool folding: !unfolded && foldable
        readonly property var labels: root.crumbLabels
        // 0ms debounce: navigation flips several flags in one JS run; sync
        // once against the settled state, never a half-switched one.
        onLabelsChanged: nctSyncT.restart()
        Component.onCompleted: sync()
        Timer { id: nctSyncT; interval: 0; onTriggered: nct.sync() }
        ListModel { id: nctModel }
        // Reconcile the pill model against the wanted labels: shared prefix
        // stays, dropped crumbs animate out (dying), new ones animate in.
        // "ord" is each live pill's position in the trail; dying pills keep a
        // stale ord but nothing reads it.
        function sync() {
            var want = labels
            var have = [], haveIdx = []
            for (var i = 0; i < nctModel.count; i++)
                if (!nctModel.get(i).dying) { have.push(nctModel.get(i).tag); haveIdx.push(i) }
            var k = 0
            while (k < have.length && k < want.length && have[k] === want[k]) k++
            if (want.length === have.length && k === have.length - 1) {
                // Only the current page's label changed (a late-loading
                // title): swap the text in place, no churn.
                nctModel.setProperty(haveIdx[k], "tag", want[k])
                return
            }
            var cutAny = false
            for (var c = k; c < have.length; c++) {
                nctModel.setProperty(haveIdx[c], "dying", true); cutAny = true
            }
            for (var a = k; a < want.length; a++)
                nctModel.append({ tag: want[a], dying: false, ord: a })
            if (want.length > have.length) unfolded = false   // deeper: refold
            var ord = 0
            for (i = 0; i < nctModel.count; i++)
                if (!nctModel.get(i).dying) nctModel.setProperty(i, "ord", ord++)
            liveCount = want.length
            if (cutAny) nctReap.restart()
        }
        Timer {
            // Just past the 90ms removal fade: dying pills leave as soon as
            // they are invisible, so the row closes up right behind the fade.
            id: nctReap; interval: 110
            onTriggered: {
                for (var i = nctModel.count - 1; i >= 0; i--)
                    if (nctModel.get(i).dying) nctModel.remove(i)
            }
        }
        Row {
            id: rowNct; spacing: 0; height: parent.height
            Repeater {
                model: nctModel
                delegate: Item {
                    id: nc
                    required property string tag
                    required property bool dying
                    required property int ord
                    // Lit and unclickable because it IS the page you are on.
                    // While the current page is still nameless its crumb is
                    // held back, so the tail pill is only the parent: leave it
                    // ordinary and clickable (root.crumbTailPending).
                    readonly property bool isLast: !dying && ord === nct.liveCount - 1 && !root.crumbTailPending
                    readonly property bool foldedOut: nct.folding && !dying
                        && ord > 0 && ord <= nct.liveCount - 1 - nct.keepTail
                    readonly property bool foldShown: nct.foldable && !dying && ord === 1
                    readonly property bool crumbHidden: dying || foldedOut
                    readonly property bool sepHidden: dying || (foldedOut && !foldShown)
                    height: 26
                    width: ncRow.width
                    clip: true
                    Row {
                        id: ncRow; spacing: 0; height: parent.height
                        // No entrance/width animation anywhere in the trail:
                        // pills growing in per navigation read as a wipe
                        // (reported from livetesting). Crumbs appear in place;
                        // the only motion left is a quick fade on removal.
                        // separator slot
                        Item {
                            height: parent.height; clip: true
                            width: (nc.ord > 0 && !nc.sepHidden) ? ncSep.implicitWidth + 12 : 0
                            Text {
                                id: ncSep; anchors.centerIn: parent
                                textFormat: Text.PlainText; text: "›"
                                color: root.textDim; font.pixelSize: 13
                            }
                        }
                        // fold-mark slot: "…" while folded, "›‹" once expanded
                        Item {
                            height: parent.height; clip: true
                            width: nc.foldShown ? ncFoldPill.implicitWidth + (nct.folding ? 0 : 6) : 0
                            opacity: nc.foldShown ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: 90 } }
                            Rectangle {
                                id: ncFoldPill
                                radius: 8; height: 26
                                anchors.verticalCenter: parent.verticalCenter
                                implicitWidth: ncFoldTx.implicitWidth + 20; width: implicitWidth
                                color: root.surface2; border.color: root.border1
                                Text {
                                    id: ncFoldTx; anchors.centerIn: parent
                                    textFormat: Text.PlainText
                                    text: nct.folding ? "…" : "›‹"
                                    color: ncFoldMa.containsMouse ? root.textHi : root.textLo
                                    font.pixelSize: 12
                                }
                            }
                            MouseArea {
                                id: ncFoldMa; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: nct.unfolded = !nct.unfolded
                            }
                        }
                        // crumb slot
                        Item {
                            height: parent.height; clip: true
                            width: nc.crumbHidden ? 0 : ncPill.implicitWidth
                            opacity: nc.crumbHidden ? 0 : 1
                            Behavior on opacity { NumberAnimation { duration: 90 } }
                            Rectangle {
                                id: ncPill
                                radius: 8; height: 26
                                anchors.verticalCenter: parent.verticalCenter
                                implicitWidth: ncTx.implicitWidth + 20; width: implicitWidth
                                color: nc.isLast ? root.accentCont : root.surface2
                                border.color: nc.isLast ? root.accentDim : root.border1
                                Behavior on color { ColorAnimation { duration: 200 } }
                                Behavior on border.color { ColorAnimation { duration: 200 } }
                                Text {
                                    id: ncTx; anchors.centerIn: parent
                                    textFormat: Text.PlainText; text: nc.tag
                                    color: nc.isLast ? root.accent : root.textLo
                                    Behavior on color { ColorAnimation { duration: 200 } }
                                    font.pixelSize: 12; font.bold: nc.isLast
                                }
                            }
                            MouseArea {
                                anchors.fill: parent; hoverEnabled: true
                                enabled: !nc.isLast && !nc.crumbHidden
                                cursorShape: Qt.PointingHandCursor
                                // ord is the pill's place in the TRAIL; navTo
                                // indexes the whole history, which the trail is
                                // a tail slice of (root.crumbBase). A synthetic
                                // section-root pill (root.crumbSynth) leads the
                                // trail without a history entry behind it: it
                                // climbs out of the drill instead, and shifts
                                // every later pill's history index by one.
                                onClicked: {
                                    if (root.crumbSynth && nc.ord === 0) root.crumbSynthGo()
                                    else root.navTo(root.crumbBase + nc.ord - (root.crumbSynth ? 1 : 0))
                                }
                            }
                        }
                    }
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
        // The library verdict this card carries, resolved ONCE per card. The
        // hover strip needs it to colour its download half and the pill needs
        // it to say what is held, and asking twice would be two QML->Python
        // calls per card on a shelf of them (the economy AlbumPresencePill's
        // single-object property exists for). Albums only: a playlist or a mix
        // has no album identity to ask about, and must never wear one's badge.
        property var libPresence: null
        property bool _libResolved: false
        function resolveLibPresence() {
            _libResolved = true
            var c = ac.card
            libPresence = (ac.kind === "album" && c && c.title)
                ? waves.libraryAlbumPresence("" + (c.artist || ""), "" + c.title,
                                             "" + (c.year || ""), c.tracks || 0, c.duration_sec || 0)
                : null
        }
        // The cross-session downloaded fact, the same rollup the full button
        // uses: every member track recorded by a real download and still up
        // to date. Session state (root.dlSt) only lives until quit; this is
        // what lets a card still say DOWNLOADED tomorrow. Collections only:
        // membership is recorded per collection id, and it is one cached
        // bridge call per card, the same budget the library verdict spends.
        readonly property bool ownable: ac.kind === "album" || ac.kind === "playlist" || ac.kind === "mix"
        property bool owned: false
        // Member ids from the last rollup, kept so the ownership listener can
        // ignore answers about other collections' tracks: at launch every
        // cold query announces its first answer, and a grid of cards each
        // re-rolling on every one of them is a signal storm on the GUI
        // thread (reported as the launch animation turning jumpy).
        property var _ownIds: null
        function refreshOwned() {
            if (!ac.ownable) { owned = false; return }
            var ids = waves.collectionMemberIds("" + (ac.card.id || ""))
            _ownIds = ids
            if (!ids || ids.length === 0) { owned = false; return }
            var pending = false
            for (var i = 0; i < ids.length; ++i) {
                var oi = waves.ownershipOf(ids[i])
                if (oi.pending === true) { pending = true; continue }
                if (!(oi.owned === true && oi.up_to_date === true)) { owned = false; return }
            }
            // Cold answers in flight and nothing firmly against: keep the last
            // known word rather than flashing the strip through DOWNLOAD; the
            // ownershipChanged nudge re-asks once the truth lands.
            if (!pending) owned = true
        }
        onCardChanged: { resolveLibPresence(); refreshOwned() }
        Component.onCompleted: { if (!_libResolved) resolveLibPresence(); refreshOwned() }
        Connections {
            target: waves
            enabled: ac.kind === "album"
            function onLibraryPresenceChanged() { ac.resolveLibPresence() }
        }
        Connections {
            target: waves
            enabled: ac.ownable
            // Empty id = broadcast (the quality setting changed). Only this
            // card's own members matter, and a burst of member answers (an
            // album's cold queries all landing) coalesces into one rollup.
            function onOwnershipChanged(tid) {
                if (tid === "" || (ac._ownIds && ac._ownIds.indexOf(tid) !== -1)) acOwnCoalesce.restart()
            }
            function onCollectionMembershipChanged(cid) { if (cid === "" + (ac.card.id || "")) ac.refreshOwned() }
        }
        Timer { id: acOwnCoalesce; interval: 50; onTriggered: ac.refreshOwned() }
        readonly property bool libPresent: !!(ac.libPresence && ac.libPresence.present === true)
        readonly property bool libFull: !!(ac.libPresence && ac.libPresence.full === true)
        readonly property bool libSure: !!(ac.libPresence && ac.libPresence.sure === true)
        // The three states the strip's download half can wear, exactly the
        // ones DownloadButton names: a proven complete copy, an unproven one,
        // and a partial copy (which stays a plain live download, since
        // completing an album is not a duplicate).
        readonly property string libState: !ac.libPresent ? ""
                                           : !ac.libFull ? "partial"
                                           : ac.libSure ? "proven" : "maybe"
        // A FULL claim gates its click the way the full button does: explain
        // the match, name the folder, leave Download anyway one click away. A
        // tag match must never be the end of the conversation.
        readonly property bool libClaim: ac.libState === "proven" || ac.libState === "maybe"
        Art {
            id: acArt
            width: ac.artSize; height: ac.artSize
            radius: 12
            // Heroes tilt too: the landing's first shelf renders through the
            // hero card, and it sat flat while every card around it moved
            // (reported from livetesting). The on-art caption and scrim are
            // children of the art, so they ride the same tilt.
            hoverFx: true
            // Videos have no audio-preview path, so they never take the raise.
            fxKind: ac.kind === "video" ? "" : ac.kind
            fxId: ac.kind === "video" ? "" : ("" + (ac.card.id || ""))
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
            // The library pill, top-left over the art: the one corner no
            // caption or hover control uses. Unlike the strip it does NOT wait
            // for a hover, which is the whole reason it is here: a shelf of
            // cards has to answer "what do I already have" at a glance, and
            // the strip only appears under the cursor. Fed from the card's own
            // resolved verdict rather than AlbumPresencePill, which would ask
            // the same question a second time per card.
            LibraryTag {
                visible: ac.libPresent
                settleKey: "" + (ac.card.id || "")
                x: 10; y: 10
                have: ac.libPresence ? (ac.libPresence.local_tracks || 0) : 0
                total: ac.card.tracks || 0
                declared: ac.libPresence ? (ac.libPresence.local_declared || 0) : 0
                albumId: ac.libPresence ? (ac.libPresence.local_album_id || "") : ""
                qclass: ac.libPresence ? (ac.libPresence.local_class || "") : ""
                proven: ac.libSure
            }
            // The artist answer to the pill above, for the same reason: a shelf
            // of artists has to say "what do I already have" at a glance. It
            // resolves itself off the rollup rather than the album verdict,
            // which is null for every non-album card, and it takes the cover's
            // lower edge rather than the pill's corner because a rollup is two
            // lines and a face is usually in the middle.
            ArtistBadges {
                bar: true
                width: parent.width
                anchors.bottom: parent.bottom
                // Gated by the NAME, never by an outer visible binding: the
                // badge hides itself when the rollup says nothing, and
                // overriding `visible` here would replace that with an
                // always-shown strip. A hero card carries its own caption on
                // this exact edge, so it stands down rather than stack on it.
                artistName: (ac.kind === "artist" && !ac.hero)
                            ? ("" + (ac.card.name || ac.card.title || "")) : ""
            }
            // Hover controls. Collections (album / playlist / mix) get the
            // full stacked pair on the art, Preview (a random track for
            // playlists/mixes) over Download; other kinds keep the corner icon.
            readonly property bool collection: ac.kind === "album" || ac.kind === "playlist" || ac.kind === "mix"
            readonly property string kindLabel: ac.kind === "album" ? "album" : ac.kind === "playlist" ? "playlist" : "mix"
            readonly property bool controlsOn: collection
                                               && (acWrapHover.hovered
                                                   || root.dlSt(ac.card.id || "") !== "" || root.pvSt(ac.kind, ac.card.id || "") !== "")
            HoverHandler { id: acWrapHover }
            RiseIn {
                id: acRiser
                on: acArt.controlsOn
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                anchors.margins: 10
                height: acSwap.implicitHeight
                readonly property string acPvSt: root.pvSt(ac.kind, ac.card.id || "")
                readonly property string acDlSt: root.dlSt(ac.card.id || "")
                // A finished download is not a live control: it goes back to
                // the split strip, whose download half now reads the checkmark
                // and DOWNLOADED, so Preview stays one click away instead of
                // being evicted by a full-width done pill for the session.
                // Session state OR the ownership rollup: this session's done
                // and last week's download must wear the same word. A LIVE
                // state (queued, running, failed) outranks the rollup, so a
                // re-download of an owned album still shows its progress.
                readonly property bool dlDone: acDlSt === "done" || (acDlSt === "" && ac.owned)
            // Idle strip and live controls are the same pill: it rolls its
            // contents over and stretches to the arriving side's size, in both
            // directions, for a preview and for a download alike.
            RollSwap {
                id: acSwap
                anchors.horizontalCenter: parent.horizontalCenter
                live: acRiser.acPvSt !== "" || (acRiser.acDlSt !== "" && !acRiser.dlDone)
                // The live side carries its own fill (a download button is a
                // filled control); only the outline is the pill's, so it takes
                // the arriving control's edge colour with it.
                liveBorder: acLive.dlOn ? acDl.edge
                                        : (acRiser.acPvSt === "error" ? root.red : root.accentDim)
                // The idle pill's outline comes with the verdict too, so the
                // two halves read as one control in one mood rather than a
                // coloured half bolted into an accent frame.
                idleBorder: acStrip.stEdge
                liveItem: Column {
                    id: acLive
                    spacing: 6
                    readonly property bool pvOn: acRiser.acPvSt !== ""
                    readonly property bool dlOn: acRiser.acDlSt !== "" && !acRiser.dlDone
                    // Full width while a full-width row is showing; a finished
                    // or failed download goes back to hugging its label, the
                    // way inline buttons do everywhere else.
                    width: (pvOn || acDl.st === "running") ? acRiser.width : acDl.implicitWidth
                    // Live preview: the full scrubber bar (same one as everywhere else)
                    PreviewBar {
                        visible: acLive.pvOn
                        bare: true
                        width: parent.width; height: implicitHeight
                        kind: ac.kind; pid: ac.card.id || ""
                        label: "Preview " + acArt.kindLabel
                    }
                    // Live download: the full dot-matrix progress bar / done / retry
                    DownloadButton {
                        id: acDl
                        visible: acLive.dlOn
                        bare: true
                        anchors.horizontalCenter: parent.horizontalCenter
                        width: st === "running" ? parent.width : implicitWidth
                        mediaId: ac.card.id || ""
                        // No ownership rollup here: this button is visible only
                        // while a LIVE download state exists, and live state always
                        // outranks the owned rollup, so the rollup's result could
                        // never render. Computing it per card was pure cost.
                        collectionCheck: false
                        label: "Download " + acArt.kindLabel
                        onTap: function() { root.browseCardDownload(ac.card) }
                    }
                }
                // Idle: the slim strip, ▶ PREVIEW | ⭳ DOWNLOAD in one thin pill
                idleItem: Item {
                    id: acStrip
                    width: acStripRow.implicitWidth; height: 30
                    // The verdict, in the tier colours the library pill uses,
                    // so the two things a card now says (the pill's what you
                    // have, the half's what a click will do) never disagree
                    // about what a colour means. Unlike the full download
                    // button this half stays UNFILLED: the strip is a thin
                    // outlined control riding on artwork, and filling one half
                    // of it made a solid block of a shape whose whole job is to
                    // stay out of the way of the cover. So the lettering
                    // carries it, which is why proven reads in the pill's
                    // lossless green rather than the filled button's accent:
                    // the accent is already on PREVIEW, one divider away.
                    // A download Waves made is the freshest fact the half can
                    // state: it wears the checkmark and the fact green ahead
                    // of any library verdict (a gold MAYBE over a file Waves
                    // itself wrote would be absurd), and its click opens the
                    // owned gate with REDOWNLOAD one click away.
                    readonly property bool dlDone: acRiser.dlDone
                    readonly property color stInk: dlDone ? root.green
                                                   : ac.libState === "proven" ? root.green
                                                   : ac.libState === "maybe" ? root.gold
                                                   : ac.libState === "partial" ? root.cyan : root.accent
                    readonly property color stEdge: dlDone ? root.greenDim
                                                    : ac.libState === "proven" ? root.greenDim
                                                    : ac.libState === "maybe" ? root.goldDim
                                                    : ac.libState === "partial" ? root.cyanDim : root.accentDim
                    // Ninety pixels of half, so the full button's wording
                    // ("PARTIALLY IN LIBRARY") cannot ride here. These are the
                    // shortest forms that still say which of the three is true,
                    // and a partial copy spends them on the count itself, which
                    // is the thing worth knowing.
                    readonly property string stWord: {
                        if (acStrip.dlDone) return "DOWNLOADED"
                        if (ac.libState === "proven") return "IN LIBRARY"
                        if (ac.libState === "maybe") return "MAYBE"
                        if (ac.libState === "partial") {
                            var p = ac.libPresence
                            var held = p.local_tracks || 0
                            var declared = p.local_declared || 0
                            var want = declared > held ? declared : (ac.card.tracks || 0)
                            if (want > held) return held + " OF " + want
                        }
                        return "DOWNLOAD"
                    }
                    // Hover swell: one handler split by x (the halves' own
                    // MouseAreas would steal the hover), so crossing the
                    // divider hands the light over without a gap. Where PREVIEW
                    // ends is the real divider, not the midpoint, and its
                    // region runs one border-width past it so the lit edge
                    // swallows the dim divider instead of sitting beside it.
                    HoverHandler { id: acStripHover }
                    readonly property real dividerX: acStripRow.children[0].width
                    readonly property real hoverX: acStripHover.point.position.x
                    HoverSwell {
                        z: 1    // above the Row, so the lit edge covers the divider
                        width: acStrip.dividerX + root.btnBorderW; height: acStrip.height
                        radR: 0
                        on: acStripHover.hovered && acStrip.hoverX <= acStrip.dividerX
                    }
                    HoverSwell {
                        z: 1
                        x: acStrip.dividerX; width: acStrip.width - acStrip.dividerX; height: acStrip.height
                        radL: 0
                        // The lit edge takes the verdict's own colour, so
                        // hovering a claimed half does not flash accent green
                        // over a gold or cyan control.
                        tone: acStrip.stInk
                        on: acStripHover.hovered && acStrip.hoverX > acStrip.dividerX
                    }
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
                        Rectangle { width: root.btnBorderW; height: 30; color: acStrip.stEdge; anchors.verticalCenter: parent.verticalCenter }
                        Item {
                            implicitWidth: acStripDl.implicitWidth + 20; implicitHeight: 30
                            Row {
                                id: acStripDl
                                anchors.centerIn: parent; spacing: 6
                                Ico { name: (acStrip.dlDone || ac.libClaim) ? "check" : "arrow-down"; color: acStrip.stInk; size: (acStrip.dlDone || ac.libClaim) ? 14 : 12; bold: 10; anchors.verticalCenter: parent.verticalCenter }
                                Text { textFormat: Text.PlainText; text: acStrip.stWord; color: acStrip.stInk; font.family: root.uiFont; font.pixelSize: 10; font.bold: true; font.letterSpacing: root.btnTrack; anchors.verticalCenter: parent.verticalCenter }
                            }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                // A finished download opens the owned gate:
                                // the fact, then REDOWNLOAD one click away. A
                                // full claim opens the claim gate, the same
                                // click the full button gives it. A partial
                                // copy downloads: with the bulk skip gate on,
                                // that fetches the rest.
                                onClicked: {
                                    if (acStrip.dlDone) { root.openRedownloadGate(ac.card); return }
                                    if (ac.libClaim)
                                        root.openLibraryClaim(ac.card.id || "", "" + (ac.card.title || ""),
                                                              "" + (ac.libPresence.local_album_id || ""))
                                    else root.browseCardDownload(ac.card)
                                }
                            }
                        }
                    }
                }
            }
            }
            RiseIn {
                on: !acArt.collection
                    && (acWrapHover.hovered || root.dlSt(ac.card.id || "") !== "")
                anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 10
                width: 38; height: 36
            Rectangle {
                anchors.fill: parent
                radius: root.btnRad
                color: "#d90d0f12"
                DownIcon {
                    anchors.centerIn: parent
                    mediaId: ac.card.id || ""
                    // This corner icon renders only for NON-collection kinds
                    // (collections get the stacked controls above), so a
                    // collection rollup here could never be seen; leaving it
                    // on ran two membership rollups per collection card.
                    collectionCheck: false
                    onTap: function() { root.browseCardDownload(ac.card) }
                }
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
    // Shared scroll pane for the two Browse surfaces (landing and drilled
    // page). Each pane keeps its own contentY for its whole life, which is
    // what makes Back land already positioned: nothing is restored because
    // nothing was lost. What remains here is the machinery for CONTENT
    // swaps under the user (a landing revalidate, a page revalidate,
    // endless-scroll growth): the column collapses for the rebuild turn and
    // the clamp would yank contentY toward the top, so the scroll range is
    // padded up to the held spot while armed and the very first frame lands
    // ON it, content filling in around it.
    component BrowseScroll: Flickable {
        id: bsp
        // The content column this pane scrolls (wired by the instance).
        property Item col: null
        // What this pane currently shows; a hold or restore is tagged with
        // it so it only ever applies to the content it was taken against.
        // The landing never changes key (""); the drill pane binds the
        // page key.
        property string restoreKey: ""
        // Endless scroll is a drilled-listing concern only; the landing
        // must never grow the drill pane's section from its own geometry.
        property bool grows: false
        clip: true
        readonly property real realContentH: (col ? col.height : 0) + 32
        readonly property real restorePad: pendingRestoreY >= 0
            ? Math.max(0, pendingRestoreY + height - realContentH) : 0
        contentWidth: width; contentHeight: realContentH + restorePad
        ScrollBar.vertical: ScrollBar {}
        boundsBehavior: Flickable.StopAtBounds
        property real pendingRestoreY: -1
        property string pendingRestoreKey: ""
        // mayDisarm: only a real layout pass may spend the hold. Disarm only
        // once the REAL content (pad excluded) reaches the target: the pad
        // alone always satisfies the clamped check.
        function applyRestore(mayDisarm) {
            if (pendingRestoreY < 0 || restoreKey !== pendingRestoreKey) return
            contentY = Math.min(pendingRestoreY, Math.max(0, contentHeight - height))
            if (mayDisarm && Math.max(0, realContentH - height) >= pendingRestoreY)
                pendingRestoreY = -1   // reached the target
        }
        // The reference the track-row shells size their "load me now" window
        // against. They read THIS, never the live contentY: a Loader
        // consults `asynchronous` once, when it is created, so binding
        // hundreds of row shells to contentY spent that many JS
        // re-evaluations per scrolled frame and could not change a single
        // row. Exact whenever a hold is armed (every path that swaps
        // content in place arms one first); free scrolling falls back to a
        // 10-row band, inside the 12 rows of slack the window carries.
        readonly property int rowBandH: 10 * 62
        readonly property real rowAnchorY: pendingRestoreY >= 0
            ? pendingRestoreY : Math.floor(contentY / rowBandH) * rowBandH
        // Arm the hold-in-place across an in-place content swap. Runs for a
        // hidden pane too: a landing revalidating behind an open page still
        // collapses its column, and the clamp does not care about
        // visibility. Only a pane the user is actively dragging declines.
        function holdScroll() {
            if (contentY <= 0 || (visible && moving)) return
            pendingRestoreKey = restoreKey
            pendingRestoreY = contentY
        }
        // A fresh page (drill-in, stack pop) starts at the top; a Back that
        // armed a hold keeps its spot instead, applied in the SAME pass
        // (before paint) so there is never a visible jump from the top.
        onRestoreKeyChanged: {
            if (pendingRestoreY >= 0 && restoreKey === pendingRestoreKey) applyRestore(false)
            else contentY = 0
        }
        onMovingChanged: root.browseMoving = moving
        onPendingRestoreYChanged: if (pendingRestoreY >= 0) bspGiveUp.restart()
        // Watch the REAL height, not contentHeight: while a hold is armed
        // the pad absorbs growth (contentHeight stays constant), so a
        // contentHeight watcher would starve both the disarm check and the
        // endless-scroll top-up until the give-up timer fired.
        onRealContentHChanged: { applyRestore(true); maybeGrow() }
        // Give-up: the content never grew back to the held spot (it truly
        // changed). Drop the pad and settle at the real bottom.
        Timer {
            id: bspGiveUp; interval: 800
            onTriggered: { bsp.pendingRestoreY = -1; bsp.returnToBounds() }
        }
        // Endless scroll on a drilled listing page (a full-listing grid or
        // track list, one section that fills the page). Nearing the bottom
        // fetches the next window. Multi-section pages (genre/mood) are
        // horizontal shelves plus preview rows that each carry their own
        // "show more", so the whole-page vertical scroll doesn't grow them.
        // Guarded by browseGrow's in-flight/exhausted checks, so re-fires
        // while sitting near the bottom are cheap no-ops.
        function maybeGrow() {
            if (!grows || root.browsePageKey === "" || !root.browsePage) return
            var secs = root.browsePage.sections || []
            if (secs.length !== 1 || !root.browseCanGrow(secs[0])) return
            if (contentY + height >= contentHeight - 800) root.browseGrow(secs[0])
        }
        onContentYChanged: maybeGrow()
    }

    // One Browse content section (a card shelf, a track list, a link
    // cloud, a drilled grid). Hoisted out of the pane so the landing and
    // the drilled page can render the SAME component from two always-alive
    // panes; `landing` replaces every "am I the landing?" read of the
    // global page key, which cannot distinguish the panes now that both
    // exist at once.
    component BrowseSection: Column {
        id: bsec
        property var sec: null
        property int secIndex: 0
        property bool landing: false
        // The scroll pane and content column this section lives in, for
        // wheel redirection, row windowing and highlight centering.
        property Item pane: null
        property Item col: null
        // Where this section sits in the pane's scroll space. The row window
        // is measured from HERE, never from the top of the page: on the
        // landing a track shelf can sit thousands of pixels down a column of
        // twenty sections, and a window aimed with the page's own scroll
        // offset then lands far past the end of every short shelf, so no row
        // ever loads and the whole shelf shows as empty row cards. A landing
        // shelf hangs off an async Loader (parent) while a drilled one is the
        // delegate itself, hence the two shapes.
        readonly property real secTop: (landing ? (parent ? parent.y : 0) : y)
                                       + (col ? col.y : 0)
        // The first landing shelf becomes the art-first hero row: big
        // covers with the caption on the artwork.
        readonly property bool artStyle: root.browseStyle === "art"
        readonly property bool hero: artStyle && landing && secIndex === 0
                                     && sec.rowKind === "cards"
        // A drilled "show more" page (one lone card section) lays its cards
        // out as a wrapping grid that scrolls with the page and re-flows on
        // resize, not a horizontal shelf.
        readonly property bool grid: sec.rowKind === "cards" && !landing
                                     && !(root.browsePage && root.browsePage.header)
                                     && ((root.browsePage && root.browsePage.sections) || []).length === 1
        // Suppress an empty/generic section headline on a drilled page, the
        // back bar already names it (avoids a stray "More" above e.g. the
        // Record Labels grid).
        readonly property bool showHeadline: ("" + (sec.title || "")).trim() !== ""
        // Every card shelf's headline opens the full listing: TIDAL's own
        // "show more" page when the row has one, else a local page built
        // from the row's items. Inert only on an already-drilled grid page
        // (self-link).
        readonly property bool headlinable: !grid
            && (!!sec.more || (sec.rowKind === "cards" && (sec.items || []).length > 0))
        function openListing() {
            if (sec.more) root.openBrowseLink(sec.more, sec.title || "More")
            else root.openBrowseSection(sec)
        }
        spacing: 8
        SectionHeader {
            visible: !bsec.artStyle && bsec.showHeadline
            label: (bsec.sec.title || "More").toUpperCase() + (bsec.headlinable ? "  \u203a" : "")
            count: (bsec.sec.items || []).length
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
            text: (bsec.sec.title || "More") + (bsec.headlinable ? "  \u203a" : "")
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
        // Width snaps to whole columns and the block centers in the pane,
        // so a half-column of dead space is split evenly left/right instead
        // of dumped on the right until the window grows enough to add
        // another column.
        Flow {
            visible: bsec.grid
            readonly property real cardW: bsec.artStyle ? 200 : 156
            spacing: bsec.artStyle ? 14 : 12
            readonly property int cols: root.gridCols(cardW, spacing, (bsec.sec.items || []).length, parent.width)
            width: cols * (cardW + spacing) - spacing
            x: Math.max(0, (parent.width - width) / 2)
            Repeater {
                model: bsec.grid ? bsec.sec.items : []
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
        // Grid footer: tells you whether more is coming or you've reached
        // the end, so a short editorial listing (e.g. 20 Top Albums)
        // doesn't read as "stuck" the way an endless genre listing keeps
        // flowing.
        Item {
            visible: bsec.grid
            width: parent.width; height: 46
            readonly property bool loadingMore: !!(bsec.sec.data && root.browseGrowing[bsec.sec.data])
            Row {
                anchors.centerIn: parent; spacing: 10
                visible: parent.loadingMore || !root.browseCanGrow(bsec.sec)
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
                            : (bsec.sec.items || []).length + " total"
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
            visible: !bsec.artStyle && !bsec.grid && bsec.sec.rowKind === "cards"
            width: parent.width; height: 238
            orientation: ListView.Horizontal
            spacing: 12; clip: true
            boundsBehavior: Flickable.StopAtBounds
            // The model gate mirrors `visible`'s LOCAL terms, never
            // `visible` itself: that is EFFECTIVE visibility, so gating on
            // it tore down every card when the pane's ancestor hid (leaving
            // the tab) and rebuilt them all synchronously inside the
            // returning click's turn, ~180ms of jank on every switch back
            // to Browse. Keeping the cards alive across a hidden pane is
            // the whole point of the two-pane design.
            model: !bsec.artStyle && !bsec.grid && bsec.sec.rowKind === "cards"
                   ? bsec.sec.items : []
            delegate: BrowseCard {
                required property var modelData
                card: modelData
            }
            ShelfWheelRedirect { pane: bsec.pane }
            ShelfEdgeFades {}
            // endless scroll: reaching the shelf's right edge pulls the
            // row's next window from TIDAL
            onAtXEndChanged: if (atXEnd && count > 0) root.browseGrow(bsec.sec)
        }
        // horizontal card shelf, art-first (unframed covers)
        ListView {
            visible: bsec.artStyle && !bsec.grid && bsec.sec.rowKind === "cards"
            width: parent.width; height: bsec.hero ? 284 : 250
            orientation: ListView.Horizontal
            spacing: 14; clip: true
            boundsBehavior: Flickable.StopAtBounds
            // Local terms, not `visible` (see the console shelf above).
            model: bsec.artStyle && !bsec.grid && bsec.sec.rowKind === "cards"
                   ? bsec.sec.items : []
            delegate: ArtCard {
                required property var modelData
                card: modelData
                hero: bsec.hero
            }
            ShelfWheelRedirect { pane: bsec.pane }
            ShelfEdgeFades {}
            onAtXEndChanged: if (atXEnd && count > 0) root.browseGrow(bsec.sec)
        }
        // vertical track list (e.g. "New Tracks" on a genre page)
        Column {
            id: bsecRows
            visible: bsec.sec.rowKind === "tracks"
            width: parent.width
            Repeater {
                model: bsec.sec.rowKind === "tracks" ? bsec.sec.items : []
                // Fixed-height shells, content windowed: a long playlist
                // used to create every TrackRow in one synchronous pass,
                // 1.7s of frozen GUI per click (measured, budget 100ms).
                // The shells give the column its full geometry in the
                // assignment turn (so the scroll range and highlight
                // positions are exact from the first frame), then each
                // row's real content loads asynchronously. Rows near the
                // viewport (or the spot a hold is armed for) and the
                // highlight target load synchronously, so what the user is
                // actually looking at is never a shell.
                delegate: Loader {
                    id: btrLd
                    required property var modelData
                    required property int index
                    width: bsec.width; height: 62   // TrackRow's fixed height
                    readonly property bool hiRow: root.browseHighlightId !== "" && modelData.id === root.browseHighlightId
                    // A screenful of rows, used to size the live window.
                    readonly property real rps: Math.max(1, bsec.pane.height / 62)
                    // Row 0 of THIS shelf, in the pane's scroll space, so the
                    // window is expressed in this shelf's own row indices.
                    readonly property real rowTop: bsec.secTop + bsecRows.y
                    readonly property real anchorRow: (bsec.pane.rowAnchorY - rowTop) / 62
                    // A shelf no longer than the window it would be measured
                    // against is never worth windowing, and a short shelf is
                    // exactly the case an aiming error can blank completely.
                    readonly property bool shortSec: (bsec.sec.items || []).length <= 3 * rps
                    // WINDOWED, not just incubated. Incubating every row
                    // spread the build cost out but still left them all
                    // alive: a 579-track playlist put ~76,000 items under
                    // one Flickable (132 per built row, measured), and the
                    // page stayed sluggish however long you sat on it,
                    // because the cost is the live item tree, not the
                    // build. Only the viewport plus two screens either side
                    // is real; the rest stay 62px shells. The shell owns
                    // the height, so unloading changes nothing about the
                    // scroll range, the hold target or the endless-scroll
                    // top-up, and two screens of slack is more than a flick
                    // covers before the next band brings the following rows
                    // in. The highlight row is exempt at any distance: it
                    // arms browseHighlightPending and centres the page, so
                    // it must exist even when the window is elsewhere.
                    active: hiRow || shortSec
                            || (index > anchorRow - 2 * rps
                                && index < anchorRow + 3 * rps)
                    asynchronous: {
                        if (hiRow) return false
                        // A few rows of slack either side absorb the fact
                        // that anchorRow moves in bands, not per pixel.
                        // rowAnchorY, not contentY: this binding must not
                        // re-evaluate on every scrolled frame (see there).
                        return index < anchorRow - 8 || index > anchorRow + rps + 12
                    }
                    // Empty row card while the content incubates, so a
                    // streaming list reads as rows filling in, not holes.
                    Rectangle {
                        anchors.fill: parent; anchors.topMargin: 3; anchors.bottomMargin: 3
                        radius: 10; color: root.surface; border.color: root.border1
                        visible: btrLd.status !== Loader.Ready
                    }
                    sourceComponent: TrackRow {
                        id: btr
                        width: btrLd.width
                        tId: btrLd.modelData.id; kind: btrLd.modelData.kind || "track"
                        title: btrLd.modelData.title; artistName: btrLd.modelData.artist || ""; artistId: btrLd.modelData.artist_id || ""
                        album: btrLd.modelData.album || ""; art: btrLd.modelData.art || ""; year: "" + (btrLd.modelData.year || ""); date: btrLd.modelData.date || ""
                        duration: btrLd.modelData.duration || ""; durationSec: btrLd.modelData.duration_sec || 0; quality: btrLd.modelData.quality || ""; popularity: btrLd.modelData.popularity || 0
                        // Numbers only on item pages, where they're ordered
                        // (album track #s / playlist positions), editorial
                        // track shelves would all read "1".
                        num: (!bsec.landing && root.browsePage && root.browsePage.header) ? (btrLd.modelData.num || 0) : 0
                        albumId: btrLd.modelData.album_id || ""
                        hi: btrLd.hiRow
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
                                var y = btr.mapToItem(bsec.col, 0, 0).y - (bsec.pane.height - btr.height) / 2
                                bsec.pane.contentY = Math.max(0, Math.min(y, bsec.pane.contentHeight - bsec.pane.height))
                                root.browseHighlightPending = false
                            }
                        }
                    }
                }
            }
        }
        // Sub-page links (genre / mood / decade / label tiles).
        // In art mode these are fixed-size tiles, so they use the SAME
        // centered, column-snapped, responsive geometry as the card grid,
        // one presentation across every drilled box view. Console mode
        // keeps the variable-width chip flow.
        Flow {
            visible: bsec.sec.rowKind === "links"
            readonly property bool tiled: bsec.artStyle
            readonly property real cardW: 200
            spacing: tiled ? 14 : 8
            readonly property int cols: root.gridCols(cardW, spacing, (bsec.sec.items || []).length, parent.width)
            width: tiled ? cols * (cardW + spacing) - spacing : parent.width
            x: tiled ? Math.max(0, (parent.width - width) / 2) : 0
            Repeater {
                model: bsec.sec.rowKind === "links" ? bsec.sec.items : []
                delegate: Loader {
                    id: blinkLd
                    required property var modelData
                    required property int index
                    sourceComponent: bsec.artStyle ? blinkTile : blinkChip
                    Component {
                        id: blinkTile
                        BrowseTile { title: blinkLd.modelData.title; path: blinkLd.modelData.path; idx: blinkLd.index; plOnly: !!blinkLd.modelData.pl }
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
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: blinkLd.modelData.pl ? root.openPlaylistsFolder(blinkLd.modelData.path, blinkLd.modelData.title)
                                                                        : root.openBrowseLink(blinkLd.modelData.path, blinkLd.modelData.title) }
                        }
                    }
                }
            }
        }
    }


    component BrowseTile: Rectangle {
        id: bt
        property string title: ""
        property string path: ""
        property int idx: 0
        // Tile lives inside the Playlists folder view: drill into the
        // playlists-only grid instead of the full editorial page.
        property bool plOnly: false
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
        // Hover tilt at tile level: the whole mosaic (cells, scrim, title)
        // moves as one slab, matching the Art component's effect.
        property real fxRx: 0
        property real fxRy: 0
        property real fxLift: 1.0
        readonly property bool fxOn: root.artFxVariant !== "none"
        // Same rest-before-tilt arming as Art: a tile wall scrolling under a
        // stationary cursor must not spring every tile it drags past.
        property bool fxArmed: false
        function _fxAim(p) {
            var cx = width / 2, cy = height / 2
            fxRy =  ((p.x - cx) / cx) * root.artFxTilt
            fxRx = -((p.y - cy) / cy) * root.artFxTilt
        }
        Timer {
            id: btFxArm; interval: 90
            onTriggered: {
                if (!btFxHover.hovered) return
                bt.fxArmed = true
                bt.fxLift = root.artFxLift
                bt._fxAim(btFxHover.point.position)
            }
        }
        Behavior on fxRx   { NumberAnimation { duration: 280; easing.type: Easing.OutBack } }
        Behavior on fxRy   { NumberAnimation { duration: 280; easing.type: Easing.OutBack } }
        Behavior on fxLift { NumberAnimation { duration: 280; easing.type: Easing.OutBack } }
        transform: [
            Rotation { origin.x: bt.width / 2; origin.y: bt.height / 2; axis: Qt.vector3d(1, 0, 0); angle: bt.fxRx },
            Rotation { origin.x: bt.width / 2; origin.y: bt.height / 2; axis: Qt.vector3d(0, 1, 0); angle: bt.fxRy },
            Scale    { origin.x: bt.width / 2; origin.y: bt.height / 2; xScale: bt.fxLift; yScale: bt.fxLift }
        ]
        HoverHandler {
            id: btFxHover
            enabled: bt.fxOn
            onHoveredChanged: {
                if (hovered) btFxArm.restart()
                else {
                    btFxArm.stop(); bt.fxArmed = false
                    bt.fxRx = 0; bt.fxRy = 0; bt.fxLift = 1.0
                }
            }
            onPointChanged: if (bt.fxArmed) bt._fxAim(point.position)
        }
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
            running: root.onScreen && !root.browseMoving && bt.visible && bt.arts.length > bt.artN && bt.artN > 0
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
            onClicked: bt.plOnly ? root.openPlaylistsFolder(bt.path, bt.title)
                                 : root.openBrowseLink(bt.path, bt.title)
        }
        // All Playlists folder chrome: the card-style hover strip (PREVIEW |
        // DOWNLOAD ALL), swapped for the live rollup button + badge once the
        // category is downloading. HoverHandler, not btMa.containsMouse: the
        // strip's own MouseAreas would steal the hover and flicker the strip.
        HoverHandler { id: btHover; enabled: bt.plOnly }
        readonly property string catId: "cat:" + path
        readonly property string catSt: plOnly ? root.dlSt(catId) : ""
        // Both states ride one riser, so a running rollup keeps showing after
        // the pointer leaves (same rule as the album cards' controls).
        RiseIn {
            on: bt.plOnly && (btHover.hovered || bt.catSt !== "")
            anchors.left: parent.left; anchors.right: parent.right
            anchors.bottom: parent.bottom; anchors.bottomMargin: 10
            height: 34
        // Same roll as the album cards: the strip and the live rollup button
        // are one pill that changes its contents, not two that blink.
        RollSwap {
            id: catSwap
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.verticalCenter: parent.verticalCenter
            live: bt.catSt !== ""
            liveBorder: catBtn.edge
            idleItem: Item {
            // Same pill as the album-card strip, one notch smaller: at the
            // card sizes the natural width overflows the 200px tile, so the
            // labels drop to 9px and the segment padding tightens to fit.
            id: catStrip
            width: catStripRow.implicitWidth; height: 30
            // Hover swell, same wiring as the album cards' strip (see there
            // for why one handler and why PREVIEW overshoots the divider).
            HoverHandler { id: catStripHover }
            readonly property real dividerX: catStripRow.children[0].width
            readonly property real hoverX: catStripHover.point.position.x
            HoverSwell {
                z: 1    // above the Row, so the lit edge covers the divider
                width: catStrip.dividerX + root.btnBorderW; height: catStrip.height
                radR: 0
                on: catStripHover.hovered && catStrip.hoverX <= catStrip.dividerX
            }
            HoverSwell {
                z: 1
                x: catStrip.dividerX; width: catStrip.width - catStrip.dividerX; height: catStrip.height
                radL: 0
                on: catStripHover.hovered && catStrip.hoverX > catStrip.dividerX
            }
            Row {
                id: catStripRow
                anchors.verticalCenter: parent.verticalCenter
                Item {
                    implicitWidth: catStripPv.implicitWidth + 14; implicitHeight: 30
                    Row {
                        id: catStripPv
                        anchors.centerIn: parent; spacing: 5
                        Ico { name: "play"; color: root.accent; size: 10; anchors.verticalCenter: parent.verticalCenter }
                        Text { textFormat: Text.PlainText; text: "PREVIEW"; color: root.accent; font.family: root.uiFont; font.pixelSize: 9; font.bold: true; font.letterSpacing: root.btnTrack; anchors.verticalCenter: parent.verticalCenter }
                    }
                    MouseArea {
                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: { root.catPendingPv = bt.path; waves.resolvePlaylistCategory(bt.path, bt.title) }
                    }
                }
                Rectangle { width: root.btnBorderW; height: 30; color: root.accentDim; anchors.verticalCenter: parent.verticalCenter }
                Item {
                    implicitWidth: catStripDl.implicitWidth + 14; implicitHeight: 30
                    Row {
                        id: catStripDl
                        anchors.centerIn: parent; spacing: 5
                        Ico { name: "arrow-down"; color: root.accent; size: 11; bold: 10; anchors.verticalCenter: parent.verticalCenter }
                        Text { textFormat: Text.PlainText; text: "DOWNLOAD ALL"; color: root.accent; font.family: root.uiFont; font.pixelSize: 9; font.bold: true; font.letterSpacing: root.btnTrack; anchors.verticalCenter: parent.verticalCenter }
                    }
                    MouseArea {
                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                        onClicked: { root.catPendingDl = bt.path; waves.resolvePlaylistCategory(bt.path, bt.title) }
                    }
                }
            }
            }
            liveItem: DownloadButton {
                id: catBtn
                bare: true
                mediaId: bt.catId
                label: "Download all"
                // Retry after a failed rollup re-queues from the cached list.
                onTap: function(){ waves.downloadPlaylistCategory(bt.path) }
            }
        }
        // The count badge deliberately overhangs the button's corner, so it
        // cannot ride inside the pill's clipping window. It sits alongside and
        // arrives with the button instead.
        FolderBadge {
            anchors.right: catSwap.right; anchors.rightMargin: -8
            anchors.top: catSwap.top; anchors.topMargin: -9
            folderId: bt.catId
            st: catBtn.st
            // Ride the swap AND keep the component's own reasons to hide: an
            // override replaces the whole binding, so `catSwap.inFrac` alone
            // pinned the badge opaque through its `gone` dismissal (a shrunken
            // ✓ parked on the corner for the life of the tile) and the plain
            // `opacity > 0.004` dropped the empty-value guard, showing a bare
            // pill with no digit for the whole folder-tree warm.
            opacity: catSwap.inFrac * (gone ? 0 : 1)
            visible: opacity > 0.004 && value !== ""
        }
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
        reuseItems: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar {}
        // Pin the scroll spot across a revalidate refill (same pattern as the
        // playlist-folder list): the fill resets contentY, so the armed spot
        // is re-applied as the delegates lay out, until it is reachable. A
        // user drag disarms it, their hand always wins.
        property real pendingY: -1
        function applyRestore() {
            if (pendingY < 0) return
            var maxY = Math.max(0, contentHeight - height)
            contentY = Math.min(pendingY, maxY)
            if (maxY >= pendingY) pendingY = -1
        }
        onMovementStarted: pendingY = -1
        // Re-check on scroll AND on size/content changes: after a page is
        // appended (contentHeight grows) or if the first page doesn't fill the
        // viewport (so it can't scroll), contentY never changes on its own,
        // without these the loader would stall. All three are idempotent thanks
        // to the libCatHasMore / libLoadingMore guards in libMaybeLoadMore.
        onContentYChanged: root.libMaybeLoadMore(lv, lv.cat)
        onContentHeightChanged: { applyRestore(); root.libMaybeLoadMore(lv, lv.cat) }
        onHeightChanged: root.libMaybeLoadMore(lv, lv.cat)
        // Breathing space inside the scroll area (see BrowseScroll).
        header: Item { width: 1; height: 8 }
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
    ListModel { id: artistVideosModel }
    ListModel { id: artistEpModel }
    ListModel { id: artistTracksModel }
    ListModel { id: libAlbumsModel }
    ListModel { id: libTracksModel }
    ListModel { id: libArtistsModel }
    ListModel { id: libPlaylistsModel }
    ListModel { id: libMixesModel }
    ListModel { id: libVideosModel }
    // Drilled-into playlist folder (issue #11). Its own model on purpose: a
    // background revalidate of the playlists tab clears the six lib models,
    // and must not wipe the folder the user is standing in.
    ListModel { id: libFolderModel }
    // Stack of {id, title, y} from the root down to the open folder.
    property var plFolderStack: []
    property string plCurrentFolder: ""
    // folder_id -> playlists remaining in its "download all" (badge digit).
    property var folderRemainMap: ({})
    // Browse category DOWNLOAD ALL flow: which tile's resolve is pending a
    // download or a preview, and the confirm prompt ({path, title, count}).
    property string catPendingDl: ""
    property string catPendingPv: ""
    property var catDlPrompt: null
    // Every way out of the bulk-download confirm that is not "Download all".
    // The tick has to go with the dialog: left armed it re-opens pre-ticked for
    // a DIFFERENT category, and confirming that one silences the confirm for
    // good, which only a full settings reset used to undo.
    function catDlDismiss() { catDlPrompt = null; cdSkip.checked = false }
    function openPlFolder(fid, title) {
        var st = plFolderStack.slice()
        if (st.length) st[st.length - 1].y = libFolderList.contentY
        st.push({ id: fid, title: title, y: 0 })
        plFolderStack = st
        plCurrentFolder = fid
        libFolderList.pendingY = 0
        waves.openPlaylistFolder(fid)
    }
    // level -1 = back to the root list; otherwise jump to that stack entry.
    function plCrumbTo(level) {
        if (level < 0) {
            plFolderStack = []
            plCurrentFolder = ""
            libFolderModel.clear()
            return
        }
        var entry = plFolderStack[level]
        plFolderStack = plFolderStack.slice(0, level + 1)
        plCurrentFolder = entry.id
        libFolderList.pendingY = entry.y
        waves.openPlaylistFolder(entry.id)
    }
    function plFolderReset() {
        plFolderStack = []
        plCurrentFolder = ""
        libFolderModel.clear()
        folderRemainMap = ({})
    }
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
                var q = it.quality || ""
                if (row.quality !== q) m.setProperty(idx, "quality", q)
                // What the files landed at, rolled up by the bridge from its
                // per-track registry, so a COLLAPSED row states the delivery.
                var ld = it.landed || ""
                if (row.landed !== ld) m.setProperty(idx, "landed", ld)
                // Per-tier counts as a JSON string, not a list: a ListModel
                // role given a JS array turns it into a nested ListModel, and
                // the delegate wants the array back. A string role also gives
                // the badge a stable value to bind against, so it rebuilds when
                // the mix really changes and not on every tick.
                var mx = JSON.stringify(it.mix || [])
                if (row.mixJson !== mx) m.setProperty(idx, "mixJson", mx)
                // Keep a row that has already moved to Completed there; otherwise
                // group by status: failed rows get their own section (with the
                // RETRY ALL header), queued rows theirs, the rest ride Downloading.
                var grp = row.moved ? "completed"
                        : it.status === "failed" ? "failed"
                        : it.status === "queued" ? "queued" : "downloading"
                if (row.uiGroup !== grp) m.setProperty(idx, "uiGroup", grp)
            } else {
                // EVERY field the drawer reads has to be named HERE, including
                // ones that are empty on arrival: a ListModel fixes its roles
                // from the first object appended, so a role missing here does
                // not exist on any row, reads as undefined in the delegate, and
                // fails silently (no warning, no binding error). `quality` was
                // exactly that: the bridge set it on every row and the drawer
                // never saw one, so a queued row could not state its tier.
                m.append({ qid: it.qid, name: it.name, type: it.type, status: it.status,
                           progress: it.progress, media_id: it.media_id, template: it.template,
                           collection: it.collection, artist: it.artist || "", tracks: it.tracks || 0,
                           art: it.art || "", quality: it.quality || "",
                           landed: it.landed || "", mixJson: JSON.stringify(it.mix || []),
                           uiGroup: (it.status === "failed" ? "failed" : it.status === "queued" ? "queued" : "downloading"), moved: false,
                           doneAt: (it.status === "done" ? Date.now() : 0), leaving: false })
            }
        }
        // A row that has left the queue takes its per-qid state with it: the
        // expanded track list AND the expansion flag itself. queueTracks is
        // copied wholesale on every live tick, so a session's worth of finished
        // albums would make each tick cost more than the one before it;
        // queueExpanded is only copied on a click, but it is the same row's
        // state and outliving its row is the same leak. One copy of each for
        // the whole sweep, not one per row.
        var dropped = []
        for (var k = m.count - 1; k >= 0; --k) {
            var gone = m.get(k).qid
            if (!seen[gone]) { dropped.push(gone); m.remove(k) }
        }
        if (dropped.length > 0) {
            var tracks = Object.assign({}, root.queueTracks), forgot = false
            var exp = Object.assign({}, root.queueExpanded), collapsed = false
            for (var d = 0; d < dropped.length; ++d) {
                if (tracks[dropped[d]] !== undefined) { delete tracks[dropped[d]]; forgot = true }
                if (exp[dropped[d]] !== undefined) { delete exp[dropped[d]]; collapsed = true }
            }
            if (forgot) root.queueTracks = tracks
            if (collapsed) root.queueExpanded = exp
        }
        queuePartition()
        updateQueueCounts()
    }

    // Stable-partition the queue model into [completed, failed, downloading,
    // queued], preserving each group's internal order. Only emits move()s when
    // something is out of place (so a steady queue animates nothing). Failed
    // sits above the active groups so retries are in reach, below Completed so
    // the drawer still opens on the familiar collapsed header.
    function queuePartition() {
        var m = queueModel, w = 0, order = ["completed", "failed", "downloading", "queued"]
        for (var g = 0; g < order.length; ++g) {
            for (var i = w; i < m.count; ++i) {
                if (m.get(i).uiGroup === order[g]) { if (i !== w) m.move(i, w, 1); w++ }
            }
        }
    }

    // qid -> model row, rebuilt in the pass that recounts the groups (every
    // structural change already ends in one, so the map costs nothing extra).
    // See queueRowIndexOf for why it exists.
    property var queueRowIndex: ({})

    function updateQueueCounts() {
        var m = queueModel, c = 0, f = 0, d = 0, q = 0, l = 0, idx = ({})
        for (var i = 0; i < m.count; ++i) {
            var row = m.get(i)
            idx[row.qid] = i
            var grp = row.uiGroup
            if (grp === "completed") c++
            else if (grp === "failed") f++
            else if (grp === "downloading") d++
            else q++
            if (row.status === "done" && !row.moved) l++
        }
        root.queueRowIndex = idx
        root.completedCount = c; root.failedCount = f; root.downloadingCount = d; root.queuedCount = q
        root.lingerCount = l
    }

    // The model row holding a qid, without walking the model to find it.
    // Progress ticks arrive per delivered segment, dozens a second per active
    // download, and each one used to scan every row until it hit its own: with
    // a few hundred rows behind it that scan ran on the GUI thread thousands of
    // times a second and is what made a long batch feel heavy (issue #24). The
    // map above is rebuilt after every structural change; a tick landing
    // between a move and that rebuild pays for one rebuild here, not a scan.
    // -1 means the row is gone, which a late tick for a cancelled job will see.
    function queueRowIndexOf(qid) {
        var i = root.queueRowIndex[qid]
        if (i !== undefined && i < queueModel.count && queueModel.get(i).qid === qid) return i
        updateQueueCounts()
        i = root.queueRowIndex[qid]
        return (i === undefined) ? -1 : i
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
        // Indexed, not searched: the linger clock promotes every row of a
        // finished batch, and a scan apiece made that whole sweep cost the
        // square of the batch.
        var i = root.queueRowIndexOf(qid)
        if (i < 0 || m.get(i).uiGroup === "completed") return
        // Land at the TOP of the Completed group (newest first, oldest at the
        // bottom); Completed is the first group, so that is model index 0. The
        // ListView move transition slides it up.
        m.setProperty(i, "moved", true)
        m.setProperty(i, "uiGroup", "completed")
        m.setProperty(i, "leaving", false)
        if (i !== 0) m.move(i, 0, 1)
        root.compBump += 1
        updateQueueCounts()
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
    // The keep-alive view pane for a category (for scroll pinning across a
    // revalidate refill). The playlists pane pins its ROOT list; the drilled
    // folder view keeps its own pendingY machinery.
    function libViewFor(cat) {
        return cat === "albums" ? libAlbumsList : cat === "tracks" ? libTracksList
             : cat === "artists" ? libArtistsGrid : cat === "playlists" ? libPlaylistsList
             : cat === "mixes" ? libMixesList : cat === "videos" ? libVideosList : null
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
        libPinRefill = false          // a re-sorted list restarts at the top
        libCatHasMore[cat] = false
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
        libPinRefill = false          // the order is changing: land at the top
        libCatHasMore[cat] = false
        // Only this category restarts; the other keep-alive panes are untouched.
        var mm = libModelFor(cat); if (mm) mm.clear()
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
        if (cat !== root.libraryCategory || !root.libCatHasMore[cat] || root.libLoadingMore) return
        if (view.count === 0 || view.contentHeight <= 0) return
        if (view.contentY + view.height > view.contentHeight - view.height * 1.5) {
            root.libLoadingMore = true
            waves.loadMoreLibrary(cat)
        }
    }

    Connections {
        target: waves
        function onLibraryLoaded(cat, items, more) {
            root.libCatHasMore[cat] = more
            // A load for a category the user already left still lands in that
            // category's own (hidden, keep-alive) pane: returning to it later
            // is then instant. Only the ACTIVE pane needs the flags and the
            // scroll pinning.
            if (cat !== root.libraryCategory) { root.libFill(cat, items); return }
            root.libLoadingMore = false
            // A quiet revalidate that actually changed the rows replaces them
            // under the user; pin the spot across the refill (BrowseScroll's
            // holdScroll idea). Sort changes and fresh loads disarmed the pin,
            // so those still land at the top as a restarted list should.
            var v = root.libViewFor(cat)
            var keepY = (root.libPinRefill && v && v.visible && !v.moving && v.contentY > 0)
                ? v.contentY : -1
            root.libFill(cat, items)
            if (keepY >= 0) { v.pendingY = keepY; v.applyRestore() }
            else if (v) {
                // A restarted list (fresh load, new sort order) begins at the
                // top. Explicit, because a clear+refill leaves the old
                // contentY in place when the new content is just as tall.
                v.pendingY = -1
                v.contentY = 0
            }
        }
        function onLibraryMore(cat, items, more) {
            if (cat !== root.libraryCategory) return
            root.libCatHasMore[cat] = more
            root.libLoadingMore = false
            root.libAppend(cat, items)
        }
        function onPlaylistFolderLoaded(fid, rows, path) {
            // Stale guard: the user already moved to another folder (or back
            // to the root) while this answer was in flight.
            if (fid !== root.plCurrentFolder) return
            root.fill(libFolderModel, rows)
            libFolderList.applyRestore()
        }
        function onFolderRemaining(fid, remaining, total) {
            // New object on purpose: mutate-and-reassign doesn't notify.
            var m = Object.assign({}, root.folderRemainMap)
            m[fid] = remaining
            root.folderRemainMap = m
        }
        // A Browse category finished resolving (count known, list cached):
        // run whichever action the user queued on the tile.
        function onPlaylistCategoryResolved(path, title, count, firstId) {
            if (root.catPendingPv === path) {
                root.catPendingPv = ""
                if (firstId !== "") root.togglePreview("playlist", firstId, 0)
            }
            if (root.catPendingDl !== path) return
            root.catPendingDl = ""
            if (count <= 0) return   // backend already set the status line
            if (waves.confirmCategoryDl)
                root.catDlPrompt = { path: path, title: title || "this category", count: count }
            else
                waves.downloadPlaylistCategory(path)
        }
        function onHomeLoaded(sections) {
            if (root.libraryCategory !== "home") return
            root.libLoadingMore = false      // Home is one self-contained landing
            // Only populate when the load actually returned shelves. An empty
            // result (a transient fetch failure) must not wipe shelves already on
            // screen; a still-empty first load simply leaves the placeholder glyph
            // up, and the next visit retries (homeSections is still empty).
            if (sections && sections.length) root.homeSections = sections
        }
        function onDownloadFolderMissing() { root.folderGateBlocking = true }
        function onDownloadFolderDefault() { root.folderNudge = true }
        function onDownloadFolderUnreachable(path) { root.folderUnreachablePath = path; root.folderUnreachable = true }
        // The recovery watch saw the drive come back and already resumed the
        // held downloads: the gate dialog closes itself.
        function onDownloadFolderRecovered() { root.folderUnreachable = false }
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
            root.browseTitleHint = ""
            root.browseStack = []
            root.browseError = false
            root.browsePageError = false
            root.browsePageLoading = false
            root.browseLoading = false
            // Including a landing payload parked mid-handover: it is the
            // previous account's, and applying it after the reveal would put
            // their personalized rows back on screen.
            root._browseParked = null
            // History snapshots hold page payloads (personalized rows) and
            // artist ids from the previous account, drop them too (both
            // stacks: a stale forward entry would otherwise replay the old
            // account's page when the forward button is pressed).
            root.navHistory = []
            root.navForwardHistory = []
            root._navRestoring = false
            root.browseHighlightId = ""
            root.homeSections = []
            // A DOWNLOAD ALL or PREVIEW whose resolve the logout generation
            // bump threw away is still armed here. Left alone, the next resolve
            // of the same category path after signing back in consumes it: a
            // confirm nobody asked for, or (with the confirm muted) the whole
            // category queued on the new account off a click made on the old.
            root.catPendingDl = ""
            root.catPendingPv = ""
            root.catDlPrompt = null
            // The keep-alive My Tidal panes hold the previous account's
            // favourites for their whole life; only the account flip may
            // clear them (loadLib no longer clears on category switches).
            libAlbumsModel.clear(); libTracksModel.clear(); libArtistsModel.clear()
            libPlaylistsModel.clear(); libMixesModel.clear(); libVideosModel.clear()
            root.libCatHasMore = ({})
            root.libLoadingMore = false
            // The saved Search drill-in can hold the previous account's artist
            // page, restorable from the Search tab: same class of leak as the
            // history stacks dropped above.
            root.searchSaved = null
            // The drilled-into folder view holds the previous account's rows.
            root.plFolderReset()
            if (waves.loggedIn && root.browseOpen) {
                root.browseLoading = true
                waves.loadBrowse()
            }
        }
        function onBrowseLoaded(p) {
            root.markRender("browse render")
            root.browseLoading = false
            // The handover is the reveal itself: the wordmark zooming out and
            // the interface fading up over ~1.4s. bootOverlay.done is still
            // false throughout, so a revalidate landing in that window took the
            // fresh-build path, raised the veil and blanked the landing under
            // the fade. That is precisely the "watched the page assemble
            // itself" symptom the launch hold exists to prevent, newly
            // reachable since the second build became asynchronous.
            // Park it and apply it on the other side, where it is an ordinary
            // in-place refresh. Only a landing that already has content can
            // wait: a first build has nothing to reveal and must go through.
            // The window spans the whole visible handover: the version drain
            // (bootBlk) and the zoom/reveal (bootZoom), which the drain's
            // last tick starts.
            if ((bootHandover.running || bootBlk.running || bootZoom.running)
                    && root.browseSections.length > 0) {
                root._browseParked = p
                return
            }
            root.applyBrowseLanding(p)
        }
        function onBrowsePageLoaded(p) {
            if (p.key !== root.browsePageKey) return   // stale: user already left this page
            root.markRender("browse page render")
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
            // A revalidate re-emit of the page already showing swaps it in
            // place; hold the user's spot across the rebuild (see holdScroll).
            if (root.browsePage && !p.error) browseDrill.holdScroll()
            root.browsePage = p.error ? null : p
            // Whether this TIDAL album is already in the user's local library is
            // resolved by onBrowsePageChanged above, which covers every way a
            // page can arrive. Re-resolve here too for the one case that is not
            // a change: a revalidate re-emitting the SAME page object.
            root._resolveLibraryPresence()
        }
        // The local library-presence index (re)built: re-query for the album on
        // screen so the badge appears/updates with no reload.
        function onLibraryPresenceChanged() {
            root._resolveLibraryPresence()
            // A search page waiting behind the veil for its badges can have
            // them now. Handed to a zero-interval timer rather than revealed
            // here: this handler belongs to a Connections created when the
            // engine loaded, and every badge's own Connections was created
            // later, inside a delegate. Qt delivers in connection order, so
            // revealing on this line dropped the veil BEFORE a single pill had
            // re-resolved, and the badges then faded in over the page they had
            // just been held back for. One event-loop pass puts the whole page
            // behind the reveal, which is the point of waiting at all.
            if (root._searchAwaitingLibrary) searchLibraryReveal.restart()
        }
        function onBrowseSectionMore(p) {
            root.browseGrew(p)
        }
        function onVideoReady(p) {
            // Stale resolve (overlay closed, or another video opened since).
            if (!root.videoNow || ("" + p.id) !== root.videoNow.id) return
            if (p.error) {
                if (root.videoSwitching) { root.videoSwitching = false; return }  // keep the old stream playing
                // Same for a promotion: the adopted peek keeps playing, just
                // at glance resolution, and the user sees no failure at all.
                if (root.videoUpgrading) { root._videoUpgradeGiveUp(); return }
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
                videoStage.grabToImage(function(result) {
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
            if (root.videoUpgrading) {
                // Promotion upgrade: the adopted peek is on stage and
                // sounding, so the real stream starts MUTED on the off-stage
                // sink and chases its clock (videoUpgradeRetry cuts across
                // once they line up). Nothing visible happens until then.
                videoOut.muted = true
                root._videoUpTries = 0
                root._videoUpAims = 0
                root._videoUpPhase = 0
                root._videoUpTarget = -1
                videoPlayer.source = p.url
                videoPlayer.play()
                videoUpgradeRetry.restart()
                return
            }
            videoPlayer.source = p.url
            videoPlayer.play()
        }
        function onVideoPeekReady(p) {
            // Stale resolve: the card closed, or retargeted to another video.
            if (!root.peekNow || ("" + p.id) !== root.peekNow.id) return
            if (p.error) { root.peekClose(); return }
            peekPlayer.source = p.url
            peekPlayer.play()
        }
        function onBrowseTileArt(path, arts) {
            // Mutate the buffer in place (no rebind) and coalesce via the timer.
            root._tileArtPending[path] = arts
            tileArtFlush.restart()
        }
        function onSearchResults(r) {
            // A search resolves over seconds (paginated fan-out), so its
            // results can land AFTER the user clicked an artist or album name
            // and left. Rendering then would yank them to the search page as
            // if their click had searched. Accept only when nothing was
            // navigated since the search was issued (_searchSeq snapshot) and
            // no other surface took over meanwhile (card clicks flip these
            // flags without a markNav; artistOpen is deliberately allowed,
            // the search tier is live on artist pages). Dropped payloads stay
            // in the backend's search cache, re-searching is instant.
            if (root._searchSeq !== root._navSeq
                || root.browseOpen || root.libraryOpen || root.settingsOpen) return
            root.navPush()
            root.markRender("search render")
            root.searchSaved = null   // a fresh search replaces the saved drill-in
            // A fresh search always lands at the top. The page keeps one
            // scroll position for every section (the sections stack inside
            // the one results Flickable), so without this the new results
            // render at the old search's scroll offset. The artist strip
            // keeps its own horizontal offset, reset alongside.
            results.contentY = 0
            artistStrip.contentX = 0
            root.navOrigin = "search"
            root.browseOpen = false
            root.artistOpen = false
            root.libraryOpen = false
            root.trackCache = ({})
            root.expandedAlbums = ({})
            // Playlist rows expand the same way, and their cache has to go with
            // them: playlists mutate, so a row left expanded across searches
            // would show yesterday's tracks with no refetch to correct them.
            root.playlistTrackCache = ({})
            root.expandedPlaylists = ({})
            // A section a user expanded stays expanded on the next search
            // (searchArtistsExpanded and the list-section flags are pref-backed),
            // so nothing is reset here.
            // Arm the build veil BEFORE the fills: the Loaders each delegate
            // creates read searchBuilding for their asynchronous flag, and the
            // ready ticks only ever arrive on later frames, never mid-fill.
            root._searchBuildStart((r.artists || []).length + (r.albums || []).length
                                 + (r.tracks || []).length + (r.videos || []).length
                                 + (r.playlists || []).length + (r.mixes || []).length)
            root.fill(artistsModel, r.artists)
            root.albumsRaw = r.albums || []
            root.tracksRaw = r.tracks || []
            root.videosRaw = r.videos || []
            root.applySort()
            root.fill(playlistsModel, r.playlists)
            root.fill(mixesModel, r.mixes)
        }
        // Assign a NEW object so the `var` property fires a change notification
        // (mutating + reassigning the same reference does not update bindings).
        function onAlbumTracksLoaded(id, tracks) { var c = Object.assign({}, root.trackCache); c[id] = tracks; root.trackCache = c }
        function onPlaylistTracksLoaded(id, tracks) { var c = Object.assign({}, root.playlistTrackCache); c[id] = tracks; root.playlistTrackCache = c }
        function onArtistMetaLoaded(id, pop) {
            for (var i = 0; i < artistsModel.count; ++i) {
                if (artistsModel.get(i).id === id) { artistsModel.setProperty(i, "popularity", pop); break }
            }
        }
        function onArtistLoadFailed(id) {
            // A Back-restore whose artist reload failed (offline, API error):
            // onArtistLoaded never fires, so clear the latch here or history
            // recording stays dead until the next successful navigation.
            if (root._navRestoring) root._navRestoring = false
            if (root._artistRestoreState && root._artistRestoreState.id === ("" + id)) {
                root._artistRestoreState = null
                artistView.pendingRestoreKey = ""
                artistView.pendingRestoreY = -1
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
                artistView.holdScroll()   // in-place swap: keep the user's spot
                root.artistData = p
                root.fillMedia(artistAlbumsModel, p.albums)
                root.fillMedia(artistEpModel, p.eps)
                root.fillMedia(artistTracksModel, p.tracks)
                root.fillMedia(artistVideosModel, p.videos || [])
                return
            }
            if (root.videoNow) root.closeVideo()   // artist link from the video player
            if (root._navRestoring) root._navRestoring = false
            else root.navPush()
            root.markNav("artist render")
            root.artistData = p
            // A Back into this artist re-applies the snapshot's expansion state
            // (armed in navBack); any other load starts the page collapsed.
            var pr = root._artistRestoreState
            root._artistRestoreState = null
            if (pr && pr.id === ("" + p.id)) {
                root.expandedAlbums = pr.ex || ({})
                root.bioExpanded = !!pr.bio
            } else {
                root.bioExpanded = false
                root.expandedAlbums = ({})
            }
            root.artistOpen = true      // target-first (see openLibrary): keep Search inactive mid-switch
            root.libraryOpen = false
            root.fillMedia(artistAlbumsModel, p.albums)
            root.fillMedia(artistEpModel, p.eps)
            root.fillMedia(artistTracksModel, p.tracks)
            root.fillMedia(artistVideosModel, p.videos || [])
        }
        function onQueueChanged(q) {
            root.reconcileQueue(q)
            var n = 0
            for (var i = 0; i < q.length; ++i) { var s = q[i].status; if (s === "queued" || s === "running") n++ }
            root.activeQueueCount = n
        }
        function onQueueItemProgress(qid, pct) {
            var i = root.queueRowIndexOf(qid)
            if (i >= 0) queueModel.setProperty(i, "progress", pct)
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
                    copy[i] = Object.assign({}, copy[i], { status: row.status, pct: row.pct,
                                                          fpct: row.fpct || 0 })
                    // A delivered quality only arrives with the state change,
                    // so keep the one already on the row when the event has
                    // none rather than blanking the track's tier.
                    if (row.quality) copy[i].quality = row.quality
                    found = true; break
                }
            }
            if (!found) copy.push({ id: row.id, num: copy.length + 1, title: row.title,
                                    duration: row.duration, status: row.status, pct: row.pct,
                                    fpct: row.fpct || 0, quality: row.quality })
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
        function onDownloadProgress(id, pct) { var h = root.dlHolder(id); if (h) h.pct = pct }
        function onDownloadState(id, st) { var h = root.dlHolder(id); if (h) h.st = st }
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
        function onForwardRequested() { root.navForward() }
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
        id: mainColumn
        anchors.fill: parent
        spacing: 0
        // Hidden until the launch sequence hands over (see bootOverlay).
        // Invisible must also mean inert: opacity does not gate input, so
        // without the enabled gate every control is live under the launch
        // screen from the first frame (issue #13).
        // 0.004 is invisible but rendered; see root.bootWarming for why the
        // page is painted before the reveal asks for it.
        // root.uiShown, not the raw dial: a pending terms gate holds the whole
        // interface at nothing, painted and inert, however far the launch
        // sequence has run.
        opacity: root.uiShown > 0 ? root.uiShown : (root.bootWarming ? 0.004 : 0)
        enabled: root.uiShown > 0

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
                            // Matrix-decrypt paste-in; a pasted TIDAL link auto-searches once
                            // settled. A paste-glyph click arms the same auto-search for plain
                            // text too (issue #18): the button means "search this", while a
                            // bare Ctrl+V still only fills the field so a term can be edited.
                            DecodeController {
                                id: searchDecoder; field: searchField; glyph: pasteGlyph
                                // The decode runs ~0.6s, long enough to click an
                                // artist name meanwhile: snapshot _navSeq at paste
                                // time and let the link auto-open only if the user
                                // has not navigated anywhere since (same rule as
                                // onSearchResults, and same artistOpen allowance:
                                // the search box is live on artist pages).
                                property int seqAtPaste: -1
                                // One-shot: set by the paste glyph's click, latched by the
                                // decode that click's paste starts, cleared by any other
                                // text change (see the field's onTextChanged) so a stale
                                // arm never fires on a later unrelated paste.
                                property bool submitPending: false
                                property bool submitArmed: false
                                onBegun: {
                                    seqAtPaste = root._navSeq
                                    submitArmed = submitPending
                                    submitPending = false
                                }
                                onDecoded: function(text) {
                                    var armed = submitArmed
                                    submitArmed = false
                                    if (!searchBox.isTidalUrl(text) && !armed) return
                                    if (seqAtPaste !== root._navSeq
                                        || root.browseOpen || root.libraryOpen || root.settingsOpen) return
                                    root._searchSeq = root._navSeq
                                    waves.search(text)
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
                                    onAccepted: { root._searchSeq = root._navSeq; waves.search(text) }
                                    // A click or Tab into a box that already holds a term selects
                                    // the whole term (deferred one tick past the click's own caret
                                    // placement, which would otherwise clear it) so the next
                                    // keystroke replaces it. Only fires on the focus transition, so
                                    // clicking again to reposition the caret mid-edit is left alone.
                                    onActiveFocusChanged: if (activeFocus) Qt.callLater(function() { searchField.selectAll() })
                                    // Returning to the app must re-arm that select-all. The
                                    // backend filter swallows WindowActivate/Deactivate (the
                                    // app-switch freeze fix), so the scene keeps its focus item
                                    // across a switch and the click that brings Waves back never
                                    // replays the transition above: the term sat unselected until
                                    // a click away and back. Ride the window's active flag (a
                                    // QWindow signal the swallow does not touch) to fire the same
                                    // deferred select-all the swallowed event used to produce.
                                    readonly property bool appActive: root.active
                                    onAppActiveChanged: if (appActive && activeFocus) Qt.callLater(function() { searchField.selectAll() })
                                    // A standard paste (a multi-char jump typing can't produce) is
                                    // detected and animated in, without ever reading the clipboard.
                                    onTextChanged: {
                                        var wasDecoding = searchDecoder.decoding
                                        searchDecoder.noteTextChanged()
                                        // Any change that is not part of a decode (typing, a clear,
                                        // a too-short paste) disarms a pending glyph auto-search.
                                        if (!wasDecoding && !searchDecoder.decoding) searchDecoder.submitPending = false
                                    }
                                }
                                PasteGlyph {
                                    id: pasteGlyph
                                    Layout.alignment: Qt.AlignVCenter
                                    // standard OS paste into the field; the field's onTextChanged
                                    // animates it. The app itself never reads the clipboard.
                                    // submitPending is set AFTER clear() (whose own text change
                                    // would disarm it) so the pasted text auto-searches once the
                                    // decode settles.
                                    onClicked: {
                                        searchField.forceActiveFocus()
                                        searchField.clear()
                                        searchDecoder.submitPending = true
                                        searchField.paste()
                                        // paste() inserts synchronously, so any decode this click
                                        // could start has already begun and latched the arm. An arm
                                        // still pending here had nothing to latch it (an empty
                                        // clipboard pastes no text) and must not wait around: a
                                        // LATER bare Ctrl+V starts its own decode, whose onBegun
                                        // reads the flag before the field's disarm can run, and a
                                        // paste the user meant to edit would search itself.
                                        searchDecoder.submitPending = false
                                    }
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
                                // Cascade in left-to-right the moment results land, decoupled
                                // from the card build veil (searchBuilding): gating the chips on
                                // the veil made them arrive late on a cold first search and flicker
                                // out then back in on every re-search. They now appear as soon as
                                // results exist and stay put while the cards paint behind the veil.
                                // Reset when cleared.
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
        // Sub-page crumb trail, pinned above the scroll area so the way back
        // is always reachable without scrolling to the top (mirrors the
        // artist page): the whole navHistory as pills, current page lit.
        Item {
            Layout.fillWidth: true; Layout.topMargin: 8
            visible: root.browseOpen && !root.artistOpen && !root.settingsOpen && !root.libraryOpen
                     && root.browsePageKey !== ""
            implicitHeight: 30
            clip: true
            NavCrumbTrail {
                x: 22; anchors.verticalCenter: parent.verticalCenter
                width: Math.min(implicitWidth, parent.width - 44)
            }
        }
        // Two always-alive panes on a flip stand: the landing on one, the
        // drilled page on the other, and navigation only ever changes which
        // is visible. Neither is torn down by leaving, so Back arrives
        // complete, already scrolled, with nothing to rebuild and nothing
        // to restore. Content is only rebuilt when the DATA changes (a
        // fresh landing payload, a different drilled page), never by
        // navigation itself.
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: root.browseOpen && !root.artistOpen && !root.settingsOpen && !root.libraryOpen
            // Fetch on first reveal too (e.g. the user signed in while
            // already on the tab); idempotent thanks to the loading flags
            // here and the in-flight guard backend-side.
            onVisibleChanged: {
                if (visible && waves.loggedIn && root.browseSections.length === 0 && !root.browseLoading) {
                    root.browseLoading = true; root.browseError = false
                    waves.loadBrowse()
                } else if (visible && waves.loggedIn) {
                    waves.refreshBrowse()   // silent, throttled; repaints only on change
                }
            }

            // While the landing loads or builds, the ambient wave video
            // behind this transparent pane stays in view on purpose: the
            // "Reading the wire…" hint sits on the living water and the
            // finished rows then fade in over it (browseReveal), instead of
            // a black floor snapping to rows.
            BrowseScroll {
                id: browseLanding
                anchors.fill: parent
                visible: root.browsePageKey === ""
                col: browseLandingCol
                Column {
                    id: browseLandingCol
                    // The 8px breathing space lives INSIDE the scroll area
                    // (y), not as an outer margin: an outer gap showed a
                    // band of raw background with scrolled rows cut off
                    // floating above it.
                    x: 22; y: 8; width: browseLanding.width - 44; spacing: 8

                    Text {
                        id: browseLandingHint
                        visible: waves.loggedIn && (root.browseLoading || root.browseBuilding)
                        width: parent.width; horizontalAlignment: Text.AlignHCenter
                        text: "Reading the wire…"
                        color: root.textLo; font.pixelSize: 22; topPadding: 96
                        // same gentle breathing as the search empty state
                        SequentialAnimation on opacity {
                            running: browseLandingHint.visible; loops: Animation.Infinite
                            NumberAnimation { from: 0.5; to: 1.0; duration: 1500; easing.type: Easing.InOutSine }
                            NumberAnimation { from: 1.0; to: 0.5; duration: 1500; easing.type: Easing.InOutSine }
                        }
                    }

                    Column {
                        visible: waves.loggedIn && root.browseError
                        width: parent.width; spacing: 12
                        Text {
                            width: parent.width; horizontalAlignment: Text.AlignHCenter
                            text: "Browse could not be loaded"
                            color: root.textLo; font.pixelSize: 18; topPadding: 80
                        }
                        Rectangle {
                            anchors.horizontalCenter: parent.horizontalCenter
                            implicitWidth: bLandRetryTxt.implicitWidth + root.btnPadH * 2
                            implicitHeight: bLandRetryTxt.implicitHeight + root.btnPadV * 2
                            radius: root.btnRad; color: "transparent"; border.color: root.accentDim
                            Text {
                                id: bLandRetryTxt; anchors.centerIn: parent; text: "RETRY"
                                color: root.accent; font.family: root.uiFont; font.pixelSize: 12
                                font.bold: true; font.letterSpacing: root.btnTrack
                            }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: { root.browseLoading = true; root.browseError = false; waves.loadBrowse() }
                            }
                        }
                    }

                    // Landing, console style: the Genres / Moods / Decades
                    // chip sets lead the page (the art-first layout renders
                    // the same data as colour tiles BELOW the content
                    // shelves instead). No page-key term: the landing pane
                    // stays alive behind a drilled page, tearing the chips
                    // down here is exactly the rebuild the two-pane design
                    // exists to avoid.
                    Repeater {
                        model: root.browseStyle === "console" && !root.browseLoading && !root.browseError
                               ? [["ALL PLAYLISTS", root.plChips(root.browseChips.moods)], ["GENRES", root.browseChips.genres],
                                  ["MOODS & ACTIVITIES", root.browseChips.moods], ["DECADES", root.browseChips.decades]]
                               : []
                        // Async for the same reason as the content shelves
                        // below: ~60 chips are a real chunk of the
                        // browse-render turn.
                        delegate: Loader {
                            id: chipGroupLd
                            required property var modelData
                            width: browseLandingCol.width
                            asynchronous: root._browseAsyncBuild
                            opacity: root.browseReveal
                            onLoaded: root._browseBuildTick()
                            sourceComponent: chipGroupComp
                            Component {
                                id: chipGroupComp
                                Column {
                                    id: chipGroup
                                    readonly property var modelData: chipGroupLd.modelData
                                    width: browseLandingCol.width; spacing: 8
                                    visible: modelData[1].length > 0
                                    // The headline opens the whole set, exactly as the
                                    // art-style cloud headlines do. Without it the console
                                    // style had no route to the playlists-only view at all:
                                    // its chips reach the mood folders, but openPlaylistsRoot
                                    // (and with it every genre and decade playlist folder)
                                    // was unreachable in this style.
                                    SectionHeader {
                                        label: chipGroup.modelData[0]
                                        count: chipGroup.modelData[1].length
                                        openable: true
                                        onOpened: chipGroup.modelData[0] === "ALL PLAYLISTS"
                                                  ? root.openPlaylistsRoot()
                                                  : root.openBrowseCloud(chipGroup.modelData[0], chipGroup.modelData[1])
                                    }
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
                                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                            onClicked: bchip.modelData.pl ? root.openPlaylistsFolder(bchip.modelData.path, bchip.modelData.title)
                                                                                          : root.openBrowseLink(bchip.modelData.path, bchip.modelData.title) }
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }

                    // Content shelves. Fresh landing shelves load through
                    // asynchronous Loaders: the browseSections assignment
                    // turn only creates n empty Loaders (a few ms), and
                    // each shelf's delegate tree then incubates across
                    // frames, so the nav-tab strike animation keeps its
                    // frames instead of freezing for the ~250 ms a
                    // synchronous build of every shelf used to take.
                    Repeater {
                        model: root.browseSections
                        delegate: Loader {
                            id: bsecLd
                            required property var modelData
                            required property int index
                            width: browseLandingCol.width
                            asynchronous: root._browseAsyncBuild
                            // Invisible while the veil is up, but still laid out.
                            opacity: root.browseBuilding ? 0 : 1
                            onLoaded: root._browseBuildTick()
                            sourceComponent: BrowseSection {
                                sec: bsecLd.modelData
                                secIndex: bsecLd.index
                                landing: true
                                pane: browseLanding
                                col: browseLandingCol
                            }
                        }
                    }

                    // Landing, art-first: genre / mood / decade colour-tile
                    // shelves close the page, content first, wayfinding
                    // last, the way the streaming services arrange their
                    // home pages.
                    Repeater {
                        model: root.browseStyle === "art" && !root.browseLoading && !root.browseError
                               ? [["All Playlists", root.plChips(root.browseChips.moods)], ["Genres", root.browseChips.genres],
                                  ["Moods & Activities", root.browseChips.moods], ["Decades", root.browseChips.decades]]
                               : []
                        // Async for the same reason as the content shelves above.
                        delegate: Loader {
                            id: tileGroupLd
                            required property var modelData
                            width: browseLandingCol.width
                            asynchronous: root._browseAsyncBuild
                            opacity: root.browseReveal
                            onLoaded: root._browseBuildTick()
                            sourceComponent: tileGroupComp
                            Component {
                                id: tileGroupComp
                                Column {
                                    id: tileGroup
                                    readonly property var modelData: tileGroupLd.modelData
                                    width: browseLandingCol.width; spacing: 8
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
                                            onClicked: tileGroup.modelData[0] === "All Playlists"
                                                       ? root.openPlaylistsRoot()
                                                       : root.openBrowseCloud(tileGroup.modelData[0], tileGroup.modelData[1])
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
                                            plOnly: !!modelData.pl
                                        }
                                        ShelfWheelRedirect { pane: browseLanding }
                                        ShelfEdgeFades {}
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // The drilled page (playlist / album / mix / genre / cloud).
            // Rebuilt only when the page DATA changes; hidden, alive and
            // still positioned while the user is elsewhere, so returning to
            // the same page (another tab and back) costs nothing.
            BrowseScroll {
                id: browseDrill
                anchors.fill: parent
                visible: root.browsePageKey !== ""
                col: browseDrillCol
                restoreKey: root.browsePageKey
                grows: true
                Column {
                    id: browseDrillCol
                    x: 22; y: 8; width: browseDrill.width - 44; spacing: 8
                    // Hidden (but still laid out, so heights resolve) until
                    // the highlighted-track scroll has been applied: opening
                    // an album from a track then reveals it already on the
                    // row, never mid-jump. Entering the state has no
                    // transition, so the un-scrolled top never shows; the
                    // reveal then FADES in so the album eases into place
                    // already scrolled instead of dropping in blank. Only
                    // the reveal direction is animated (the Transition's
                    // `to: ""`); a Behavior can't do direction here without
                    // racing the pending change.
                    opacity: 1
                    states: State {
                        name: "positioning"; when: root.browseHighlightPending
                        PropertyChanges { target: browseDrillCol; opacity: 0 }
                    }
                    transitions: Transition {
                        to: ""   // leaving "positioning" == the reveal
                        NumberAnimation { property: "opacity"; duration: 260; easing.type: Easing.OutCubic }
                    }

                    // Item header (playlist / mix / album page): a
                    // full-width hero, the artwork doubles as a dimmed
                    // backdrop so the strip above the track list isn't
                    // mostly empty panel.
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
                        // Left-to-right scrim: keep the caption side
                        // readable, let the backdrop breathe on the right.
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
                                hoverFx: true
                                fxKind: browseItemHeader.hd ? ("" + (browseItemHeader.hd.kind || "")) : ""
                                fxId: browseItemHeader.hd ? ("" + (browseItemHeader.hd.id || "")) : ""
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
                                // Title + the presence pill right beside it.
                                Row {
                                    width: parent.width
                                    spacing: 10
                                    Text {
                                        id: bihTitle
                                        textFormat: Text.PlainText
                                        text: browseItemHeader.hd ? (browseItemHeader.hd.title || "") : ""
                                        color: root.textHi; font.pixelSize: 26; font.bold: true
                                        width: Math.min(implicitWidth,
                                                        parent.width - (bihPill.visible ? bihPill.width + 10 : 0))
                                        elide: Text.ElideRight
                                        anchors.verticalCenter: parent.verticalCenter
                                    }
                                    LibraryTag {
                                        id: bihPill
                                        anchors.verticalCenter: parent.verticalCenter
                                        // One pill for every album page you open, so the
                                        // page you open next must not read as news.
                                        settleKey: browseItemHeader.hd ? ("" + (browseItemHeader.hd.id || "")) : ""
                                        visible: !!(browseItemHeader.hd && browseItemHeader.hd.kind === "album"
                                                    && root.libraryPresence && root.libraryPresence.present)
                                        have: root.libraryPresence ? (root.libraryPresence.local_tracks || 0) : 0
                                        total: browseItemHeader.hd ? (browseItemHeader.hd.num_tracks || 0) : 0
                                        declared: root.libraryPresence ? (root.libraryPresence.local_declared || 0) : 0
                                        albumId: root.libraryPresence ? (root.libraryPresence.local_album_id || "") : ""
                                        qclass: root.libraryPresence ? (root.libraryPresence.local_class || "") : ""
                                        // Same identity axis as the search-row pills: a proven
                                        // match drops the "?" here too.
                                        proven: !!(root.libraryPresence && root.libraryPresence.present
                                                   && root.libraryPresence.sure === true)
                                    }
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
                                    // Album pages only (the header's artist/year facts are "" elsewhere,
                                    // and a playlist must never wear an album's library state).
                                    libAlbum: (browseItemHeader.hd && browseItemHeader.hd.kind === "album")
                                              ? ({ artist: browseItemHeader.hd.artist || "", title: browseItemHeader.hd.title || "",
                                                   year: "" + (browseItemHeader.hd.year || ""), tracks: browseItemHeader.hd.num_tracks || 0,
                                                   duration_sec: browseItemHeader.hd.duration_sec || 0 })
                                              : null
                                    onTap: function() {
                                        if (browseItemHeader.hd)
                                            root.browseCardDownload({ kind: browseItemHeader.hd.kind, id: browseItemHeader.hd.id })
                                    }
                                }
                            }
                        }
                    }

                    Text {
                        id: browseDrillHint
                        visible: waves.loggedIn && root.browsePageLoading
                        width: parent.width; horizontalAlignment: Text.AlignHCenter
                        text: "Reading the wire…"
                        color: root.textLo; font.pixelSize: 22; topPadding: 96
                        // same gentle breathing as the search empty state
                        SequentialAnimation on opacity {
                            running: browseDrillHint.visible; loops: Animation.Infinite
                            NumberAnimation { from: 0.5; to: 1.0; duration: 1500; easing.type: Easing.InOutSine }
                            NumberAnimation { from: 1.0; to: 0.5; duration: 1500; easing.type: Easing.InOutSine }
                        }
                    }

                    Column {
                        visible: waves.loggedIn && root.browsePageError
                        width: parent.width; spacing: 12
                        Text {
                            width: parent.width; horizontalAlignment: Text.AlignHCenter
                            text: "Browse could not be loaded"
                            color: root.textLo; font.pixelSize: 18; topPadding: 80
                        }
                        Rectangle {
                            anchors.horizontalCenter: parent.horizontalCenter
                            implicitWidth: bDrillRetryTxt.implicitWidth + root.btnPadH * 2
                            implicitHeight: bDrillRetryTxt.implicitHeight + root.btnPadV * 2
                            radius: root.btnRad; color: "transparent"; border.color: root.accentDim
                            Text {
                                id: bDrillRetryTxt; anchors.centerIn: parent; text: "RETRY"
                                color: root.accent; font.family: root.uiFont; font.pixelSize: 12
                                font.bold: true; font.letterSpacing: root.btnTrack
                            }
                            MouseArea {
                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                onClicked: root.retryBrowsePage()
                            }
                        }
                    }

                    // Empty state: a drilled page that loaded fine but has
                    // nothing Waves can render (e.g. an editorial article
                    // page with no music). Better than a blank page below
                    // the back bar.
                    Text {
                        visible: waves.loggedIn && root.browsePageKey !== "" && root.browsePage
                                 && !root.browsePageLoading && !root.browsePageError
                                 && ((root.browsePage.sections || []).length === 0)
                        width: parent.width; horizontalAlignment: Text.AlignHCenter
                        textFormat: Text.PlainText
                        text: "Nothing to show here"
                        color: root.textLo; font.pixelSize: 18; topPadding: 96
                    }

                    // The drilled page's sections, synchronous: the
                    // highlight machinery (browseHighlightPending + the
                    // 120 ms centering timer) measures row positions right
                    // after load and needs the full page laid out in one
                    // turn. Long track lists are windowed inside
                    // BrowseSection, so "the full page" is shells plus the
                    // viewport, never five hundred built rows.
                    Repeater {
                        model: root.browsePage ? root.browsePage.sections : []
                        delegate: BrowseSection {
                            required property var modelData
                            required property int index
                            width: browseDrillCol.width
                            sec: modelData
                            secIndex: index
                            landing: false
                            pane: browseDrill
                            col: browseDrillCol
                        }
                    }
                }
            }
        }


        // ---- Search results --------------------------------------------
        Flickable {
            id: results
            // Breathing space inside the scroll area (contentCol y), not as
            // an outer margin; see BrowseScroll.
            Layout.fillWidth: true; Layout.fillHeight: true
            visible: !root.artistOpen && !root.settingsOpen && !root.libraryOpen && !root.browseOpen
            clip: true
            contentWidth: width; contentHeight: contentCol.height + 32
            ScrollBar.vertical: ScrollBar {}
            boundsBehavior: Flickable.StopAtBounds

            // Hold the scroll position steady across a window resize. The ARTISTS
            // row fills the width, so its cards (and the page height) grow and
            // shrink as the window is dragged; without this a shrink clamps
            // contentY to the new bottom and the page visibly jumps. Capture the
            // scroll ratio once at the start of a resize gesture and re-apply it on
            // every height change until the drag settles (140ms after the last
            // width change), so the view stays put. Scoped to an active resize, so
            // the build-in reveal of a fresh search (height grows with no width
            // change) is never touched.
            property real _resizeRatio: -1
            Timer { id: resizeSettle; interval: 140; onTriggered: results._resizeRatio = -1 }
            onWidthChanged: {
                if (_resizeRatio < 0) {
                    var span = contentHeight - height
                    _resizeRatio = span > 0 ? contentY / span : 0
                }
                resizeSettle.restart()
            }
            onContentHeightChanged: {
                if (_resizeRatio < 0) return
                var maxY = Math.max(0, contentHeight - height)
                contentY = Math.min(_resizeRatio * maxY, maxY)
            }

            // While a search builds, the ambient wave video behind this
            // transparent pane stays in view on purpose: the "Reading the
            // wire…" hint sits on the living water and the finished result
            // cards then fade in over it (searchReveal), instead of a black
            // floor snapping to cards in one hard paint.
            Column {
                id: contentCol
                x: 22; y: 8; width: results.width - 44; spacing: 8

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

                Text {
                    id: searchBuildHint
                    visible: waves.loggedIn && root.searchBuilding
                    width: parent.width; horizontalAlignment: Text.AlignHCenter
                    text: "Reading the wire…"
                    color: root.textLo; font.pixelSize: 22; topPadding: 96
                    // same gentle breathing as the empty state and the Browse hint
                    SequentialAnimation on opacity {
                        running: searchBuildHint.visible; loops: Animation.Infinite
                        NumberAnimation { from: 0.5; to: 1.0; duration: 1500; easing.type: Easing.InOutSine }
                        NumberAnimation { from: 1.0; to: 0.5; duration: 1500; easing.type: Easing.InOutSine }
                    }
                }

                // ARTISTS
                SectionHeader { id: artistsHead; opacity: root.searchReveal; visible: root.sectionVisible("artists", artistsModel.count); label: "ARTISTS"; count: artistsModel.count }
                // Collapsed default (the mixed All view): a horizontal strip of
                // fixed-width cards that scrolls left/right, like the browse
                // shelves. A window resize reveals more or fewer cards but never
                // resizes the ones on screen, so it stays smooth (the browse tabs
                // are smooth for the same reason). SHOW ALL switches to the fill
                // grid below. The strip is a plain Row (not a virtualized
                // ListView) so all cards instantiate and each still fires one
                // build-veil tick; only ~12 artist cards, so that is cheap.
                Flickable {
                    id: artistStrip
                    visible: root.searchArtistsStripMode && root.sectionVisible("artists", artistsModel.count)
                    width: parent.width; height: artistRow.height
                    contentWidth: artistRow.width; contentHeight: artistRow.height
                    clip: true
                    flickableDirection: Flickable.HorizontalFlick
                    boundsBehavior: Flickable.StopAtBounds
                    readonly property real cardW: 200
                    Row {
                        id: artistRow
                        spacing: 12
                        Repeater {
                            model: artistsModel
                            delegate: Loader {
                                width: artistStrip.cardW
                                // Reserve a fixed cell (width + 142) while the async Loader is
                                // still empty (item null) so the strip does not collapse behind
                                // the build veil; snap to the card's exact height once loaded.
                                height: item ? item.implicitHeight : width + 142
                                // Live only in strip mode, so exactly one of the strip and the
                                // grid instantiates its cards. That keeps the build veil's
                                // one-tick-per-artist count exact (never zero, never doubled).
                                active: root.searchArtistsStripMode
                                asynchronous: root.searchBuilding
                                opacity: root.searchReveal
                                onLoaded: root._searchBuildTick()
                                sourceComponent: ArtistSearchCard {
                                    aArt: model.art; aName: model.name; aPop: model.popularity; aId: model.id
                                }
                            }
                        }
                    }
                    // Vertical wheel scrolls the page, sideways wheel/trackpad
                    // scrolls the strip (shared with the browse shelves).
                    ShelfWheelRedirect { pane: results }
                    ShelfEdgeFades {}
                }
                // Expanded (SHOW ALL) or the Artists filter: the fill grid. Cards
                // stretch edge-to-edge and the column count snaps at whole-column
                // boundaries (shared gridCols). A resize here re-fits the cards
                // (the previous method), which the user opts into by expanding;
                // the results Flickable anchors its scroll (see _resizeRatio) so
                // the page below does not jump.
                Flow {
                    id: artistFlow
                    visible: !root.searchArtistsStripMode && root.sectionVisible("artists", artistsModel.count)
                    width: parent.width; spacing: 12
                    property int cols: root.gridCols(190, spacing, artistsModel.count, width)
                    property real cardW: (width - (cols - 1) * spacing) / cols
                    Repeater {
                        model: artistsModel
                        delegate: Loader {
                            width: artistFlow.cardW
                            height: item ? item.implicitHeight : width + 142
                            // Complement of the strip: live only when NOT in strip mode.
                            active: !root.searchArtistsStripMode
                            asynchronous: root.searchBuilding
                            opacity: root.searchReveal
                            onLoaded: root._searchBuildTick()
                            sourceComponent: ArtistSearchCard {
                                aArt: model.art; aName: model.name; aPop: model.popularity; aId: model.id
                            }
                        }
                    }
                }
                ShowAllLabel {
                    opacity: root.searchReveal
                    sectionTop: artistsHead
                    // Offer SHOW ALL only when the strip overflows (there is more
                    // to reveal than fits); SHOW LESS collapses the grid back to
                    // the strip. Mixed (All) view only.
                    visible: root.filterType === "all" && (root.searchArtistsExpanded || artistStrip.contentWidth > artistStrip.width + 1)
                    expanded: root.searchArtistsExpanded
                    count: artistsModel.count
                    onToggled: root.toggleSearchSection("artists")
                }

                // ALBUMS
                SectionHeader { id: albumsHead; opacity: root.searchReveal; visible: root.sectionVisible("albums", albumsModel.count); label: "ALBUMS"; count: albumsModel.count }
                Repeater {
                    model: albumsModel
                    delegate: Loader {
                        // The section filter must hide the LOADER (the Column
                        // child); an invisible item inside a sized Loader would
                        // still occupy its row. In the mixed All view only the
                        // first 5 show until SHOW ALL; the delegate still loads
                        // (and fires its build-veil tick) while hidden, so the
                        // one-tick-per-item count stays exact.
                        visible: root.searchRowVisible("albums", albumsModel.count, index, root.searchAlbumsExpanded)
                        width: contentCol.width
                        asynchronous: root.searchBuilding
                        opacity: root.searchReveal
                        onLoaded: root._searchBuildTick()
                        sourceComponent: AlbumBlock {
                            albumId: model.id; title: model.title; artistName: model.artist; artistId: model.artist_id
                            art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; durationSec: model.duration_sec || 0; quality: model.quality; popularity: model.popularity
                        }
                    }
                }
                SearchSectionMore { section: "albums"; sectionTop: albumsHead; count: albumsModel.count; expanded: root.searchAlbumsExpanded }

                // TRACKS
                SectionHeader { id: tracksHead; opacity: root.searchReveal; visible: root.sectionVisible("tracks", tracksModel.count); label: "TRACKS"; count: tracksModel.count }
                Repeater {
                    model: tracksModel
                    delegate: Loader {
                        visible: root.searchRowVisible("tracks", tracksModel.count, index, root.searchTracksExpanded)
                        width: contentCol.width
                        asynchronous: root.searchBuilding
                        opacity: root.searchReveal
                        onLoaded: root._searchBuildTick()
                        sourceComponent: TrackRow {
                            tId: model.id; title: model.title; artistName: model.artist; artistId: model.artist_id
                            album: model.album; art: model.art; year: model.year; date: model.date; duration: model.duration; durationSec: model.duration_sec || 0; quality: model.quality; popularity: model.popularity
                            albumId: model.album_id || ""
                        }
                    }
                }
                SearchSectionMore { section: "tracks"; sectionTop: tracksHead; count: tracksModel.count; expanded: root.searchTracksExpanded }

                // VIDEOS
                SectionHeader { id: videosHead; opacity: root.searchReveal; visible: root.sectionVisible("videos", videosModel.count); label: "VIDEOS"; count: videosModel.count }
                // Art-first video results: 16:9 thumbnails at grid size, with
                // the title, artist, release date and a full download button
                // reading underneath. Cells are sized from the section width,
                // so the column count follows the window.
                Flow {
                    id: videoGrid
                    width: contentCol.width
                    spacing: 18
                    readonly property int cols: Math.max(2, Math.floor(width / 320))
                    readonly property real cellW: (width - (cols - 1) * spacing) / cols
                    Repeater {
                        model: videosModel
                        delegate: Loader {
                            visible: root.searchRowVisible("videos", videosModel.count, index, root.searchVideosExpanded)
                            width: videoGrid.cellW
                            height: Math.round(videoGrid.cellW * 9 / 16) + 54
                            asynchronous: root.searchBuilding
                            opacity: root.searchReveal
                            onLoaded: root._searchBuildTick()
                            sourceComponent: VideoCell {
                                width: videoGrid.cellW
                                vid: model.id; vcTitle: model.title; vcArtist: model.artist
                                artUrl: model.art; artBigUrl: model.art_big || ""
                                vcDuration: model.duration
                                vcExplicit: model.explicit === true
                                vcSpec: model.quality || ""
                                vcDate: model.date
                            }
                        }
                    }
                }
                SearchSectionMore { section: "videos"; sectionTop: videosHead; count: videosModel.count; expanded: root.searchVideosExpanded }

                // PLAYLISTS
                SectionHeader { id: playlistsHead; opacity: root.searchReveal; visible: root.sectionVisible("playlists", playlistsModel.count); label: "PLAYLISTS"; count: playlistsModel.count }
                Repeater {
                    model: playlistsModel
                    delegate: Loader {
                        visible: root.searchRowVisible("playlists", playlistsModel.count, index, root.searchPlaylistsExpanded)
                        width: contentCol.width
                        asynchronous: root.searchBuilding
                        opacity: root.searchReveal
                        // Reserve the collapsed row's height while the async
                        // build runs (same rationale as the artist strip's
                        // fixed cell): without it the section collapses to
                        // zero and pops open as each row lands.
                        height: item ? item.implicitHeight : 64
                        onLoaded: root._searchBuildTick()
                        sourceComponent: PlaylistBlock {
                            plId: model.id; title: model.title; creator: model.creator || ""
                            art: model.art; trackCount: model.tracks
                        }
                    }
                }
                SearchSectionMore { section: "playlists"; sectionTop: playlistsHead; count: playlistsModel.count; expanded: root.searchPlaylistsExpanded }

                // MIXES
                SectionHeader { id: mixesHead; opacity: root.searchReveal; visible: root.sectionVisible("mixes", mixesModel.count); label: "MIXES"; count: mixesModel.count }
                Repeater {
                    model: mixesModel
                    delegate: Loader {
                        visible: root.searchRowVisible("mixes", mixesModel.count, index, root.searchMixesExpanded)
                        width: contentCol.width; height: 66
                        asynchronous: root.searchBuilding
                        opacity: root.searchReveal
                        onLoaded: root._searchBuildTick()
                        sourceComponent: Rectangle {
                        radius: 10; color: root.surface; border.color: root.border1
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 13
                            Art {
                                width: 46; height: 46; hoverFx: true
                                fxKind: "mix"; fxId: "" + (model.id || "")
                                url: model.art
                            }
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
                SearchSectionMore { section: "mixes"; sectionTop: mixesHead; count: mixesModel.count; expanded: root.searchMixesExpanded }
            }
        }

        // ---- Artist page -----------------------------------------------
        ColumnLayout {
            id: artistPane
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 8
            visible: root.artistOpen && !root.settingsOpen && !root.libraryOpen
            // No gap between the back bar and the scroll area (rows would be
            // cut off floating over raw background); the 8px lives inside
            // artistView (artistCol y) instead.
            spacing: 0

            // Sticky crumb trail, stays put while the page scrolls: the whole
            // navHistory as pills, this artist as the last, lit one.
            Item {
                Layout.fillWidth: true; Layout.leftMargin: 22; implicitHeight: 26
                clip: true
                NavCrumbTrail {
                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                    width: Math.min(implicitWidth, parent.width)
                }
            }

            Flickable {
                id: artistView
                Layout.fillWidth: true; Layout.fillHeight: true
                clip: true
                // Hold-in-place restore while a Back restore is armed, same
                // reasoning as BrowseScroll: the first frame lands on the saved
                // spot and content fills in around it, never a visible ratchet.
                readonly property real restorePad: pendingRestoreY >= 0
                    ? Math.max(0, pendingRestoreY + height - realContentH) : 0
                readonly property real realContentH: artistCol.height + 32
                contentWidth: width; contentHeight: realContentH + restorePad
                ScrollBar.vertical: ScrollBar {}
                boundsBehavior: Flickable.StopAtBounds
                // A fresh artist page starts at the top (it used to inherit the
                // previous artist's scroll offset); a Back that arms a restore
                // lands on the saved spot instead. Same pre-paint mechanism as
                // BrowseScroll: the restore is tagged with the artist id it
                // belongs to and applied on the id change and during layout
                // (onContentHeightChanged), before the frame paints, so Back
                // never visibly jumps. Explicit contentY writes (openSearch,
                // navBack same-artist) still win: they run after the id change.
                readonly property string _artistKey: root.artistData ? "" + (root.artistData.id || "") : ""
                property real pendingRestoreY: -1
                property string pendingRestoreKey: ""
                // mayDisarm: as on BrowseScroll, only a real layout pass may spend
                // the restore. On the id change contentHeight still belongs to
                // the outgoing page, so a tall one would disarm it before the
                // incoming artist has laid out.
                function applyRestore(mayDisarm) {
                    if (pendingRestoreY < 0 || _artistKey !== pendingRestoreKey) return
                    contentY = Math.min(pendingRestoreY, Math.max(0, contentHeight - height))
                    // Real content only (pad excluded), as on BrowseScroll.
                    if (mayDisarm && Math.max(0, realContentH - height) >= pendingRestoreY)
                        pendingRestoreY = -1   // reached the target
                }
                // Pin the spot across an in-place revalidate swap of the same
                // artist, exactly as BrowseScroll.holdScroll does.
                function holdScroll() {
                    if (!visible || contentY <= 0 || moving) return
                    pendingRestoreKey = _artistKey
                    pendingRestoreY = contentY
                }
                on_ArtistKeyChanged: {
                    if (pendingRestoreY >= 0 && _artistKey === pendingRestoreKey) applyRestore(false)
                    else contentY = 0
                }
                onPendingRestoreYChanged: if (pendingRestoreY >= 0) artistRestoreGiveUp.restart()
                // Real height, not the padded contentHeight: see BrowseScroll.
                onRealContentHChanged: applyRestore(true)
                Timer {
                    id: artistRestoreGiveUp; interval: 800
                    onTriggered: { artistView.pendingRestoreY = -1; artistView.returnToBounds() }
                }

                Column {
                    id: artistCol
                    x: 22; y: 8; width: artistView.width - 44; spacing: 12

                    // Artist header, bio sits to the right of the photo, capped
                    // to the photo height, with Read more for the rest.
                Row {
                    width: parent.width; spacing: 20
                    Column {
                        spacing: 10
                        Art {
                            id: artistArt
                            width: 150; height: 150; hoverFx: true
                            fxKind: "artist"; fxId: "" + (root.artistData.id || "")
                            url: root.artistData.art || ""
                        }
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
                        Text {
                            textFormat: Text.PlainText; text: root.artistData.name || ""
                            color: root.textHi; font.pixelSize: 30; font.bold: true
                            width: parent.width
                            elide: Text.ElideRight
                        }
                        // What you already hold by this artist, between the
                        // name and the button that would add to it: the cards'
                        // two voices standing on the page rather than on the
                        // photograph.
                        ArtistBadges {
                            artistName: root.artistData.name || ""
                        }
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

                // Top tracks: first 5 only, SHOW ALL reveals the rest. Both
                // the fold and the expansion persist across artists (prefs).
                SectionHeader {
                    id: topTracksHead
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
                        album: model.album; art: model.art; year: model.year; date: model.date; duration: model.duration; durationSec: model.duration_sec || 0; quality: model.quality; popularity: model.popularity
                        albumId: model.album_id || ""
                    }
                }
                ShowAllLabel {
                    visible: !root.artistTracksCollapsed && artistTracksModel.count > 5
                    sectionTop: topTracksHead
                    expanded: root.topTracksExpanded
                    count: artistTracksModel.count
                    onToggled: root.toggleArtistExpand("tracks")
                }

                // Albums (expand inline): first 5, SHOW ALL for the rest, the
                // expansion remembered across artists like every section here.
                SectionHeader {
                    id: artistAlbumsHead
                    visible: artistAlbumsModel.count > 0
                    label: "ALBUMS"; count: artistAlbumsModel.count
                    collapsible: true; collapsed: root.artistAlbumsCollapsed
                    onToggled: root.toggleArtistSection("albums")
                }
                Repeater {
                    model: root.artistAlbumsCollapsed ? null : artistAlbumsModel
                    delegate: AlbumBlock {
                        required property var model
                        required property int index
                        visible: index < 5 || root.artistAlbumsExpanded
                        width: artistCol.width
                        albumId: model.id; title: model.title; artistName: model.artist; artistId: ""
                        art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; durationSec: model.duration_sec || 0; quality: model.quality; popularity: model.popularity
                    }
                }
                ShowAllLabel {
                    visible: !root.artistAlbumsCollapsed && artistAlbumsModel.count > 5
                    sectionTop: artistAlbumsHead
                    expanded: root.artistAlbumsExpanded
                    count: artistAlbumsModel.count
                    onToggled: root.toggleArtistExpand("albums")
                }

                // EPs & singles: same first-5 overview.
                SectionHeader {
                    id: artistEpsHead
                    visible: artistEpModel.count > 0
                    label: "EPS & SINGLES"; count: artistEpModel.count
                    collapsible: true; collapsed: root.artistEpsCollapsed
                    onToggled: root.toggleArtistSection("eps")
                }
                Repeater {
                    model: root.artistEpsCollapsed ? null : artistEpModel
                    delegate: AlbumBlock {
                        required property var model
                        required property int index
                        visible: index < 5 || root.artistEpsExpanded
                        width: artistCol.width
                        albumId: model.id; title: model.title; artistName: model.artist; artistId: ""
                        art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; durationSec: model.duration_sec || 0; quality: model.quality; popularity: model.popularity
                    }
                }
                ShowAllLabel {
                    visible: !root.artistEpsCollapsed && artistEpModel.count > 5
                    sectionTop: artistEpsHead
                    expanded: root.artistEpsExpanded
                    count: artistEpModel.count
                    onToggled: root.toggleArtistExpand("eps")
                }

                // Videos: the same art-first grid as the search results, at
                // the bottom of the page. The preview shows whole rows only
                // (at least 6: two rows of 3 at the usual width), because a
                // grid cut off at 5 leaves a hole where the sixth cell goes;
                // SHOW ALL reveals the rest, same as Top tracks, and the
                // collapse persists across artist pages.
                SectionHeader {
                    id: artistVideosHead
                    visible: artistVideosModel.count > 0
                    label: "VIDEOS"; count: artistVideosModel.count
                    collapsible: true; collapsed: root.artistVideosCollapsed
                    onToggled: root.toggleArtistSection("videos")
                    // Videos-only download-all (chosen in the videos_dl_lab):
                    // deliberately independent of the "Music videos"
                    // discography toggle, this button IS the explicit intent.
                    // The "vids:" media id matches the backend's
                    // _VIDEOS_GROUP_PREFIX group, so queued / running with
                    // rollup % / done / failed all come from the shared
                    // artist-group machinery.
                    trailing: Component {
                        DownloadButton {
                            mediaId: "vids:" + root.artistData.id
                            label: "All videos"
                            onTap: function(){ waves.downloadArtistVideos(root.artistData.id) }
                        }
                    }
                }
                Flow {
                    id: artistVideoGrid
                    width: artistCol.width
                    spacing: 18
                    readonly property int cols: Math.max(2, Math.floor(width / 320))
                    readonly property real cellW: (width - (cols - 1) * spacing) / cols
                    readonly property int fillCount: Math.ceil(6 / cols) * cols
                    Repeater {
                        model: root.artistVideosCollapsed ? null : artistVideosModel
                        delegate: VideoCell {
                            required property var model
                            required property int index
                            visible: index < artistVideoGrid.fillCount || root.artistVideosExpanded
                            width: artistVideoGrid.cellW
                            vid: model.id; vcTitle: model.title; vcArtist: model.artist
                            artUrl: model.art; artBigUrl: model.art_big || ""
                            vcDuration: model.duration
                            vcExplicit: model.explicit === true
                            vcSpec: model.quality || ""
                            vcDate: model.date
                        }
                    }
                }
                ShowAllLabel {
                    visible: !root.artistVideosCollapsed && artistVideosModel.count > artistVideoGrid.fillCount
                    sectionTop: artistVideosHead
                    expanded: root.artistVideosExpanded
                    count: artistVideosModel.count
                    onToggled: root.toggleArtistExpand("videos")
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
            onResetSettingsRequested: root.confirmSettingsReset = true
            onFactoryResetRequested: root.confirmFactoryReset = true
        }

        // ---- Library page ----------------------------------------------
        ColumnLayout {
            id: libraryPane
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.topMargin: 8
            visible: root.libraryOpen
            // No gap between the header row and the lists (see artistPane);
            // each list carries its own 8px inside the scroll area.
            spacing: 0

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
                    id: libAlbumsList
                    cat: "albums"; model: libAlbumsModel
                    delegate: AlbumBlock {
                        required property var model
                        width: ListView.view.width
                        albumId: model.id; title: model.title; artistName: model.artist; artistId: ""
                        art: model.art; year: model.year; releaseDate: model.date; trackCount: model.tracks; durationSec: model.duration_sec || 0; quality: model.quality; popularity: model.popularity
                    }
                }
                LibList {
                    id: libTracksList
                    cat: "tracks"; model: libTracksModel
                    delegate: TrackRow {
                        required property var model
                        width: ListView.view.width
                        tId: model.id; title: model.title; artistName: model.artist; artistId: model.artist_id
                        album: model.album; art: model.art; year: model.year; date: model.date; duration: model.duration; durationSec: model.duration_sec || 0; quality: model.quality; popularity: model.popularity
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
                    reuseItems: true
                    boundsBehavior: Flickable.StopAtBounds
                    // Breathing space inside the scroll area (see BrowseScroll).
                    header: Item { width: 1; height: 8 }
                    ScrollBar.vertical: ScrollBar {}
                    // Same revalidate scroll pinning as LibList (this grid is
                    // the one category pane that isn't a LibList).
                    property real pendingY: -1
                    function applyRestore() {
                        if (pendingY < 0) return
                        var maxY = Math.max(0, contentHeight - height)
                        contentY = Math.min(pendingY, maxY)
                        if (maxY >= pendingY) pendingY = -1
                    }
                    onMovementStarted: pendingY = -1
                    onContentYChanged: root.libMaybeLoadMore(libArtistsGrid, "artists")
                    onContentHeightChanged: { applyRestore(); root.libMaybeLoadMore(libArtistsGrid, "artists") }
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
                                    Art {
                                        anchors.centerIn: parent
                                        width: parent.width; height: width; hoverFx: true
                                        fxKind: "artist"; fxId: "" + (model.id || "")
                                        url: model.art
                                        // The one place you browse artists you
                                        // follow should not be the one place that
                                        // cannot say what you already hold. A
                                        // child of the Art, so it takes the tilt
                                        // and the rounded clip with it.
                                        ArtistBadges {
                                            bar: true
                                            width: parent.width
                                            anchors.bottom: parent.bottom
                                            artistName: "" + (model.name || "")
                                        }
                                    }
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
                    contentWidth: width; contentHeight: homeCol.height + 32
                    ScrollBar.vertical: ScrollBar {}
                    boundsBehavior: Flickable.StopAtBounds
                    Column {
                        id: homeCol
                        // y matches the favourites lists' 8px in-scroll padding
                        // exactly, or the content would jump vertically when
                        // switching between the category tabs.
                        y: 8
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
                                    // Local terms, not `visible` (effective visibility
                                    // tears cards down on tab leave, rebuilds them in
                                    // the returning click's turn; see the Browse shelves).
                                    model: homeSec.modelData.rowKind === "cards" ? homeSec.modelData.items : []
                                    delegate: ArtCard {
                                        required property var modelData
                                        card: modelData
                                    }
                                    ShelfWheelRedirect { pane: homePane }
                                    ShelfEdgeFades {}
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
                                            duration: modelData.duration || ""; durationSec: modelData.duration_sec || 0; quality: modelData.quality || ""; popularity: modelData.popularity || 0
                                            albumId: modelData.album_id || ""
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                LibList {
                    id: libPlaylistsList
                    cat: "playlists"; model: libPlaylistsModel
                    // Hidden (state intact, scroll kept) while drilled into a
                    // folder; the folder view below takes over.
                    visible: root.libraryCategory === "playlists" && root.plFolderStack.length === 0
                    delegate: LibPlaylistRow {}
                }
                // Drilled-into playlist folder: pinned breadcrumb strip + the
                // folder's rows (subfolders first). Served from the cached
                // sweep, so landing is instant and already positioned.
                Item {
                    visible: root.libraryCategory === "playlists" && root.plFolderStack.length > 0
                    anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 22
                    Flow {
                        id: plCrumbStrip
                        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
                        anchors.topMargin: 8
                        spacing: 6
                        Rectangle {
                            radius: 8; implicitHeight: 26; implicitWidth: crumbRootTx.implicitWidth + 20
                            color: root.surface2; border.color: root.border1
                            Text {
                                id: crumbRootTx; anchors.centerIn: parent
                                textFormat: Text.PlainText; text: "Playlists"
                                color: root.textLo; font.pixelSize: 12
                            }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.plCrumbTo(-1) }
                        }
                        Repeater {
                            model: root.plFolderStack
                            delegate: Row {
                                id: crumbSeg
                                required property var modelData
                                required property int index
                                spacing: 6
                                readonly property bool last: index === root.plFolderStack.length - 1
                                Text {
                                    textFormat: Text.PlainText; text: "›"
                                    color: root.textDim; font.pixelSize: 13
                                    anchors.verticalCenter: parent.verticalCenter
                                }
                                Rectangle {
                                    radius: 8; implicitHeight: 26; implicitWidth: crumbTx.implicitWidth + 20
                                    color: crumbSeg.last ? root.accentCont : root.surface2
                                    border.color: crumbSeg.last ? root.accentDim : root.border1
                                    Text {
                                        id: crumbTx; anchors.centerIn: parent
                                        textFormat: Text.PlainText; text: crumbSeg.modelData.title
                                        color: crumbSeg.last ? root.accent : root.textLo
                                        font.pixelSize: 12; font.bold: crumbSeg.last
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        enabled: !crumbSeg.last
                                        onClicked: root.plCrumbTo(crumbSeg.index)
                                    }
                                }
                            }
                        }
                    }
                    ListView {
                        id: libFolderList
                        anchors.top: plCrumbStrip.bottom; anchors.topMargin: 8
                        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                        clip: true; spacing: 8
                        cacheBuffer: 800
                        boundsBehavior: Flickable.StopAtBounds
                        ScrollBar.vertical: ScrollBar {}
                        header: Item { width: 1; height: 8 }
                        footer: Item { width: 1; height: 20 }
                        model: libFolderModel
                        delegate: LibPlaylistRow {}
                        // Land already positioned (crumb-back restores the
                        // saved offset before paint; a fresh drill-in lands at
                        // 0): the no-visible-scroll rule.
                        property real pendingY: -1
                        function applyRestore() {
                            if (pendingY < 0) return
                            var maxY = Math.max(0, contentHeight - height)
                            contentY = Math.min(pendingY, maxY)
                            if (maxY >= pendingY) pendingY = -1
                        }
                        onContentHeightChanged: applyRestore()
                    }
                }
                LibList {
                    id: libMixesList
                    cat: "mixes"; model: libMixesModel
                    delegate: Rectangle {
                        required property var model
                        width: ListView.view.width; height: 64; radius: 10; color: root.surface; border.color: root.border1
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 13
                            Art {
                                width: 44; height: 44; hoverFx: true
                                fxKind: "mix"; fxId: "" + (model.id || "")
                                url: model.art
                            }
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
                    id: libVideosList
                    cat: "videos"; model: libVideosModel
                    delegate: Rectangle {
                        required property var model
                        width: ListView.view.width; height: 50; color: "transparent"
                        Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: root.divider }
                        RowLayout {
                            anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; spacing: 12
                            VideoThumb { url: model.art; videoId: model.id; vTitle: model.title; vArtist: model.artist }
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
            // Universal seek on the bottom playback line (seek-lab "spark"):
            // no expansion, so casual mouse travel can't trigger anything by
            // accident. The hit zone reaches 16px up from the edge so seeking
            // never means aiming at the window's resize-grab strip; an aim
            // tick + time flag ride the cursor and the single real seek fires
            // on release. Declared FIRST among the bar's children so the
            // status text and the mini player stack above it and keep their
            // clicks; presses land here only where nothing else claims them.
            Item {
                visible: root.previewKind !== "" && root.previewDuration > 0
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
                height: 16
                MouseArea {
                    id: bottomSeekMa
                    anchors.fill: parent
                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    preventStealing: true
                    property bool scrubbing: false
                    function frac(mx) { return width > 0 ? Math.max(0, Math.min(1, mx / width)) : 0 }
                    onPressed: function(m){ m.accepted = true; scrubbing = true; root.previewScrubbing = true; root.scrubPreviewVisual(frac(m.x)) }
                    onPositionChanged: function(m){ if (scrubbing) root.scrubPreviewVisual(frac(m.x)) }
                    onReleased: function(m){ if (scrubbing) { scrubbing = false; root.previewScrubbing = false; root.seekPreview(frac(m.x)) } }
                    onCanceled: { scrubbing = false; root.previewScrubbing = false }
                }
                // aim tick + time flag under the cursor
                Rectangle {
                    visible: bottomSeekMa.containsMouse || bottomSeekMa.scrubbing
                    x: bottomSeekMa.mouseX; anchors.bottom: parent.bottom
                    width: 1; height: 10; color: root.accentSoft; opacity: 0.8
                }
                Rectangle {
                    visible: bottomSeekMa.containsMouse || bottomSeekMa.scrubbing
                    x: Math.max(4, Math.min(parent.width - width - 4, bottomSeekMa.mouseX - width / 2))
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 16
                    width: bottomSeekTxt.implicitWidth + 12; height: 16; radius: 4
                    color: root.surfaceHi; border.color: root.border1
                    Text {
                        id: bottomSeekTxt
                        anchors.centerIn: parent; textFormat: Text.PlainText
                        text: root.fmtMs(bottomSeekMa.frac(bottomSeekMa.mouseX) * root.previewDuration)
                        color: root.accentContTx; font.family: root.mono; font.pixelSize: 9
                    }
                }
            }
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
                RowLayout {
                    id: statusMark
                    visible: !root.appUpdAvailable
                    spacing: 0
                    opacity: root.previewKind !== "" ? 0 : 1
                    Behavior on opacity { NumberAnimation { duration: 180 } }
                    // "checking" while the probe is out, "current" for a few
                    // seconds after a check that found nothing. A check that
                    // DOES find something needs no state here: appUpdAvailable
                    // flips and the gold notice takes this slot instead.
                    property string verState: ""
                    Text {
                        textFormat: Text.PlainText
                        text: "TIDAL · WAVES CONSOLE · "
                        color: root.textDim; font.family: root.mono; font.pixelSize: 11; font.letterSpacing: 0.5
                    }
                    Text {
                        id: verMark
                        textFormat: Text.PlainText
                        // Hover only brightens: swapping in a longer label would
                        // shove the whole wordmark sideways under the cursor.
                        // The transient check states are allowed to resize, they
                        // follow a deliberate click.
                        text: statusMark.verState === "checking" ? "CHECKING…"
                            : statusMark.verState === "current" ? "UP TO DATE"
                            : "v" + waves.appVersion
                        color: statusMark.verState === "current" ? root.accent
                             : verMa.containsMouse ? root.textLo : root.textDim
                        font.family: root.mono; font.pixelSize: 11; font.letterSpacing: 0.5
                        Behavior on color { ColorAnimation { duration: 140 } }
                        MouseArea {
                            id: verMa
                            anchors.fill: parent; anchors.margins: -4
                            hoverEnabled: true
                            cursorShape: statusMark.verState === "checking" ? Qt.ArrowCursor
                                                                           : Qt.PointingHandCursor
                            // The same check the Settings card runs. Not marked
                            // manual: the user is nowhere near the updater, so a
                            // found update should still raise its toast.
                            onClicked: {
                                if (statusMark.verState === "checking") return
                                statusMark.verState = "checking"
                                waves.checkAppUpdate(false)
                            }
                        }
                    }
                    // A check that comes back empty says so, then goes quiet.
                    Timer {
                        id: verSettle
                        interval: 3200
                        onTriggered: statusMark.verState = ""
                    }
                    Connections {
                        target: waves
                        function onAppUpdateChecked(available, current, latest, manual) {
                            if (statusMark.verState !== "checking") return
                            statusMark.verState = available ? "" : "current"
                            if (!available) verSettle.restart()
                        }
                    }
                }
            }
            // Now playing, persists across every view so playback can always be
            // paused/stopped, and clicking the title/artist jumps back to the
            // artist page (and expands the track's album). One shared player.
            // It docks to the bar's right corner whenever that corner is free
            // (the wordmark hides while playing) and slides to the centre only
            // when the update notice needs the corner, one animated move each
            // way so the controls never look like they are bouncing around.
            // Hovering anywhere on the bottom bar (not just the ✕) reveals the
            // mini player's "[stop]" label while something is playing.
            HoverHandler { id: npBarHover }
            Row {
                id: nowPlaying
                anchors.verticalCenter: parent.verticalCenter
                x: statusUpdate.visible ? (statusBar.width - width) / 2
                                        : statusBar.width - width - 22
                Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutQuad } }
                spacing: 9
                visible: root.previewKind !== ""
                // Fixed box so swapping > / || / … never nudges the art + text. The
                // play caret is drawn larger so it stands at the pause bars' height.
                Item {
                    anchors.verticalCenter: parent.verticalCenter
                    // Always 20px. The transient "[buffering]" label paints
                    // outside the box (leftward, over open bar space) so its
                    // arrival and exit never resize the row: a resize moves the
                    // right-docked row's x, and with the animated dock the whole
                    // art + titles visibly slid when the stream became ready
                    // (livetest: looked like the bar re-animating in).
                    width: 20; height: 20
                    Text {
                        id: npGlyph
                        anchors.centerIn: parent
                        textFormat: Text.PlainText
                        text: root.previewPlaying ? "||" : ">"
                        color: root.accent; font.family: root.mono; font.bold: true
                        font.pixelSize: root.previewPlaying ? 13 : 18
                        // Steps aside for the buffering label, but only when
                        // that label is actually showing: in a window too tight
                        // for it the caret stays, so the box is never blank.
                        opacity: (root.previewLoading && npBuffer.visible) ? 0 : 1
                        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.InOutSine } }
                    }
                    Text {
                        id: npBuffer
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.right: parent.right
                        textFormat: Text.PlainText
                        text: "[buffering]"
                        color: root.accent; font.family: root.mono; font.bold: true
                        font.pixelSize: 10
                        // It paints leftward, outside the box, into whatever bar
                        // space is open between the status text and the row. That
                        // space is not guaranteed: a narrow window with a long
                        // title leaves less than the label needs, and the label
                        // would print over the status text. Show it only when it
                        // fits; the caret takes the box back when it does not.
                        // Measured against the status text's REAL right edge (39
                        // is where it starts: margin + busy dot + spacing) and not
                        // against npInfo.leftGuard, which caps that width at a
                        // fifth of the bar for its own budgeting.
                        readonly property bool fits: (nowPlaying.x - 39 - statusText.width - 12) >= implicitWidth
                        visible: root.previewLoading && fits
                        property real breathe: 1
                        opacity: breathe
                        SequentialAnimation on breathe {
                            // Gated on visible, not previewLoading alone: when
                            // the label does not fit, animating the opacity of
                            // a hidden item would still dirty the scene every
                            // frame for the whole buffering period.
                            running: npBuffer.visible; loops: Animation.Infinite
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
                    // Docked right (no update notice) the row spans from the
                    // status text's guard to the bar's right margin; centred
                    // (notice up) it is bounded symmetrically by the wider side.
                    readonly property real budget: Math.max(120, statusUpdate.visible
                        ? statusBar.width - 2 * Math.max(leftGuard, rightGuard) - fixedParts
                        : statusBar.width - leftGuard - 22 - fixedParts)
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
            // Playback position line along the very bottom edge, 4px so the
            // seek target sits clear of the window's resize-grab edge, with a
            // phosphor spark breathing at the playhead on the shared LED clock.
            Rectangle {
                id: bottomSeekLine
                anchors.bottom: parent.bottom; anchors.left: parent.left
                height: 4; color: root.accent
                visible: root.previewKind !== "" && root.previewDuration > 0
                width: parent.width * root.pvFrac(root.previewKind, root.previewId)
            }
            Rectangle {
                visible: bottomSeekLine.visible
                x: bottomSeekLine.width - width / 2; anchors.bottom: parent.bottom
                width: 5; height: 5; radius: 1
                color: root.accentSoft
                opacity: 0.35 + 0.65 * root.ledPulse
            }
        }
    }

    // Clicking anywhere outside a focused text field releases its focus (caret
    // stops blinking, outline fades back), the way native fields behave. Covers
    // the search box and every settings path field alike. A PointHandler, and
    // specifically NOT the two obvious alternatives, both tried and measured
    // (scratchpad/focus_catcher_probe.py): a full-window MouseArea carries the
    // default Arrow cursor and overrode every button's pointing hand while the
    // search field held focus (which openSearch's auto-focus makes permanent on
    // that page), and a TapHandler SWALLOWS the click, so navigating away from
    // search took one click to unfocus and a second to actually land. A
    // PointHandler only observes: the same press both releases focus and
    // reaches the control under it, and no cursor is overridden.
    Item {
        id: focusDismissCatcher
        anchors.fill: parent; z: 1000
        PointHandler {
            enabled: {
                var i = root.activeFocusItem
                return i !== null && (i instanceof TextInput || i instanceof TextEdit)
            }
            acceptedButtons: Qt.AllButtons
            onActiveChanged: {
                if (!active) return
                var t = root.activeFocusItem
                if (!t || !(t instanceof TextInput || t instanceof TextEdit)) return
                // The search field's bounds are its whole styled box, so a click
                // on the box chrome (paste glyph and friends) keeps focus.
                var bounds = (t === searchField) ? searchBox : t
                var p = focusDismissCatcher.mapToItem(bounds, point.position.x, point.position.y)
                if (p.x < 0 || p.y < 0 || p.x > bounds.width || p.y > bounds.height)
                    t.focus = false
            }
        }
    }

    // Sort the original full search data (not a lossy model copy) so every
    // field, including the full date, survives re-sorting. One control, every
    // section that has the data: albums, tracks and videos all follow it.
    // Artists, playlists and mixes carry no date to sort by and stay in the
    // API's relevance order.
    function applySort() {
        var dir = root.sortAsc ? 1 : -1
        function ordered(raw, hasPop) {
            var arr = (raw || []).slice()
            if (sortBox.currentIndex === 1) arr.sort(function(a, b){ return dir * ((a.date || a.year || "").localeCompare(b.date || b.year || "")) })
            else if (sortBox.currentIndex === 2) arr.sort(function(a, b){ return dir * a.title.localeCompare(b.title) })
            else if (hasPop) arr.sort(function(a, b){ return dir * ((a.popularity || 0) - (b.popularity || 0)) })  // Relevance = popularity
            else if (root.sortAsc) arr.reverse()  // no popularity data: relevance is the API's order, the arrow flips it
            return arr
        }
        root.fillMedia(albumsModel, ordered(root.albumsRaw, true))
        root.fillMedia(tracksModel, ordered(root.tracksRaw, true))
        root.fillMedia(videosModel, ordered(root.videosRaw, false))
    }

    // ====================================================================
    // Queue drawer
    // ====================================================================
    Drawer {
        id: queueDrawer
        // 420, not the 340 it shipped at: the quality a row states is the
        // element that costs the title its width, and at 340 either the tier
        // or the title had to give way. It is the floor now rather than the
        // width, since the handle below drags it wider, and the clamp is
        // re-read here so shrinking the window pulls an over-wide drawer in.
        edge: Qt.RightEdge; height: root.height
        width: Math.max(420, Math.min(root.width - 80, root.queueWidth))
        background: Rectangle { color: root.surface; border.color: root.line1 }
        ColumnLayout {
            anchors.fill: parent; anchors.margins: 16; spacing: 12
            RowLayout {
                Layout.fillWidth: true
                Text { text: "Download queue"; color: root.textHi; font.pixelSize: 16; font.bold: true }
                Item { Layout.fillWidth: true }
                // Pause / resume all. Gold while running (pausing interrupts but
                // loses nothing), green once paused, where RESUME is the way
                // forward. Both are SpecBtn, so the fill, the border and the
                // hover lift are the exit prompt's, not a local imitation.
                SpecBtn {
                    visible: queueModel.count > 0
                    warn: !waves.paused
                    primary: waves.paused
                    label: waves.paused ? "RESUME" : "PAUSE"
                    onClicked: waves.paused ? waves.resumeQueue() : waves.pauseQueue()
                }
                // Stop everything, abort running downloads + clear the queue
                SpecBtn {
                    visible: root.activeQueueCount > 0
                    danger: true; label: "STOP"
                    onClicked: waves.stopAll()
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
                    // Two extra pixels of headroom so the header's chips are not
                    // squeezed against the rows above and below them.
                    implicitHeight: 34
                    RowLayout {
                        anchors.fill: parent; anchors.topMargin: 8; anchors.bottomMargin: 4
                        anchors.leftMargin: 2; anchors.rightMargin: 2; spacing: 8
                        // Above the header-wide collapse MouseArea below, so the
                        // per-section CLEAR gets the click. Plain Text does not
                        // consume events, so collapsing still works everywhere else.
                        z: 3
                        Ico { visible: secItem.section === "completed"; name: "check"; color: root.accent; size: 13; Layout.alignment: Qt.AlignVCenter }
                        Text {
                            textFormat: Text.PlainText
                            id: secLbl
                            text: secItem.section === "completed" ? "COMPLETED · " + root.completedCount
                                : secItem.section === "failed" ? "FAILED · " + root.failedCount
                                : secItem.section === "downloading" ? "DOWNLOADING · " + root.downloadingCount
                                : "QUEUED · " + root.queuedCount
                            // Brightness tracks how live the section is: the work
                            // happening right now reads near-white, what is only
                            // waiting stays dim, so the eye lands on Downloading
                            // first when the drawer opens.
                            color: secItem.section === "completed" ? root.accentContTx
                                 : secItem.section === "failed" ? root.red
                                 : secItem.section === "downloading" ? root.textHi : root.textDim
                            font.family: root.mono; font.pixelSize: 10; font.bold: true; font.letterSpacing: 1.4
                            Layout.alignment: Qt.AlignVCenter
                        }
                        Rectangle { Layout.fillWidth: true; Layout.alignment: Qt.AlignVCenter; height: 1; color: root.divider }
                        // One-click retry of every failed row, riding the Failed
                        // header itself so it appears exactly when it applies
                        // and takes no room anywhere else (issue #18).
                        SpecBtn {
                            compact: true; primary: true; label: "RETRY ALL"
                            visible: secItem.section === "failed"
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: waves.retryAllFailed()
                        }
                        // Every section clears itself. Riding the header means
                        // the scope needs no spelling out (it takes the section
                        // it sits on), so the control stays one short word in
                        // that section's own colour instead of a grey footer
                        // button that has to name what it sweeps. Downloading
                        // has none: stopping a live transfer is the row's own
                        // control, never a bulk one.
                        SpecBtn {
                            compact: true
                            // Completed is tidy-up, so it takes the green
                            // recipe; the other two discard work you asked for,
                            // which is the danger red the exit prompt uses.
                            primary: secItem.section === "completed"
                            danger: secItem.section !== "completed"
                            label: "CLEAR"
                            visible: secItem.section !== "downloading"
                            Layout.alignment: Qt.AlignVCenter
                            onClicked: {
                                if (secItem.section === "completed") waves.clearFinished()
                                else if (secItem.section === "failed") waves.clearFailed()
                                else waves.clearQueued()
                            }
                        }
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
                    // What this release is being fetched at. Once its tracks
                    // report, they are the truth: an album whose tracks did not
                    // all land at the same tier says MIXED and the expanded
                    // ledger itemizes it.
                    //
                    // Both of these come off the ROW, rolled up by the bridge
                    // from the registry it keeps for every job. They used to be
                    // computed here from root.queueTracks[qid], which is filled
                    // by loadQueueTracks: a network fetch, so it only runs when
                    // the user expands the row. A row nobody opened therefore
                    // went on advertising the tier it had ASKED for and could
                    // never say MIXED, which is precisely the row that needed
                    // to, since nobody was looking at its ledger.
                    readonly property var tierMix: JSON.parse(model.mixJson || "[]")
                    // The one tier they all landed at. It outranks the asked-for
                    // tier the moment the first track reports: a release with no
                    // hi-res master answers a HI-RES request in LOSSLESS from
                    // end to end, and no MIXED will ever catch that (one tier is
                    // not a mix).
                    readonly property string landed: "" + (model.landed || "")
                    readonly property string tier: tierMix.length > 1 ? ""
                                                 : landed !== "" ? landed
                                                 : ("" + (model.quality || ""))
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
                                // The quality the row is fetching at, beside the
                                // dismiss X and centred with it. Compact: the
                                // drawer has room for the tier word, not for the
                                // full spec behind it, and the expanded ledger
                                // states the per-track detail anyway. The DONE
                                // chip owns the spare width while it lingers.
                                QualTag {
                                    Layout.alignment: Qt.AlignVCenter
                                    visible: !doneChip.visible && (qrow.tier !== "" || qrow.tierMix.length > 1)
                                    compact: true
                                    q: qrow.tier
                                    mix: qrow.tierMix
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
                                    // The count, not the array: every live tick
                                    // (queueTrackState/Pct, 2 a second while an album
                                    // downloads) reassigns root.queueTracks wholesale,
                                    // and an array model tears down and rebuilds every
                                    // row per tick. Each rebuild costs a layout-settle
                                    // frame, which reads as the open sliver vibrating
                                    // under the pointer. With the count as the model
                                    // the rows stay alive and each one's texts
                                    // re-evaluate in place.
                                    model: qtrackClip.shown ? (root.queueTracks[qrow.model.qid] || []).length : 0
                                    delegate: RowLayout {
                                        required property int index
                                        // Depends on root.queueTracks, so each tick
                                        // refreshes this row's snapshot in place.
                                        readonly property var td: (root.queueTracks[qrow.model.qid] || [])[index] || ({})
                                        Layout.fillWidth: true; spacing: 8
                                        Text {
                                            textFormat: Text.PlainText
                                            text: (td.num || "") + ""
                                            color: root.textDim; font.family: root.mono; font.pixelSize: 10
                                            Layout.preferredWidth: 16; horizontalAlignment: Text.AlignRight
                                        }
                                        Text {
                                            textFormat: Text.PlainText
                                            text: td.title || ""
                                            // Grey until the track is actually
                                            // yours: a finished download or a
                                            // copy already in the library reads
                                            // bright, a failure reads red, the
                                            // rest stay quiet.
                                            color: td.status === "failed" ? root.red
                                                 : (td.status === "done" || td.status === "skipped") ? root.textHi
                                                 : root.textLo
                                            Behavior on color { ColorAnimation { duration: 260 } }
                                            font.pixelSize: 11; elide: Text.ElideRight; Layout.fillWidth: true
                                        }
                                        // The tier, in a column of its own so
                                        // qualities stack and scan vertically.
                                        // Faded while it is still only what the
                                        // job is asking for, full strength once
                                        // the file has landed at it.
                                        Text {
                                            objectName: "qTrackTier"
                                            textFormat: Text.PlainText
                                            readonly property string delivered: "" + (td.quality || "")
                                            readonly property string tier: root.queueTrackTier(
                                                delivered, "" + (td.status || ""), "" + (qrow.model.quality || ""))
                                            // Still only the request: there is a tier to
                                            // state and no file has landed at it yet.
                                            readonly property bool promised: delivered === "" && tier !== ""
                                            visible: tier !== ""
                                            text: tier
                                            color: root.qualFg(tier)
                                            opacity: promised ? 0.45 : 1
                                            Behavior on opacity { NumberAnimation { duration: 260 } }
                                            font.family: root.mono; font.pixelSize: 10
                                            Layout.preferredWidth: 56; horizontalAlignment: Text.AlignRight
                                        }
                                        // The outcome in words: a track you
                                        // already hold says so, rather than
                                        // spending the reader a glyph they have
                                        // to learn. "pending" is the state a
                                        // fetched track list starts in (see
                                        // queueTrackTier), so it reads QUEUED
                                        // like the tracks awaiting their turn,
                                        // never a blank dot. One overlapped
                                        // cell so a state switch crossfades:
                                        // a downloading track is the word
                                        // DOWNLOADING, faded, with the full
                                        // green pouring over it in lockstep
                                        // with the track's own progress.
                                        Item {
                                            id: scell
                                            readonly property bool dl: td.status === "running"
                                            // The stream finishing is not the work
                                            // finishing: extraction, tagging and the
                                            // move still run with the bar at 100.
                                            // Same threshold as the LED twinkle.
                                            readonly property bool fin: dl && (td.pct || 0) >= 99.9
                                            onDlChanged: if (dl && dlWord._live) dlWord.replay()
                                            onFinChanged: if (fin && finWord._live) finWord.replay()
                                            Layout.preferredWidth: 78
                                            implicitHeight: Math.max(sWord.implicitHeight, dlWord.implicitHeight)
                                            DecryptText {
                                                id: sWord
                                                objectName: "qTrackWord"
                                                anchors.right: parent.right
                                                anchors.verticalCenter: parent.verticalCenter
                                                opacity: scell.dl ? 0 : 1
                                                visible: opacity > 0
                                                Behavior on opacity { NumberAnimation { duration: 180 } }
                                                // UNAVAILABLE is TIDAL's answer,
                                                // not a failure of ours: it wears
                                                // the muted red so it reads as
                                                // "you did not get this one"
                                                // without the alarm of RETRY,
                                                // which would promise something a
                                                // retry cannot deliver.
                                                target: td.status === "done" ? "COMPLETED"
                                                    : td.status === "failed" ? "FAILED"
                                                    : td.status === "unavailable" ? "UNAVAILABLE"
                                                    : td.status === "skipped" ? "IN LIBRARY"
                                                    : td.status === "cancelled" ? "CANCELLED"
                                                    : (td.status === "queued" || td.status === "pending") ? "QUEUED"
                                                    : td.status === "running" ? "" : "·"
                                                color: td.status === "done" ? root.accent
                                                     : td.status === "failed" ? root.red
                                                     : td.status === "unavailable" ? root.redDim
                                                     : td.status === "skipped" ? root.libAccent : root.textDim
                                                Behavior on color { ColorAnimation { duration: 260 } }
                                                font.pixelSize: 10
                                                font.bold: target !== "·" && target !== "QUEUED" && target !== "CANCELLED"
                                            }
                                            Item {
                                                anchors.right: parent.right
                                                anchors.verticalCenter: parent.verticalCenter
                                                width: dlWord.implicitWidth; height: dlWord.implicitHeight
                                                opacity: scell.dl && !scell.fin ? 1 : 0
                                                visible: opacity > 0
                                                Behavior on opacity { NumberAnimation { duration: 260 } }
                                                DecryptText {
                                                    id: dlWord
                                                    // Faded green, warming as the
                                                    // download progresses, while the
                                                    // full-bright fill sweeps over it.
                                                    // Decrypts in each time the track
                                                    // starts downloading.
                                                    target: "DOWNLOADING"
                                                    color: Qt.alpha(root.accent, 0.28 + 0.4 * Math.max(0, Math.min(100, td.pct || 0)) / 100)
                                                    font.pixelSize: 10; font.bold: true
                                                }
                                                // The green ink pours over the faded
                                                // word in lockstep with the track's
                                                // own progress, smoothed between ticks.
                                                Item {
                                                    clip: true
                                                    anchors.left: parent.left
                                                    anchors.top: parent.top; anchors.bottom: parent.bottom
                                                    width: parent.width * Math.max(0, Math.min(100, td.pct || 0)) / 100
                                                    Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                                    Text {
                                                        textFormat: Text.PlainText
                                                        // Mirrors the scramble frames
                                                        // so the fill stays letter-
                                                        // aligned while it decodes.
                                                        text: dlWord.text; color: root.accent
                                                        font.family: root.mono; font.pixelSize: 10; font.bold: true
                                                    }
                                                }
                                            }
                                            // The stream landing hands the slot to
                                            // FINISHING: its own word with its OWN
                                            // fill riding fpct (the finalize steps:
                                            // extract, tag, move), so it opens
                                            // empty and earns its ink, instead of
                                            // inheriting the finished download's
                                            // full fill and draining backwards.
                                            Item {
                                                anchors.right: parent.right
                                                anchors.verticalCenter: parent.verticalCenter
                                                width: finWord.implicitWidth; height: finWord.implicitHeight
                                                opacity: scell.fin ? 1 : 0
                                                visible: opacity > 0
                                                Behavior on opacity { NumberAnimation { duration: 260 } }
                                                DecryptText {
                                                    id: finWord
                                                    target: "FINISHING"
                                                    color: Qt.alpha(root.accent, 0.28 + 0.4 * Math.max(0, Math.min(100, td.fpct || 0)) / 100)
                                                    font.pixelSize: 10; font.bold: true
                                                }
                                                Item {
                                                    clip: true
                                                    anchors.left: parent.left
                                                    anchors.top: parent.top; anchors.bottom: parent.bottom
                                                    width: parent.width * Math.max(0, Math.min(100, td.fpct || 0)) / 100
                                                    Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                                                    Text {
                                                        textFormat: Text.PlainText
                                                        text: finWord.text; color: root.accent
                                                        font.family: root.mono; font.pixelSize: 10; font.bold: true
                                                    }
                                                }
                                            }
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
                // One button, not two: the per-section CLEARs above handle the
                // targeted cases, so all this one owes the user is "empty the
                // list". It never touches a transfer already writing bytes,
                // which the row's own stop control is for.
                // Pushes the button to the trailing edge, under the section
                // headers' own controls, so the three CLEARs line up vertically.
                Item { Layout.fillWidth: true }
                // The exit prompt's own danger button, hugging its label: full
                // width is for modal CTAs, an inline button that stretches is
                // just a wide empty box.
                SpecBtn {
                    danger: true; label: "CLEAR ALL"
                    onClicked: waves.clearQueue()
                }
            }
        }

        // ---- Drag the drawer wider by its own edge ----------------------
        // The handle IS the border, never a bar beside it. `parent` here is the
        // Popup's own content item, which sits at leftPadding (the background's
        // 1px border), so undoing exactly that puts x=0 on the border line.
        // Declared after the ColumnLayout, which is what puts it on top; it
        // covers the layout's 16px left margin, where nothing else lives.
        Item {
            id: queueGrip
            anchors.left: parent.left
            anchors.leftMargin: -queueDrawer.leftPadding
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            width: 16
            z: 50

            // Where the grip point is on the edge (the spot the magnet glow
            // centres on). WRITTEN by the MouseArea below, never read off
            // mouseY: it must not follow the pointer during the drag (the
            // glow's squeeze stays on the spot that was taken hold of), and
            // letting go must leave it exactly where it was let go. Bound to
            // mouseY it did the opposite, snapping on release to wherever the
            // pointer had wandered to by then, or to a stale reading if the
            // pointer had left the 16px band altogether.
            property real handY: 0
            readonly property bool lit: gripMouse.containsMouse || gripMouse.pressed

            // True only while the grip point is being PLACED rather than
            // moved: the pointer arriving on the band, or taking hold of it.
            // Both are jumps to somewhere the light was not, and the light
            // belongs under the pointer at once; the smoothing below is for
            // travelling along the edge. Without this, grabbing the edge low
            // down after the light was last seen high up drags a comet
            // through the drag's first quarter second.
            property bool placing: false
            function placeHand(y) { placing = true; handY = y; placing = false }

            Rectangle { x: 0; width: 1; height: parent.height; color: root.line1 }

            // The edge lights where the pointer is before anything is grabbed, and
            // runs the full height once it is: the whole edge is the handle,
            // not one notch on it.
            Rectangle {
                id: gripGlow
                x: 0
                width: gripMouse.pressed ? 4 : 3
                // What the light is centred on: the grip point, held clear of the
                // very ends so a blob in a corner is not half off the edge.
                // The travel animation lives HERE rather than on y, because y
                // moves when the HEIGHT changes too, and the two disagreeing is
                // the whole bug: y was smoothed by distance (1600 px/s) while
                // the height was timed (220ms), so letting go near the bottom
                // of a tall window re-formed the finished blob up at the top
                // and then slid it down. Centre and height cannot disagree.
                property real centre: Math.max(35, Math.min(parent.height - 35, queueGrip.handY))
                Behavior on centre { enabled: !queueGrip.placing; SmoothedAnimation { velocity: 1600 } }
                // Grabbed, the light runs the whole edge: twice the drawer's
                // height, so its own fade stays off screen and only the bright
                // middle shows. Let go, it collapses back to a blob. Nothing
                // but the height changes between those two, and it changes
                // about the centre, so both ends travel the same distance in
                // the same time and the grip point keeps the middle throughout.
                height: gripMouse.pressed ? parent.height * 2 : 190
                y: centre - height / 2
                opacity: queueGrip.lit ? 1 : 0
                gradient: Gradient {
                    GradientStop { position: 0.0;  color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0) }
                    GradientStop { position: 0.42; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.93) }
                    GradientStop { position: 0.58; color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0.93) }
                    GradientStop { position: 1.0;  color: Qt.rgba(root.accent.r, root.accent.g, root.accent.b, 0) }
                }
                Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                Behavior on width { NumberAnimation { duration: 140 } }
                Behavior on opacity { NumberAnimation { duration: 180 } }
            }
            // The stretch being squeezed, brighter than the lit edge around it
            // so the grip still reads once the glow has gone full height.
            Rectangle {
                x: 0; width: 4
                height: gripMouse.pressed ? 56 : 0
                y: queueGrip.handY - height / 2
                color: root.accentSoft
                Behavior on height { NumberAnimation { duration: 130; easing.type: Easing.OutCubic } }
            }

            MouseArea {
                id: gripMouse
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.SplitHCursor
                // A horizontal drag is also the Drawer's own dismiss gesture,
                // which is the same axis this one drags in. Without it the
                // drawer slides shut instead of resizing.
                preventStealing: true
                onPressedChanged: root.queueEdgeHeld = pressed
                property real startScene: 0
                property real startWidth: 0
                // Hovering, the glow follows the pointer; holding, its centre
                // stays put and the pointer's job is the width instead. The
                // pressed guard matters: mid-drag the edge lags the pointer,
                // and a vertical wiggle makes the pointer leave and re-enter
                // the band, each re-entry an onEntered. Unguarded, every one
                // of them teleported the glow to the pointer's y (reported
                // as jumping up and down while resizing).
                onEntered: if (!pressed) queueGrip.placeHand(mouseY)
                onPressed: function (m) {
                    queueGrip.placeHand(m.y)
                    startScene = mapToItem(null, m.x, m.y).x
                    startWidth = queueDrawer.width
                }
                onPositionChanged: function (m) {
                    if (!pressed) { queueGrip.handY = m.y; return }
                    var want = startWidth + (startScene - mapToItem(null, m.x, m.y).x)
                    root.queueWidth = Math.max(420, Math.min(root.width - 80, want))
                }
            }
        }
    }

    // ====================================================================
    // Login overlay
    // ====================================================================
    // One badge serves every page; it targets whichever scrollable is on
    // screen and stays hidden near the top of each.
    BackToTop {
        id: scrollDressing
        // Scroll dressing belongs to the interface, not to the launch screen:
        // it lives outside the main column, so without this it painted its
        // edge fades over the opening water at full strength (the landing is
        // scrollable from the first frame, so the bottom fade was always on).
        // uiShown, so a pending terms gate keeps it down too.
        opacity: root.uiShown
        flick: root.artistOpen ? artistView
             : root.libraryOpen ? (root.libraryCategory === "home" ? homePane
                 : root.libraryCategory === "artists" ? libArtistsGrid
                 : root.libraryCategory === "albums" ? libAlbumsList
                 : root.libraryCategory === "tracks" ? libTracksList
                 : root.libraryCategory === "playlists" ? libPlaylistsList
                 : root.libraryCategory === "mixes" ? libMixesList
                 : root.libraryCategory === "videos" ? libVideosList : null)
             : root.browseOpen ? (root.browsePageKey === "" ? browseLanding : browseDrill)
             : root.settingsOpen ? null
             : results
    }

    Rectangle {
        id: loginPanel
        property bool urlOpened: false
        anchors.fill: parent
        // sessionResolved gates the overlay so an already-signed-in launch
        // doesn't flash the logged-out screen while the cached-token network
        // check is still in flight.
        visible: waves.sessionResolved && !waves.loggedIn
        // A logged-out cold launch fades in with the rest of the interface.
        opacity: root.bootContentShown
        color: "#d606070e"
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
        // Update opt-in prompt (updateOptInGate): answered = either button was
        // pressed, the prompt never returns. A click-away answers nothing and
        // only counts a dismissal; after two of those it stops asking too.
        property bool updatePromptAnswered: false
        property int updatePromptDismissals: 0
        // True when this install went through the first-run terms gate, so
        // the opt-in prompt keeps its fresh-setup wording across launches.
        property bool updatePromptFresh: false
        // Exit warning (exitGate): "Don't warn me again" mutes the
        // downloads-still-running close prompt permanently.
        property bool exitWarnMuted: false
    }
    FfmpegManager { id: appFfmpeg; objectName: "appFfmpeg" }

    // Download-folder gate: no folder is set at all (fresh install). Blocks, the
    // download did not start; the CTA jumps straight to the Downloads setting.
    Rectangle {
        id: folderGate
        objectName: "folderGate"
        anchors.fill: parent
        visible: root.folderGateBlocking
        color: "#f406070e"
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
        color: "#cc06070e"
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

    // DOWNLOAD ALL on a Browse playlist category: state the size once,
    // confirm, with a persistent opt-out for heavy bulk users.
    Rectangle {
        id: catDlGate
        objectName: "catDlGate"
        anchors.fill: parent
        visible: root.catDlPrompt !== null
        color: "#cc06070e"
        MouseArea { anchors.fill: parent; onClicked: root.catDlDismiss() }   // click-away cancels
        Rectangle {
            anchors.centerIn: parent; width: 460
            implicitHeight: cdCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: cdCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true
                    color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap
                    text: root.catDlPrompt
                          ? "Download " + root.catDlPrompt.count + (root.catDlPrompt.count === 1 ? " playlist?" : " playlists?")
                          : ""
                }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: root.catDlPrompt
                          ? "Everything in " + root.catDlPrompt.title + ", each playlist in its own folder. Progress shows in the queue."
                          : ""
                }
                Row {
                    spacing: 8
                    Check {
                        id: cdSkip
                        anchors.verticalCenter: parent.verticalCenter
                        onToggled: checked = !checked
                    }
                    Text {
                        textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter
                        text: "Don't ask again"; color: root.textLo; font.pixelSize: 13
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: cdSkip.checked = !cdSkip.checked }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.topMargin: 4; spacing: 12
                    GateAction {
                        showArrow: false; label: "Download all"
                        onClicked: {
                            var p = root.catDlPrompt
                            var mute = cdSkip.checked
                            root.catDlDismiss()
                            if (mute) waves.muteCategoryDlConfirm()
                            if (p) waves.downloadPlaylistCategory(p.path)
                        }
                    }
                    GateAction { showArrow: false; neutral: true; label: "Cancel"; onClicked: root.catDlDismiss() }
                }
            }
        }
    }

    // Reset all settings to default (Advanced settings). Confirms before
    // anything happens; the reset keeps the account signed in and only puts
    // the settings-page values back to their factory defaults.
    Rectangle {
        id: settingsResetGate
        objectName: "settingsResetGate"
        anchors.fill: parent
        visible: root.confirmSettingsReset
        color: "#cc06070e"
        MouseArea { anchors.fill: parent; onClicked: root.confirmSettingsReset = false }   // click-away cancels
        Rectangle {
            anchors.centerIn: parent; width: 460
            implicitHeight: srCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: srCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text { textFormat: Text.PlainText; Layout.fillWidth: true; color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap; text: "Reset all settings to default?" }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: "Every option in Settings goes back to its factory value, including your download folder and quality choices. You stay signed in, and your downloaded music is not touched."
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.topMargin: 4; spacing: 12
                    GateAction {
                        showArrow: false; label: "Reset settings"
                        onClicked: {
                            root.confirmSettingsReset = false
                            waves.resetSettingsDefaults()
                            settingsPage.externalReset()
                        }
                    }
                    GateAction { showArrow: false; neutral: true; label: "Cancel"; onClicked: root.confirmSettingsReset = false }
                }
            }
        }
    }

    // Reset application (Advanced settings): the factory wipe. Confirms with
    // an explicit description of what is erased; on confirm the backend wipes
    // its saved state and the app closes so the next launch is a first run.
    Rectangle {
        id: factoryResetGate
        objectName: "factoryResetGate"
        anchors.fill: parent
        visible: root.confirmFactoryReset
        color: "#cc06070e"
        MouseArea { anchors.fill: parent; onClicked: root.confirmFactoryReset = false }   // click-away cancels
        Rectangle {
            anchors.centerIn: parent; width: 460
            implicitHeight: frCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: frCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text { textFormat: Text.PlainText; Layout.fillWidth: true; color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap; text: "Reset Waves completely?" }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: "This signs you out and permanently erases everything Waves has saved on this computer: all settings, the record of which tracks you own, caches and logs. Your downloaded music files are not touched. Waves closes when it finishes, and the next launch starts like a brand-new install."
                }
                RowLayout {
                    Layout.fillWidth: true; Layout.topMargin: 4; spacing: 12
                    GateAction {
                        showArrow: false; danger: true; label: "Erase everything and close"
                        onClicked: {
                            root.confirmFactoryReset = false
                            waves.factoryReset()
                            Qt.quit()
                        }
                    }
                    GateAction { showArrow: false; neutral: true; label: "Cancel"; onClicked: root.confirmFactoryReset = false }
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
        color: "#cc06070e"
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
                    text: "The folder is set, but writing to it failed just now. If it lives on a network drive or NAS, it may have disconnected while the computer slept. Reconnect it, or choose a different folder. The download is held: Waves keeps checking and resumes on its own the moment the folder is back."
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
        // are mutually exclusive on termsCurrentAccepted (the same test the
        // terms gate shows on, version included), so this never stacks with
        // termsGate, a terms-revision re-prompt included.
        visible: waves.loggedIn && (
            (!setupSettings.ffmpegSetupDone && !root.termsCurrentAccepted)
            || (setupSettings.ffmpegSetupDone && root.termsCurrentAccepted
                && appFfmpeg.stateKey === "missing" && !ffmpegGate.sessionSnoozed
                && !setupSettings.ffmpegPromptDismissed)
        )
        color: "#f406070e"
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
        color: "#cc06070e"
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
    // termsVersion is the identity of the text below. It is stored with the
    // acceptance so a later revision can re-prompt only the users who agreed
    // to something older, and so the accepted wording is knowable after the
    // fact. Local only: nothing about the acceptance is ever reported.
    readonly property string termsVersion: "1.0"
    readonly property string termsVersionStamp: "Terms v1.0 (10 August 2026)"

    Settings {
        id: legalSettings; category: "legal"
        property bool termsAccepted: false
        property string termsAcceptedVersion: ""
        property string termsAcceptedDate: ""
    }

    // The stored version is READ, not just written: an acceptance of an older
    // revision is not an acceptance of this one, so bumping termsVersion
    // re-prompts exactly the users whose stamp is older (or absent, the
    // pre-stamp acceptances). String compare on purpose: any difference from
    // the current version means "not these terms".
    readonly property bool termsCurrentAccepted: legalSettings.termsAccepted
        && legalSettings.termsAcceptedVersion === termsVersion

    Rectangle {
        id: termsGate
        anchors.fill: parent
        // Above the launch overlay (z 100000). Nothing outranks a gate that
        // has to be read and agreed to: at default z the launch wordmark was
        // painted straight across the terms card for the whole handover.
        z: 100001
        // ...and it is what the launch sequence reveals, riding the same dial
        // the interface normally rides (root.bootContentShown): the wordmark's
        // zoom fades directly into this card, with the app itself still
        // unpainted behind it. Agreement first, interface second.
        readonly property bool wanted: waves.loggedIn && setupSettings.ffmpegSetupDone
            && !root.termsCurrentAccepted
        visible: wanted && root.bootContentShown > 0
        opacity: root.bootContentShown
        color: "#f406070e"
        // Eat every click so nothing behind the gate is reachable; with no close
        // control and no outside-click handler, the gate cannot be dismissed.
        MouseArea { anchors.fill: parent; hoverEnabled: true }

        Rectangle {
            anchors.centerIn: parent; width: 540
            // Never taller than the window: on a short window the card stops
            // growing and the terms body scrolls inside it instead of the
            // buttons being pushed off screen.
            implicitHeight: Math.min(termsCol.implicitHeight + 40, termsGate.height - 32)
            radius: 14; color: root.surface2; border.color: root.outline

            ColumnLayout {
                id: termsCol; anchors.centerIn: parent; width: parent.width - 40
                height: Math.min(implicitHeight, parent.height - 40)
                spacing: 13

                Text {
                    Layout.fillWidth: true
                    text: "Before you continue"; color: root.textHi; font.pixelSize: 18; font.bold: true
                }
                Flickable {
                    id: termsBodyFlick
                    Layout.fillWidth: true; Layout.fillHeight: true
                    Layout.preferredHeight: termsBody.implicitHeight
                    Layout.minimumHeight: 60
                    contentHeight: termsBody.implicitHeight
                    clip: true; boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {
                        // Tolerance, not a bare >: layout rounding leaves the
                        // content a hair taller than the view and armed the
                        // bar on a window with room to spare.
                        policy: termsBodyFlick.contentHeight - termsBodyFlick.height > 1
                            ? ScrollBar.AlwaysOn : ScrollBar.AlwaysOff
                    }
                    Text {
                        id: termsBody
                        textFormat: Text.PlainText
                        // The gutter is reserved whether or not the bar is up:
                        // sizing it off the bar's own visibility would feed the
                        // wrap back into the height that decides it.
                        width: termsBodyFlick.width - 14; wrapMode: Text.WordWrap
                        // lineHeight leading is not counted into implicitHeight
                        // for the last line, which clipped it (and armed the
                        // scrollbar) even with the whole card to spare.
                        bottomPadding: 6
                        color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                        text: "Waves is a personal, non-commercial tool for accessing your own TIDAL account. By continuing, you agree that:\n\n"
                            + "•  You will use Waves only for lawful, personal, non-commercial purposes, and only with content you are authorized to access.\n"
                            + "•  You will not use Waves to infringe copyright or to reproduce, distribute, or pirate any content. Respect the rights of artists and rights-holders.\n"
                            + "•  Your use of Waves may violate TIDAL's Terms of Service. You accept that risk and any consequences to your account, and you are responsible for complying with all laws that apply to you.\n"
                            + "•  Waves is provided \"as is\", without warranty of any kind, and to the fullest extent permitted by law its developers and contributors accept no liability for any damages arising from it. See sections 15 and 16 of the AGPL-3.0.\n"
                            + "•  You will indemnify and hold harmless the developers and contributors of Waves against any claim, loss or demand arising from your use of it or your breach of these terms.\n\n"
                            + "Waves is not affiliated with, endorsed by, or sponsored by TIDAL. TIDAL is a trademark of its owner, used here only to identify the service Waves works with."
                    }
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
                    // freshSetup: the opt-in prompt that follows this gate reads
                    // differently for a first run than for a user who updated in.
                    onClicked: if (ackChk.checked) {
                        // Record WHICH terms were agreed to, not just that some
                        // were: a future revision re-prompts only the users
                        // whose stored version is older.
                        legalSettings.termsAcceptedVersion = root.termsVersion
                        legalSettings.termsAcceptedDate = new Date().toISOString().slice(0, 10)
                        legalSettings.termsAccepted = true
                        setupSettings.updatePromptFresh = true
                    }
                }
                Text {
                    Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter
                    textFormat: Text.PlainText
                    text: root.termsVersionStamp
                    color: root.textDim; font.pixelSize: 11
                }
            }
        }
    }

    // ====================================================================
    // The library claim, opened up. A Download button reading DOWNLOADED
    // because the LIBRARY SCAN matched a folder (not because Waves has a
    // record of downloading it) stays clickable, and lands here.
    //
    // WHY THIS EXISTS: the match is inferred from tags, so it can be wrong,
    // and a wrong one used to be silent and terminal: a button that says
    // DOWNLOADED, does nothing, and explains nothing. This says what was
    // matched, shows where it is so the user can judge for themselves, and
    // keeps DOWNLOAD ANYWAY one click away. Nothing is remembered, because
    // DOWNLOAD ANYWAY is one-shot: the click starts the download, and from
    // there the live queue state drives the button.
    //
    // Detection must never prevent a download the user could otherwise make;
    // this is the escape hatch that keeps that true, for an album and for a
    // single track alike. The two differ only in wording and in which download
    // ANYWAY makes: an album needs a claim override so the bulk gate does not
    // then skip every track it contains, a track was never gated to begin with.
    // ====================================================================
    Rectangle {
        id: libraryClaimGate
        objectName: "libraryClaimGate"
        // Same reason as exitGate: a Drawer paints in the window's overlay
        // layer, so a gate parented to the page sits under it whatever its z.
        parent: Overlay.overlay
        anchors.fill: parent
        z: 1200
        property bool shown: false
        property string albumId: ""
        property string albumTitle: ""
        property string folder: ""
        // "album" or "track". Picks the wording and the download the ANYWAY
        // click makes; everything else about the conversation is the same.
        property string kind: "album"
        // "claim" (a library tag match, the original conversation) or
        // "owned" (a record of a download Waves made; REDOWNLOAD re-fetches).
        property string mode: "claim"
        property var card: null
        readonly property bool isTrack: kind === "track"
        readonly property bool isOwned: mode === "owned"
        visible: opacity > 0
        opacity: shown ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        color: "#cc06070e"
        function proceed() {
            var aid = libraryClaimGate.albumId
            var wasTrack = libraryClaimGate.isTrack
            libraryClaimGate.shown = false
            if (aid === "") return
            if (libraryClaimGate.isOwned) {
                // Force first, then start the normal per-kind download: the
                // override is what makes the job actually fetch owned tracks.
                waves.registerRedownload(aid)
                if (libraryClaimGate.card) root.browseCardDownload(libraryClaimGate.card)
                return
            }
            // A single track needs no override: the bulk claim gate rides only
            // on collection jobs, so one track asked for by name has never been
            // skippable and downloadTrack really downloads.
            if (wasTrack) { waves.downloadTrack(aid); return }
            // "Anyway" registers a claim override first: with the bulk claim
            // gate on, a plain downloadAlbum of a fully-claimed album would
            // skip every track it contains and report done having fetched
            // nothing, which is exactly what this click just declined.
            waves.downloadAlbumAnyway(aid)
        }
        function reveal() {
            var p = libraryClaimGate.folder
            libraryClaimGate.shown = false
            if (p !== "") waves.revealLibraryAlbum(p)
        }
        MouseArea { anchors.fill: parent; onClicked: libraryClaimGate.shown = false }
        Rectangle {
            anchors.centerIn: parent; width: 480
            implicitHeight: lcgCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: lcgCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap
                    text: libraryClaimGate.isOwned ? "You already downloaded this"
                                                   : "This looks like it is already in your library"
                }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    // A track match names a file, not a folder, and the folder
                    // line below is then the album it was found sitting in,
                    // which is also the evidence the identity was proven from.
                    // The owned mode is a different conversation: a record of
                    // a download Waves made, not a guess to be judged.
                    text: libraryClaimGate.isOwned
                        ? ((libraryClaimGate.albumTitle !== ""
                              ? "Waves downloaded “" + libraryClaimGate.albumTitle + "” itself, so this is a record, not a guess."
                              : "Waves downloaded this itself, so this is a record, not a guess.")
                           + " Redownloading fetches every track fresh and replaces the copies Waves wrote;"
                           + " nothing else on disk is touched.")
                        : ((libraryClaimGate.isTrack
                            ? (libraryClaimGate.albumTitle !== ""
                                ? "A file in your music library matches “" + libraryClaimGate.albumTitle + "”."
                                : "A file in your music library matches this track.")
                            : (libraryClaimGate.albumTitle !== ""
                                ? "A folder in your music library matches “" + libraryClaimGate.albumTitle + "”."
                                : "A folder in your music library matches this album."))
                           + " The match is made from tags, so it can be wrong. Downloading again makes a"
                           + " second copy; your existing files are never touched either way.")
                }
                // The evidence, so the user can judge the match rather than
                // take the button's word for it.
                Text {
                    visible: libraryClaimGate.folder !== ""
                    textFormat: Text.PlainText; Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    elide: Text.ElideMiddle
                    color: root.textDim; font.family: root.mono; font.pixelSize: 12
                    text: libraryClaimGate.folder
                }
                // This gate is the only one with no explicit dismiss button
                // (three CTAs crowd the row), so the way out is spelled out.
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textDim; font.pixelSize: 12
                    text: "Click away to leave it alone."
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 4; spacing: 12
                    SpecBtn {
                        primary: true; label: "SHOW IN FOLDER"
                        visible: libraryClaimGate.folder !== ""
                        onClicked: libraryClaimGate.reveal()
                    }
                    SpecBtn { label: libraryClaimGate.isOwned ? "REDOWNLOAD" : "DOWNLOAD ANYWAY"; onClicked: libraryClaimGate.proceed() }
                }
            }
        }
    }

    // ====================================================================
    // Update opt-in prompt, the last step of the first-run chain (login >
    // FFmpeg > terms > this) and shown once to existing installs that
    // predate it. Lands directly after the terms gate's privacy promise on
    // purpose: here is the one optional outbound connection, asked for.
    // Either button answers it for good; a click-away re-asks next launch,
    // then stops. Design settled in scratchpad/update_optin_lab.qml.
    // ====================================================================
    Rectangle {
        id: updateOptInGate
        objectName: "updateOptInGate"
        // Same reason as exitGate: a Drawer paints in the window's overlay
        // layer, so a gate parented to the page sits under it whatever its z.
        parent: Overlay.overlay
        anchors.fill: parent
        // Above the video overlay (999) and the peek card (990): a first-run
        // prompt must never paint underneath a playing video.
        z: 1200
        // termsCurrentAccepted, not the bare boolean: during a terms-revision
        // re-prompt this card must wait behind the terms gate, never beside it.
        readonly property bool shouldShow: waves.loggedIn && setupSettings.ffmpegSetupDone && root.termsCurrentAccepted
            && bootOverlay.done
            && !ffmpegGate.visible
            && !setupSettings.updatePromptAnswered
            && setupSettings.updatePromptDismissals < 2
            && !sessionDismissed
            && !autoUpdateOn
        // Rather than pop: the card slides up from below the frame while this
        // scrim dims the app behind it, and both reverse on dismissal.
        visible: opacity > 0
        opacity: shouldShow ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 520; easing.type: Easing.OutCubic } }
        color: "#cc06070e"
        // Read once at startup: while this gate can be on screen the only
        // in-app write to auto_update is the gate's own accept.
        readonly property bool autoUpdateOn: waves.wavesPref("auto_update") === true
        property bool sessionDismissed: false
        // Set by the terms gate's acknowledge, so the copy can address a
        // fresh setup instead of a user who updated into this prompt.
        // Persisted (setupSettings): a click-away re-asks NEXT LAUNCH, and a
        // session-only flag would greet that same fresh install with the
        // "you have been running without update checks" veteran copy.
        readonly property bool freshSetup: setupSettings.updatePromptFresh
        function answer(enabled) {
            setupSettings.updatePromptAnswered = true
            waves.resolveUpdateOptIn(enabled)
        }
        MouseArea { anchors.fill: parent; onClicked: { updateOptInGate.sessionDismissed = true; setupSettings.updatePromptDismissals++ } }
        Rectangle {
            // 500, not the other gates' 460: sized so the body sets as three
            // even lines (the terms gate already runs wider at 540).
            anchors.centerIn: parent; width: 500
            implicitHeight: uoCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            // The slide itself: parked below the bottom edge until the gate
            // opens, then rides up to centre as the scrim dims.
            anchors.verticalCenterOffset: updateOptInGate.shouldShow
                ? 0 : updateOptInGate.height / 2 + height
            Behavior on anchors.verticalCenterOffset {
                NumberAnimation { duration: 520; easing.type: Easing.OutCubic }
            }
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: uoCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap
                    text: updateOptInGate.freshSetup ? "Stay current with Waves" : "Want to hear about updates?"
                }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    text: (updateOptInGate.freshSetup
                            ? "Waves updates often, and most updates fix problems you would otherwise run into. "
                            : "Waves never asked before, so you have been running without update checks. ")
                        + "A once-a-day check tells you when a new version is out. Nothing is downloaded or installed unless you choose it."
                }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textDim; font.pixelSize: 12; lineHeight: 1.3
                    text: "You can change this any time in Settings."
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 4; spacing: 12
                    SpecBtn { primary: true; label: "TURN ON UPDATE CHECKS"; onClicked: updateOptInGate.answer(true) }
                    SpecBtn { label: "NOT NOW"; onClicked: updateOptInGate.answer(false) }
                }
            }
        }
    }

    // Exit warning: the window close was vetoed (onClosing) because downloads
    // are still running. KEEP DOWNLOADING cancels the exit; EXIT ANYWAY
    // re-closes for real. The checkbox persists on either button; a click
    // away is treated as keeping the downloads, without persisting it.
    Rectangle {
        id: exitGate
        objectName: "exitGate"
        // A Drawer (the queue) lives in the window's OVERLAY layer, which
        // paints over ordinary content whatever z that content carries, so a
        // gate parented to the page was masked by the drawer's dim while the
        // queue was open. Joining the same layer is what actually puts it on
        // top; the z below then orders it inside that layer.
        parent: Overlay.overlay
        anchors.fill: parent
        // Above the video overlay (999): the close veto opens this gate, and
        // with a video full-screen a default z would paint it underneath,
        // leaving the window looking un-closable.
        z: 1200
        visible: open
        property bool open: false
        // Lets the EXIT ANYWAY re-close pass the onClosing veto. Reset when
        // the gate opens so a cancelled exit re-arms the warning.
        property bool confirmed: false
        onOpenChanged: if (open) { confirmed = false; exitSkip.checked = false }
        color: "#cc06070e"
        MouseArea { anchors.fill: parent; onClicked: exitGate.open = false }
        Rectangle {
            // 420: narrower than the info-heavy gates, this one is a short
            // question and the body still sets as two lines.
            anchors.centerIn: parent; width: 420
            implicitHeight: exCol.implicitHeight + 40
            radius: 14; color: root.surface2; border.color: root.outline
            MouseArea { anchors.fill: parent }   // a click on the card must not dismiss
            ColumnLayout {
                id: exCol; anchors.centerIn: parent; width: parent.width - 40; spacing: 14
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textHi; font.pixelSize: 18; font.bold: true; wrapMode: Text.WordWrap
                    text: "Exit while downloading?"
                }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textLo; font.pixelSize: 13; font.bold: true; lineHeight: 1.3
                    // The 0 case is reachable: the last download can finish
                    // while this gate is up. The copy must not read "0
                    // downloads are still running." at that moment.
                    text: root.activeQueueCount === 0
                        ? "All downloads finished."
                        : root.activeQueueCount === 1
                            ? "A download is still running."
                            : root.activeQueueCount + " downloads are still running."
                }
                Text {
                    textFormat: Text.PlainText; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    horizontalAlignment: Text.AlignHCenter
                    color: root.textLo; font.pixelSize: 13; lineHeight: 1.3
                    Layout.topMargin: -8   // the pair reads as one statement, not two paragraphs
                    text: root.activeQueueCount === 0
                        ? "It is safe to exit now."
                        : "Exiting now ends " + (root.activeQueueCount === 1 ? "it" : "them")
                            + ", and unfinished tracks will need to be downloaded again."
                }
                RowLayout {
                    Layout.alignment: Qt.AlignHCenter
                    Layout.topMargin: 4; spacing: 12
                    SpecBtn {
                        primary: true; label: "KEEP DOWNLOADING"
                        onClicked: {
                            if (exitSkip.checked) setupSettings.exitWarnMuted = true
                            exitGate.open = false
                        }
                    }
                    SpecBtn {
                        danger: true; label: "EXIT ANYWAY"
                        onClicked: {
                            if (exitSkip.checked) setupSettings.exitWarnMuted = true
                            exitGate.confirmed = true
                            exitGate.open = false
                            root.close()
                        }
                    }
                }
                Row {
                    // "Don't warn me again" row: catDlGate grammar, centred to
                    // match this card's centered text; below the buttons so the
                    // choice reads first and the escape hatch last.
                    Layout.alignment: Qt.AlignHCenter
                    spacing: 8
                    Check {
                        id: exitSkip
                        anchors.verticalCenter: parent.verticalCenter
                        onToggled: checked = !checked
                    }
                    Text {
                        textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter
                        text: "Don't warn me again"; color: root.textLo; font.pixelSize: 13
                        MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: exitSkip.checked = !exitSkip.checked }
                    }
                }
            }
        }
    }

    // ==== Open-water launch sequence ("Z FILL", picked in the boot lab) ====
    // The app opens on its own water video, bright (scrim held at 0.55) with
    // the WAVES wordmark centred and the running version tucked at its
    // bottom-right. Once the session resolves and the landing has content
    // (2s cap), a progress bar completes over the version and drains away,
    // then the wordmark zooms toward the viewer as the water dims to its
    // resting scrim and the interface fades in. One video instance start to
    // finish, so the handover is seamless by construction.
    Item {
        id: bootOverlay
        anchors.fill: parent
        z: 100000
        visible: !done
        property bool started: false
        property bool done: false

        // Input shield. The interface underneath hides by opacity alone, so
        // without this it stays clickable and hoverable while invisible: a
        // click on the launch water could land on a preview control and start
        // full-volume audio, and the hand cursor roamed over buttons nobody
        // could see (issue #13). Eats clicks, hover, and wheel, and keeps the
        // plain arrow cursor, until the reveal starts painting the content.
        MouseArea {
            id: bootShield
            anchors.fill: parent
            enabled: root.bootContentShown === 0
            hoverEnabled: enabled
            acceptedButtons: Qt.AllButtons
            onWheel: function(w){ w.accepted = true }
        }

        function maybeStart() {
            if (started || done) return
            if (!waves.sessionResolved) return
            if (waves.loggedIn && root.browseSections.length === 0) return
            started = true
            bootSeq.start()
        }
        Component.onCompleted: maybeStart()
        Connections {
            target: waves
            function onSessionResolvedChanged() { bootOverlay.maybeStart() }
            function onLoggedInChanged() { bootOverlay.maybeStart() }
        }
        Connections {
            target: root
            // Held handover: the landing's first payload arrived, re-check the
            // gate (the build it just started re-holds until the shelves are
            // in, so the reveal still lands on a finished page).
            function onBrowseSectionsChanged() { bootOverlay.maybeStart(); if (bootOverlay.handoverHeld) bootOverlay.handover() }
            // Held handover: the landing finished assembling, go now.
            function onBrowseBuildingChanged() {
                if (bootOverlay.handoverHeld && !root.browseBuilding) bootOverlay.handover()
            }
            // Held handover: the fetch failed; nothing further is coming, so
            // reveal the error state rather than sit on the wordmark.
            function onBrowseErrorChanged() {
                if (bootOverlay.handoverHeld && root.browseError) bootOverlay.handover()
            }
        }
        // Readiness cap: never hold the launch look longer than this.
        Timer {
            interval: 2000; running: true
            onTriggered: { if (!bootOverlay.started) { bootOverlay.started = true; bootSeq.start() } }
        }

        // ---- handover gate ------------------------------------------------
        // Covering the Browse landing's assembly is the whole point of this
        // sequence, so the opening frame holds until the shelves have finished
        // incubating. Without the gate the overlay lifted on its own schedule
        // and the page was watched dropping in shelf by shelf, as if it were
        // scrolling itself (reported from livetesting). Capped, so a stalled
        // or endless build can never pin the launch screen. Everything that
        // signals the handover (the version drain, then the zoom) lives in
        // bootHandover, downstream of this gate, so their timing together is
        // exactly what it was before the gate existed.
        property bool handoverHeld: false
        function handover() {
            if (done) return
            // Data still in flight: signed in but the landing has nothing to
            // reveal yet (a slow token check or first fetch outran the opening
            // frame; the readiness cap below starts the sequence regardless).
            // Hold here, wordmark and version still up, exactly the frame the
            // bootSeq comment says any wait belongs on; the payload arriving,
            // the fetch erroring, or the cap releases it. Without this leg the
            // gate only covered the shelf assembly, and a slow login revealed
            // onto the bare "Reading the wire…" landing (reported from
            // livetesting).
            if (waves.loggedIn && root.browseSections.length === 0 && !root.browseError
                    && !handoverCap.expired) { handoverHeld = true; return }
            if (root.browseBuilding && !handoverCap.expired) { handoverHeld = true; return }
            handoverHeld = false
            handoverCap.stop()
            bootHandover.start()
        }
        Timer {
            id: handoverCap
            property bool expired: false
            // Sized for the data wait (login round-trip + first landing fetch
            // on a slow network), not just the shelf assembly: the build leg
            // has its own 800ms stall guard (browseBuildGuard), so only a
            // genuinely dead network ever rides this cap to the end, and even
            // then the launch screen cannot be pinned past it.
            interval: 8000
            onTriggered: { expired = true; bootOverlay.handover() }
        }

        // ---- the wordmark (typography as in WelcomeBanner) ----------------
        Item {
            id: bootTitle
            property real shown: 0   // faded up by bootIntro
            property real zoom: 1
            anchors.fill: parent
            scale: bootTitle.zoom
            Text {
                anchors.centerIn: parent; anchors.verticalCenterOffset: 2
                textFormat: Text.PlainText; text: "WAVES"
                font.family: root.mono; font.pixelSize: 112; font.bold: true; font.letterSpacing: 26
                color: "#0a160d"; opacity: 0.9 * bootTitle.shown   // shadow
            }
            Text {
                anchors.centerIn: parent
                textFormat: Text.PlainText; text: "WAVES"
                font.family: root.mono; font.pixelSize: 112; font.bold: true; font.letterSpacing: 26
                color: root.textHi; opacity: bootTitle.shown
            }
        }

        // ---- version readout: read once at launch (set and forget) --------
        // Outside bootTitle so it never scales with the zoom; tucked at the
        // bottom-right of the wordmark with slight padding.
        TextMetrics {
            id: bootTm
            font.family: root.mono; font.pixelSize: 112; font.bold: true; font.letterSpacing: 26
            text: "WAVES"
        }
        Text {
            id: bootVer
            property real shown: 0   // faded up by bootIntro, with the wordmark
            textFormat: Text.PlainText
            text: "v" + waves.appVersion
            font.family: root.mono; font.pixelSize: 14; font.bold: true; font.letterSpacing: 6
            color: root.accent
            // Right edge aligned under the S (minus the trailing letterSpacing).
            x: bootOverlay.width / 2 + bootTm.width / 2 - 26 - width
            // Tucked just under the wordmark's baseline. The metrics box runs
            // a descender's worth below the caps, and "WAVES" has none, so
            // sitting at its bottom edge left the readout stranded well clear
            // of the glyphs; pull back up to hug them.
            y: bootOverlay.height / 2 + bootTm.height / 2 - 24
            opacity: 0.85 * shown
        }

        // The version's exit, built on the progress bars' cells: a bar
        // completes over the text, drains to dim, then empties out.
        Timer {
            id: bootBlk
            interval: 36; repeat: true
            property int tick: 0
            property string base: ""
            // How long one full drain lasts, in ms. The walk is three passes
            // over the cells (fill, dim, empty) plus the closing tick that
            // stops the timer and clears the readout, one tick per cell, so
            // its length follows the version string and is never a constant.
            // bootHandover waits exactly this before the zoom: a fixed pause
            // was shorter than the walk (700ms against 792ms for "v0.1.11"),
            // so the last cells were still emptying once the zoom had begun.
            readonly property int runMs: interval * (3 * base.length + 1)
            onTriggered: {
                tick += 1
                var m = base.length
                var out = ""
                for (var i = 0; i < m; ++i) {
                    if (tick > 2 * m + i) out += " "
                    else if (tick > m + i) out += "░"
                    else if (tick > i) out += "█"
                    else out += base[i]
                }
                bootVer.text = out
                // The zoom starts HERE, welded to the walk actually finishing.
                // It used to run after a wall-clock pause sized to runMs, but
                // these ticks ride the GUI thread: any boot work (the landing
                // assembling) slips them, and the readout was still draining
                // while the interface faded up underneath (reported from
                // livetesting). The drain's own last tick can never be early
                // or late relative to itself.
                if (tick > 3 * m) { stop(); tick = 0; bootVer.shown = 0; bootZoom.start() }
            }
        }

        // The opening is a fade-up, not a cut: the wordmark and the version
        // rise out of the dark together, on one curve, so they read as a
        // single composed mark rather than two arrivals. Deliberately NOT
        // part of bootSeq: that sequence waits on the session and the landing
        // (up to 2s), and the opening frame must never sit blank while it
        // does, so the fade runs from the first frame regardless.
        ParallelAnimation {
            id: bootIntro
            running: true
            NumberAnimation {
                target: bootTitle; property: "shown"; from: 0; to: 1
                duration: 900; easing.type: Easing.OutCubic
            }
            NumberAnimation {
                target: bootVer; property: "shown"; from: 0; to: 1
                duration: 900; easing.type: Easing.OutCubic
            }
        }

        SequentialAnimation {
            id: bootSeq
            // Long enough for the fade-up (~0.9s) to finish and the composed
            // frame to settle before anything starts leaving.
            PauseAnimation { duration: 1900 }   // hold the opening frame; the water fades in under it
            // Any wait for the landing happens HERE, on the opening frame with
            // the wordmark and version still up, never after the version has
            // drained: that readout emptying out reads as "about to hand over"
            // and must stay welded to the zoom that follows it.
            ScriptAction { script: { handoverCap.restart(); bootOverlay.handover() } }
        }

        // Handover = start the version drain; its last tick starts bootZoom
        // (see bootBlk.onTriggered), so nothing of the readout is ever left
        // on screen when the zoom and the interface reveal begin, however
        // busy the GUI thread was during the walk.
        SequentialAnimation {
            id: bootHandover
            // The drain is also where the interface gets painted for the
            // first time, invisibly, so the zoom that follows it is not the
            // frame that pays for it (see root.bootWarming).
            ScriptAction { script: { root.bootWarming = true; bootBlk.base = bootVer.text; bootBlk.restart() } }
        }
        SequentialAnimation {
            id: bootZoom
            // Tight zoom: the wordmark is gone in ~0.7s and the interface only
            // starts appearing once the title is mostly faded, so text never
            // lingers over visible UI.
            ParallelAnimation {
                NumberAnimation { target: bootTitle; property: "zoom"; to: 2.4; duration: 700; easing.type: Easing.InCubic }
                NumberAnimation { target: bootTitle; property: "shown"; to: 0; duration: 550; easing.type: Easing.InQuad }
                NumberAnimation { target: root; property: "bootScrimLevel"; to: 0.93; duration: 800; easing.type: Easing.InOutSine }
                SequentialAnimation {
                    PauseAnimation { duration: 350 }
                    NumberAnimation { target: root; property: "bootContentShown"; to: 1; duration: 620; easing.type: Easing.InOutSine }
                }
            }
            ScriptAction {
                script: {
                    // Pin the final values (the overlay never runs again).
                    root.bootScrimLevel = 0.93
                    root.bootContentShown = 1
                    bootOverlay.done = true
                    // A revalidate that landed during the reveal waited here.
                    // Applied now, with done set, it takes the ordinary
                    // in-place refresh path: no veil, nothing to watch.
                    if (root._browseParked) {
                        var parked = root._browseParked
                        root._browseParked = null
                        root.applyBrowseLanding(parked)
                    }
                }
            }
        }
    }
}
