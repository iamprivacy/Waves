import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Effects
import QtQuick.Layouts
import QtQuick.Dialogs
import QtQuick.Shapes
import "HeartGib.js" as HeartGib

// Schema-driven settings page. The bridge (`waves`) supplies the grouped field
// schema; this renders a control per field type and applies only changed keys.
Item {
    id: page
    property bool active: false
    // The page's scroller, for the host's trackpad guard (MomentumGuard).
    readonly property Flickable scroller: settingsFlick
    // Nothing on the page leaves it any more (CANCEL discards edits in place),
    // but the host still listens: kept for a programmatic close.
    signal closed()
    // Diagnostics-section reset actions. The page only asks; Main.qml shows the
    // confirmation dialog and calls the backend, then (for the settings
    // reset) tells the page to re-read the freshly-defaulted schema.
    signal resetSettingsRequested()
    signal factoryResetRequested()
    // The FLAC check's fix (FFmpeg & FLAC section). Same shape as the resets: the
    // page asks, Main.qml confirms with the count and calls the backend.
    signal flacFixRequested(int count, string where)
    signal flacResumeRequested(string where)
    function externalReset() { editMap = ({}); unsaved = ({}); dirty = false; refreshSchema(); syncLibraryMirrors(); flacRefresh() }

    // Waves palette (kept local so this file is self-contained)
    readonly property color accent:       "#3dff6e"
    readonly property color accentText:   "#03210e"
    readonly property color surface:      "#15181d"
    readonly property color surface2:     "#191c22"
    readonly property color border1:      "#262a31"
    readonly property color textHi:       "#e6e8ec"
    readonly property color textLo:       "#a8acb4"
    readonly property color textDim:      "#6b6f78"
    // New Console tokens (mirrors Main.qml)
    readonly property color surface0:     "#121418"
    readonly property color surface3:     "#1d2128"
    readonly property color surfaceHi:    "#22262e"
    readonly property color outline:      "#3a3f49"
    readonly property color divider:      "#22262d"
    readonly property color accentDim:    "#22a64a"
    readonly property color accentCont:   "#06210f"
    readonly property color accentContTx: "#86ffaa"
    readonly property color gold:         "#ffb01f"
    readonly property color goldCont:     "#2a2008"
    readonly property color green:        "#3ef08a"
    readonly property color greenDim:     "#2aa862"
    readonly property color greenCont:    "#08230f"
    readonly property color cyan:         "#56c8d8"
    readonly property color red:          "#ff5a52"
    readonly property color redCont:      "#2a0e0c"
    readonly property string mono:        monoFont
    // Console button spec (Button Lab verdict, 2026-07-02), mirrors Main.qml.
    readonly property string uiFont:   uiFontFamily
    readonly property real   btnTrack: 0.3
    readonly property int    btnRad:   7
    readonly property int    btnPadH:  10
    readonly property int    btnPadV:  5

    property var groups: []
    property var editMap: ({})
    // The keys staged since the last SAVE or CANCEL: what the unsaved marks
    // read. editMap outlives a save (it keeps the controls on the saved
    // values) and holds the live diagnostics switches, so it cannot say
    // what is still unsaved.
    property var unsaved: ({})
    // What the last SAVE wrote, until the schema is pulled again: the schema's
    // own values are only refreshed on the next open, so anything that asks
    // "is this saved yet" (the Notifications arm note) reads this first.
    property var savedVals: ({})
    function saved(f) { return savedVals[f.key] !== undefined ? savedVals[f.key] : f.value }
    property bool dirty: false
    property bool savedFlash: false
    Timer { id: flashTimer; interval: 2200; onTriggered: page.savedFlash = false }
    Timer { id: auUpToDateTimer; interval: 4000; onTriggered: page.auUpToDate = false }
    Timer { id: auCheckFailedTimer; interval: 4000; onTriggered: page.auCheckFailed = false }
    // The control tree is expensive to instantiate, but the schema's *shape*
    // never changes, only the persisted values do, and those only when we
    // save. So we build `groups` once and keep the delegates alive; rebuilding
    // the whole Repeater on every open was what made switching to Settings feel
    // laggy. `needsRefresh` forces a one-time rebuild after a save so the
    // controls reflect the freshly-saved values on the next open.
    property bool needsRefresh: false

    // ---- Shared FFmpeg manager ----
    // Injected from Main as the single app-wide FfmpegManager instance (the same
    // one the first-run setup step uses), so install state lives in one place.
    property var ff: null

    // ---- Ownership-badge scan status ----
    // Mirrored from the bridge so the "Music library" card can explain a blank
    // badge: "unreadable" means the folder exists but the OS won't let Waves
    // list it (a network/external drive without permission), which otherwise
    // looks identical to an empty library. See backend.libraryScanStatus().
    property string libraryScanStatus: waves.libraryScanStatus()
    property var libraryScanProgress: waves.libraryScanProgress()
    // True when the last scan met a folder listing it could not trust (a
    // network share whose directory paging repeats): the scan still says "ok",
    // but badges for artists not yet opened may be blank until asked for.
    property bool libraryScanPartial: waves.libraryScanPartial()
    property bool libraryListingReconciled: waves.libraryListingReconciled()
    // How badly the folder listing was truncated: {entries, distinct}. The
    // note below turns it into real numbers, because "incomplete" on its own
    // told the user nothing they could act on.
    property var libraryListingShape: waves.libraryListingShape()
    // SAVED library state, mirrored for the composite "library" card: the master
    // switch, the chosen source, the separate folder path, and the download
    // folder (scanned when the source follows it). The card's controls stage
    // into editMap like every other field and SAVE CHANGES commits them, so
    // these mirrors are only the fallback a control shows when it holds no
    // staged edit, and what the Rescan button is gated on (a staged edit has
    // not taken effect yet). Re-synced from the backend on every open, after a
    // save/reset, and on every librarySourceChanged.
    property bool libraryEnabledLive: waves.wavesPref("library_enabled") === true
    property bool libraryBulkSkipLive: waves.wavesPref("library_bulk_skip") !== false
    property bool libraryMbArbiterLive: waves.wavesPref("library_mb_arbiter") === true
    property bool libraryMbLookupLive: waves.wavesPref("library_mb_lookup") === true
    property bool libraryFollowLive: waves.wavesPref("releases_auto_import") === true
    property int libraryMinTracksLive: Number(waves.wavesPref("releases_import_min_tracks")) || 5
    // Library follows the saved floor would leave out today (the card
    // offers to unfollow them); re-read with the mirrors and on
    // followFloorChanged.
    property int libraryFloorExcess: 0
    property string librarySourceLive: waves.librarySource()
    property string libraryFolderLive: waves.wavesPref("library_folder") || ""
    property string downloadFolderLive: waves.libraryDownloadFolder()
    // Pull every library mirror back from the backend. Called on open AND after a
    // settings reset: the reset clears library_folder through applySettings, but
    // it rebuilds the page in place rather than reopening it, so without this the
    // card kept showing (as its staged-edit fallback) the folder just cleared.
    function syncLibraryMirrors() {
        libraryScanStatus = waves.libraryScanStatus()
        libraryScanProgress = waves.libraryScanProgress()
        libraryScanPartial = waves.libraryScanPartial()
        libraryListingReconciled = waves.libraryListingReconciled()
        libraryListingShape = waves.libraryListingShape()
        libraryEnabledLive = waves.wavesPref("library_enabled") === true
        libraryBulkSkipLive = waves.wavesPref("library_bulk_skip") !== false
        libraryMbArbiterLive = waves.wavesPref("library_mb_arbiter") === true
        libraryMbLookupLive = waves.wavesPref("library_mb_lookup") === true
        libraryFollowLive = waves.wavesPref("releases_auto_import") === true
        libraryMinTracksLive = Number(waves.wavesPref("releases_import_min_tracks")) || 5
        libraryFloorExcess = waves.followsUnderFloor()
        librarySourceLive = waves.librarySource()
        libraryFolderLive = waves.wavesPref("library_folder") || ""
        downloadFolderLive = waves.libraryDownloadFolder()
    }
    Connections {
        target: waves
        function onLibraryScanStatusChanged() {
            page.libraryScanStatus = waves.libraryScanStatus()
            page.libraryScanProgress = waves.libraryScanProgress()
            page.libraryScanPartial = waves.libraryScanPartial()
            page.libraryListingReconciled = waves.libraryListingReconciled()
            page.libraryListingShape = waves.libraryListingShape()
            page.downloadFolderLive = waves.libraryDownloadFolder()
            // The FLAC card's "Waves library" names these folders too.
            page.flacRefresh()
        }
        function onLibrarySourceChanged() {
            // Fired for the master switch and the source alike (any committed
            // library edit); pull the folder too so a reset lands everywhere.
            page.libraryEnabledLive = waves.wavesPref("library_enabled") === true
            page.libraryBulkSkipLive = waves.wavesPref("library_bulk_skip") !== false
            page.libraryMbArbiterLive = waves.wavesPref("library_mb_arbiter") === true
            page.libraryMbLookupLive = waves.wavesPref("library_mb_lookup") === true
            page.libraryFollowLive = waves.wavesPref("releases_auto_import") === true
            page.libraryMinTracksLive = Number(waves.wavesPref("releases_import_min_tracks")) || 5
            page.libraryFloorExcess = waves.followsUnderFloor()
            page.librarySourceLive = waves.librarySource()
            page.libraryFolderLive = waves.wavesPref("library_folder") || ""
            page.flacRefresh()
        }
        function onFollowFloorChanged() {
            page.libraryMinTracksLive = Number(waves.wavesPref("releases_import_min_tracks")) || 5
            page.libraryFloorExcess = waves.followsUnderFloor()
        }
    }
    // The scanning note's text: real progress, not a bare spinner. During the
    // folder walk it counts discoveries live; once reads start it shows
    // What "incomplete" actually means, in the numbers the scan measured. Some
    // network shares answer "list this folder" with the same page of names over
    // and over, so the system reports thousands of entries that are only a few
    // hundred different folders, and everything past that page is invisible to
    // every app on this Mac, not just Waves. Waves works around it by asking for
    // artists by name, which the share does answer correctly, so anything it has
    // a name for still resolves. No folder name and no artist name goes in here.
    function libTruncationNote() {
        var shape = page.libraryListingShape || ({})
        var entries = shape.entries || 0
        var distinct = shape.distinct || 0
        var measured = entries > distinct && distinct > 0
        return (measured
                ? ("This folder does not list properly. The system offers " + entries
                   + " entries for it but only " + distinct
                   + " of them are different folders, so the rest never reach Waves. ")
                : "This folder's listing comes back incomplete, which some network shares do. ")
            + "Everything inside the folders it did name is indexed. Waves finds the others by asking for "
            + "artists by name, which it does whenever you search, open a page, or start a download, and "
            + "what it finds is kept. Artists you have never looked up may show no badge yet."
    }
    // Whether the truncation is worth a word at all. A listing the recovery
    // has already made good (every folder the fresh mount named is indexed)
    // needs nothing from the user, and anything on the card in the warning
    // color reads as an error whatever it says, so it shows nothing. The
    // moment a recovery leaves a folder out, the warning is back.
    readonly property bool libTruncationShown: libraryScanPartial && !libraryListingReconciled
                                               && libraryScanStatus === "ok"

    // done/total, what remains, and an ETA computed from the observed read rate.
    function libScanNote() {
        // Compact on purpose: the read-phase note shares one line with the
        // album being read, so every dropped word is room for a long title.
        var p = page.libraryScanProgress || {}
        if (p.phase === "read" && p.total > 0) {
            var s = "Reading albums: " + p.done + " of " + p.total
            if (p.eta_secs !== undefined && p.eta_secs >= 0)
                s += p.eta_secs < 90 ? " · about a minute left" : " · about " + Math.round(p.eta_secs / 60) + " minutes left"
            return s
        }
        if (p.phase === "walk" && (p.checked > 0 || p.found > 0))
            return "Finding your albums: checked " + p.checked + " folders · " + p.found + " found…"
        return "Scanning your music library…"
    }
    // True while the read phase is running with a known size: gates the LED
    // progress bar and the "now reading" artist line under the folder field.
    readonly property bool libScanReading: libraryScanStatus === "scanning"
                                           && (libraryScanProgress || {}).phase === "read"
                                           && (libraryScanProgress || {}).total > 0

    // ---- In-app updater state ----
    property var appUp: ({})             // last waves.appUpdateStatus()
    property string auState: ""          // install lifecycle: "" | downloading | done | failed | canceled
    property string auMsg: ""
    property real auPct: 0
    property bool auUpdate: false        // a newer release is available
    property string auLatest: ""         // latest version tag when available
    property bool auChecking: false      // a user-initiated check is in flight
    property bool auUpToDate: false      // transient "✓ up to date" after a check
    property bool auCheckFailed: false   // transient "could not check" after a check that never got an answer
    readonly property bool auBusy: auState === "downloading" || auState === "verifying" || auState === "installing"
    readonly property bool auDone: auState === "done"
    function auRefresh() {
        page.appUp = waves.appUpdateStatus()
        // A swap staged in an earlier session and re-armed at launch is the
        // same "restart to finish" state as one installed a minute ago, so the
        // card shows it whenever Settings is opened, not only on the signal.
        if (page.appUp.pending_restart === true && page.auState === "") {
            page.auState = "done"
            page.auMsg = (page.appUp.pending_version
                          ? "Update to " + page.appUp.pending_version + " is ready. "
                          : "An update is ready. ") + "Restart to finish."
        }
    }

    // ---- FLAC signature check state (FFmpeg & FLAC card) ----
    // A plain copy of waves.flacCheckState(); re-read on open and on every
    // flacCheckChanged. Nothing on this page writes to it.
    property var flacState: waves.flacCheckState()
    // The folder choice (waves.flacCheckSources()): which source is selected,
    // the folder picked with Browse, the scope key the selection names and the
    // Waves library folders. The backend remembers the choice; this is a copy.
    property var flacSources: waves.flacCheckSources()
    // Whether the next scan also decodes every file (slow, finds damaged ones).
    property bool flacDeep: false
    // Read every file again instead of reusing an earlier scan's verdicts.
    property bool flacRecheck: false
    function flacRefresh() { page.flacState = waves.flacCheckState(); page.flacSources = waves.flacCheckSources() }
    // The last run's counts describe other folders than the ones selected now:
    // the card then reads "Not checked yet" and offers no FIX (the backend
    // refuses that fix too) until the selection is scanned.
    function flacStale() {
        var st = page.flacState
        return !st.running && (st.scope || "") !== (page.flacSources.key || "")
    }
    // Where a fix would write, for the confirmation dialog.
    function flacWhere() {
        var src = page.flacSources
        return src.source === "folder" ? (src.folder || "") : "Your Waves library: " + (src.waves || []).join(", ")
    }
    function flacCount(key) { var c = page.flacState.counts || {}; return c[key] || 0 }
    // A count with the locale's thousands separator (a library can hold tens of thousands).
    function flacNum(n) { return Number(n || 0).toLocaleString(Qt.locale(), "f", 0) }
    // A fix stopped before it finished (the app quit or crashed, or it was
    // canceled) and the backend remembers its folders: RESUME LAST FIX is
    // offered, unless FIX already offers the same files from this session's scan.
    function flacResumable() {
        var st = page.flacState
        return !st.running && (st.resume_source || "") !== "" && (page.flacStale() || !(st.fixable > 0))
    }
    // Where a resume writes, for the confirmation dialog.
    function flacResumeWhere() {
        var st = page.flacState, folders = st.resume_folders || []
        return st.resume_source === "folder" ? (folders[0] || "") : "Your Waves library: " + folders.join(", ")
    }
    // The time left beside the progress bar ("" until the backend has a pace to go by).
    function flacTimeLeft() {
        var s = page.flacState.eta
        if (s === undefined || s < 0) return ""
        if (s < 60) return "less than a minute left"
        var m = Math.round(s / 60)
        if (m < 60) return "about " + m + " min left"
        var h = Math.floor(m / 60), r = m % 60
        return "about " + h + " h" + (r > 0 ? " " + r + " min" : "") + " left"
    }
    // The one-line status under the card's title, for every phase.
    function flacNote() {
        var st = page.flacState
        var n = st.files || 0, total = st.total || 0
        if (st.phase === "scanning") {
            if (total === 0) return "Looking for FLAC files… " + page.flacNum(st.found) + " found"
            return (st.mode === "deep" ? "Checking " : "Scanning ") + page.flacNum(n) + " of " + page.flacNum(total) + "…"
        }
        if (st.phase === "fixing") return "Writing " + page.flacNum(n) + " of " + page.flacNum(total || n) + "…"
        if (st.phase === "failed") return st.error || "The check failed."
        if (page.flacResumable() && (st.phase === "idle" || page.flacStale())) return "The last fix stopped before it finished."
        if ((st.phase !== "done" && st.phase !== "cancelled") || page.flacStale()) return "Not checked yet."
        var unset = page.flacCount("unset"), bad = page.flacCount("mismatch"), sealed = page.flacCount("sealed")
        var head = st.phase === "cancelled" ? "Stopped after " : (st.last === "fix" ? "Done: " : "Checked ")
        var parts = []
        if (st.last === "fix") {
            parts.push(page.flacNum(sealed) + (sealed === 1 ? " signature written" : " signatures written"))
            if (unset > 0) parts.push(page.flacNum(unset) + " left")
        } else {
            parts.push(page.flacNum(n) + (n === 1 ? " file" : " files"))
            parts.push(unset === 0 ? "none without a signature" : page.flacNum(unset) + " without a signature")
            // Files an earlier scan already read, unchanged since: their
            // verdict was reused rather than read (or decoded) again.
            var kept = st.cached || 0
            if (kept > 0) parts.push(page.flacNum(kept) + " unchanged since an earlier check")
        }
        if (bad > 0) parts.push(page.flacNum(bad) + (bad === 1 ? " does not match its signature" : " do not match their signature"))
        var other = page.flacCount("unreadable") + page.flacCount("decode_failed") + page.flacCount("unsupported")
                  + page.flacCount("length_differs") + page.flacCount("changed") + page.flacCount("busy")
                  + page.flacCount("write_failed")
        if (other > 0) parts.push(page.flacNum(other) + " skipped")
        // Folders the walk could not open or list: their files went unchecked,
        // so a run that missed some never reads as a clean one.
        var missed = page.flacMissed()
        if (missed > 0) parts.push(page.flacNum(missed) + (missed === 1 ? " folder could not be opened" : " folders could not be opened"))
        if (other > 0 || missed > 0) parts[parts.length - 1] += " (see the report)"
        return head + parts.join(", ") + "."
    }
    // How many folders the last finished run could not open (0 while stale).
    function flacMissed() {
        var st = page.flacState
        if (st.running || page.flacStale() || (st.phase !== "done" && st.phase !== "cancelled")) return 0
        return st.folders_unreadable || 0
    }

    // ---- Diagnostics export state ----
    property bool diagBusy: false        // an export is being written
    property string diagPath: ""         // last exported bundle path ("" = none yet)
    property bool diagFailed: false
    function auCheck() {
        if (page.auBusy || page.auChecking) return
        page.auChecking = true; page.auUpToDate = false; page.auCheckFailed = false; waves.checkAppUpdate(true)
    }

    // Deep-link: land the view on a section card, e.g. arriving from the
    // status bar's update notice. Deliberately a jump, not an animated
    // scroll: the user should drop in where they need to be, not watch the
    // page fly past. The destination card is opened as part of the jump:
    // with every section starting collapsed, a deep link to a shut card
    // would otherwise land on a bare header.
    function jumpToCard(cardId) {
        // The FLAC signatures card sits inside FFmpeg & FLAC: open that section,
        // then land on the card itself. The section grows over its 220ms open
        // animation, so the spot rides pendingY until it is reachable.
        if (cardId === "flac") {
            jumpToCard("processing")
            if (!page.flacCardItem) return
            page.pendingY = Math.max(0, page.flacCardItem.mapToItem(settingsFlick.contentItem, 0, 0).y - 16)
            page._restoreScroll()
            // The card sits near its section's end, so its top can lie past the
            // furthest reachable scroll and the restore would stay armed for
            // good, snapping back on a later re-measure (a SCAN's progress
            // row). Once the open animation has settled, settle for the
            // reachable spot and disarm.
            if (page.pendingY >= 0) { page.flacJumpY = page.pendingY; flacJumpSettle.restart() }
            return
        }
        for (var i = 0; i < secRep.count; i++) {
            var it = secRep.itemAt(i)
            // Match a special card key (ffmpeg/updates) or a plain section id
            // (e.g. "downloads"), so the folder gate can deep-link a regular
            // section the same way the update notice targets its card.
            if (it && (it.modelData.card === cardId || it.modelData.id === cardId)) {
                // An explicit destination outranks the remembered spot, which
                // would otherwise be re-applied on the same open and fight it.
                // Cleared here, not in showSection: that returns early when
                // the section is already the one shown, and a link can land
                // while the page has no height (keptY is re-armed on open).
                page.pendingY = -1
                page.keptY = 0
                page.showSection(it.modelData.id)
                settingsFlick.contentY = 0
                return
            }
        }
    }

    // ---- Holding your place across tabs ---------------------------------
    // The page stays mounted while another tab shows, but hiding it collapses
    // its Flickable to zero height and StopAtBounds then clamps contentY to
    // the top. Staying mounted is therefore not enough to keep the spot: the
    // position has to be recorded, and the clamp itself must not be recorded
    // (it lands while the height is 0, which is exactly how it is told apart
    // from a real scroll). Restored on the way back in, in the same layout
    // pass the height returns, so the page paints where it was rather than
    // scrolling there (see the app-wide no-visible-scroll rule).
    // The FLAC card while its section is built (jumpToCard("flac") lands on it).
    property var flacCardItem: null
    property real keptY: 0
    property real pendingY: -1
    // The spot jumpToCard("flac") armed, until its section has finished opening.
    property real flacJumpY: -1
    // Restarted on every re-measure while armed, so it fires once the
    // section's 220ms open animation stops growing the page.
    Timer {
        id: flacJumpSettle
        interval: 300
        onTriggered: {
            if (page.pendingY >= 0 && page.pendingY === page.flacJumpY) {
                page._restoreScroll()
                page.pendingY = -1
            }
            page.flacJumpY = -1
        }
    }
    // True while _restoreScroll itself writes contentY, so that write is not
    // taken for the user scrolling.
    property bool restoringScroll: false
    function _restoreScroll() {
        if (pendingY < 0 || settingsFlick.height <= 0) return
        var most = Math.max(0, settingsFlick.contentHeight - settingsFlick.height)
        restoringScroll = true
        settingsFlick.contentY = Math.min(pendingY, most)
        restoringScroll = false
        // Consume the restore only once it fit in full. A schema refresh on
        // the way back in rebuilds every card, so contentHeight passes
        // through near-zero before regrowing (the open animation takes
        // 220ms); consuming on the first, clamped application would strand
        // the page near the top. Staying armed rides each re-measure until
        // the remembered spot is reachable; any real user scroll or a
        // section click disarms it below.
        if (pendingY <= most) pendingY = -1
    }

    function val(f) { return editMap[f.key] !== undefined ? editMap[f.key] : f.value }
    // Secondary value for a composite field (the cover_sizes "separate file"
    // dropdown), read/written under its own file_key.
    function val2(f) { return editMap[f.file_key] !== undefined ? editMap[f.file_key] : f.file_value }
    // Child toggle value for the cover_scope composite, under its own child_key.
    function valChild(f) { return editMap[f.child_key] !== undefined ? editMap[f.child_key] : f.child_value }
    function setv(key, v) {
        var e = Object.assign({}, editMap); e[key] = v; editMap = e
        var u = Object.assign({}, unsaved); u[key] = true; unsaved = u
        dirty = true
    }
    // A "kinds" field holds a comma-joined pick from its options (the
    // Releases auto-download's albums, EPs, singles, featured); a tick
    // re-joins it in the options' own order.
    function kindOn(f, v) { return String(val(f)).split(",").indexOf(v) >= 0 }
    function kindToggle(f, v) {
        var on = !kindOn(f, v), out = []
        for (var i = 0; i < f.options.length; i++) {
            var o = f.options[i].value
            if (o === v ? on : kindOn(f, o)) out.push(o)
        }
        setv(f.key, out.join(","))
    }
    // A value applied LIVE (the diagnostics toggles push their pref straight
    // to the backend): shown from the edit map like any other, but never an
    // unsaved change, so SAVE and CANCEL stay inert for it. Marking it dirty
    // made CANCEL promise an undo it could not deliver.
    function setLive(key, v) { var e = Object.assign({}, editMap); e[key] = v; editMap = e }
    // Fields whose value the engine launders before use (the illegal-character
    // stand-in), by key. Their delegates register themselves here so the save
    // gate can find them without the page walking the whole schema.
    property var sanitizeKeys: ({})
    // True while the field holds something the laundering would strip, which is
    // what turns its box red. A per-character table is red while ANY of its
    // stand-ins is, so one bad row still holds the save.
    function sanitizeDirty(f) {
        if (f.type === "char_map") {
            var m = mapVal(f)
            for (var ch in m) if (mapCharDirty(f, ch)) return true
            return false
        }
        return f.sanitize === true && String(val(f)) !== waves.sanitizeFilenameReplacement(String(val(f)))
    }
    // The per-character stand-ins, as a plain {character: stand-in} object.
    // A character absent from it follows the general stand-in; present with an
    // empty value means removed outright, which is why the two are distinct.
    function mapVal(f) {
        var v = editMap[f.key] !== undefined ? editMap[f.key] : f.value
        return v ? v : ({})
    }
    function mapHas(f, ch) { return mapVal(f)[ch] !== undefined }
    function mapText(f, ch) { var v = mapVal(f)[ch]; return v === undefined ? "" : String(v) }
    function mapCharDirty(f, ch) {
        var v = mapText(f, ch)
        return v !== waves.sanitizeFilenameReplacement(v)
    }
    function mapSet(f, ch, t) {
        var m = {}, cur = mapVal(f)
        for (var k in cur) m[k] = cur[k]
        m[ch] = t
        setv(f.key, m)
    }
    function mapClear(f, ch) {
        var m = {}, cur = mapVal(f)
        for (var k in cur) if (k !== ch) m[k] = cur[k]
        setv(f.key, m)
    }
    // Stage a whole table at once (the recommended set, from the offer strip or
    // the Restore default link). Emitted so the boxes can re-bind their text:
    // the same precaution the single-value Restore default takes, since an
    // imperative write to a TextField's text would kill the binding for good.
    signal mapRestaged(string key)
    function mapStage(f, m) {
        var copy = {}
        for (var k in m) copy[k] = m[k]
        setv(f.key, copy)
        mapRestaged(f.key)
    }
    // Whether the table on screen already is the recommended one, entry for
    // entry (an extra override, or one missing, counts as different).
    function mapIsDefault(f) {
        var d = f.default_value
        if (d === undefined) return true
        var cur = mapVal(f)
        for (var k in d) if (cur[k] === undefined || String(cur[k]) !== String(d[k])) return false
        for (var c in cur) if (d[c] === undefined) return false
        return true
    }
    // The recommended stand-ins in the order the table lists them, so the offer
    // strip and the boxes under it read the same way round.
    function mapDefaultChars(f) {
        var d = f.default_value ? f.default_value : ({})
        var chars = f.chars ? f.chars : []
        var out = []
        for (var i = 0; i < chars.length; i++) if (d[chars[i].char] !== undefined) out.push(chars[i].char)
        return out
    }
    // What an unnamed character falls back to, read live so the placeholders
    // follow the general stand-in box as it is typed in, before any save.
    function generalStandIn() {
        var f = fieldByKey("filename_illegal_replacement")
        return f ? String(val(f)) : ""
    }
    // Any red box blocks SAVE CHANGES. Rewriting the value on save instead
    // would flash "changes saved" over a silent correction; graying the button
    // leaves the bad character on screen, red, for the user to fix.
    // Judges the value on screen, not just what was typed this visit: a config
    // edited by hand outside the app shows red too, and a red box with a live
    // Save button would be the page contradicting itself.
    function hasInvalidEdits() {
        for (var k in page.sanitizeKeys) {
            if (sanitizeDirty(page.sanitizeKeys[k])) return true
        }
        return false
    }
    // Within a section, on/off switches render as a tile grid and everything
    // else as labeled rows. A `master` switch is neither: it rides beside the
    // section title (masterField below).
    function boolFields(fields) { return fields.filter(function(f){ return f.type === "bool" && f.embedded !== true && f.rowToggle !== true && f.master !== true }) }
    function rowFields(fields)  { return fields.filter(function(f){ return (f.type !== "bool" || f.rowToggle === true) && f.embedded !== true && f.third !== true && f.type !== "char_map" && f.master !== true }) }
    function masterField(fields) {
        for (var i = 0; i < fields.length; i++) if (fields[i].master === true) return fields[i]
        return null
    }
    // The line under a master section's blurb. Off: what off means. Staged
    // on: what SAVE will do. Saved on: nothing to say.
    function masterNote(f) {
        if (val(f) !== true) return f.off_note || ""
        if (saved(f) !== true) return f.arm_note || ""
        return ""
    }
    // The per-character stand-in table renders under the short-value row it
    // extends, so it is pulled out of the ordinary rows above them.
    function mapFields(fields)  { return fields.filter(function(f){ return f.type === "char_map" }) }
    // Fields whose value is a character or two (the delimiters, the illegal
    // character stand-in): a whole row for a comma is absurd, so three of
    // them share one line, each a compact box beside its help.
    function thirdFields(fields) { return fields.filter(function(f){ return f.type !== "bool" && f.embedded !== true && f.third === true }) }
    // Fields marked `embedded` are rendered inside a section card (the updater
    // card hosts auto_update + update_cadence); look their descriptors up here.
    function fieldByKey(key) {
        for (var g = 0; g < groups.length; g++) {
            var fs = groups[g].fields
            for (var i = 0; i < fs.length; i++) if (fs[i].key === key) return fs[i]
        }
        return null
    }
    // A field with `depends_on` is shown only while that key is on, using the
    // live edited value if present, otherwise the value baked into the schema.
    function depOK(f) {
        if (!f.depends_on) return true
        var cur = editMap[f.depends_on] !== undefined ? editMap[f.depends_on] : f.depends_on_value
        return cur === true
    }
    // Dropdown options are {value, label}; find the row for a stored value.
    // Tolerates a missing options list: the composite cover_sizes delegate
    // instantiates its dropdowns for every field (one shown by type), so this
    // is evaluated even where a field has no options.
    function enumIndex(options, value) {
        if (!options) return 0
        for (var i = 0; i < options.length; i++) if (options[i].value === value) return i
        return 0
    }
    function urlToPath(u) {
        var s = decodeURIComponent(String(u).replace(/^file:\/\//, ""))
        // Windows file URLs are file:///C:/… ; after stripping file:// that leaves
        // /C:/…, drop the leading slash before the drive letter so the result is a
        // valid path (Linux file:///home/… already yields /home/… and is untouched).
        if (/^\/[A-Za-z]:/.test(s)) s = s.substring(1)
        // A network share puts its host in the URL authority: \\nas\music (or
        // //nas/music) becomes file://nas/music, which the strip above turns
        // into the RELATIVE "nas/music"; saved like that, downloads land in a
        // nas/music folder beside the working directory. Restore the UNC form.
        else if (s === "localhost" || s.indexOf("localhost/") === 0) s = s.substring(9) || "/"
        else if (s.length > 0 && s[0] !== "/") s = "//" + s
        return s
    }
    // Inverse of urlToPath for a path's parent directory: file URL for the
    // folder containing `p`, or "" when there is no usable directory. Lets a
    // Browse… dialog open right where the current value already points.
    function pathUrl(p) {
        var s = String(p || "").trim()
        if (s === "") return ""
        // Windows drive-letter paths need the extra slash (file:///C:/…); a
        // UNC path (//nas/music or \\nas\music) keeps its host in the
        // authority (file://nas/…).
        if (/^\\\\/.test(s)) s = "//" + s.substring(2).replace(/\\/g, "/")
        var drive = /^[A-Za-z]:/.test(s)
        if (drive) s = s.replace(/\\/g, "/")
        // Percent-encode the path so a folder named "AC#DC" or "What?" is not
        // cut at the "#" or "?" (a URL reads those as fragment and query);
        // urlToPath decodes it back.
        try { s = encodeURI(s).replace(/#/g, "%23").replace(/\?/g, "%3F") } catch (e) { return "" }
        if (/^\/\//.test(s)) return "file:" + s
        return (drive ? "file:///" : "file://") + s
    }
    function dirUrlOf(p) {
        var s = String(p || "").trim()
        var cut = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"))
        return cut > 0 ? pathUrl(s.substring(0, cut)) : ""
    }
    function refreshSchema() { groups = waves.settingsSchema(); savedVals = ({}); needsRefresh = false }
    // A switch tile narrower than this puts its switch beside the label
    // instead of beside the words, so the help keeps a readable width.
    readonly property real tileCompactW: 288
    // Every switch tile puts its switch in one place: the top right corner,
    // tilePadT from the top, with the first line of the title level with
    // it (tileTitleNudge). The words start there whatever their length, so
    // a short help leaves its room at the bottom instead of floating the
    // title and switch down.
    readonly property real switchW: 54
    readonly property real switchH: 26
    readonly property real tilePadT: 12
    readonly property real tilePadB: 12
    // A tile's sub-option strip: flush along its bottom edge, in room every
    // tile in the row keeps, so turning the parent on never grows the row
    // (notes/pocket_anim_lab, C2d).
    readonly property real tileStripH: 34
    // One clock for a switch and everything it opens, one for a key: every
    // layer reads the same eased value, so nothing starts or ends alone.
    readonly property int openMs: 260
    readonly property int keyMs: 160
    function mix(a, b, k) { return Qt.rgba(a.r + (b.r - a.r) * k, a.g + (b.g - a.g) * k, a.b + (b.b - a.b) * k, a.a + (b.a - a.a) * k) }
    FontMetrics { id: tileTitleFm; font.pixelSize: 14; font.weight: Font.Medium }
    FontMetrics { id: tileHelpFm; font.pixelSize: 12 }
    readonly property real tileTitleLineH: Math.ceil(tileTitleFm.height)
    readonly property real tileHelpLineH: tileHelpFm.height * 1.15
    readonly property real tileTitleNudge: Math.max(0, Math.round((switchH - tileTitleLineH) / 2))
    // A switch tile's height and help lines. `third`: three across (the
    // narrower tile, one more help line); `compact`: the help runs under
    // the switch (one more again, and a head at least the switch's height);
    // `lines`: the most lines a label in the row wraps to; `help`: the most
    // a help in the row takes (at most tileHelpLines), so a wide window,
    // where every help is short, does not keep the narrow window's room.
    function tileHelpLines(third, compact) { return (third ? 4 : 3) + (compact ? 1 : 0) }
    function tileHeight(third, compact, lines, help) {
        var title = tileTitleNudge + lines * tileTitleLineH
        var head = compact ? Math.max(switchH, title) : title
        var n = help === undefined ? tileHelpLines(third, compact) : Math.max(1, help)
        return Math.ceil(tilePadT + head + 3 + n * tileHelpLineH + tilePadB)
    }
    // The least a value row's label and help keep beside its control; a
    // control that would leave less moves under them.
    readonly property real rowWordsMinW: 260

    // ---- Section rail state ---------------------------------------------
    // One section shows at a time, picked from the rail. The rail groups the
    // backend's sections (settingsSchema) under three heads; every section id
    // the backend sends must be listed here, or it can never be opened (a
    // test holds the two lists together).
    readonly property var railGroups: [
        { head: "MUSIC", ids: ["downloads", "library", "releases", "discography"] },
        { head: "FILES", ids: ["files", "metadata", "processing"] },
        { head: "APP",   ids: ["appearance", "notifications", "pacing", "updates", "diagnostics"] }
    ]
    // The section on show. Persisted as a waves pref, so Settings reopens on
    // the section you left it on, across visits and across launches.
    property string curSec: "downloads"
    // A rail id is a section only while the schema sends its group: one the
    // backend leaves out (Notifications without banner support) is hidden on
    // the rail and must not be reachable by arrow keys or a remembered pick.
    function railHas(id) {
        for (var i = 0; i < railGroups.length; i++)
            if (railGroups[i].ids.indexOf(id) >= 0) return groupById(id) !== null
        return false
    }
    function loadSection() {
        var v = String(waves.wavesPref("settings_section") || "")
        // A section that no longer exists (renamed or merged by an update)
        // falls back to the first one rather than an empty pane.
        if (railHas(v)) curSec = v
    }
    function showSection(id) {
        if (!railHas(id) || id === curSec) return
        curSec = id; pendingY = -1; settingsFlick.contentY = 0
        waves.setWavesPref("settings_section", id)
    }
    // The rail's head over a section, as the small crumb above its title
    // ("MUSIC" reads "Music").
    function railCrumb(id) {
        for (var i = 0; i < railGroups.length; i++)
            if (railGroups[i].ids.indexOf(id) >= 0) {
                var h = railGroups[i].head
                return h.charAt(0) + h.slice(1).toLowerCase()
            }
        return ""
    }
    // Arrow keys on the rail step through its sections in order.
    function stepSection(by) {
        var all = []
        for (var i = 0; i < railGroups.length; i++)
            for (var j = 0; j < railGroups[i].ids.length; j++)
                if (railHas(railGroups[i].ids[j])) all.push(railGroups[i].ids[j])
        var k = all.indexOf(curSec) + by
        if (k >= 0 && k < all.length) showSection(all[k])
    }
    // The rail's own scroller, for the heart burst, which bounces off the
    // rail's edges (gibOverlay).
    property Flickable railScroller: null
    function groupById(id) { for (var i = 0; i < groups.length; i++) if (groups[i].id === id) return groups[i]; return null }
    // Does this section hold an unsaved edit? (the rail's green dot)
    function secDirty(id) {
        var g = groupById(id)
        if (!g) return false
        for (var i = 0; i < g.fields.length; i++) if (fieldDirty(g.fields[i])) return true
        return false
    }

    // CANCEL throws away what you typed and leaves you on the page, at the
    // spot you were reading. It does NOT leave Settings: the nav does that.
    // The schema is re-pulled because a save earlier in this visit leaves
    // `groups` holding the pre-save values (applySettings keeps editMap
    // showing the saved ones), so dropping editMap alone would show stale
    // text. Arm the scroll restore first: rebuilding every card re-measures
    // the column, and the restore rides that layout pass instead of letting
    // the clamp strand the page at the top.
    function discardEdits() {
        pendingY = settingsFlick.contentY
        editMap = ({})
        unsaved = ({})
        dirty = false
        refreshSchema()
        _restoreScroll()
    }

    // Line-art glyph (SVG path data, 16-unit box) for each section's leading
    // icon tile, drawn by `SectionIcon` via QtQuick.Shapes. Falls back to the
    // sliders glyph for any unknown section id.
    function iconPath(id) {
        switch (id) {
        case "downloads":   return "M8 2.5V9.3 M5.2 6.6 L8 9.4 L10.8 6.6 M3.4 12.6H12.6"
        case "files":       return "M2.6 5.4H6.2L7.4 6.7H13.4V11.9H2.6Z"
        case "metadata":    return "M8.6 2.6H3.1V8L9 13.9L14.4 8.5Z M5.7 4.9A0.55 0.55 0 1 1 4.6 4.9A0.55 0.55 0 1 1 5.7 4.9Z"
        case "processing":  return "M5.2 5.2H10.8V10.8H5.2Z M6.7 5.2V3.6 M9.3 5.2V3.6 M6.7 10.8V12.4 M9.3 10.8V12.4 M5.2 6.7H3.6 M5.2 9.3H3.6 M10.8 6.7H12.4 M10.8 9.3H12.4"
        case "discography": return "M2.6 8A5.4 5.4 0 1 0 13.4 8A5.4 5.4 0 1 0 2.6 8Z M6.9 8A1.1 1.1 0 1 0 9.1 8A1.1 1.1 0 1 0 6.9 8Z"
        // Book spines on a shelf: the section never had its own glyph and
        // fell through to the generic sliders.
        case "library":     return "M2.6 12.6H13.4 M4.4 5V12.6 M6.8 4.2V12.6 M9.2 3.4V12.6 M11.6 5.6V12.6"
        // A bell: what rings when an artist you follow releases something.
        case "releases":    return "M4.6 11H11.4L10.4 9.6V7A2.4 2.4 0 0 0 5.6 7V9.6Z M7 12.6H9 M8 4.6V3.4"
        // A phone with buzz marks at its sides: the alert this section
        // controls, kept apart from the Releases bell.
        case "notifications": return "M6.2 2.4H9.8A1.2 1.2 0 0 1 11 3.6V12.4A1.2 1.2 0 0 1 9.8 13.6H6.2A1.2 1.2 0 0 1 5 12.4V3.6A1.2 1.2 0 0 1 6.2 2.4Z M7.5 11.6H8.5 M2.8 6.2V9.8 M13.2 6.2V9.8"
        case "updates":     return "M8 3V12.4 M4.6 6.4L8 3L11.4 6.4"
        case "coffee":      return "M2.8 6.4H11V10.2A3.2 3.2 0 0 1 7.8 13.4H6A3.2 3.2 0 0 1 2.8 10.2Z M11 7.4H12A1.9 1.9 0 0 1 12 11.2H11 M5.6 2.4V4.4 M8.2 2.4V4.4"
        case "pacing":      return "M2.8 11.6A5.4 5.4 0 1 1 13.2 11.6 M8 9.2L10.6 5.8 M7.2 9.2A0.8 0.8 0 1 0 8.8 9.2A0.8 0.8 0 1 0 7.2 9.2Z"
        case "appearance":  return "M1.8 8C3.6 4.9 5.6 3.8 8 3.8C10.4 3.8 12.4 4.9 14.2 8C12.4 11.1 10.4 12.2 8 12.2C5.6 12.2 3.6 11.1 1.8 8Z M6.3 8A1.7 1.7 0 1 0 9.7 8A1.7 1.7 0 1 0 6.3 8Z"
        // Pulse/heartbeat trace: diagnostics watch the app's vitals.
        case "diagnostics": return "M2.6 8H5.4L6.8 4.6L9.2 11.4L10.6 8H13.4"
        default:            return "M3 5.4H13 M3 10.6H13 M5.4 5.4A1.5 1.5 0 1 0 8.4 5.4A1.5 1.5 0 1 0 5.4 5.4Z M7.6 10.6A1.5 1.5 0 1 0 10.6 10.6A1.5 1.5 0 1 0 7.6 10.6Z"
        }
    }

    // Front-load the build at startup (behind the login overlay) so every
    // open of the page is instant. The startup update check is opt-in and
    // throttled in the backend, it no-ops unless the user enabled it.
    Component.onCompleted: { refreshSchema(); loadSection(); auRefresh(); waves.startupUpdateCheck(); waves.startupFfmpegUpdateCheck() }

    onActiveChanged: {
        if (active) {
            // Arm before anything else: a schema refresh below can re-measure
            // the column, and the restore rides the layout pass that follows.
            pendingY = keptY
            _restoreScroll()
            editMap = ({})
            unsaved = ({})
            dirty = false
            if (needsRefresh) refreshSchema()
            // Refresh local status only (no network). Update checks are
            // user-initiated via the "Check for updates" button.
            if (page.ff) page.ff.refresh()
            auRefresh()
            // Re-sync the library card's live mirrors (its state applies
            // outside the Save flow, so nothing else refreshes them on open).
            syncLibraryMirrors()
            flacRefresh()
        }
    }

    // In-app updater signals from the backend. (FFmpeg signals are handled by
    // the shared FfmpegManager, `page.ff`.)
    Connections {
        target: waves
        function onSettingsPersistedExternally() {
            // The backend persisted schema-backed settings without the Save
            // button ("Don't ask again", the player's quality menu, the
            // folder auto-heal): rebuild now if the page is idle, otherwise
            // on its next open, so it never shows values the app already
            // changed underneath it. Unsaved edits are never clobbered.
            if (page.active && !page.dirty) page.refreshSchema()
            else page.needsRefresh = true
        }
        function onAppUpdateStateChanged(state, msg) {
            page.auState = state; page.auMsg = msg
            if (state === "done" || state === "failed" || state === "cancelled") page.auPct = 0
        }
        function onAppUpdateProgress(pct) { page.auPct = pct }
        function onAppUpdateStatusChanged() { page.auRefresh() }
        function onAppUpdatePending(v) {
            page.auState = "done"
            page.auMsg = "Update to " + v + " is ready. Restart to finish."
        }
        function onAppUpdateChecked(available, current, latest) {
            var wasManual = page.auChecking
            page.auChecking = false
            page.auUpdate = available
            page.auLatest = latest
            // Only flash "up to date" for a user-initiated check, never the
            // silent startup one. A check that threw (offline, API error)
            // arrives as available=false with an EMPTY latest: that is not
            // "up to date", it is "could not check", and says so instead.
            if (!available && wasManual) {
                if (latest === "") { page.auCheckFailed = true; auCheckFailedTimer.restart() }
                else { page.auUpToDate = true; auUpToDateTimer.restart() }
            }
        }
        function onDiagnosticsExported(path) {
            page.diagBusy = false
            page.diagPath = path
            page.diagFailed = (path === "")
        }
        function onFlacCheckChanged() { page.flacRefresh() }
    }


    // ---- Control helpers ------------------------------------------------
    // "Lossless (16-bit)" -> main "Lossless", sub "16-bit".
    function optMain(l) { var m = /^(.*?)\s*\((.*)\)\s*$/.exec(String(l)); return m ? m[1] : String(l) }
    function optSub(l)  { var m = /^(.*?)\s*\((.*)\)\s*$/.exec(String(l)); return m ? m[2] : "" }
    // Options that climb (quality, months, sizes) draw a level mark.
    function isLadder(key) {
        return ["quality_audio", "quality_video", "releases_window_months", "metadata_cover_dimension"].indexOf(key) >= 0
    }
    // Does this row hold an unsaved edit?
    // The library card's switches ride their own sub-keys, so those count too.
    readonly property var dirtyKeys: ["key", "file_key", "child_key", "enabled_key", "bulk_key", "mb_key", "lookup_key", "follow_key", "min_tracks_key"]
    function fieldDirty(f) {
        if (!f) return false
        for (var i = 0; i < dirtyKeys.length; i++) {
            var k = f[dirtyKeys[i]]
            if (k && unsaved[k]) return true
        }
        return (f.lo !== undefined && unsaved[f.lo.key] === true) || (f.hi !== undefined && unsaved[f.hi.key] === true)
    }

    // ---- Controls -------------------------------------------------------

    // A key switch: the cap slides over engraved ON / OFF legends and its
    // LED lights. Pure visual: the enclosing tile's (or row's) MouseArea
    // drives `checked` via page.val/page.setv.
    component SToggle: Rectangle {
        id: tg
        property bool checked: false
        implicitWidth: 54; implicitHeight: 26
        width: implicitWidth; height: implicitHeight
        radius: 7; color: page.surface0
        border.color: checked ? page.accentDim : page.outline
        Behavior on border.color { ColorAnimation { duration: page.openMs } }
        Text {
            x: 8; anchors.verticalCenter: parent.verticalCenter
            text: "ON"; color: page.accent; font.family: page.mono; font.pixelSize: 8; font.bold: true; font.letterSpacing: 1
            opacity: tg.checked ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: page.openMs } }
        }
        Text {
            anchors.right: parent.right; anchors.rightMargin: 6; anchors.verticalCenter: parent.verticalCenter
            text: "OFF"; color: page.textDim; font.family: page.mono; font.pixelSize: 8; font.bold: true; font.letterSpacing: 1
            opacity: tg.checked ? 0 : 1
            Behavior on opacity { NumberAnimation { duration: page.openMs } }
        }
        Rectangle {
            y: 3; width: 24; height: parent.height - 6; radius: 5
            x: tg.checked ? tg.width - width - 3 : 3
            Behavior on x { NumberAnimation { duration: page.openMs; easing.type: Easing.OutBack; easing.overshoot: 1.3 } }
            gradient: Gradient {
                GradientStop { position: 0; color: page.surfaceHi }
                GradientStop { position: 1; color: page.surface3 }
            }
            border.color: page.outline
            Rectangle {
                anchors.centerIn: parent; width: 18; height: 11; radius: 5.5
                color: page.accent; opacity: tg.checked ? 0.2 : 0
                Behavior on opacity { NumberAnimation { duration: page.openMs } }
            }
            Rectangle {
                anchors.centerIn: parent; width: 10; height: 4; radius: 2
                color: tg.checked ? page.accent : page.outline
                Behavior on color { ColorAnimation { duration: page.openMs } }
            }
        }
    }

    // The inside of a switch tile: title, help and switch, placed the same
    // way in every tile (see tilePadT). `compact` (a narrow tile) lets the
    // help run under the switch, so it keeps a readable width. Rows a tile
    // adds (a hint, a nested key) go under the help.
    component TileBody: Item {
        id: tb
        property string title: ""
        property string help: ""
        property bool helpShown: help !== ""
        property bool checked: false
        property bool compact: false
        property int helpLines: 3
        property real padL: 16
        property real padR: 14
        // The switch takes its own click (on a tile that is no click target).
        property bool switchClickable: false
        signal switchClicked()
        readonly property int titleLines: tbTitle.lineCount
        // The lines the help takes at its full room (`helpCap`), measured
        // apart from what shows: a nested key or a missing prerequisite
        // changes what shows but never the row's height.
        property int helpCap: helpLines
        readonly property int helpNatural: help === "" ? 0 : tbMeasure.lineCount
        default property alias extras: tbCol.data
        objectName: "tileBody"
        anchors.fill: parent
        ColumnLayout {
            id: tbCol
            anchors.left: parent.left; anchors.leftMargin: tb.padL
            anchors.right: parent.right; anchors.rightMargin: tb.compact ? tb.padR : tb.padR + page.switchW + 14
            anchors.top: parent.top; anchors.topMargin: page.tilePadT
            spacing: 3
            Text {
                id: tbTitle
                textFormat: Text.PlainText; text: tb.title; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium
                wrapMode: Text.WordWrap; maximumLineCount: tb.compact ? 3 : 2; elide: Text.ElideRight
                Layout.fillWidth: true
                Layout.topMargin: page.tileTitleNudge
                Layout.rightMargin: tb.compact ? page.switchW + 10 : 0
                // Narrow: the help starts under the switch, never beside it.
                Layout.preferredHeight: tb.compact ? Math.max(page.switchH - page.tileTitleNudge, implicitHeight) : implicitHeight
            }
            Text {
                visible: tb.helpShown
                textFormat: Text.PlainText
                text: tb.help; color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                wrapMode: Text.WordWrap; maximumLineCount: Math.max(1, tb.helpLines)
                elide: Text.ElideRight; Layout.fillWidth: true
            }
        }
        Text {
            id: tbMeasure
            visible: false; width: tbCol.width
            textFormat: Text.PlainText; text: tb.help; font.pixelSize: 12; lineHeight: 1.15
            wrapMode: Text.WordWrap; maximumLineCount: Math.max(1, tb.helpCap)
        }
        SToggle {
            objectName: "tileSwitch"
            anchors.right: parent.right; anchors.rightMargin: tb.padR
            y: page.tilePadT
            checked: tb.checked
            MouseArea {
                anchors.fill: parent; enabled: tb.switchClickable; visible: enabled
                cursorShape: Qt.PointingHandCursor
                onClicked: tb.switchClicked()
            }
        }
    }

    // The round check (notes/check_lab, look C). `k` runs 0 to 1 as it
    // picks; fill, ring and mark all read it, so they move as one.
    component RoundCheck: Rectangle {
        id: rck
        property real k: 0
        width: 13; height: 13; radius: 6.5
        color: Qt.rgba(page.accent.r, page.accent.g, page.accent.b, k)
        border.width: 1; border.color: page.mix(page.textLo, page.accent, k)
        Ico { anchors.centerIn: parent; opacity: rck.k; scale: 0.6 + 0.4 * rck.k; name: "check"; color: page.accentText; size: 9; bold: 14 }
    }

    // A channel key: a small lit key with an LED, for multi-picks.
    component SCheck: Item {
        id: ck
        property bool on: false
        property string label: ""
        property real k: ck.on ? 1 : 0
        Behavior on k { NumberAnimation { duration: page.keyMs; easing.type: Easing.OutCubic } }
        // Circle 13 + gap 7 + label + 14 of padding (from the label's own width,
        // not the row's, so a layout narrowing the key cannot loop back).
        implicitWidth: ckTxt.implicitWidth + 34; implicitHeight: 26
        Rectangle {
            anchors.fill: parent; radius: 7
            color: page.mix(page.surface0, page.accentCont, ck.k)
            border.color: page.mix(page.outline, page.accentDim, ck.k)
        }
        Row {
            id: ckRow
            anchors.centerIn: parent; spacing: 7
            // A small round check, so the key reads as a pick (a bare dot
            // read as a status light).
            RoundCheck { anchors.verticalCenter: parent.verticalCenter; k: ck.k }
            Text {
                id: ckTxt
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText; text: ck.label
                // Hugs its label, but elides when a layout gives it less.
                width: Math.min(implicitWidth, Math.max(0, ck.width - 34))
                elide: Text.ElideRight
                color: page.mix(page.textLo, page.accentContTx, ck.k)
                font.pixelSize: 12; font.weight: Font.Medium
            }
        }
    }

    // Segmented selector: every option on show, a sprung thumb under the
    // pick. "Lossless (16-bit)" splits into a name and a mono subline; climbing
    // options carry a four-step level mark. Like the pull-down it replaced, it
    // takes Tab focus, and Left / Right (or Up / Down) step the pick.
    // `currentIndex` is a binding on page.val(...): it is never assigned here,
    // `picked` routes through page.setv and the binding follows, so a canceled
    // edit cannot leave a stale pick on screen.
    component SChoice: Rectangle {
        id: ch
        property var options: []
        property int currentIndex: 0
        property bool ladder: false
        signal picked(int index)
        activeFocusOnTab: true
        function stepPick(by) {
            var k = Math.max(0, Math.min(options.length - 1, currentIndex + by))
            if (k !== currentIndex && enabled) picked(k)
        }
        Keys.onLeftPressed: stepPick(-1)
        Keys.onRightPressed: stepPick(1)
        Keys.onUpPressed: stepPick(-1)
        Keys.onDownPressed: stepPick(1)
        readonly property bool twoLine: {
            for (var i = 0; i < options.length; i++) if (page.optSub(options[i].label) !== "") return true
            return false
        }
        readonly property Item curItem: chRep.count > 0 && currentIndex >= 0 && currentIndex < chRep.count ? chRep.itemAt(currentIndex) : null
        implicitWidth: chRow.implicitWidth + 4
        implicitHeight: twoLine ? 42 : 30
        radius: 8; color: page.surface0
        border.color: activeFocus ? page.accentDim : page.outline
        Rectangle {
            visible: ch.curItem !== null
            y: 2; height: parent.height - 4; radius: 6
            x: ch.curItem ? ch.curItem.x + 2 : 2
            width: ch.curItem ? ch.curItem.width : 0
            color: page.accentCont; border.color: page.accentDim
            Behavior on x { NumberAnimation { duration: 300; easing.type: Easing.OutBack; easing.overshoot: 1.1 } }
            Behavior on width { NumberAnimation { duration: 300; easing.type: Easing.OutCubic } }
        }
        Row {
            id: chRow
            x: 2; y: 2; height: parent.height - 4
            Repeater {
                id: chRep
                model: ch.options
                delegate: Item {
                    id: chSeg
                    required property var modelData
                    required property int index
                    readonly property bool sel: index === ch.currentIndex
                    readonly property int lit: Math.max(1, Math.round((index + 1) / Math.max(1, ch.options.length) * 4))
                    width: chCol.width + (ch.ladder ? 42 : 24); height: parent.height
                    Row {
                        visible: ch.ladder
                        x: 11; anchors.verticalCenter: parent.verticalCenter; spacing: 2
                        height: 13
                        Repeater {
                            model: 4
                            delegate: Rectangle {
                                required property int index
                                anchors.bottom: parent.bottom
                                width: 2; height: 4 + index * 3; radius: 1
                                color: index < chSeg.lit ? (chSeg.sel ? page.accent : page.textDim) : page.divider
                                Behavior on color { ColorAnimation { duration: 200 } }
                            }
                        }
                    }
                    Column {
                        id: chCol
                        x: ch.ladder ? 31 : 12; anchors.verticalCenter: parent.verticalCenter
                        spacing: 1
                        Text {
                            textFormat: Text.PlainText; text: page.optMain(chSeg.modelData.label)
                            color: chSeg.sel ? page.accent : chMa.containsMouse ? page.textHi : page.textLo
                            font.pixelSize: 12; font.weight: Font.DemiBold
                            Behavior on color { ColorAnimation { duration: 160 } }
                        }
                        Text {
                            visible: text !== ""
                            textFormat: Text.PlainText; text: page.optSub(chSeg.modelData.label)
                            color: chSeg.sel ? page.accentDim : page.textDim
                            font.family: page.mono; font.pixelSize: 9; font.letterSpacing: 0.4
                        }
                    }
                    MouseArea {
                        id: chMa
                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: ch.picked(chSeg.index)
                    }
                }
            }
        }
    }

    // An inset field; a green caret bar rises on its left edge while typing.
    component SText: Rectangle {
        property alias text: tf.text
        // What an empty box stands for, grayed (the per-character stand-ins
        // show the value the character falls back to).
        property alias placeholderText: tf.placeholderText
        property bool focused: tf.activeFocus
        // Red outline while the value carries something the field cannot keep.
        property bool invalid: false
        radius: 7; color: invalid ? page.redCont : page.surface0
        border.color: invalid ? page.red : tf.activeFocus ? page.accentDim : page.outline
        Behavior on border.color { ColorAnimation { duration: 160 } }
        implicitHeight: 34
        property var onEdited: (function(t){})
        Rectangle {
            x: 4; width: 3; radius: 1.5; anchors.verticalCenter: parent.verticalCenter
            height: tf.activeFocus ? parent.height - 14 : 0
            color: page.accent
            Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
        }
        TextField {
            id: tf
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 12
            verticalAlignment: TextInput.AlignVCenter
            color: page.textHi; font.family: page.mono; font.pixelSize: 13
            placeholderTextColor: page.textDim
            background: Rectangle { color: "transparent" }
            onTextEdited: parent.onEdited(text)
            // A long path template otherwise renders scrolled to its END (the cursor
            // lands there once the text is set), hiding the start. Scroll back to the
            // start whenever the field isn't being actively edited: on initial
            // populate, and again after the user clicks away.
            onTextChanged: if (!activeFocus) cursorPosition = 0
            onActiveFocusChanged: if (!activeFocus) cursorPosition = 0
        }
    }

    // Numeric stepper: an LED readout between minus and plus keys. Integer by
    // default; set `step`/`decimals` for decimals (step 0.5 / decimals 1 for
    // the second-scale delays). Drag the readout sideways to scrub; each
    // change ticks the digits.
    component SStepper: Rectangle {
        id: st
        property real value: 0
        property real minimum: 1
        property real maximum: 9999
        property real step: 1
        property int decimals: 0
        signal changed(real v)
        // Clamp/round and emit ONLY, never assign `value` here. `value` is a
        // declarative binding to page.val(modelData); the parent's onChanged
        // routes through page.setv, which updates editMap and re-evaluates that
        // binding, so the display follows. Assigning `value` would break the
        // binding, and since delegates are kept alive across close/reopen a
        // canceled edit would then persist visually with Save disabled.
        function apply(v) {
            var r = Math.pow(10, decimals)
            changed(Math.round(Math.max(minimum, Math.min(maximum, v)) * r) / r)
        }
        implicitWidth: 126; implicitHeight: 32
        width: implicitWidth; height: implicitHeight
        radius: 8; color: page.surface0
        border.color: stScrub.pressed ? page.accentDim : page.outline
        onValueChanged: stTick.restart()
        Item {
            width: 32; height: parent.height
            Ico { anchors.centerIn: parent; name: "minus"; size: 15; color: st.value > st.minimum ? page.accent : page.textDim }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: st.apply(st.value - st.step) }
        }
        Rectangle { x: 32; y: 7; width: 1; height: parent.height - 14; color: page.divider }
        Item {
            x: 33; width: parent.width - 66; height: parent.height
            Text {
                id: stTxt
                anchors.centerIn: parent
                textFormat: Text.PlainText; text: st.value.toFixed(st.decimals)
                color: page.accent; font.family: page.mono; font.pixelSize: 16; font.bold: true
            }
            SequentialAnimation {
                id: stTick
                NumberAnimation { target: stTxt; property: "scale"; to: 1.16; duration: 60 }
                NumberAnimation { target: stTxt; property: "scale"; to: 1; duration: 200; easing.type: Easing.OutBack }
            }
            MouseArea {
                id: stScrub
                anchors.fill: parent; cursorShape: Qt.SizeHorCursor; preventStealing: true
                property real x0: 0
                property real v0: 0
                onPressed: function(m) { x0 = m.x; v0 = st.value }
                onPositionChanged: function(m) { if (pressed) st.apply(v0 + Math.round((m.x - x0) / 12) * st.step) }
            }
        }
        Rectangle { x: parent.width - 33; y: 7; width: 1; height: parent.height - 14; color: page.divider }
        Item {
            x: parent.width - 32; width: 32; height: parent.height
            Ico { anchors.centerIn: parent; name: "plus"; size: 14; color: st.value < st.maximum ? page.accent : page.textDim }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: st.apply(st.value + st.step) }
        }
    }

    // The flag grid's on/off tile (words on the left, the key switch on the
    // right), factored out so a composite card can carry switches of its own
    // that look like every other switch on the page (the Library card's two
    // options). Same box, same hover, same whole-tile click target; `keyName`
    // stages into the edit map, so it commits with SAVE CHANGES like the
    // grid's tiles do. Disabling an ancestor grays and deadens it, since
    // `enabled` propagates to the MouseArea.
    component FlagTile: Rectangle {
        id: ftile
        property string keyName: ""
        property string label: ""
        property string help: ""
        property bool checked: false
        // A third of the row is narrower, so the help gets one more line
        // (the same 100 / 4 the flag grid's three-across rows use).
        property int helpLines: 3
        // Narrow: the switch sits beside the label, as in the flag grid. These
        // tiles sit inside a padded card (the Library card), so they go
        // compact a little sooner than the grid's.
        readonly property bool compact: width < page.tileCompactW + 72
        // This tile's label lines, and the most any tile beside it has (the
        // host sets it), so a row of tiles keeps one height.
        readonly property int labelLines: ftBody.titleLines
        property int rowLabelLines: 1
        // The same for the help: its lines, and the most beside it.
        readonly property int helpNatural: ftBody.helpNatural
        property int rowHelpLines: 1
        height: page.tileHeight(helpLines > 3, compact, Math.max(labelLines, rowLabelLines), Math.max(helpNatural, rowHelpLines))
        radius: 10; color: page.surface0
        border.color: (ftMouse.containsMouse && ftile.enabled) ? page.outline : page.border1
        Behavior on border.color { ColorAnimation { duration: 120 } }
        MouseArea {
            id: ftMouse
            anchors.fill: parent; hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: page.setv(ftile.keyName, !ftile.checked)
        }
        TileBody {
            id: ftBody
            title: ftile.label; help: ftile.help; checked: ftile.checked
            compact: ftile.compact
            helpLines: page.tileHelpLines(ftile.helpLines > 3, ftile.compact)
        }
    }

    // The auto-check strip (words with the cadence under them, key switch),
    // shared by the
    // Updates and FFmpeg cards so the two are identical by construction.
    // `autoField`/`cadField` are the card's embedded schema descriptors.
    component AutoCheckTile: Rectangle {
        id: act
        property var autoField: null
        property var cadField: null
        readonly property bool autoOn: autoField ? page.val(autoField) === true : false
        // For the host card's implicitHeight formula (the tile itself is
        // anchored top/bottom, so its own implicitHeight is not used).
        readonly property real rowImplicitHeight: actRow.implicitHeight
        radius: 10; color: page.surface0; border.color: page.border1
        RowLayout {
            id: actRow
            // From the top like every switch tile (see tilePadT).
            anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
            anchors.leftMargin: 16; anchors.rightMargin: 14; anchors.topMargin: page.tilePadT; spacing: 14
            ColumnLayout {
                Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; spacing: 3
                Text { textFormat: Text.PlainText; text: act.autoField ? act.autoField.label : ""; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true; Layout.topMargin: page.tileTitleNudge }
                Text {
                    text: "Notifies only; nothing downloads until you click Update. The check sends none of your data."
                    color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                    wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true
                }
                // Under the words rather than beside them: the strip is half a
                // card wide, and the words need that width more.
                CadenceSeg {
                    Layout.topMargin: 6
                    field: act.cadField
                    opacity: act.autoOn ? 1 : 0.35; enabled: act.autoOn
                    Behavior on opacity { NumberAnimation { duration: 180 } }
                }
            }
            SToggle {
                objectName: "tileSwitch"
                Layout.alignment: Qt.AlignTop
                checked: act.autoOn
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: if (act.autoField) page.setv(act.autoField.key, !act.autoOn)
                }
            }
        }
    }

    // The cadence pick (daily / weekly) for the auto-check strip. `field` is
    // the embedded enum descriptor.
    component CadenceSeg: SChoice {
        property var field: null
        options: field ? field.options : []
        currentIndex: field ? page.enumIndex(field.options, page.val(field)) : 0
        onPicked: function(i) { if (field) page.setv(field.key, field.options[i].value) }
    }

    // Clipboard write helper (copy only, never read): a hidden TextEdit is the
    // pure-QML way to reach the system clipboard. Used by the template-token
    // reference table's per-row copy buttons.
    TextEdit { id: copyBuf; visible: false }
    function copyText(t) { copyBuf.text = t; copyBuf.selectAll(); copyBuf.copy() }

    // Live example line under a path-template field: folder › folder › file,
    // resolved by the backend against a canned generic sample library through
    // the app's REAL path formatter, so the preview is byte-for-byte what a
    // download would be named. Folders render dim, the file name brighter,
    // and any unresolved {token} goes gold so typos jump out while typing.
    component PathPreviewLine: Flow {
        id: pv
        property string path: ""
        spacing: 5
        Text { text: "↳"; color: page.accentDim; font.family: page.mono; font.pixelSize: 11; textFormat: Text.PlainText }
        Repeater {
            model: pv.path.split("/")
            delegate: Row {
                required property string modelData
                required property int index
                spacing: 5
                Text {
                    text: modelData
                    textFormat: Text.PlainText
                    color: modelData.indexOf("{") >= 0 ? page.gold
                         : index === pv.path.split("/").length - 1 ? page.textLo : page.textDim
                    font.family: page.mono; font.pixelSize: 11
                }
                Text {
                    visible: index < pv.path.split("/").length - 1
                    text: "›"
                    color: page.accentDim; font.family: page.mono; font.pixelSize: 11
                }
            }
        }
    }

    // The expand/collapse indicator lives in its own file (ExpandChevron.qml)
    // so it can be reused across the app, settings sections, the download
    // queue, album blocks, etc.

    // Per-section line-art icon (see `iconPath`). Stroke-only, accent-colored,
    // drawn in a 16-unit box and scalable via `px`.
    component RailGlyph: Canvas {
        id: rg
        property string glyph: ""
        property color stroke: page.accent
        property bool live: false
        property real t: 0
        width: 16; height: 16
        onStrokeChanged: requestPaint()
        onLiveChanged: { t = 0; requestPaint() }
        onGlyphChanged: requestPaint()
        FrameAnimation { running: rg.live && rg.visible && page.active
            onTriggered: { rg.t += frameTime; rg.requestPaint() } }
        function ease(x) { return x < 0.5 ? 2 * x * x : 1 - Math.pow(-2 * x + 2, 2) / 2 }
        onPaint: {
            var c = getContext("2d"); c.reset()
            c.scale(width / 16, height / 16)
            c.strokeStyle = stroke; c.fillStyle = stroke
            c.lineWidth = 1.5; c.lineCap = "round"; c.lineJoin = "round"
            var t = rg.t, g = glyph, P = Math.PI
            if (!live) { c.path = page.iconPath(g); c.stroke(); return }
            if (g === "downloads" || g === "updates") {
                // The arrow keeps dropping into the tray (or lifting off it).
                var p = (t * 0.85) % 1, up = g === "updates"
                var dy = up ? (1 - p) * 3.4 - 1.7 : p * 3.4 - 1.7
                c.globalAlpha = Math.sin(p * P)
                c.save(); c.translate(0, dy)
                c.path = up ? "M8 4V11 M4.9 7.1L8 4L11.1 7.1" : "M8 3.2V9.3 M5.2 6.6 L8 9.4 L10.8 6.6"
                c.stroke(); c.restore(); c.globalAlpha = 1
                if (!up) { c.path = "M3.4 12.6H12.6"; c.stroke() }
                else { c.beginPath(); c.moveTo(5, 13.2); c.lineTo(11, 13.2); c.globalAlpha = 0.5; c.stroke(); c.globalAlpha = 1 }
            } else if (g === "library") {
                // A bright head sweeps the shelf, lighting each spine as it
                // passes; the shelf dims again before the next pass.
                var xs = [4.4, 6.8, 9.2, 11.6], tops = [5, 4.2, 3.4, 5.6]
                var cyc = t % 2.2, hx = 2.6 + (cyc / 1.4) * 11.6
                var fade = cyc < 1.4 ? 1 : Math.max(0, 1 - (cyc - 1.4) / 0.6)
                c.path = "M2.6 12.6H13.4"; c.stroke()
                for (var i = 0; i < 4; i++) {
                    var lit = hx >= xs[i] ? fade : 0
                    c.globalAlpha = 0.32 + 0.68 * lit
                    c.beginPath(); c.moveTo(xs[i], tops[i]); c.lineTo(xs[i], 12.6); c.stroke()
                }
                if (cyc < 1.4) {
                    c.globalAlpha = 0.9; c.lineWidth = 1
                    c.beginPath(); c.moveTo(hx, 2.4); c.lineTo(hx, 14); c.stroke()
                }
                c.globalAlpha = 1
            } else if (g === "releases") {
                // The bell rings in short bursts.
                var q = (t % 1.8), env = q < 0.9 ? Math.sin(q / 0.9 * P) : 0
                var a = Math.sin(q * 26) * 0.32 * env
                c.save(); c.translate(8, 3.6); c.rotate(a); c.translate(-8, -3.6)
                c.path = "M4.6 11H11.4L10.4 9.6V7A2.4 2.4 0 0 0 5.6 7V9.6Z M8 4.6V3.4"; c.stroke(); c.restore()
                c.save(); c.translate(-a * 3.2, 0); c.path = "M7 12.6H9"; c.stroke(); c.restore()
            } else if (g === "discography") {
                // The disc spins, a groove glint going round.
                c.path = "M2.6 8A5.4 5.4 0 1 0 13.4 8A5.4 5.4 0 1 0 2.6 8Z M6.9 8A1.1 1.1 0 1 0 9.1 8A1.1 1.1 0 1 0 6.9 8Z"; c.stroke()
                var r0 = t * 4.2
                c.lineWidth = 1.1
                c.beginPath(); c.arc(8, 8, 3.4, r0, r0 + 1.1); c.stroke()
                c.globalAlpha = 0.5; c.beginPath(); c.arc(8, 8, 3.4, r0 + P, r0 + P + 0.7); c.stroke(); c.globalAlpha = 1
            } else if (g === "files") {
                // A page rises out of the folder and settles back in.
                var lift = 1.0 + 1.8 * ease(0.5 - 0.5 * Math.cos(t * 2.6))
                c.lineWidth = 1.2; c.globalAlpha = 0.85
                c.beginPath(); c.moveTo(5.2, 6.7); c.lineTo(5.2, 6.7 - lift); c.lineTo(10.8, 6.7 - lift); c.lineTo(10.8, 6.7); c.stroke()
                c.globalAlpha = 1; c.lineWidth = 1.5
                c.path = "M2.6 5.4H6.2L7.4 6.7H13.4V11.9H2.6Z"; c.stroke()
            } else if (g === "metadata") {
                // The tag swings on its hole.
                c.save(); c.translate(5.15, 4.9); c.rotate(Math.sin(t * 3.2) * 0.2); c.translate(-5.15, -4.9)
                c.path = page.iconPath(g); c.stroke(); c.restore()
            } else if (g === "processing") {
                // A pulse runs round the chip's pins.
                c.path = "M5.2 5.2H10.8V10.8H5.2Z"; c.stroke()
                var pins = [[6.7, 5.2, 6.7, 3.6], [9.3, 5.2, 9.3, 3.6], [10.8, 6.7, 12.4, 6.7], [10.8, 9.3, 12.4, 9.3],
                            [9.3, 10.8, 9.3, 12.4], [6.7, 10.8, 6.7, 12.4], [5.2, 9.3, 3.6, 9.3], [5.2, 6.7, 3.6, 6.7]]
                var head = (t * 7) % 8
                for (var i = 0; i < 8; i++) {
                    var dd = (head - i + 8) % 8
                    c.globalAlpha = 0.3 + 0.7 * Math.max(0, 1 - dd / 3)
                    c.beginPath(); c.moveTo(pins[i][0], pins[i][1]); c.lineTo(pins[i][2], pins[i][3]); c.stroke()
                }
                c.globalAlpha = 0.4 + 0.6 * (0.5 + 0.5 * Math.sin(t * 5))
                c.beginPath(); c.arc(8, 8, 0.9, 0, 2 * P); c.fill(); c.globalAlpha = 1
            } else if (g === "appearance") {
                // The eye glances side to side and blinks.
                var b = t % 3.2, sy = b > 2.9 ? Math.abs(Math.cos((b - 2.9) / 0.3 * P)) : 1
                sy = Math.max(0.08, sy)
                c.save(); c.translate(8, 8); c.scale(1, sy); c.translate(-8, -8)
                c.path = "M1.8 8C3.6 4.9 5.6 3.8 8 3.8C10.4 3.8 12.4 4.9 14.2 8C12.4 11.1 10.4 12.2 8 12.2C5.6 12.2 3.6 11.1 1.8 8Z"; c.stroke()
                var lx = Math.max(-1, Math.min(1, Math.sin(t * 1.3) * 1.6)) * 1.8
                c.beginPath(); c.arc(8 + lx, 8, 1.7, 0, 2 * P); c.stroke(); c.restore()
            } else if (g === "notifications") {
                // Two short buzzes, marks flaring at the sides.
                var w2 = t % 2.2, env2 = 0
                if (w2 < 0.42) env2 = Math.sin(w2 / 0.42 * P)
                else if (w2 > 0.58 && w2 < 1.0) env2 = Math.sin((w2 - 0.58) / 0.42 * P)
                var dx = Math.sin(t * 70) * 0.55 * env2
                c.save(); c.translate(8 + dx, 8); c.rotate(dx * 0.06); c.translate(-8, -8)
                c.path = "M6.2 2.4H9.8A1.2 1.2 0 0 1 11 3.6V12.4A1.2 1.2 0 0 1 9.8 13.6H6.2A1.2 1.2 0 0 1 5 12.4V3.6A1.2 1.2 0 0 1 6.2 2.4Z M7.5 11.6H8.5"
                c.stroke(); c.restore()
                var o = env2 * 0.5
                c.globalAlpha = 0.25 + 0.75 * env2
                c.beginPath(); c.moveTo(2.8 - o, 6.2); c.lineTo(2.8 - o, 9.8); c.moveTo(13.2 + o, 6.2); c.lineTo(13.2 + o, 9.8); c.stroke()
                c.globalAlpha = env2 * 0.7
                c.beginPath(); c.moveTo(1.2 - o, 7); c.lineTo(1.2 - o, 9); c.moveTo(14.8 + o, 7); c.lineTo(14.8 + o, 9); c.stroke()
                c.globalAlpha = 1
            } else if (g === "pacing") {
                // The needle revs up and drops back.
                c.path = "M2.8 11.6A5.4 5.4 0 1 1 13.2 11.6"; c.stroke()
                var s = ease(0.5 - 0.5 * Math.cos(t * 2.4))
                var ang = -P * 0.86 + s * P * 0.72
                c.beginPath(); c.moveTo(8, 9.2); c.lineTo(8 + Math.cos(ang) * 4.2, 9.2 + Math.sin(ang) * 4.2); c.stroke()
                c.beginPath(); c.arc(8, 9.2, 0.8, 0, 2 * P); c.stroke()
            } else if (g === "diagnostics") {
                // The trace scrolls by like a monitor, a bright head drawing it.
                c.globalAlpha = 0.28; c.path = page.iconPath(g); c.stroke(); c.globalAlpha = 1
                var hx = 2 + ((t * 9) % 14)
                c.save(); c.beginPath(); c.rect(hx - 4.5, 0, 4.5, 16); c.clip()
                c.path = page.iconPath(g); c.stroke(); c.restore()
                c.beginPath(); c.arc(Math.min(hx, 13.4), 8, 0.01, 0, 2 * P); c.stroke()
            } else { c.path = page.iconPath(g); c.stroke() }
        }
    }
    component SectionIcon: Shape {
        id: si
        property string glyph: ""
        property color stroke: page.accent
        property real px: 16
        width: 16; height: 16
        antialiasing: true
        scale: px / 16
        // Ease between states (e.g. the FFmpeg glyph red→yellow→green) so the
        // status read-out doesn't pop when ffmpeg is installed or linked.
        Behavior on stroke { ColorAnimation { duration: 220 } }
        ShapePath {
            strokeColor: si.stroke; strokeWidth: 1.5; fillColor: "transparent"
            capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
            PathSvg { path: page.iconPath(si.glyph) }
        }
    }

    // Browse routes the picked path through page.setv, exactly like typing into
    // the field: it updates editMap, and the SText's `text: page.val(modelData)`
    // binding re-evaluates to show it. We deliberately do NOT write the SText's
    // `text` alias directly, that imperative write would destroy the binding,
    // and because delegates are kept alive across close/reopen a canceled Browse
    // would then persist visually with Save disabled.
    FolderDialog {
        id: folderDlg
        property string targetKey: ""
        onAccepted: page.setv(targetKey, page.urlToPath(selectedFolder))
    }
    FileDialog {
        id: fileDlg
        property string targetKey: ""
        onAccepted: page.setv(targetKey, page.urlToPath(selectedFile))
    }

    // ---- Section rail ---------------------------------------------------
    // Left of the pane, under the sticky header. Click a section to show it;
    // once the rail has focus (any click on it), Up / Down step through them.
    Rectangle {
        id: rail
        x: 22; y: 62; width: 224; height: page.height - y - 14
        radius: 12; color: page.surface; border.color: page.border1
        z: 2
        // Where the selection plate sits, in the rail list's coordinates.
        property real selY: 0
        Component.onCompleted: page.railScroller = railFlick
        Keys.onUpPressed: page.stepSection(-1)
        Keys.onDownPressed: page.stepSection(1)
        // Keep the picked row in view when the rail scrolls (a short window,
        // a deep link, or the arrow keys landing past the edge).
        function reveal() {
            var top = selY - 8, bottom = selY + 34 + 8
            if (top < railFlick.contentY) railFlick.contentY = Math.max(0, top)
            else if (bottom > railFlick.contentY + railFlick.height)
                railFlick.contentY = Math.min(railFlick.contentHeight - railFlick.height, bottom - railFlick.height)
        }
        // The list scrolls when the window is too short to hold it.
        Flickable {
            id: railFlick
            anchors.fill: parent
            anchors.bottomMargin: 2
            contentHeight: railFoot.y + railFoot.height
            clip: true; boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height
        // The selection plate: it glides between rows (the one motion on a
        // section switch; the pane itself changes instantly).
        Rectangle {
            x: 8; width: parent.width - 16; height: 34; radius: 8
            y: rail.selY
            color: page.accentCont; border.color: page.accentDim
            Behavior on y { NumberAnimation { duration: 300; easing.type: Easing.OutBack; easing.overshoot: 0.8 } }
            Rectangle { x: -1; width: 3; height: 16; radius: 1.5; anchors.verticalCenter: parent.verticalCenter; color: page.accent }
        }
        Column {
            id: railList
            x: 8; y: 10; width: parent.width - 16; spacing: 2
            Repeater {
                model: page.railGroups
                delegate: Column {
                    id: railGroup
                    required property var modelData
                    required property int index
                    width: parent.width; spacing: 2
                    Item { width: 1; height: railGroup.index === 0 ? 2 : 12 }
                    Text {
                        textFormat: Text.PlainText
                        x: 10; text: railGroup.modelData.head; color: page.textDim
                        font.family: page.mono; font.pixelSize: 10; font.letterSpacing: 1.4
                        bottomPadding: 4
                    }
                    Repeater {
                        model: railGroup.modelData.ids
                        delegate: Rectangle {
                            id: railItem
                            required property string modelData
                            objectName: "railItem_" + modelData
                            readonly property var g: page.groupById(modelData)
                            readonly property bool sel: page.curSec === modelData
                            visible: g !== null
                            width: parent.width; height: 34; radius: 8
                            color: "transparent"
                            // Hover is a fixed-color layer that fades its opacity: a
                            // color fade from "transparent" runs through transparent
                            // black and dips darker than the rail before it lightens.
                            Rectangle {
                                anchors.fill: parent; radius: 8; color: page.surface2
                                opacity: !railItem.sel && railMa.containsMouse ? 1 : 0
                                Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutQuad } }
                            }
                            function place() {
                                if (!sel) return
                                rail.selY = mapToItem(railFlick.contentItem, 0, 0).y
                                if (railFlick.interactive) rail.reveal()
                            }
                            onSelChanged: place()
                            onYChanged: place()
                            Connections { target: railGroup; function onYChanged() { railItem.place() } }
                            Behavior on color { ColorAnimation { duration: 120 } }
                            // The FFmpeg / Updates status lights, and an unsaved-edit dot.
                            readonly property color statusColor:
                                  railItem.modelData === "updates" && page.auUpdate ? page.gold
                                : railItem.modelData === "processing" && page.ff && page.ff.stateKey === "missing" ? page.red
                                : railItem.modelData === "processing" && page.ff && page.ff.stateKey === "path" ? page.gold
                                : "transparent"
                            Row {
                                x: 10; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                                // The picked section's icon loops a small motion of its own.
                                RailGlyph {
                                    anchors.verticalCenter: parent.verticalCenter
                                    glyph: railItem.modelData
                                    live: railItem.sel
                                    objectName: "railGlyph_" + railItem.modelData
                                    stroke: railItem.sel ? page.accent : page.textLo
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    textFormat: Text.PlainText
                                    text: railItem.g ? railItem.g.group : ""
                                    color: railItem.sel ? page.accent : page.textHi
                                    font.pixelSize: 13; font.weight: railItem.sel ? Font.DemiBold : Font.Normal
                                    // A wide font (DejaVu on Linux) set bold for the pick
                                    // can outgrow the rail: shrink a point or two first,
                                    // elide only past that.
                                    fontSizeMode: Text.HorizontalFit; minimumPixelSize: 11
                                    width: rail.width - 72; elide: Text.ElideRight
                                }
                            }
                            Rectangle {
                                anchors.right: parent.right; anchors.rightMargin: 10
                                anchors.verticalCenter: parent.verticalCenter
                                width: 7; height: 7; radius: 3.5
                                color: page.secDirty(railItem.modelData) ? page.accent : railItem.statusColor
                            }
                            MouseArea {
                                id: railMa
                                anchors.fill: parent; anchors.topMargin: -1; anchors.bottomMargin: -1
                                hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onClicked: { rail.forceActiveFocus(); page.showSection(railItem.modelData) }
                            }
                        }
                    }
                }
            }
        }

        // The credit and the coffee button end the rail: pinned to its foot
        // while the list leaves room, scrolling up with it when it does not.
        Item {
            id: railFoot
            width: railFlick.width
            height: footer.height + coffee.height + 14
            y: Math.max(railList.y + railList.height + 12, railFlick.height - height)
        // ── footer credit (easter egg), "made with [♥] by iamprivacy".
        // Types itself out while Settings shows it (the rail scrolled far
        // enough on a short window), backspaces away when it goes; the heart
        // lub-dubs on hover and bursts into color pixels when clicked (the
        // burst sim is the page-root `gibOverlay`, boxed into the rail).
        Item {
            id: footer
            objectName: "settingsCredit"
            width: parent.width; height: 44; y: coffee.height + 10
            // In view: the rail is scrolled far enough to show it.
            readonly property bool fits: railFoot.y + footer.y + 20 < railFlick.contentY + railFlick.height

            readonly property int total: 25   // "made with "10 + heart 1 + " by "4 + "iamprivacy"10
            property int n: 0
            property bool inView: false
            property bool cursorPresent: false
            property bool heartGibbed: false
            property real beat: 1.0

            readonly property var  heartModel: HeartGib.build()
            readonly property int  heartPx: 2
            readonly property real heartW: HeartGib.pxW(heartModel, heartPx)
            readonly property real heartH: HeartGib.pxH(heartModel, heartPx)
            // After the heart bursts, "[love]" takes its place; the slot grows
            // from the heart's width to the word's width so the line reflows.
            readonly property real lovedW: fLovedMeas.implicitWidth
            readonly property real slotW:  heartGibbed ? lovedW : heartW
            readonly property bool heartShown: visFor(10, 1) > 0 && !heartGibbed
            readonly property bool heartLive:  footer.n >= footer.total && !heartGibbed

            function visFor(off, len) { return Math.max(0, Math.min(footer.n - off, len)) }
            function gibHeart() {
                if (footer.heartGibbed) return
                beatAnim.stop(); beatReset.stop(); footer.beat = 1.0
                var p = heartCv.mapToItem(page, 0, 0)
                gibOverlay.explode(p.x, p.y, footer.heartModel, footer.heartPx)
                footer.heartGibbed = true
            }
            // Put the credit back the way a fresh visit would: type it out
            // again from nothing, with a whole heart. The burst calls this
            // when the last of the mess is gone; a credit out of view just
            // resets, and types itself the next time it shows.
            function rearmCredit() {
                footer.heartGibbed = false
                footer.n = 0
                footer.cursorPresent = false
                if (!footer.inView) return
                footer.cursorPresent = true
                fTypeFwd.from = 0
                fTypeFwd.duration = footer.total * 52
                fTypeFwd.restart()
            }
            function recomputeInView() {
                var nowIn = page.active && footer.fits
                if (nowIn !== footer.inView) footer.inView = nowIn
            }
            Component.onCompleted: Qt.callLater(footer.recomputeInView)
            onFitsChanged: footer.recomputeInView()
            Connections { target: page
                function onActiveChanged() { footer.recomputeInView() }
            }
            onInViewChanged: {
                if (footer.inView) {
                    fFarewell.stop(); fTypeBack.stop()
                    footer.cursorPresent = true
                    fTypeFwd.from = footer.n
                    fTypeFwd.duration = Math.max(1, footer.total - footer.n) * 52
                    fTypeFwd.restart()
                } else {
                    fTypeFwd.stop()
                    beatAnim.stop(); footer.beat = 1.0
                    if (footer.heartGibbed) {
                        // Leaving mid-burst: the mess clears now instead of
                        // waiting out its time, and the credit resets.
                        fTypeBack.stop()
                        gibOverlay.release()
                    } else {
                        fTypeBack.from = footer.n
                        fTypeBack.duration = Math.max(1, footer.n) * 34
                        fTypeBack.restart()
                    }
                }
            }
            NumberAnimation { id: fTypeFwd;  target: footer; property: "n"; to: footer.total; easing.type: Easing.Linear }
            NumberAnimation { id: fTypeBack; target: footer; property: "n"; to: 0; easing.type: Easing.Linear
                              onStopped: if (footer.n === 0) fFarewell.restart() }
            Timer { id: fBlink; interval: 460; repeat: true; running: footer.cursorPresent
                    property bool on: true
                    onTriggered: on = !on
                    onRunningChanged: if (running) on = true }
            Timer { id: fFarewell; interval: 1300; repeat: false
                    onTriggered: if (!footer.inView && footer.n === 0) footer.cursorPresent = false }
            SequentialAnimation { id: beatAnim; loops: Animation.Infinite
                NumberAnimation { target: footer; property: "beat"; from: 1.00; to: 1.18; duration: 130; easing.type: Easing.OutQuad }
                NumberAnimation { target: footer; property: "beat"; from: 1.18; to: 0.97; duration: 150; easing.type: Easing.InQuad }
                NumberAnimation { target: footer; property: "beat"; from: 0.97; to: 1.09; duration: 110; easing.type: Easing.OutQuad }
                NumberAnimation { target: footer; property: "beat"; from: 1.09; to: 1.00; duration: 170; easing.type: Easing.OutCubic }
                PauseAnimation { duration: 460 }
            }
            NumberAnimation { id: beatReset; target: footer; property: "beat"; to: 1.0; duration: 180; easing.type: Easing.OutCubic }

            Item {
                id: fCredit
                anchors.centerIn: parent
                width: fMeas.implicitWidth + 9
                height: fMeas.implicitHeight
                // hidden width probe for the "[love]" replacement
                Text { id: fLovedMeas; visible: false; text: "[love]"; font.family: page.mono; font.pixelSize: 11 }
                Row { id: fMeas; visible: false
                    Text { text: "made with "; font.family: page.mono; font.pixelSize: 11 }
                    Item { width: footer.slotW; height: footer.heartH }
                    Text { text: " by ";       font.family: page.mono; font.pixelSize: 11 }
                    Text { text: "iamprivacy"; font.family: page.mono; font.pixelSize: 11 }
                }
                Row {
                    anchors.left: parent.left
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 0
                    Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter
                           text: "made with ".substring(0, footer.visFor(0, 10)); color: page.accent; font.family: page.mono; font.pixelSize: 11 }
                    Item { width: footer.slotW; height: footer.heartH; anchors.verticalCenter: parent.verticalCenter
                        Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
                        Canvas {
                            id: heartCv
                            width: footer.heartW; height: footer.heartH
                            anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                            smooth: false; antialiasing: false
                            visible: footer.heartShown
                            opacity: footer.heartShown ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: 120 } }
                            transformOrigin: Item.Center
                            scale: footer.beat
                            Component.onCompleted: requestPaint()
                            onPaint: { var ctx = getContext('2d'); ctx.reset(); ctx.clearRect(0, 0, width, height)
                                       HeartGib.draw(ctx, footer.heartModel, 0, 0, footer.heartPx) }
                        }
                        // after the heart bursts, "[love]" fades in where it was and
                        // stays until the mess is gone and the credit retypes
                        Text {
                            anchors.centerIn: parent
                            text: "[love]"; color: page.accent
                            font.family: page.mono; font.pixelSize: 11
                            opacity: footer.heartGibbed ? 1 : 0
                            Behavior on opacity { NumberAnimation { duration: 280 } }
                        }
                        MouseArea { anchors.fill: parent; anchors.margins: -4
                            hoverEnabled: true
                            enabled: footer.heartLive
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onEntered: { beatReset.stop(); beatAnim.start() }
                            onExited:  { beatAnim.stop(); beatReset.start() }
                            onClicked: footer.gibHeart() }
                    }
                    Text { textFormat: Text.PlainText; anchors.verticalCenter: parent.verticalCenter
                           text: " by ".substring(0, footer.visFor(11, 4)); color: page.accent; font.family: page.mono; font.pixelSize: 11 }
                    Item { implicitWidth: fGh.implicitWidth; implicitHeight: fGh.implicitHeight; anchors.verticalCenter: parent.verticalCenter
                        Text { id: fGh; textFormat: Text.PlainText; text: "iamprivacy".substring(0, footer.visFor(15, 10))
                               color: fGhMA.containsMouse ? page.textHi : page.gold
                               font.family: page.mono; font.pixelSize: 11
                               font.underline: fGhMA.containsMouse }
                        MouseArea { id: fGhMA; anchors.fill: parent; hoverEnabled: true
                            enabled: footer.visFor(15, 10) === 10
                            cursorShape: enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
                            onClicked: Qt.openUrlExternally("https://github.com/iamprivacy") }
                    }
                    Rectangle { anchors.verticalCenter: parent.verticalCenter
                        width: 7; height: 13; radius: 1; color: page.gold
                        visible: footer.cursorPresent
                        opacity: fBlink.on ? 0.9 : 0.0 }
                }
            }
        }

        // Buy Me a Coffee, above the credit; opens the page in the browser.
        // No box: a white ceramic mug with the coffee showing through an
        // open rim, and the label in Buy Me a Coffee's own yellow. The mug
        // steams all the time, and the steam climbs higher and quicker
        // while hovered. The mug's body sits on the middle of the label's
        // capitals, not on the canvas middle (the steam headroom would push
        // it low).
        Item {
            id: coffee
            objectName: "settingsCoffee"
            anchors.horizontalCenter: parent.horizontalCenter
            y: 4
            readonly property color yellow:    "#ffdd00"
            readonly property color yellowHot: "#ffe94d"
            readonly property color ceramic:   "#f6f1e8"   // lip, outline and handle
            readonly property color glaze:     "#ece4d6"   // body
            readonly property color brew:      "#6b4024"
            readonly property color steam:     "#d9d4cc"
            readonly property real  ms: 1.1                // mug scale over its 22-unit drawing
            width: mug.width + 7 + coffeeTxt.implicitWidth
            height: Math.max(coffeeTxt.implicitHeight + page.btnPadV * 2 + 2, 30)
            // 0 at rest, 1 hovered; eases so the steam speeds up smoothly.
            property real h: coffeeMa.containsMouse ? 1 : 0
            Behavior on h { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
            // Steam clock, in seconds; runs only while Settings shows.
            property real t: 0
            FrameAnimation {
                running: coffee.visible && page.active
                onTriggered: { coffee.t += frameTime * (0.6 + 0.75 * coffee.h); mug.requestPaint() }
            }
            scale: coffeeMa.pressed ? 0.97 : 1
            Behavior on scale { NumberAnimation { duration: 90 } }

            FontMetrics { id: coffeeFm; font: coffeeTxt.font }
            Canvas {
                id: mug
                width: 22 * coffee.ms; height: 22 * coffee.ms
                // The body's middle (y 15 in mug units) on the capitals' middle.
                y: coffeeTxt.y + coffeeTxt.baselineOffset + coffeeFm.tightBoundingRect("B").y
                   + coffeeFm.tightBoundingRect("B").height / 2 - 15 * coffee.ms
                function body(c) {
                    c.beginPath(); c.moveTo(4, 10); c.lineTo(4, 16.5); c.arcTo(4, 20, 7.5, 20, 3.5)
                    c.lineTo(11.5, 20); c.arcTo(15, 20, 15, 16.5, 3.5); c.lineTo(15, 10); c.closePath()
                }
                function handle(c) { c.beginPath(); c.moveTo(15, 11.6); c.bezierCurveTo(19.4, 11.6, 19.4, 17.4, 15, 17.4); c.stroke() }
                // One curl of steam from the rim (y 8.6) up to `top`, fading as it rises.
                function wisp(c, x0, ph, amp, top, a) {
                    var g = coffee.steam, gr = c.createLinearGradient(0, 8.6, 0, top)
                    gr.addColorStop(0, Qt.rgba(g.r, g.g, g.b, a)); gr.addColorStop(1, Qt.rgba(g.r, g.g, g.b, 0))
                    c.strokeStyle = gr; c.lineWidth = 1.4; c.beginPath()
                    for (var y = 8.6; y >= top; y -= 0.4) {
                        var k = (8.6 - y) / (8.6 - top)
                        var x = x0 + Math.sin((8.6 - y) * 0.85 - ph * 6.283) * amp * (0.3 + k)
                        if (y === 8.6) c.moveTo(x, y); else c.lineTo(x, y)
                    }
                    c.stroke()
                }
                onPaint: {
                    var c = getContext("2d"); c.reset()
                    c.scale(coffee.ms, coffee.ms)
                    var t = coffee.t, h = coffee.h
                    c.lineCap = "round"; c.lineJoin = "round"; c.lineWidth = 1.6
                    // Handle first so the body's fill covers its root.
                    c.strokeStyle = coffee.ceramic; handle(c)
                    c.fillStyle = coffee.glaze; body(c); c.fill(); c.stroke()
                    // The open rim: the lip, then the coffee inside it.
                    c.fillStyle = coffee.ceramic
                    c.beginPath(); c.ellipse(3.2, 7.1, 12.6, 5.8); c.fill()
                    c.fillStyle = coffee.brew
                    c.beginPath(); c.ellipse(4.6, 8.1, 9.8, 3.8); c.fill()
                    wisp(c, 7.2, t * 0.9, 1.15 + 0.25 * h, 2.6 - 1.6 * h, 1)
                    wisp(c, 11.4, t * 0.9 + 0.45, 1.15 + 0.25 * h, 3.0 - 1.6 * h, 1)
                    // A third wisp joins while hovered.
                    if (h > 0.02) wisp(c, 9.3, t * 1.1 + 0.2, 1.1, 2.2, 0.6 * h)
                }
            }
            Text {
                id: coffeeTxt
                x: mug.width + 7
                anchors.verticalCenter: parent.verticalCenter
                textFormat: Text.PlainText
                text: "Buy me a coffee"
                color: Qt.tint(coffee.yellow, Qt.rgba(coffee.yellowHot.r, coffee.yellowHot.g, coffee.yellowHot.b, coffee.h))
                font.family: page.uiFont; font.pixelSize: 14; font.bold: true
            }
            MouseArea {
                id: coffeeMa
                anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: Qt.openUrlExternally("https://www.buymeacoffee.com/iamprivacy")
            }
        }
        }
        }
    }

    // ---- Layout ---------------------------------------------------------
    ColumnLayout {
        anchors.fill: parent
        spacing: 8

        // Sticky header (back + actions), stays while the list scrolls
        Item {
            Layout.fillWidth: true; Layout.leftMargin: 22; Layout.rightMargin: 22; Layout.topMargin: 6
            implicitHeight: 44
            Item {
                anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                width: backR.width; height: backR.height
                Row {
                    id: backR; spacing: 8
                    // No back arrow / back-on-click: you reach Settings from the nav,
                    // and CANCEL / SAVE (right) close it. Title only.
                    Text { text: "Settings"; color: page.textHi; font.pixelSize: 22; font.bold: true }
                }
            }
            Row {
                anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; spacing: 10
                Rectangle {
                    width: cancelTxt.width + page.btnPadH * 2; height: cancelTxt.height + page.btnPadV * 2; radius: page.btnRad; color: "transparent"; border.color: "transparent"
                    // Nothing typed, nothing to throw away: reads as inert
                    // rather than as a way out of the page.
                    opacity: page.dirty ? 1 : 0.4
                    Text { id: cancelTxt; anchors.centerIn: parent; text: "CANCEL"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                    MouseArea {
                        anchors.fill: parent; enabled: page.dirty
                        cursorShape: page.dirty ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: page.discardEdits()
                    }
                }
                Rectangle {
                    id: saveBtn; objectName: "settingsSave"
                    // A red field holds the whole save, so nothing the engine
                    // would refuse can reach the config in the first place.
                    readonly property bool canSave: page.dirty && !page.hasInvalidEdits()
                    width: saveTxt.width + page.btnPadH * 2; height: saveTxt.height + page.btnPadV * 2; radius: page.btnRad
                    color: page.accentCont; opacity: canSave ? 1 : 0.4
                    border.color: page.accentDim; border.width: 1
                    Text { id: saveTxt; anchors.centerIn: parent; text: "SAVE CHANGES"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                    MouseArea {
                        anchors.fill: parent; enabled: saveBtn.canSave
                        cursorShape: saveBtn.canSave ? Qt.PointingHandCursor : Qt.ArrowCursor
                        // Keep editMap so the controls keep showing the values
                        // we just saved (clearing it would revert them to the
                        // now-stale schema defaults); it's reset on next open,
                        // when needsRefresh pulls the fresh persisted values.
                        // flacRefresh: a saved download folder moves the FLAC
                        // card's "Waves library", so its last scan may no
                        // longer describe what it shows.
                        onClicked: { waves.applySettings(page.editMap); page.savedVals = Object.assign({}, page.savedVals, page.editMap); page.syncLibraryMirrors(); page.unsaved = ({}); page.dirty = false; page.needsRefresh = true; page.savedFlash = true; flashTimer.restart(); page.flacRefresh() }
                    }
                }
            }
        }

        Flickable {
            id: settingsFlick
            objectName: "settingsPane"
            Layout.fillWidth: true; Layout.fillHeight: true
            // Room for the section rail on the left.
            Layout.leftMargin: 246
            clip: true
            contentWidth: width
            contentHeight: col.height + 28
            // Dragging the bar (or clicking its track) never emits
            // movementStarted, so it disarms a pending restore itself.
            ScrollBar.vertical: WavesScrollBar { onPressedChanged: if (pressed) page.pendingY = -1 }
            boundsBehavior: Flickable.StopAtBounds
            // Only a real scroll is worth remembering: while the page is
            // hidden the height is 0 and every write here is the clamp.
            // A move the restore did not make, and that is not the bounds
            // clamp pinning the view to the bottom, is the user scrolling:
            // it disarms the restore so a later re-measure cannot yank the view.
            // (A re-measure can re-emit this without contentY moving; only a
            // real move counts.)
            property real seenY: 0
            onContentYChanged: {
                if (height > 0 && page.active) page.keptY = contentY
                var moved = Math.abs(contentY - seenY) > 0.5
                seenY = contentY
                if (moved && height > 0 && page.pendingY >= 0 && !page.restoringScroll
                        && contentY < contentHeight - height - 0.5)
                    page.pendingY = -1
            }
            // The height returning is the page coming back on screen; put the
            // spot back in that same pass, before it paints.
            onHeightChanged: page._restoreScroll()
            onContentHeightChanged: {
                if (flacJumpSettle.running) flacJumpSettle.restart()
                page._restoreScroll()
            }
            // The user taking over cancels a partially-applied restore, so a
            // later re-measure can't yank the view away from where they
            // scrolled to.
            onMovementStarted: page.pendingY = -1

            Column {
                id: col
                x: 22; width: parent.width - 44; spacing: 12

                // FFmpeg manager card, declared here as a lazy template and
                // shown by a Loader at the top of the "Processing" section
                // (see the Repeater below). Declaring it inside `col` is fine:
                // a Component is non-visual, so it adds nothing to the layout.
                Component {
                    id: ffmpegCardComp
                    Item {
                    id: ffCard
                    width: parent ? parent.width : 0
                    // A managed install mirrors the Updates card: twin tiles,
                    // status + actions on the left, the shared auto-check
                    // toggle + cadence segment on the right, keeping FFmpeg's
                    // darker palette and MANAGED chip. The other states
                    // (missing / system-linked) keep the single wide card.
                    readonly property bool twin: page.ff.stateKey === "managed"
                    implicitHeight: twin
                        ? Math.max(ffLeftCol.implicitHeight + 28, ffTileR.rowImplicitHeight + 24, 108)
                        : ffSingle.implicitHeight

                    // ---- Single wide card: missing / system-linked states ----
                    Rectangle {
                    id: ffSingle
                    visible: !ffCard.twin
                    width: parent.width; radius: 12
                    color: page.surface0; border.color: page.outline
                    implicitHeight: ffCol.implicitHeight + 28

                    ColumnLayout {
                        id: ffCol
                        x: 16; y: 14; width: parent.width - 32; spacing: 9

                        // Header, status dot + label + (UPDATE pill) + MANAGED/SYSTEM·version
                        // chip, mirroring the first-run setup pop-up's FFmpeg card.
                        RowLayout {
                            Layout.fillWidth: true; spacing: 9
                            Rectangle {
                                width: 7; height: 7; radius: 4; Layout.alignment: Qt.AlignVCenter
                                color: page.ff.stateKey === "managed" ? page.green
                                     : page.ff.stateKey === "path" ? page.gold : page.red
                            }
                            Text { text: "FFMPEG"; color: page.textHi; font.pixelSize: 13; font.bold: true; font.letterSpacing: 1.4; Layout.alignment: Qt.AlignVCenter }
                            Item { Layout.fillWidth: true }
                            Rectangle {
                                visible: page.ff.updateAvailable && !page.ff.busy
                                Layout.alignment: Qt.AlignVCenter
                                radius: 5; color: page.goldCont; border.color: page.gold
                                implicitWidth: updT.implicitWidth + 14; implicitHeight: 19
                                Text { id: updT; textFormat: Text.PlainText; anchors.centerIn: parent; text: "UPDATE"; color: page.gold; font.family: page.mono; font.pixelSize: 10; font.bold: true }
                            }
                            Rectangle {
                                visible: page.ff.stateKey !== "missing"
                                Layout.alignment: Qt.AlignVCenter
                                radius: 5; implicitHeight: 19; implicitWidth: chipTxt.implicitWidth + 16
                                color: page.ff.stateKey === "managed" ? page.green : page.gold
                                Text {
                                    textFormat: Text.PlainText
                                    id: chipTxt; anchors.centerIn: parent
                                    text: (page.ff.stateKey === "managed" ? "MANAGED" : "SYSTEM")
                                          + (page.ff.status.version ? " · " + page.ff.status.version : "")
                                    color: page.ff.stateKey === "managed" ? page.greenCont : page.goldCont
                                    font.family: page.mono; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.4
                                }
                            }
                        }

                        // One-line status (mirrors the setup pop-up's wording)
                        Text {
                            textFormat: Text.PlainText
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            color: page.textLo; font.pixelSize: 13
                            text: page.ff.stateKey === "managed"
                                    ? "Installed and managed by Waves."
                                  : page.ff.stateKey === "path"
                                    ? "Using the FFmpeg already on your system."
                                  : "FFmpeg is not installed."
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: "Used to convert videos to MP4, extract FLAC from MP4 containers, and downsample hi-res audio. Without it those steps are skipped."
                            color: page.textDim; font.pixelSize: 12
                        }

                        // Progress (while downloading/installing): the same LED
                        // dot-matrix pill the updater card uses.
                        LedBar {
                            visible: page.ff.busy; Layout.fillWidth: true; Layout.topMargin: 2
                            radius: page.btnRad; mono: page.mono
                            pct: page.ff.pct
                            label: (page.ff.message || "Working…") + " · " + Math.round(page.ff.pct) + "%"
                        }

                        // Failure message
                        Text {
                            textFormat: Text.PlainText
                            visible: page.ff.lifeState === "failed"
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: page.ff.failPrefix + page.ff.message
                            color: page.red; font.pixelSize: 12
                        }

                        // MISSING, one prominent install action (centered), as in
                        // the setup pop-up.
                        Rectangle {
                            visible: page.ff.stateKey === "missing" && !page.ff.busy
                            Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 2; Layout.preferredHeight: ffInstTxt.implicitHeight + page.btnPadV * 2
                            implicitWidth: ffInstTxt.implicitWidth + page.btnPadH * 2; radius: page.btnRad
                            color: page.accentCont; border.width: 1; border.color: page.accentDim
                            Text { id: ffInstTxt; anchors.centerIn: parent; text: "INSTALL FFMPEG"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.ff.install() }
                        }

                        // SYSTEM/linked, offer Waves' managed copy (one-click
                        // updates), the pop-up's "Let Waves manage instead" upsell.
                        ColumnLayout {
                            visible: page.ff.stateKey === "path" && !page.ff.busy
                            Layout.fillWidth: true; spacing: 11
                            Rectangle { Layout.fillWidth: true; Layout.topMargin: 3; implicitHeight: 1; color: page.divider }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 11
                                Rectangle {
                                    Layout.preferredWidth: 30; Layout.preferredHeight: 30; radius: 8
                                    color: page.surface3; Layout.alignment: Qt.AlignTop
                                    // Vector sync-twin retry mark (see Main.qml RetryMark; inline
                                    // copy, file-scope components don't cross QML files).
                                    Canvas {
                                        anchors.centerIn: parent; width: 17; height: 17
                                        antialiasing: true
                                        onPaint: {
                                            var ctx = getContext("2d"); ctx.reset()
                                            var b = width, cx = width / 2, cy = height / 2, r = b * 0.335
                                            ctx.strokeStyle = page.accent; ctx.fillStyle = page.accent
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
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 2
                                    Text { text: "Let Waves manage FFmpeg instead"; color: page.textHi; font.pixelSize: 13; font.weight: Font.Medium; Layout.fillWidth: true }
                                    Text {
                                        text: "Then you can update it in one click whenever you want. Most system installs have no built-in update path."
                                        color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; lineHeight: 1.3; Layout.fillWidth: true
                                    }
                                }
                                Rectangle {
                                    Layout.alignment: Qt.AlignVCenter
                                    implicitWidth: ffMgdTxt.implicitWidth + page.btnPadH * 2; implicitHeight: ffMgdTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    // Same primary-button look as the app's filled download buttons.
                                    color: page.accentCont; border.width: 1; border.color: page.accentDim
                                    // "SWITCH TO MANAGED", not "INSTALL": FFmpeg is already
                                    // on the system here, this changes who provides it,
                                    // and "install" reads like something is missing.
                                    Text { id: ffMgdTxt; anchors.centerIn: parent; text: "SWITCH TO MANAGED"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.ff.install() }
                                }
                            }
                        }

                        // BUSY, cancel (centered).
                        Rectangle {
                            visible: page.ff.busy
                            Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 2; Layout.preferredHeight: ffCanTxt.implicitHeight + page.btnPadV * 2
                            implicitWidth: ffCanTxt.implicitWidth + page.btnPadH * 2; radius: page.btnRad; color: "transparent"; border.color: page.red
                            Text { id: ffCanTxt; anchors.centerIn: parent; text: "CANCEL"; color: page.red; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.ff.cancel() }
                        }

                        // Source attribution, only the build source for THIS
                        // machine is shown, so users aren't confused by sources
                        // that don't apply to their OS/arch.
                        Text { // guard:deliberate-richtext ffmpeg-attribution
                            visible: page.ff.status.source ? true : false
                            Layout.fillWidth: true; Layout.topMargin: 4; wrapMode: Text.WordWrap
                            textFormat: Text.StyledText
                            linkColor: page.cyan
                            color: page.textDim; font.pixelSize: 11
                            text: "Managed builds for " + (page.ff.status.os || "") + "/" + (page.ff.status.arch || "")
                                  + " come from <a href=\"" + (page.ff.status.source_url || "") + "\">"
                                  + (page.ff.status.source || "") + "</a>"
                                  + (page.ff.status.source_license ? " · " + page.ff.status.source_license : "")
                                  + ". Thank you to the maintainers. FFmpeg © the FFmpeg project (ffmpeg.org)."
                            onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                        }
                    }
                    }

                    // ---- Managed: left tile, status + actions (the Updates
                    // card's layout, FFmpeg's darker palette) ----------------
                    Rectangle {
                        visible: ffCard.twin
                        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: (parent.width - 10) / 2
                        radius: 12; color: page.surface0; border.color: page.outline
                        ColumnLayout {
                            id: ffLeftCol
                            x: 16; y: 14; width: parent.width - 32; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Rectangle { width: 7; height: 7; radius: 4; color: page.green }
                                Text { text: "FFMPEG"; color: page.textHi; font.pixelSize: 13; font.bold: true; font.letterSpacing: 1.4 }
                                Text {
                                    textFormat: Text.PlainText
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                    // A pending update is worth shouting about, as
                                    // on the Updates card: gold and full-size.
                                    color: page.ff.updateAvailable ? page.gold : page.textDim
                                    font.pixelSize: page.ff.updateAvailable ? 14 : 12
                                    font.weight: page.ff.updateAvailable ? Font.DemiBold : Font.Normal
                                    text: page.ff.updateAvailable ? "update available"
                                        : "installed and managed by Waves"
                                }
                                Rectangle {
                                    visible: page.ff.updateAvailable && !page.ff.busy
                                    Layout.alignment: Qt.AlignVCenter
                                    radius: 5; color: page.goldCont; border.color: page.gold
                                    implicitWidth: ffUpdT.implicitWidth + 14; implicitHeight: 19
                                    Text { id: ffUpdT; textFormat: Text.PlainText; anchors.centerIn: parent; text: "UPDATE"; color: page.gold; font.family: page.mono; font.pixelSize: 10; font.bold: true }
                                }
                                Rectangle {
                                    Layout.alignment: Qt.AlignVCenter
                                    radius: 5; implicitHeight: 19; implicitWidth: ffChipTxt.implicitWidth + 16
                                    color: page.green
                                    Text {
                                        textFormat: Text.PlainText
                                        id: ffChipTxt; anchors.centerIn: parent
                                        text: "MANAGED" + (page.ff.status.version ? " · " + page.ff.status.version : "")
                                        color: page.greenCont
                                        font.family: page.mono; font.pixelSize: 10; font.bold: true; font.letterSpacing: 0.4
                                    }
                                }
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Used to convert videos to MP4, extract FLAC from MP4 containers, and downsample hi-res audio. Without it those steps are skipped."
                                color: page.textDim; font.pixelSize: 12
                            }

                            // Progress while an update is downloading/installing.
                            LedBar {
                                visible: page.ff.busy; Layout.fillWidth: true
                                radius: page.btnRad; mono: page.mono
                                pct: page.ff.pct
                                label: (page.ff.message || "Working…") + " · " + Math.round(page.ff.pct) + "%"
                            }

                            // Failure message
                            Text {
                                textFormat: Text.PlainText
                                visible: page.ff.lifeState === "failed"
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: page.ff.failPrefix + page.ff.message
                                color: page.red; font.pixelSize: 12
                            }

                            RowLayout {
                                Layout.topMargin: 2; spacing: 10
                                Rectangle {
                                    id: ffPrimary
                                    readonly property string label: page.ff.busy ? "Updating…"
                                        : page.ff.updateAvailable ? "Update"
                                        : (page.ff.checking ? "Checking…" : "Check for updates")
                                    implicitWidth: ffPrimTxt.implicitWidth + page.btnPadH * 2; implicitHeight: ffPrimTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    opacity: (page.ff.busy || page.ff.checking) ? 0.6 : 1.0
                                    color: page.accentCont; border.color: page.accentDim
                                    Text {
                                        textFormat: Text.PlainText
                                        id: ffPrimTxt; anchors.centerIn: parent; text: ffPrimary.label.toUpperCase()
                                        color: page.accent
                                        font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                    }
                                    MouseArea {
                                        anchors.fill: parent; enabled: !page.ff.busy && !page.ff.checking
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: { if (page.ff.updateAvailable) page.ff.install(); else page.ff.checkUpdates() }
                                    }
                                }
                                Rectangle {
                                    visible: !page.ff.busy
                                    implicitWidth: ffRemTxt.implicitWidth + page.btnPadH * 2; implicitHeight: ffRemTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    color: page.redCont; border.color: Qt.alpha(page.red, 0.55)
                                    Text { id: ffRemTxt; anchors.centerIn: parent; text: "REMOVE"; color: page.red; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.ff.remove() }
                                }
                                // Cancel while busy
                                Rectangle {
                                    visible: page.ff.busy
                                    implicitWidth: ffCanTxt2.implicitWidth + page.btnPadH * 2; implicitHeight: ffCanTxt2.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    color: "transparent"; border.color: page.red
                                    Text { id: ffCanTxt2; anchors.centerIn: parent; text: "CANCEL"; color: page.red; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.ff.cancel() }
                                }
                                // Transient confirmation after a check finds nothing new.
                                Text {
                                    visible: page.ff.upToDate && !page.ff.updateAvailable && !page.ff.checking
                                    text: "✓ Up to date"; color: page.green; font.pixelSize: 12
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                Text {
                                    visible: page.ff.checkFailed && !page.ff.updateAvailable && !page.ff.checking
                                    text: "✗ Could not check"; color: page.red; font.pixelSize: 12
                                    Layout.alignment: Qt.AlignVCenter
                                }
                            }

                            // Source attribution (same block as the single card).
                            Text { // guard:deliberate-richtext ffmpeg-attribution-managed
                                visible: page.ff.status.source ? true : false
                                Layout.fillWidth: true; Layout.topMargin: 2; wrapMode: Text.WordWrap
                                textFormat: Text.StyledText
                                linkColor: page.cyan
                                color: page.textDim; font.pixelSize: 11
                                text: "Managed builds for " + (page.ff.status.os || "") + "/" + (page.ff.status.arch || "")
                                      + " come from <a href=\"" + (page.ff.status.source_url || "") + "\">"
                                      + (page.ff.status.source || "") + "</a>"
                                      + (page.ff.status.source_license ? " · " + page.ff.status.source_license : "")
                                      + ". Thank you to the maintainers. FFmpeg © the FFmpeg project (ffmpeg.org)."
                                onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                            }
                        }
                    }

                    // ---- Managed: right tile, the shared auto-check controls,
                    // identical to the Updates card's tile by construction ----
                    AutoCheckTile {
                        id: ffTileR
                        visible: ffCard.twin
                        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: (parent.width - 10) / 2
                        radius: 12; color: page.surface0; border.color: page.outline
                        autoField: page.fieldByKey("ffmpeg_auto_update")
                        cadField: page.fieldByKey("ffmpeg_update_cadence")
                    }
                }
                }

                // In-app updater card, declared as a lazy template, shown by a
                // Loader at the top of the "Updates" section (mirrors the FFmpeg
                // card above). Twin-tile layout: status + actions on the left,
                // the auto-check toggle + spring cadence segment on the right
                // (the two `embedded` schema fields render here, not as rows).
                Component {
                    id: updatesCardComp
                    Item {
                    id: auCard
                    width: parent ? parent.width : 0
                    implicitHeight: Math.max(auLeftCol.implicitHeight + 24, auTileR.rowImplicitHeight + 24, 108)

                    readonly property string st: page.appUp.state ? page.appUp.state : "not_configured"
                    // Self-install, or a package-manager-owned copy whose
                    // manager the app can run for you (brew upgrade): both
                    // land on the same Update & restart button.
                    readonly property bool canInstall: page.appUp.can_self_install === true
                                                       || page.appUp.can_managed_install === true
                    readonly property string cur: page.appUp.current_version ? page.appUp.current_version : ""
                    // Embedded field descriptors (may be null before the schema loads).
                    readonly property var afAuto: page.fieldByKey("auto_update")
                    readonly property var afCad: page.fieldByKey("update_cadence")

                    // ---- Left tile: status, actions, releases link -----------
                    Rectangle {
                        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: (parent.width - 10) / 2
                        radius: 10; color: page.surface; border.color: page.border1
                        ColumnLayout {
                            id: auLeftCol
                            x: 14; y: 12; width: parent.width - 28; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Rectangle {
                                    width: 7; height: 7; radius: 4
                                    color: page.auUpdate ? page.gold : (auCard.st === "ready" ? page.green : page.textDim)
                                }
                                Text { textFormat: Text.PlainText; text: "Waves " + (auCard.cur || ""); color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                Text {
                                    textFormat: Text.PlainText
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                    // A pending update is the one state worth shouting about:
                                    // gold and full-size, not the dim caption gray.
                                    color: page.auUpdate ? page.gold : page.textDim
                                    font.pixelSize: page.auUpdate ? 14 : 12
                                    font.weight: page.auUpdate ? Font.DemiBold : Font.Normal
                                    text: auCard.st === "not_configured"
                                            ? "automatic updates aren't available in this build"
                                          : auCard.st === "source"
                                            ? ("from source" + (page.auUpdate ? " · v" + page.auLatest + " available" : " · up to date"))
                                          : auCard.st === "managed"
                                            ? ("via " + (page.appUp.channel_label || "a package manager")
                                               + (page.auUpdate ? " · v" + page.auLatest + " available" : " · up to date"))
                                          : (page.auUpdate ? "v" + page.auLatest + " available"
                                             : (page.auDone ? "installed; restart to finish" : "up to date"))
                                }
                                Rectangle {
                                    visible: page.auUpdate && !page.auBusy
                                    radius: 4; color: "#2a2008"; border.color: page.gold
                                    implicitWidth: auBadge.implicitWidth + 14; implicitHeight: 18
                                    Text { id: auBadge; anchors.centerIn: parent; text: "UPDATE"; color: page.gold; font.family: page.mono; font.pixelSize: 10; font.bold: true }
                                }
                            }
                            Text {
                                textFormat: Text.PlainText
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: auCard.st !== "managed"
                                    ? "Checks the public releases page and only notifies you; sends none of your data."
                                    : auCard.canInstall
                                      ? ("This copy is managed by " + (page.appUp.channel_label || "a package manager")
                                         + "; Update & restart runs its upgrade"
                                         + (page.appUp.update_hint ? " (" + page.appUp.update_hint + ")" : "") + " for you.")
                                      : ("This copy updates through " + (page.appUp.channel_label || "its package manager")
                                         + (page.appUp.update_hint ? " (" + page.appUp.update_hint + ")" : "")
                                         + "; checks here only notify you.")
                                color: page.textDim; font.pixelSize: 12
                            }

                            // Progress (while downloading/installing): the shared
                            // LED dot-matrix pill (LedBar.qml, extracted from the
                            // DownIcon running style via the Progress Lab).
                            LedBar {
                                visible: page.auBusy; Layout.fillWidth: true
                                radius: page.btnRad; mono: page.mono
                                pct: page.auPct
                                label: (page.auMsg || "Working…") + " · " + Math.round(page.auPct) + "%"
                            }

                            // Failure message
                            Text {
                                textFormat: Text.PlainText
                                visible: page.auState === "failed"
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Update failed: " + page.auMsg
                                color: page.red; font.pixelSize: 12
                            }
                            // The last restart did not land (the helper could not
                            // swap the install folder) and was re-armed once: the
                            // restart prompt says why instead of repeating itself.
                            Text {
                                textFormat: Text.PlainText
                                visible: page.auDone && (page.appUp.swap_failure || "") !== ""
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: (page.appUp.swap_failure || "") + " Restart to try once more."
                                color: page.red; font.pixelSize: 12
                            }

                            RowLayout {
                                Layout.topMargin: 2; spacing: 10

                                Rectangle {
                                    id: auPrimary
                                    readonly property bool restartMode: page.auDone
                                    readonly property bool updateMode: page.auUpdate && auCard.canInstall && !page.auDone
                                    readonly property bool browseMode: page.auUpdate && !auCard.canInstall && auCard.st !== "not_configured" && !page.auDone
                                    readonly property bool checkMode: !page.auUpdate && !page.auDone && auCard.st !== "not_configured"
                                    // Check is a lit primary too (Console verdict:
                                    // the section's one action shouldn't read as
                                    // a dim outline next to lit toggles).
                                    readonly property bool accent: restartMode || updateMode || checkMode
                                    readonly property string label: page.auBusy ? "Updating…"
                                        : restartMode ? "Restart now"
                                        : updateMode ? "Update & restart"
                                        : browseMode ? "Open releases page"
                                        : checkMode ? (page.auChecking ? "Checking…" : "Check for updates")
                                        : ""
                                    visible: label !== ""
                                    implicitWidth: auPrimTxt.implicitWidth + page.btnPadH * 2; implicitHeight: auPrimTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    opacity: (page.auBusy || page.auChecking) ? 0.6 : 1.0
                                    color: auPrimary.accent ? page.accentCont : "transparent"
                                    border.color: auPrimary.accent ? page.accentDim : page.outline
                                    Text {
                                        textFormat: Text.PlainText
                                        id: auPrimTxt; anchors.centerIn: parent; text: auPrimary.label.toUpperCase()
                                        color: auPrimary.accent ? page.accent : page.textLo
                                        font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                    }
                                    MouseArea {
                                        anchors.fill: parent; enabled: !page.auBusy && !page.auChecking
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (auPrimary.restartMode) waves.restartForUpdate()
                                            else if (auPrimary.updateMode) waves.installAppUpdate()
                                            else if (auPrimary.browseMode) waves.openReleasesPage()
                                            else if (auPrimary.checkMode) page.auCheck()
                                        }
                                    }
                                }

                                // Cancel while busy
                                Rectangle {
                                    visible: page.auBusy
                                    implicitWidth: auCanTxt.implicitWidth + page.btnPadH * 2; implicitHeight: auCanTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    color: "transparent"; border.color: page.red
                                    Text { id: auCanTxt; anchors.centerIn: parent; text: "CANCEL"; color: page.red; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.cancelAppUpdate() }
                                }

                                // Transient confirmation after a check finds nothing new.
                                Text {
                                    visible: page.auUpToDate && !page.auUpdate && !page.auBusy && !page.auChecking
                                    text: "✓ Up to date"; color: page.green; font.pixelSize: 12
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                // And the honest word when the check never got an answer.
                                Text {
                                    visible: page.auCheckFailed && !page.auUpdate && !page.auBusy && !page.auChecking
                                    text: "✗ Could not check"; color: page.red; font.pixelSize: 12
                                    Layout.alignment: Qt.AlignVCenter
                                }

                                // Releases link, once the repo is known.
                                Text { // guard:deliberate-richtext updater-releases-link
                                    visible: page.appUp.releases_url ? true : false
                                    Layout.alignment: Qt.AlignVCenter
                                    textFormat: Text.StyledText
                                    linkColor: page.cyan
                                    color: page.textDim; font.pixelSize: 11
                                    text: "<a href=\"" + (page.appUp.releases_url || "") + "\">releases &amp; changelog</a>"
                                    onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                                }
                            }
                        }
                    }

                    // ---- Right tile: auto-check toggle + cadence segment ------
                    // (AutoCheckTile, shared with the FFmpeg card.)
                    AutoCheckTile {
                        id: auTileR
                        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: (parent.width - 10) / 2
                        autoField: auCard.afAuto
                        cadField: auCard.afCad
                    }
                    }
                }

                // FLAC signature check (FFmpeg & FLAC section, at its end).
                // The folder row picks what SCAN walks: the Waves library (the
                // download and library folders) or a folder chosen with
                // Browse, remembered by the backend. SCAN reads every FLAC
                // header there (reads only); FIX writes the missing signature into
                // the files the scan found without one, after Main.qml's
                // confirmation; every run writes a CSV report, and VIEW REPORTS
                // shows them. The card renders page.flacState and never
                // decides anything itself.
                Component {
                    id: flacCardComp
                    Rectangle {
                        id: flacCard
                        objectName: "flacCard"
                        width: parent ? parent.width : 0
                        radius: 10; color: page.surface; border.color: page.border1
                        implicitHeight: flacCol.implicitHeight + 24
                        readonly property var st: page.flacState
                        readonly property bool running: st.running === true
                        readonly property var src: page.flacSources
                        readonly property bool chosen: src.source === "folder"
                        readonly property int fixable: running || page.flacStale() ? 0 : (st.fixable || 0)
                        readonly property real fraction: (st.total || 0) > 0 ? Math.min(1, (st.files || 0) / st.total) : 0
                        ColumnLayout {
                            id: flacCol
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.top: parent.top; anchors.topMargin: 12
                            anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Rectangle {
                                    width: 7; height: 7; radius: 4
                                    color: flacCard.running ? page.gold
                                         : flacCard.st.phase === "failed" ? page.red
                                         : page.flacMissed() > 0 ? page.gold
                                         : (flacCard.st.phase === "done" || flacCard.st.phase === "cancelled") && !page.flacStale() ? page.green
                                         : page.textDim
                                }
                                Text { text: "FLAC signatures"; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                // Wraps rather than elides: the end of the line can be
                                // the part that matters (folders a scan could not open).
                                Text {
                                    textFormat: Text.PlainText
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    color: flacCard.st.phase === "failed" ? page.red
                                         : (flacCard.fixable > 0 || (!page.flacStale() && page.flacCount("mismatch") > 0)
                                            || page.flacMissed() > 0 || page.flacResumable()) ? page.gold
                                         : page.textDim
                                    font.pixelSize: 12
                                    text: page.flacNote()
                                }
                            }
                            // While a run is on: the share of files worked through
                            // and, once the backend has a pace to go by, the time left.
                            RowLayout {
                                Layout.fillWidth: true; spacing: 10
                                visible: flacCard.running
                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: 4; radius: 2
                                    color: page.border1
                                    Rectangle {
                                        objectName: "flacBarFill"
                                        height: parent.height; radius: 2
                                        width: parent.width * flacCard.fraction
                                        color: flacCard.st.phase === "fixing" ? page.gold : page.accent
                                    }
                                }
                                Text {
                                    textFormat: Text.PlainText
                                    visible: text !== ""
                                    color: page.textDim; font.pixelSize: 12
                                    text: page.flacTimeLeft()
                                }
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "A FLAC file saved without its audio signature plays fine but fails strict checks (flac -t, Music Assistant). Scan reads the headers in your Waves library or a custom location below and writes a report. Fix adds the missing signature to those files, 34 header bytes each, and never changes the audio or touches a file that already has one. A file that has not changed since an earlier check is not read again unless Recheck all is on."
                                color: page.textDim; font.pixelSize: 12
                            }
                            // The folder choice, written straight to the backend's
                            // prefs (an action card, like Diagnostics: nothing to
                            // stage). The chosen path only ever comes from the
                            // system dialog, never typed, and the row is inert
                            // while a run is on.
                            FolderDialog {
                                id: flacFolderDlg
                                onAccepted: waves.flacCheckSetFolder(page.urlToPath(selectedFolder))
                            }
                            RowLayout {
                                Layout.fillWidth: true; Layout.topMargin: 4; spacing: 8
                                enabled: !flacCard.running
                                opacity: flacCard.running ? 0.5 : 1.0
                                // The two sources in the app's button vocabulary: the
                                // selected one lit (accent fill), the other outlined.
                                Rectangle {
                                    implicitWidth: flacSegW.implicitWidth + page.btnPadH * 2
                                    implicitHeight: flacSegW.implicitHeight + page.btnPadV * 2
                                    radius: page.btnRad
                                    color: !flacCard.chosen ? page.accentCont : "transparent"
                                    border.color: !flacCard.chosen ? page.accentDim : page.outline
                                    Text { id: flacSegW; anchors.centerIn: parent; text: "WAVES LIBRARY"; color: !flacCard.chosen ? page.accent : page.textLo; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.flacCheckSetSource("waves") }
                                }
                                Rectangle {
                                    implicitWidth: flacSegF.implicitWidth + page.btnPadH * 2
                                    implicitHeight: flacSegF.implicitHeight + page.btnPadV * 2
                                    radius: page.btnRad
                                    color: flacCard.chosen ? page.accentCont : "transparent"
                                    border.color: flacCard.chosen ? page.accentDim : page.outline
                                    Text { id: flacSegF; anchors.centerIn: parent; text: "CUSTOM LOCATION"; color: flacCard.chosen ? page.accent : page.textLo; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        // No folder picked yet: go straight to the dialog,
                                        // which selects this source when it returns one.
                                        onClicked: {
                                            if ((flacCard.src.folder || "") === "") { flacFolderDlg.open(); return }
                                            waves.flacCheckSetSource("folder")
                                        }
                                    }
                                }
                                // What a scan of the selection walks: the chosen folder,
                                // or the Waves library's folders.
                                Rectangle {
                                    Layout.fillWidth: true; Layout.preferredHeight: 36; radius: 8
                                    color: page.surface2; border.color: page.outline
                                    Text {
                                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                        anchors.leftMargin: 12; anchors.rightMargin: 12
                                        textFormat: Text.PlainText; elide: Text.ElideMiddle
                                        font.family: page.mono; font.pixelSize: 13
                                        readonly property string wavesList: (flacCard.src.waves || []).join(", ")
                                        color: (flacCard.chosen ? flacCard.src.folder : wavesList) ? page.textHi : page.textDim
                                        text: flacCard.chosen ? (flacCard.src.folder || "No folder chosen")
                                                              : (wavesList || "No download folder is set")
                                    }
                                }
                                // BROWSE as on every other folder field (SAVE CHANGES vocabulary).
                                Rectangle {
                                    visible: flacCard.chosen
                                    implicitWidth: flacBrwTxt.implicitWidth + page.btnPadH * 2
                                    implicitHeight: flacBrwTxt.implicitHeight + page.btnPadV * 2
                                    radius: page.btnRad; color: page.accentCont; border.color: page.accentDim
                                    Text { id: flacBrwTxt; anchors.centerIn: parent; text: "BROWSE"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            var du = page.pathUrl(flacCard.src.folder)
                                            if (du !== "") flacFolderDlg.currentFolder = du
                                            flacFolderDlg.open()
                                        }
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; Layout.topMargin: 2; Layout.bottomMargin: 2; spacing: 10
                                // SCAN, or CANCEL while a run is on.
                                Rectangle {
                                    implicitWidth: flacScanTxt.implicitWidth + page.btnPadH * 2
                                    implicitHeight: flacScanTxt.implicitHeight + page.btnPadV * 2
                                    radius: page.btnRad
                                    color: flacCard.running ? "transparent" : page.accentCont
                                    border.color: flacCard.running ? page.outline : page.accentDim
                                    Text {
                                        id: flacScanTxt; anchors.centerIn: parent
                                        textFormat: Text.PlainText
                                        text: flacCard.running ? "CANCEL" : "SCAN"
                                        color: flacCard.running ? page.textLo : page.accent
                                        font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                    }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (flacCard.running) waves.flacCheckCancel()
                                            else waves.flacCheckScan(page.flacDeep, page.flacRecheck)
                                        }
                                    }
                                }
                                // VIEW REPORTS: the reports folder, newest report selected,
                                // whenever one exists (a run in progress included). It
                                // sits beside SCAN so FIX appearing never moves it.
                                Rectangle {
                                    objectName: "flacReportsBtn"
                                    visible: flacCard.st.reports === true
                                    implicitWidth: flacRepTxt.implicitWidth + page.btnPadH * 2
                                    implicitHeight: flacRepTxt.implicitHeight + page.btnPadV * 2
                                    radius: page.btnRad; color: "transparent"; border.color: page.outline
                                    Text { id: flacRepTxt; anchors.centerIn: parent; text: "VIEW REPORTS"; color: page.textLo; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.flacCheckRevealReports() }
                                }
                                // FIX N FILES: only after a scan found files without a signature.
                                Rectangle {
                                    visible: flacCard.fixable > 0
                                    implicitWidth: flacFixTxt.implicitWidth + page.btnPadH * 2
                                    implicitHeight: flacFixTxt.implicitHeight + page.btnPadV * 2
                                    radius: page.btnRad; color: page.goldCont; border.color: Qt.alpha(page.gold, 0.6)
                                    Text {
                                        id: flacFixTxt; anchors.centerIn: parent
                                        textFormat: Text.PlainText
                                        text: "FIX " + page.flacNum(flacCard.fixable) + (flacCard.fixable === 1 ? " FILE" : " FILES")
                                        color: page.gold
                                        font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                    }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: page.flacFixRequested(flacCard.fixable, page.flacWhere())
                                    }
                                }
                                // RESUME LAST FIX: a fix stopped before it finished. Asks
                                // through Main.qml's gate like FIX; the backend then scans
                                // the same folders again and writes what is still missing.
                                Rectangle {
                                    objectName: "flacResumeBtn"
                                    visible: page.flacResumable()
                                    implicitWidth: flacResTxt.implicitWidth + page.btnPadH * 2
                                    implicitHeight: flacResTxt.implicitHeight + page.btnPadV * 2
                                    radius: page.btnRad; color: page.goldCont; border.color: Qt.alpha(page.gold, 0.6)
                                    Text {
                                        id: flacResTxt; anchors.centerIn: parent
                                        text: "RESUME LAST FIX"
                                        color: page.gold
                                        font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                    }
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: page.flacResumeRequested(page.flacResumeWhere())
                                    }
                                }
                                Item { Layout.fillWidth: true }
                                // The two switches, side by side on the right at their
                                // natural width; where the buttons leave less room (a
                                // narrow window, a long FIX count) the labels wrap rather
                                // than running off the card. The buttons never stretch.
                                RowLayout {
                                    objectName: "flacOptions"
                                    spacing: 16
                                    Layout.fillWidth: true
                                    Layout.maximumWidth: implicitWidth
                                    opacity: flacCard.running ? 0.5 : 1.0
                                    // Deep check: decode every file too. Off by default,
                                    // it takes about a second per track.
                                    RowLayout {
                                        objectName: "flacDeepRow"
                                        spacing: 8
                                        Layout.fillWidth: true
                                        Layout.maximumWidth: implicitWidth
                                        SToggle {
                                            checked: page.flacDeep
                                            MouseArea {
                                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                enabled: !flacCard.running
                                                onClicked: page.flacDeep = !page.flacDeep
                                            }
                                        }
                                        Text {
                                            id: flacDeepTxt
                                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                                            text: "Deep check: decode to find damage (slow)"
                                            color: page.textDim; font.pixelSize: 12
                                        }
                                    }
                                    // Recheck all: a scan normally takes an earlier scan's
                                    // verdict for a file whose size and time are unchanged.
                                    // This reads every file again (with Deep check, decodes
                                    // it), which is the only way to find damage that left
                                    // both alone. What it finds is remembered as usual.
                                    RowLayout {
                                        objectName: "flacRecheckRow"
                                        spacing: 8
                                        Layout.fillWidth: true
                                        Layout.maximumWidth: implicitWidth
                                        SToggle {
                                            checked: page.flacRecheck
                                            MouseArea {
                                                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                enabled: !flacCard.running
                                                onClicked: page.flacRecheck = !page.flacRecheck
                                            }
                                        }
                                        Text {
                                            id: flacRecheckTxt
                                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                                            text: "Recheck all: read unchanged files too"
                                            color: page.textDim; font.pixelSize: 12
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Reset row (Diagnostics section footer): explainer on the left,
                // the two reset actions inline on the right. The buttons only
                // raise page signals; Main.qml owns the confirmation dialogs
                // and the backend calls, so nothing here is destructive.
                Component {
                    id: resetRowComp
                    Rectangle {
                        width: parent ? parent.width : 0
                        radius: 10; color: page.surface; border.color: page.border1
                        implicitHeight: rstRow.implicitHeight + 20
                        RowLayout {
                            id: rstRow
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 12
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text { text: "Start over"; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                Text {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    text: "Put every setting back to its default, or wipe everything Waves has saved on this computer. Downloaded music is never touched."
                                    color: page.textDim; font.pixelSize: 12
                                }
                            }
                            Rectangle {
                                implicitWidth: rstDefTxt.implicitWidth + page.btnPadH * 2
                                implicitHeight: rstDefTxt.implicitHeight + page.btnPadV * 2
                                radius: page.btnRad; Layout.alignment: Qt.AlignVCenter
                                color: page.accentCont; border.color: page.accentDim
                                Text {
                                    id: rstDefTxt; anchors.centerIn: parent
                                    text: "RESET ALL SETTINGS"; color: page.accent
                                    font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                }
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                    onClicked: page.resetSettingsRequested()
                                }
                            }
                            Rectangle {
                                implicitWidth: rstAppTxt.implicitWidth + page.btnPadH * 2
                                implicitHeight: rstAppTxt.implicitHeight + page.btnPadV * 2
                                radius: page.btnRad; Layout.alignment: Qt.AlignVCenter
                                color: page.redCont; border.color: Qt.alpha(page.red, 0.55)
                                Text {
                                    id: rstAppTxt; anchors.centerIn: parent
                                    text: "RESET APPLICATION"; color: page.red
                                    font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                }
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                    onClicked: page.factoryResetRequested()
                                }
                            }
                        }
                    }
                }

                // Diagnostics card: status + export on the left, the two
                // privacy toggles on the right (its `embedded` schema fields).
                // Identity PII never reaches the log, so the card's job is
                // one action (export) and one decision (verbose on/off).
                Component {
                    id: diagCardComp
                    Item {
                    id: dgCard
                    width: parent ? parent.width : 0
                    implicitHeight: Math.max(dgLeftCol.implicitHeight + 24, dgRightCol.implicitHeight + 24, 108)

                    readonly property var dfVerbose: page.fieldByKey("verbose_diagnostics")
                    readonly property var dfRedact: page.fieldByKey("diagnostics_redact_content")
                    readonly property bool vbOn: dfVerbose ? page.val(dfVerbose) === true : false
                    readonly property bool rdOn: dfRedact ? page.val(dfRedact) === true : false

                    // ---- Left tile: status, export action ------------------
                    Rectangle {
                        anchors.left: parent.left; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: (parent.width - 10) / 2
                        radius: 10; color: page.surface; border.color: page.border1
                        ColumnLayout {
                            id: dgLeftCol
                            x: 14; y: 12; width: parent.width - 28; spacing: 6
                            RowLayout {
                                Layout.fillWidth: true; spacing: 8
                                Rectangle { width: 7; height: 7; radius: 4; color: dgCard.vbOn ? page.green : page.textDim }
                                Text { text: "Diagnostic report"; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                Text {
                                    textFormat: Text.PlainText
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                    color: page.textDim; font.pixelSize: 12
                                    text: dgCard.vbOn ? "verbose logging on" : "logging warnings only"
                                }
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Bundles recent activity and error logs into one text file, ready to attach to a bug report. Personal details are always removed."
                                color: page.textDim; font.pixelSize: 12
                            }
                            RowLayout {
                                Layout.topMargin: 2; spacing: 10
                                Rectangle {
                                    id: dgPrimary
                                    implicitWidth: dgPrimTxt.implicitWidth + page.btnPadH * 2; implicitHeight: dgPrimTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    opacity: page.diagBusy ? 0.6 : 1.0
                                    color: page.accentCont; border.color: page.accentDim
                                    Text {
                                        textFormat: Text.PlainText
                                        id: dgPrimTxt; anchors.centerIn: parent
                                        text: (page.diagBusy ? "Exporting…" : "Export report").toUpperCase()
                                        color: page.accent
                                        font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                    }
                                    MouseArea {
                                        anchors.fill: parent; enabled: !page.diagBusy
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            // Do NOT re-push the two prefs here. Both toggles below
                                            // apply live via setWavesPref, so the backend already
                                            // holds the truth and there is no unsaved change to
                                            // honor. Pushing vbOn/rdOn instead sent whatever the
                                            // card was rendering, and after a close-and-reopen that
                                            // is the STALE baked-in schema value (onActiveChanged
                                            // clears editMap but only rebuilds `groups` when
                                            // needsRefresh is set). Exporting therefore switched
                                            // both prefs back off, stopped the freeze watchdog, and
                                            // produced a bundle containing exactly the searches and
                                            // titles the user had asked to hide.
                                            page.diagFailed = false
                                            page.diagBusy = true
                                            waves.exportDiagnostics()
                                        }
                                    }
                                }
                                Rectangle {
                                    visible: page.diagPath !== "" && !page.diagBusy
                                    implicitWidth: dgShowTxt.implicitWidth + page.btnPadH * 2; implicitHeight: dgShowTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                    color: "transparent"; border.color: page.outline
                                    Text { id: dgShowTxt; anchors.centerIn: parent; text: "SHOW FILE"; color: page.textLo; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: waves.revealDiagnostics(page.diagPath) }
                                }
                                Text {
                                    visible: page.diagPath !== "" && !page.diagBusy
                                    text: "✓ Saved"; color: page.green; font.pixelSize: 12
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                Text {
                                    visible: page.diagFailed
                                    text: "Export failed"; color: page.red; font.pixelSize: 12
                                    Layout.alignment: Qt.AlignVCenter
                                }
                            }
                        }
                    }

                    // ---- Right tile: verbose + redact-content switches (words
                    // left, switch right, as everywhere on the page) ----------
                    Rectangle {
                        anchors.right: parent.right; anchors.top: parent.top; anchors.bottom: parent.bottom
                        width: (parent.width - 10) / 2
                        radius: 10; color: page.surface; border.color: page.border1
                        ColumnLayout {
                            id: dgRightCol
                            x: 14; width: parent.width - 28; spacing: 8
                            anchors.verticalCenter: parent.verticalCenter
                            RowLayout {
                                Layout.fillWidth: true; spacing: 13
                                ColumnLayout {
                                    Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; spacing: 3
                                    Text { textFormat: Text.PlainText; text: dgCard.dfVerbose ? dgCard.dfVerbose.label : ""; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true; Layout.topMargin: page.tileTitleNudge }
                                    Text {
                                        text: "Logs detailed activity to help diagnose slowdowns, freezes and crashes. Turn on, reproduce the problem, then export."
                                        color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                                        wrapMode: Text.WordWrap; maximumLineCount: 4; elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                }
                                SToggle {
                                    Layout.alignment: Qt.AlignTop
                                    checked: dgCard.vbOn
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            var v = !dgCard.vbOn
                                            page.setLive("verbose_diagnostics", v)
                                            // Applies live: the watchdog and detail level flip now,
                                            // not on Save, so "turn on, reproduce, export" just works.
                                            waves.setWavesPref("verbose_diagnostics", v)
                                            // Applied without a Save, so the baked schema `groups`
                                            // is now stale; force a rebuild on the next reopen or
                                            // the card renders the pre-change value.
                                            page.needsRefresh = true
                                        }
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 13
                                ColumnLayout {
                                    Layout.fillWidth: true; Layout.alignment: Qt.AlignTop; spacing: 3
                                    Text { textFormat: Text.PlainText; text: dgCard.dfRedact ? dgCard.dfRedact.label : ""; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true; Layout.topMargin: page.tileTitleNudge }
                                    Text {
                                        text: "Exports always remove usernames, paths, addresses and tokens. This also hides searches and titles, which can make bugs harder to reproduce."
                                        color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                                        wrapMode: Text.WordWrap; maximumLineCount: 4; elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                }
                                SToggle {
                                    Layout.alignment: Qt.AlignTop
                                    checked: dgCard.rdOn
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            var v = !dgCard.rdOn
                                            page.setLive("diagnostics_redact_content", v)
                                            waves.setWavesPref("diagnostics_redact_content", v)
                                            page.needsRefresh = true
                                        }
                                    }
                                }
                            }
                        }
                    }
                    }
                }

                // Settings sections, schema-driven. Only the one picked on
                // the rail shows (no transition: the switch is instant); the
                // rest stay built, so a deep link to a card inside one (the
                // FLAC check) can measure it the moment its section shows.
            Repeater {
                id: secRep
                model: page.groups
                delegate: Rectangle {
                    id: card
                    required property var modelData
                    visible: card.modelData.id === page.curSec
                    width: col.width
                    radius: 12; clip: true
                    color: "transparent"
                    implicitHeight: hd.implicitHeight + bodyWrap.height
                    // The section on show is always open (the old accordion is
                    // gone); kept as a property because the body's fades read it.
                    readonly property bool open: true
                    // A section with a master switch (Notifications) sleeps
                    // while it is off: the body fades and takes no clicks.
                    readonly property var masterF: page.masterField(card.modelData.fields)
                    readonly property bool masterOff: card.masterF !== null && page.val(card.masterF) !== true

                    // Section header: icon, the rail group as a crumb, title, blurb.
                    Item {
                        id: hd
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        implicitHeight: Math.max(30, hdRow.implicitHeight) + 8
                        RowLayout {
                            id: hdRow
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 0; anchors.rightMargin: 0
                            spacing: 12
                            Rectangle {
                                id: glyphTile
                                Layout.preferredWidth: 34; Layout.preferredHeight: 34; radius: 8
                                color: page.surface3; Layout.alignment: Qt.AlignVCenter
                                // The FFmpeg section's glyph doubles as a status light:
                                // red = not found, yellow = found but unmanaged (system
                                // PATH or a linked path), and managed-by-Waves reads as
                                // the standard accent, so a healthy FFmpeg section looks
                                // like every other section (page.green is mintier than
                                // the accent and made this one glyph stand out). The
                                // Updates glyph goes gold while a newer release is
                                // available. Every other section keeps the accent glyph.
                                readonly property bool ffStatus: card.modelData.card === "ffmpeg" && page.ff
                                readonly property bool auStatus: card.modelData.card === "updates" && page.auUpdate
                                readonly property color statusColor: glyphTile.auStatus ? page.gold
                                    : !glyphTile.ffStatus ? page.accent
                                    : page.ff.stateKey === "managed" ? page.accent
                                    : page.ff.stateKey === "path" ? page.gold
                                    : page.red
                                // No status ring: it made the FFmpeg (and Updates) tile the
                                // only outlined section, which looked out of place. The status
                                // still reads from the glyph color (red/gold/green) below.
                                border.width: 0
                                SectionIcon {
                                    anchors.centerIn: parent
                                    glyph: card.modelData.id !== undefined ? card.modelData.id : ""
                                    stroke: glyphTile.statusColor
                                    px: 20
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true; spacing: 2
                                Text {
                                    textFormat: Text.PlainText
                                    text: page.railCrumb(card.modelData.id) + "  \u203a"
                                    color: page.textDim; font.family: page.uiFont; font.pixelSize: 12; font.weight: Font.Medium
                                    Layout.bottomMargin: -1
                                }
                                // The master switch rides right after the title:
                                // it names what the whole section is, so it reads
                                // as the section's own on/off, not one more option.
                                RowLayout {
                                    Layout.fillWidth: true; spacing: 12
                                    Text {
                                        textFormat: Text.PlainText
                                        text: card.modelData.group; color: page.textHi
                                        font.pixelSize: 20; font.weight: Font.DemiBold
                                        Layout.fillWidth: card.masterF === null; elide: Text.ElideRight
                                    }
                                    SToggle {
                                        objectName: "sectionMaster"
                                        visible: card.masterF !== null
                                        Layout.alignment: Qt.AlignVCenter
                                        checked: !card.masterOff
                                        MouseArea {
                                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                            onClicked: page.setv(card.masterF.key, card.masterOff)
                                        }
                                    }
                                    Item { visible: card.masterF !== null; Layout.fillWidth: true }
                                }
                                Text {
                                    textFormat: Text.PlainText
                                    visible: text !== ""
                                    text: card.modelData.desc !== undefined ? card.modelData.desc : ""
                                    color: page.textDim; font.pixelSize: 12
                                    wrapMode: Text.WordWrap; Layout.fillWidth: true
                                }
                                Text {
                                    objectName: "sectionMasterNote"
                                    textFormat: Text.PlainText
                                    visible: text !== ""
                                    text: card.masterF !== null ? page.masterNote(card.masterF) : ""
                                    color: card.masterOff ? page.textLo : page.accent; font.pixelSize: 12
                                    wrapMode: Text.WordWrap; Layout.fillWidth: true
                                }
                            }
                        }
                    }

                    // The body. Driving a real (animated) height keeps the
                    // Flickable's contentHeight binding re-measuring as rows
                    // inside it show and hide.
                    Item {
                        id: bodyWrap
                        objectName: "sectionBody"
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: hd.bottom
                        clip: true
                        // Asleep under an off master: the Library card's 0.4.
                        enabled: !card.masterOff
                        opacity: card.masterOff ? 0.4 : 1
                        Behavior on opacity { NumberAnimation { duration: page.openMs } }
                        height: card.open ? inner.implicitHeight + 26 : 0
                        Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

                        Rectangle { id: bodyDiv; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: 1; color: page.divider }

                        Column {
                            id: inner
                            x: 1; y: 14; width: parent.width - 2; spacing: 10
                            // Fade in quickly on open; on close, outlast the height
                            // collapse (220ms) so content doesn't vanish early.
                            // States, not a Behavior: a Behavior whose duration
                            // binding also reads card.open captures the OLD value
                            // when the flip triggers it, so the two swapped and
                            // every close faded the content out in 160ms under a
                            // 220ms collapse: content vanishing early, the exact
                            // artifact the asymmetry exists to prevent. (Measured
                            // on HoverSwell in Main.qml, which is a Transition for
                            // this reason.)
                            opacity: 0
                            states: State { name: "open"; when: card.open
                                PropertyChanges { target: inner; opacity: 1 } }
                            transitions: [
                                Transition { to: "open"
                                    NumberAnimation { property: "opacity"; duration: 160; easing.type: Easing.OutQuad } },
                                Transition { from: "open"
                                    NumberAnimation { property: "opacity"; duration: 220; easing.type: Easing.OutQuad } }
                            ]

                            // FFmpeg manager card, Processing section only.
                            Loader {
                                active: card.modelData.card === "ffmpeg"
                                visible: active
                                width: inner.width
                                height: (active && item) ? item.implicitHeight : 0
                                sourceComponent: ffmpegCardComp
                            }

                            // In-app updater card, Updates section only.
                            Loader {
                                active: card.modelData.card === "updates"
                                visible: active
                                width: inner.width
                                height: (active && item) ? item.implicitHeight : 0
                                sourceComponent: updatesCardComp
                            }

                            // Diagnostics card, Diagnostics section only.
                            Loader {
                                active: card.modelData.card === "diagnostics"
                                visible: active
                                width: inner.width
                                height: (active && item) ? item.implicitHeight : 0
                                sourceComponent: diagCardComp
                            }

                            // The per-character stand-in table: one box for each
                            // character a file name cannot hold. A box left empty
                            // follows the general stand-in (shown grayed inside
                            // it), so the table costs nothing until a character
                            // is actually given something of its own. Folded away
                            // unless it holds an override, since almost nobody
                            // needs more than the one general stand-in.
                            Component {
                                id: charMapComp
                                Rectangle {
                                    id: mapCard
                                    required property var modelData
                                    // Set by the one-time offer's own buttons; the strip also
                                    // goes once the table holds anything, so filling a box in
                                    // by hand answers it too.
                                    property bool offerAnswered: false
                                    width: inner.width
                                    radius: 10; color: page.surface; border.color: page.border1
                                    implicitHeight: mapCol.implicitHeight + 20
                                    Column {
                                        id: mapCol
                                        x: 14; y: 10; width: parent.width - 28; spacing: 6
                                        // Open on arrival when the user has overrides
                                        // saved, so their own settings are never hidden
                                        // behind a fold they have to remember. Decided
                                        // once, not bound: clearing the last override
                                        // would otherwise fold the table away under the
                                        // hand that just cleared it.
                                        property bool expanded: false
                                        Component.onCompleted: expanded = Object.keys(page.mapVal(mapCard.modelData)).length > 0
                                        Row {
                                            width: parent.width; spacing: 10
                                            Text {
                                                textFormat: Text.PlainText
                                                id: mapLabel
                                                text: mapCard.modelData.label; color: page.textHi
                                                font.pixelSize: 14; font.weight: Font.Medium
                                            }
                                            // Same per-field Restore default as the value rows,
                                            // and the permanent way back to the recommended table
                                            // once the one-time strip below has been answered.
                                            Text {
                                                id: mapDefaultLink
                                                readonly property bool changed: !page.mapIsDefault(mapCard.modelData)
                                                visible: mapCard.modelData.default_value !== undefined
                                                anchors.verticalCenter: mapLabel.verticalCenter
                                                textFormat: Text.PlainText
                                                text: changed ? "Use recommended" : "Recommended"
                                                color: !changed ? page.textDim
                                                                : mapDefaultMa.containsMouse ? page.accent : page.accentDim
                                                opacity: changed ? 1 : 0.5
                                                font.pixelSize: 12
                                                font.underline: changed && mapDefaultMa.containsMouse
                                                MouseArea {
                                                    id: mapDefaultMa
                                                    anchors.fill: parent; anchors.margins: -4
                                                    enabled: mapDefaultLink.changed
                                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                                    onClicked: {
                                                        page.mapStage(mapCard.modelData, mapCard.modelData.default_value)
                                                        mapCol.expanded = true
                                                    }
                                                }
                                            }
                                        }
                                        Text {
                                            textFormat: Text.PlainText
                                            visible: modelData.help !== ""
                                            width: parent.width
                                            text: modelData.help; color: page.textDim; font.pixelSize: 12
                                            wrapMode: Text.WordWrap
                                        }

                                        // Shown once, to an install that predates the table and
                                        // has no stand-ins of its own: the recommended set is
                                        // never applied behind the user's back, because their
                                        // folders are already spelled the old way. Answering it
                                        // any of the three ways takes it away for good; the
                                        // Recommended link above stays as the way back.
                                        Rectangle {
                                            id: offerStrip
                                            visible: mapCard.modelData.offer === true
                                                     && !mapCard.offerAnswered
                                                     && Object.keys(page.mapVal(mapCard.modelData)).length === 0
                                            width: parent.width
                                            implicitHeight: offerCol.implicitHeight + 20
                                            radius: 8
                                            color: page.accentCont; border.color: page.accentDim
                                            Column {
                                                id: offerCol
                                                x: 12; y: 10; width: parent.width - 24; spacing: 6
                                                Text {
                                                    textFormat: Text.PlainText
                                                    text: "New: recommended stand-ins"
                                                    color: page.accent; font.pixelSize: 13; font.weight: Font.Medium
                                                }
                                                Text {
                                                    width: parent.width; wrapMode: Text.WordWrap
                                                    textFormat: Text.PlainText
                                                    text: "Waves can write these five characters instead of removing them. "
                                                          + "Folders already on disk keep the names they have; this applies to new downloads."
                                                    color: page.textDim; font.pixelSize: 12
                                                }
                                                // One pill per proposal. Run as bare text they blur
                                                // into each other (" / → - \ → - : → · "), and the
                                                // pill also makes the spaces around " · " visible,
                                                // which are half of what that stand-in is.
                                                Flow {
                                                    width: parent.width; spacing: 8; topPadding: 2
                                                    Repeater {
                                                        model: page.mapDefaultChars(mapCard.modelData)
                                                        delegate: Rectangle {
                                                            required property string modelData
                                                            implicitWidth: chipRow.width + 20
                                                            implicitHeight: 26
                                                            radius: 6
                                                            color: page.surface2; border.color: page.outline
                                                            Row {
                                                                id: chipRow
                                                                anchors.centerIn: parent; spacing: 6
                                                                Text {
                                                                    anchors.verticalCenter: parent.verticalCenter
                                                                    textFormat: Text.PlainText
                                                                    text: parent.parent.modelData
                                                                    color: page.textHi
                                                                    font.family: page.mono; font.pixelSize: 12
                                                                }
                                                                Text {
                                                                    anchors.verticalCenter: parent.verticalCenter
                                                                    textFormat: Text.PlainText
                                                                    text: "→"; color: page.accent; font.pixelSize: 11
                                                                }
                                                                Text {
                                                                    anchors.verticalCenter: parent.verticalCenter
                                                                    textFormat: Text.PlainText
                                                                    text: mapCard.modelData.default_value[parent.parent.modelData]
                                                                    color: page.textHi
                                                                    font.family: page.mono; font.pixelSize: 12
                                                                }
                                                            }
                                                        }
                                                    }
                                                }
                                                Text {
                                                    width: parent.width; elide: Text.ElideRight
                                                    topPadding: 2
                                                    textFormat: Text.PlainText
                                                    text: "Mercury: Act 1  →  Mercury · Act 1"
                                                    color: page.textDim; font.family: page.mono; font.pixelSize: 11
                                                }
                                                Row {
                                                    topPadding: 2; spacing: 8
                                                    // Staged, not saved: the values land in the boxes
                                                    // below and SAVE CHANGES lights up, so the table can
                                                    // still be tweaked (or canceled) before it is real.
                                                    Rectangle {
                                                        implicitWidth: offUseTxt.implicitWidth + page.btnPadH * 2
                                                        implicitHeight: offUseTxt.implicitHeight + page.btnPadV * 2
                                                        radius: page.btnRad
                                                        color: page.accentCont; border.color: page.accentDim
                                                        Text {
                                                            id: offUseTxt; anchors.centerIn: parent; text: "USE THESE"
                                                            color: page.accent; font.pixelSize: 13; font.family: page.uiFont
                                                            font.bold: true; font.letterSpacing: page.btnTrack
                                                        }
                                                        MouseArea {
                                                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                            onClicked: {
                                                                page.mapStage(mapCard.modelData, mapCard.modelData.default_value)
                                                                mapCol.expanded = true
                                                            }
                                                        }
                                                    }
                                                    Rectangle {
                                                        implicitWidth: offKeepTxt.implicitWidth + page.btnPadH * 2
                                                        implicitHeight: offKeepTxt.implicitHeight + page.btnPadV * 2
                                                        radius: page.btnRad
                                                        color: "transparent"; border.color: page.outline
                                                        Text {
                                                            id: offKeepTxt; anchors.centerIn: parent; text: "KEEP REMOVING THEM"
                                                            color: page.textDim; font.pixelSize: 13; font.family: page.uiFont
                                                            font.bold: true; font.letterSpacing: page.btnTrack
                                                        }
                                                        MouseArea {
                                                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                            // The only one of the three that decides
                                                            // anything on its own, so it is the only one
                                                            // that writes the "asked already" stamp.
                                                            onClicked: {
                                                                waves.resolveIllegalMapOffer()
                                                                mapCard.offerAnswered = true
                                                            }
                                                        }
                                                    }
                                                    Rectangle {
                                                        implicitWidth: offOwnTxt.implicitWidth + page.btnPadH * 2
                                                        implicitHeight: offOwnTxt.implicitHeight + page.btnPadV * 2
                                                        radius: page.btnRad
                                                        color: "transparent"; border.color: page.outline
                                                        Text {
                                                            id: offOwnTxt; anchors.centerIn: parent; text: "SET MY OWN"
                                                            color: page.textDim; font.pixelSize: 13; font.family: page.uiFont
                                                            font.bold: true; font.letterSpacing: page.btnTrack
                                                        }
                                                        MouseArea {
                                                            anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                            // Opens the table and steps out of the way.
                                                            // No stamp: leaving without typing anything
                                                            // has decided nothing, so it asks again.
                                                            onClicked: {
                                                                mapCol.expanded = true
                                                                mapCard.offerAnswered = true
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                        Text {
                                            textFormat: Text.PlainText
                                            topPadding: 2
                                            text: (mapCol.expanded ? "▾  " : "▸  ") + "Set one per character"
                                            color: page.accent; font.pixelSize: 12
                                            MouseArea {
                                                anchors.fill: parent; anchors.margins: -4
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: mapCol.expanded = !mapCol.expanded
                                            }
                                        }
                                        Grid {
                                            id: mapGrid
                                            visible: mapCol.expanded
                                            width: parent.width
                                            // As many per line as fit, so nine short rows
                                            // stay two lines on a normal window instead of
                                            // three lines of mostly gap.
                                            columns: Math.max(3, Math.min(5, Math.floor(width / 200)))
                                            columnSpacing: 10; rowSpacing: 8
                                            topPadding: 4
                                            Repeater {
                                                model: modelData.chars ? modelData.chars : []
                                                delegate: Row {
                                                    id: charRow
                                                    required property var modelData
                                                    readonly property var fieldData: mapCard.modelData
                                                    readonly property string ch: charRow.modelData.char
                                                    readonly property bool overridden: page.mapHas(charRow.fieldData, charRow.ch)
                                                    width: (mapGrid.width - mapGrid.columnSpacing * (mapGrid.columns - 1)) / mapGrid.columns
                                                    spacing: 8
                                                    Text {
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        width: 14
                                                        textFormat: Text.PlainText
                                                        text: charRow.ch; color: page.textHi
                                                        font.family: page.mono; font.pixelSize: 13
                                                    }
                                                    Text {
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        textFormat: Text.PlainText
                                                        text: "→"
                                                        // Accent marks the characters carrying a
                                                        // stand-in of their own; the rest read as
                                                        // the plumbing they are.
                                                        color: charRow.overridden ? page.accent : page.textDim
                                                        font.pixelSize: 12
                                                    }
                                                    SText {
                                                        id: charBox
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        // The stand-in row's box, a shade wider: this
                                                        // one may hold spaces around its character
                                                        // (" · "), which want to be visible.
                                                        width: 88; height: 32
                                                        text: page.mapText(charRow.fieldData, charRow.ch)
                                                        // An empty box says what the character
                                                        // becomes anyway: the general stand-in, or
                                                        // nothing at all when there is none.
                                                        placeholderText: charRow.overridden
                                                                         ? "none"
                                                                         : (page.generalStandIn() === "" ? "none" : page.generalStandIn())
                                                        invalid: page.mapCharDirty(charRow.fieldData, charRow.ch)
                                                        onEdited: function(t){ page.mapSet(charRow.fieldData, charRow.ch, t) }
                                                        // A whole table staged at once (the offer strip,
                                                        // the Recommended link) has to reach the boxes
                                                        // the same way the clear button does.
                                                        Connections {
                                                            target: page
                                                            function onMapRestaged(key) {
                                                                if (key !== charRow.fieldData.key)
                                                                    return
                                                                charBox.text = Qt.binding(function() {
                                                                    return page.mapText(charRow.fieldData, charRow.ch)
                                                                })
                                                            }
                                                        }
                                                    }
                                                    Item {
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        width: 16; height: 16
                                                        visible: charRow.overridden
                                                        Ico {
                                                            anchors.centerIn: parent
                                                            name: "close"; size: 11
                                                            color: clearMa.containsMouse ? page.textHi : page.textDim
                                                        }
                                                        MouseArea {
                                                            id: clearMa
                                                            anchors.fill: parent; anchors.margins: -3
                                                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                                            // Back to following the general stand-in.
                                                            // Clearing the box cannot mean this: an
                                                            // empty stand-in is itself a choice
                                                            // (remove the character outright).
                                                            onClicked: {
                                                                page.mapClear(charRow.fieldData, charRow.ch)
                                                                charBox.text = Qt.binding(function() {
                                                                    return page.mapText(charRow.fieldData, charRow.ch)
                                                                })
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                    Component.onCompleted: page.sanitizeKeys[modelData.key] = modelData
                                }
                            }

                            // Value-bearing settings (str / enum / int / float) as
                            // labeled rows. One shared delegate feeds two Repeaters:
                            // the Files & Naming card renders its path-template
                            // rows first, the token reference right under them, then
                            // its remaining value rows; every other card renders all
                            // of its rows in the first Repeater.
                            Component {
                                id: rowFieldComp
                                Rectangle {
                                    id: fieldRow
                                    required property var modelData
                                    visible: page.depOK(modelData)
                                    // Third fields sit three-up in the Flow below.
                                    width: modelData.third === true ? (inner.width - 20) / 3 : inner.width
                                    radius: 10; color: "transparent"; border.color: "transparent"
                                    // Three-up cards hold a common height whatever their
                                    // help runs to, so a row of them reads as one band
                                    // instead of three ragged blocks; their content
                                    // centers in it rather than hugging the top.
                                    readonly property real cardH: modelData.third === true ? (inlineRow.stack ? 124 : 96) : body.implicitHeight + 20
                                    // A switch row with a sub-option (child_key) carries it in
                                    // a band tucked behind the card, sliding out while the
                                    // switch is on; as it opens the card's bottom corners
                                    // square off so the sides run straight and only the band
                                    // rounds the bottom (notes/footer_lab, H5).
                                    readonly property bool hasBand: modelData.rowToggle === true && modelData.child_key !== undefined
                                    readonly property bool bandOn: hasBand && page.val(modelData) === true
                                    readonly property bool bandVal: hasBand && page.valChild(modelData) === true
                                    property real bandOpen: bandOn ? 1 : 0
                                    Behavior on bandOpen { NumberAnimation { duration: page.openMs; easing.type: Easing.OutCubic } }
                                    property real bandK: bandVal ? 1 : 0
                                    Behavior on bandK { NumberAnimation { duration: page.keyMs; easing.type: Easing.OutCubic } }
                                    readonly property real bandBody: hasBand ? (bandCol.implicitHeight + 22) * bandOpen : 0
                                    implicitHeight: cardH + bandBody
                                    Rectangle {
                                        id: rowBand
                                        visible: fieldRow.hasBand && fieldRow.bandOpen > 0
                                        y: fieldRow.cardH - 10
                                        width: parent.width; height: 10 + fieldRow.bandBody
                                        radius: 10; clip: true
                                        color: page.mix(page.surface0, page.accentCont, fieldRow.bandK)
                                        // The card's soft shade on the band, right under its edge.
                                        Rectangle {
                                            y: 10; width: parent.width; height: 9
                                            opacity: 0.9 * fieldRow.bandOpen
                                            gradient: Gradient {
                                                GradientStop { position: 0; color: "#80000000" }
                                                GradientStop { position: 1; color: "#00000000" }
                                            }
                                        }
                                        Item {
                                            x: 14; y: 10 + 11; width: parent.width - 28; height: bandCol.implicitHeight
                                            opacity: fieldRow.bandOpen
                                            // The shipped round check.
                                            RoundCheck { id: bandCheck; y: 2; k: fieldRow.bandK }
                                            Column {
                                                id: bandCol
                                                x: bandCheck.width + 10; width: parent.width - x; spacing: 2
                                                Text {
                                                    textFormat: Text.PlainText; width: parent.width; elide: Text.ElideRight
                                                    text: fieldRow.modelData.child_label !== undefined ? fieldRow.modelData.child_label : ""
                                                    color: page.mix(page.textHi, page.accentContTx, fieldRow.bandK)
                                                    font.pixelSize: 13
                                                }
                                                Text {
                                                    textFormat: Text.PlainText; width: parent.width; wrapMode: Text.WordWrap
                                                    visible: text !== ""
                                                    text: fieldRow.modelData.child_help !== undefined ? fieldRow.modelData.child_help : ""
                                                    color: page.textDim; font.pixelSize: 12
                                                }
                                            }
                                        }
                                        MouseArea {
                                            anchors.fill: parent; anchors.topMargin: 10
                                            enabled: fieldRow.bandOn
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: page.setv(fieldRow.modelData.child_key, !fieldRow.bandVal)
                                        }
                                    }
                                    // The card itself, over the band.
                                    Rectangle {
                                        width: parent.width; height: fieldRow.cardH
                                        radius: 10; color: page.surface
                                        // Squares the bottom corners as the band opens.
                                        Rectangle {
                                            visible: fieldRow.hasBand
                                            y: parent.height - 10; width: parent.width; height: 10
                                            color: page.surface; opacity: fieldRow.bandOpen
                                        }
                                    }
                                    Rectangle {
                                        x: 0; width: 3; radius: 1.5
                                        height: page.fieldDirty(fieldRow.modelData) ? Math.max(0, fieldRow.cardH - 28) : 0
                                        y: (fieldRow.cardH - height) / 2
                                        color: page.accent
                                        Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                                    }
                                    Item {
                                        id: body
                                        x: 14; width: parent.width - 28
                                        y: modelData.third === true ? Math.max(10, (fieldRow.cardH - implicitHeight) / 2) : 10
                                        implicitHeight: modelData.type === "str" && modelData.inline !== true ? strCol.implicitHeight
                                                      : modelData.type === "cover_sizes" ? coverCol.implicitHeight
                                                      : modelData.type === "library" ? libraryLoader.implicitHeight
                                                      : inlineRow.implicitHeight

                                        // Enum / int / float / short str: label + help on
                                        // the left, control on the right. A str field
                                        // flagged "inline" holds a value of a character or
                                        // two, so it gets a small box here instead of a
                                        // full-width one under the help. When the control
                                        // would leave the words too little room (a long
                                        // choice, or a narrow window) it moves under them
                                        // instead, every option still on show.
                                        GridLayout {
                                            id: inlineRow
                                            visible: (modelData.type !== "str" || modelData.inline === true)
                                                     && modelData.type !== "cover_sizes" && modelData.type !== "library"
                                            width: parent.width; columnSpacing: 14; rowSpacing: 10
                                            readonly property real ctrlW: modelData.type === "enum" ? inlineChoice.implicitWidth
                                                                        : modelData.type === "kinds" ? kindsRow.implicitWidth
                                                                        : modelData.type === "range" ? rangeRow.implicitWidth
                                                                        : 0
                                            // Third-width boxes stack as a band, all three at once,
                                            // so the row keeps one height.
                                            readonly property bool stack: modelData.third === true ? width < 272
                                                                        : ctrlW > 0 && width - ctrlW - 14 < page.rowWordsMinW
                                            columns: stack ? 1 : 2
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 2
                                                // A Flow, so the default link drops under a label that
                                                // fills a narrow box.
                                                Flow {
                                                    Layout.fillWidth: true
                                                    spacing: 10
                                                    Text { id: inlineLabel; textFormat: Text.PlainText; text: modelData.label; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                                    // Same per-field Restore default link as the
                                                    // full-width str rows (see strDefaultLink).
                                                    Text {
                                                        id: inlineDefaultLink
                                                        readonly property bool changed: modelData.default_value !== undefined
                                                                                        && String(page.val(modelData)) !== String(modelData.default_value)
                                                        visible: modelData.inline === true && modelData.default_value !== undefined
                                                        height: inlineLabel.height; verticalAlignment: Text.AlignVCenter
                                                        textFormat: Text.PlainText
                                                        text: changed ? "Restore default" : "Default"
                                                        color: !changed ? page.textDim
                                                                        : inlineDefaultMa.containsMouse ? page.accent : page.accentDim
                                                        opacity: changed ? 1 : 0.5
                                                        font.pixelSize: 12
                                                        font.underline: changed && inlineDefaultMa.containsMouse
                                                        MouseArea {
                                                            id: inlineDefaultMa
                                                            anchors.fill: parent; anchors.margins: -4
                                                            enabled: inlineDefaultLink.changed
                                                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                                            // Re-bind rather than write, for the same
                                                            // binding-preservation reasons as strDefaultMa.
                                                            onClicked: {
                                                                page.setv(modelData.key, modelData.default_value)
                                                                inlineStr.text = Qt.binding(function() { return page.val(modelData) })
                                                            }
                                                        }
                                                    }
                                                }
                                                Text { textFormat: Text.PlainText; visible: modelData.help !== ""; text: modelData.help; color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                            }
                                            // A pick of several: one checkbox per option,
                                            // each hugging its label (the tiles' nested
                                            // checkbox look).
                                            Row {
                                                id: kindsRow
                                                readonly property var field: modelData
                                                visible: modelData.type === "kinds"; Layout.alignment: Qt.AlignVCenter
                                                spacing: 6
                                                Repeater {
                                                    model: kindsRow.field.type === "kinds" ? kindsRow.field.options : []
                                                    delegate: Item {
                                                        id: kindBox
                                                        required property var modelData
                                                        objectName: "settingsKind_" + modelData.value
                                                        readonly property bool on: page.kindOn(kindsRow.field, modelData.value)
                                                        width: kindLine.implicitWidth; height: kindLine.implicitHeight
                                                        MouseArea {
                                                            anchors.fill: parent; anchors.margins: -4
                                                            cursorShape: Qt.PointingHandCursor
                                                            onClicked: page.kindToggle(kindsRow.field, kindBox.modelData.value)
                                                        }
                                                        SCheck {
                                                            id: kindLine
                                                            on: kindBox.on
                                                            label: kindBox.modelData.label
                                                        }
                                                    }
                                                }
                                            }
                                            SChoice {
                                                id: inlineChoice
                                                visible: modelData.type === "enum"; Layout.alignment: Qt.AlignVCenter
                                                options: modelData.type === "enum" ? modelData.options : []
                                                currentIndex: modelData.type === "enum" ? page.enumIndex(modelData.options, page.val(modelData)) : 0
                                                ladder: page.isLadder(modelData.key)
                                                onPicked: function(i) { page.setv(modelData.key, modelData.options[i].value) }
                                            }
                                            SText {
                                                id: inlineStr
                                                visible: modelData.type === "str" && modelData.inline === true
                                                Layout.alignment: Qt.AlignVCenter
                                                // The stepper's value-box scale, so the short-value
                                                // rows share one control vocabulary.
                                                Layout.preferredWidth: 64; Layout.preferredHeight: 32
                                                text: page.val(modelData)
                                                invalid: page.sanitizeDirty(modelData)
                                                Component.onCompleted: if (modelData.sanitize === true) page.sanitizeKeys[modelData.key] = modelData
                                                onEdited: function(t){ page.setv(modelData.key, t) }
                                            }
                                            SToggle {
                                                visible: modelData.rowToggle === true; Layout.alignment: Qt.AlignVCenter
                                                checked: modelData.rowToggle === true && page.val(modelData) === true
                                                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.setv(modelData.key, !(page.val(modelData) === true)) }
                                            }
                                            // A pair of steppers on one row (a delay range, a
                                            // batch and its pause), under an optional switch.
                                            Row {
                                                id: rangeRow
                                                visible: modelData.type === "range"; Layout.alignment: Qt.AlignVCenter
                                                spacing: 8
                                                readonly property bool on: modelData.type !== "range" || modelData.toggle !== true || page.val(modelData) === true
                                                SToggle {
                                                    visible: modelData.toggle === true; anchors.verticalCenter: parent.verticalCenter
                                                    checked: modelData.type === "range" && page.val(modelData) === true
                                                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.setv(modelData.key, !(page.val(modelData) === true)) }
                                                }
                                                Item { visible: modelData.toggle === true; width: 4; height: 1 }
                                                SStepper {
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    opacity: rangeRow.on ? 1 : 0.4; enabled: rangeRow.on
                                                    value: modelData.lo ? page.val(modelData.lo) : 0
                                                    minimum: modelData.lo && modelData.lo.minimum !== undefined ? modelData.lo.minimum : 0
                                                    maximum: modelData.lo && modelData.lo.maximum !== undefined ? modelData.lo.maximum : 9999
                                                    step: modelData.lo && modelData.lo.step !== undefined ? modelData.lo.step : 1
                                                    decimals: modelData.lo && modelData.lo.decimals !== undefined ? modelData.lo.decimals : 0
                                                    onChanged: function(v){ page.setv(modelData.lo.key, v) }
                                                }
                                                Text {
                                                    anchors.verticalCenter: parent.verticalCenter; opacity: rangeRow.on ? 1 : 0.4
                                                    textFormat: Text.PlainText; text: modelData.mid || ""; color: page.textLo; font.pixelSize: 12
                                                }
                                                SStepper {
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    opacity: rangeRow.on ? 1 : 0.4; enabled: rangeRow.on
                                                    value: modelData.hi ? page.val(modelData.hi) : 0
                                                    minimum: modelData.hi && modelData.hi.minimum !== undefined ? modelData.hi.minimum : 0
                                                    maximum: modelData.hi && modelData.hi.maximum !== undefined ? modelData.hi.maximum : 9999
                                                    step: modelData.hi && modelData.hi.step !== undefined ? modelData.hi.step : 1
                                                    decimals: modelData.hi && modelData.hi.decimals !== undefined ? modelData.hi.decimals : 0
                                                    onChanged: function(v){ page.setv(modelData.hi.key, v) }
                                                }
                                            }
                                            SStepper {
                                                visible: modelData.type === "int" || modelData.type === "float"; Layout.alignment: Qt.AlignVCenter
                                                value: (modelData.type === "int" || modelData.type === "float") ? page.val(modelData) : 0
                                                minimum: modelData.minimum !== undefined ? modelData.minimum : 1
                                                maximum: modelData.maximum !== undefined ? modelData.maximum : 9999
                                                step: modelData.step !== undefined ? modelData.step : 1
                                                decimals: modelData.decimals !== undefined ? modelData.decimals : 0
                                                onChanged: function(v){ page.setv(modelData.key, v) }
                                            }
                                        }

                                        // Cover sizes: the embedded-cover size (this enum) plus a
                                        // progressively-disclosed size for the saved cover.jpg, so a
                                        // second size is available with no extra settings row for
                                        // everyone who doesn't want it.
                                        Column {
                                            id: coverCol
                                            visible: modelData.type === "cover_sizes"
                                            width: parent.width; spacing: 10
                                            property bool expanded: page.val2(modelData) !== "follow"
                                            // Each size row stacks its choice under the words when
                                            // the choice would crowd them (as the rows above do).
                                            GridLayout {
                                                width: parent.width; columnSpacing: 14; rowSpacing: 10
                                                columns: width - coverChoice.implicitWidth - 14 < page.rowWordsMinW ? 1 : 2
                                                ColumnLayout {
                                                    Layout.fillWidth: true; spacing: 2
                                                    Text { textFormat: Text.PlainText; text: modelData.label; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                                    Text { textFormat: Text.PlainText; visible: modelData.help !== ""; text: modelData.help; color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                                }
                                                SChoice {
                                                    id: coverChoice
                                                    Layout.alignment: Qt.AlignVCenter
                                                    options: modelData.options ? modelData.options : []
                                                    currentIndex: page.enumIndex(modelData.options, page.val(modelData))
                                                    ladder: true
                                                    onPicked: function(i) { page.setv(modelData.key, modelData.options[i].value) }
                                                }
                                            }
                                            Text {
                                                textFormat: Text.PlainText
                                                text: (coverCol.expanded ? "▾  " : "▸  ") + "Separate cover.jpg size"
                                                color: page.accent; font.pixelSize: 12
                                                MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: coverCol.expanded = !coverCol.expanded }
                                            }
                                            GridLayout {
                                                visible: coverCol.expanded
                                                width: parent.width; columnSpacing: 14; rowSpacing: 10
                                                columns: width - coverFileChoice.implicitWidth - 14 < page.rowWordsMinW ? 1 : 2
                                                ColumnLayout {
                                                    Layout.fillWidth: true; spacing: 2
                                                    Text { textFormat: Text.PlainText; text: modelData.file_label ? modelData.file_label : "Separate cover.jpg size"; color: page.textHi; font.pixelSize: 13; font.weight: Font.Medium }
                                                    Text { text: "Size of the saved cover.jpg. \"Same as embedded\" matches the size above."; color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                                }
                                                SChoice {
                                                    id: coverFileChoice
                                                    Layout.alignment: Qt.AlignVCenter
                                                    options: modelData.file_options ? modelData.file_options : []
                                                    currentIndex: page.enumIndex(modelData.file_options, page.val2(modelData))
                                                    onPicked: function(i) { page.setv(modelData.file_key, modelData.file_options[i].value) }
                                                }
                                            }
                                        }

                                        // Music library: the ownership-badge scan's composite card. A
                                        // master switch (off by default) heads it and grays the rest out;
                                        // switch, source and folder all stage into editMap like every
                                        // other field, and SAVE CHANGES commits them together AND starts
                                        // the first scan (applySettings). Only Rescan acts immediately,
                                        // and only on the saved configuration, so nothing the card shows
                                        // takes effect before a save.
                                        //
                                        // LOADED, not merely hidden like the smaller composites above. This
                                        // row component is instantiated once per settings field, so a plain
                                        // "visible:" card was built ~26 times over: its progress bar sizes
                                        // its cell Repeater off a width that is non-zero while hidden, so
                                        // each invisible copy built a couple of hundred rectangles, and each
                                        // carried its own FolderDialog. That tripled the whole page's item
                                        // count, on the very page whose comment at the top of this file
                                        // explains that control-tree instantiation cost is what made
                                        // switching to Settings feel laggy.
                                        Loader {
                                            id: libraryLoader
                                            active: modelData.type === "library"
                                            visible: active
                                            width: parent.width
                                            sourceComponent: Column {
                                            id: libraryCol
                                            width: parent.width; spacing: 10
                                            // Staged-first reads: each control shows its editMap value when
                                            // one exists and the saved mirror otherwise, so CANCEL falls
                                            // back on its own when the editMap clears. (After a save the
                                            // editMap deliberately keeps its keys, so these also equal the
                                            // just-saved state then.)
                                            readonly property bool enabledE: page.editMap["library_enabled"] !== undefined ? page.editMap["library_enabled"] : page.libraryEnabledLive
                                            readonly property bool bulkE: page.editMap["library_bulk_skip"] !== undefined ? page.editMap["library_bulk_skip"] : page.libraryBulkSkipLive
                                            readonly property bool mbE: page.editMap["library_mb_arbiter"] !== undefined ? page.editMap["library_mb_arbiter"] : page.libraryMbArbiterLive
                                            // Not part of libDirty either: the lookup is not scan configuration.
                                            readonly property bool lookupE: page.editMap["library_mb_lookup"] !== undefined ? page.editMap["library_mb_lookup"] : page.libraryMbLookupLive
                                            // Not part of libDirty: following is not scan
                                            // configuration, so Rescan stays usable while it waits.
                                            readonly property bool followE: page.editMap["releases_auto_import"] !== undefined ? page.editMap["releases_auto_import"] : page.libraryFollowLive
                                            readonly property int minTracksE: page.editMap["releases_import_min_tracks"] !== undefined ? page.editMap["releases_import_min_tracks"] : page.libraryMinTracksLive
                                            readonly property string sourceE: page.editMap["library_source"] !== undefined ? page.editMap["library_source"] : page.librarySourceLive
                                            readonly property string folderE: page.editMap["library_folder"] !== undefined ? page.editMap["library_folder"] : page.libraryFolderLive
                                            readonly property bool separate: sourceE !== "download"
                                            // The folder the STAGED source would scan (drives the field and
                                            // the hint), and the saved-state twin that gates Rescan: a
                                            // staged edit has not taken effect, so rescanning acts only on
                                            // the saved configuration.
                                            readonly property string root: separate ? folderE : page.downloadFolderLive
                                            readonly property string savedRoot: page.librarySourceLive !== "download" ? page.libraryFolderLive : page.downloadFolderLive
                                            readonly property bool libDirty: enabledE !== page.libraryEnabledLive || bulkE !== page.libraryBulkSkipLive || mbE !== page.libraryMbArbiterLive || sourceE !== page.librarySourceLive || folderE !== page.libraryFolderLive
                                            readonly property bool scanning: page.libraryScanStatus === "scanning"
                                            readonly property bool scannable: page.libraryEnabledLive && !libDirty && libraryCol.savedRoot !== "" && !libraryCol.scanning

                                            // Header: the master switch at the left, the card's honest
                                            // "still new" mark as a chip at the right. The mark used to
                                            // be a three-line red paragraph ABOVE the switch, which read
                                            // as a crash warning and pushed the control it warned about
                                            // down the card. As the page's existing gold chip (the same
                                            // box the FFmpeg row wears) it admits the same thing in the
                                            // vocabulary the rest of the app already uses, and the
                                            // detail moves to the quiet line below. Gold, not red: red
                                            // here says broken, gold says be aware.
                                            //
                                            // The switch is the card's only always-active control: off
                                            // (the default) grays everything below it out and, once
                                            // saved, no folder resolves and nothing is ever scanned.
                                            // Deliberately NOT a bordered tile like the flag grid's:
                                            // those tile up in a multi-column flow and need their own
                                            // edges to read as separate cells, while this is the one
                                            // switch that governs the card that already frames it. It
                                            // hugs its content instead, and the click target is exactly
                                            // the switch and its label.
                                            Item {
                                                width: parent.width
                                                height: Math.max(libTglRow.height, 20)
                                                Row {
                                                    id: libTglRow
                                                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                                    spacing: 10
                                                    SToggle { anchors.verticalCenter: parent.verticalCenter; checked: libraryCol.enabledE }
                                                    Text {
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        text: "Scan a music library"
                                                        color: libTglMouse.containsMouse ? page.textHi : page.textLo
                                                        font.pixelSize: 14; font.weight: Font.Medium
                                                        Behavior on color { ColorAnimation { duration: 120 } }
                                                    }
                                                    // The off-state reassurance rides the same line rather
                                                    // than a second row: it is the one fact worth stating
                                                    // next to the switch, and only while it is off.
                                                    Text {
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        visible: !libraryCol.enabledE
                                                        text: "no folder is scanned and no badges show"
                                                        color: page.textDim; font.pixelSize: 12
                                                    }
                                                }
                                                MouseArea {
                                                    id: libTglMouse
                                                    // Sized to the switch + label, not the card: a full-width
                                                    // hit area would arm the pointer over empty space.
                                                    width: libTglRow.width; height: libTglRow.height
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                                    onClicked: page.setv("library_enabled", !libraryCol.enabledE)
                                                }
                                                Rectangle {
                                                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                                    radius: 5; color: page.goldCont; border.color: page.gold
                                                    implicitWidth: expTxt.implicitWidth + 14; implicitHeight: 19
                                                    width: implicitWidth; height: implicitHeight
                                                    Text {
                                                        id: expTxt; anchors.centerIn: parent
                                                        textFormat: Text.PlainText
                                                        text: "EXPERIMENTAL"
                                                        color: page.gold; font.family: page.mono; font.pixelSize: 10; font.bold: true
                                                    }
                                                }
                                            }

                                            // The card's own help, then what the chip is short for. The
                                            // reassurance is scoped to the files the user already has:
                                            // scanning only ever READS them (a test pins the scanner's
                                            // import closure away from the download engine), so the worst
                                            // a bug here can do is mislabel a badge. Deliberately not
                                            // phrased as "Waves never writes files", which would be a
                                            // promise the app cannot keep: downloading writes new files,
                                            // and replacing a copy is what a re-download with
                                            // skip-existing off is FOR.
                                            Text {
                                                textFormat: Text.PlainText
                                                visible: modelData.help !== ""; text: modelData.help; color: page.textDim; font.pixelSize: 12; width: parent.width; wrapMode: Text.WordWrap
                                                opacity: libraryCol.enabledE ? 1 : 0.4
                                                Behavior on opacity { NumberAnimation { duration: 160 } }
                                            }
                                            Text {
                                                textFormat: Text.PlainText
                                                text: "Matching is new, so a badge can still be wrong. A future version will support upgrading what you already have."
                                                color: page.textDim; font.pixelSize: 12; width: parent.width; wrapMode: Text.WordWrap
                                                opacity: libraryCol.enabledE ? 1 : 0.4
                                                Behavior on opacity { NumberAnimation { duration: 160 } }
                                            }

                                            // The card's folder picker; stages into editMap like the field.
                                            FolderDialog {
                                                id: libFolderDlg
                                                onAccepted: page.setv("library_folder", page.urlToPath(selectedFolder))
                                            }

                                            // One dense row: source toggle + (separate) folder field + Browse +
                                            // Rescan. In download mode the field and Browse drop out and a
                                            // spacer keeps Rescan hard right, so that mode is a single row
                                            // with no empty gap. Grayed and inert while the master switch is
                                            // (staged) off: `enabled: false` on the row disables every child
                                            // MouseArea and the field in one place. When the row is too
                                            // tight for a usable field (a wide font at the minimum window),
                                            // the field and Browse take a line of their own below.
                                            GridLayout {
                                                id: libFolderRow
                                                width: parent.width; columnSpacing: 8; rowSpacing: 8
                                                readonly property real buttonsW: segDl.implicitWidth + segSep.implicitWidth + brwTxt.implicitWidth + startTxt.implicitWidth + page.btnPadH * 8 + columnSpacing * 4
                                                readonly property bool roomy: width - buttonsW >= 200
                                                enabled: libraryCol.enabledE
                                                opacity: libraryCol.enabledE ? 1 : 0.4
                                                Behavior on opacity { NumberAnimation { duration: 160 } }
                                                // The picked choice is filled green. The other is an
                                                // outline with bright words, so it reads as one click
                                                // away, never as disabled (the gray fill and dim words
                                                // it had looked like the grayed RESCAN); pointing at it
                                                // turns it green.
                                                Rectangle {
                                                    // The page's button recipe (SAVE CHANGES, BROWSE on the
                                                    // other folder fields): caps, hugging the label.
                                                    implicitWidth: segDl.implicitWidth + page.btnPadH * 2; implicitHeight: segDl.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                                    color: !libraryCol.separate ? page.accentCont : "transparent"
                                                    border.color: !libraryCol.separate ? page.accent : (segDlMouse.containsMouse ? page.accentDim : page.outline)
                                                    Layout.row: 0; Layout.column: 0
                                                    Text { id: segDl; anchors.centerIn: parent; textFormat: Text.PlainText; text: "MY DOWNLOAD FOLDER"; color: !libraryCol.separate || segDlMouse.containsMouse ? page.accent : page.textHi; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                                    MouseArea { id: segDlMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: page.setv("library_source", "download") }
                                                }
                                                Rectangle {
                                                    implicitWidth: segSep.implicitWidth + page.btnPadH * 2; implicitHeight: segSep.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                                    color: libraryCol.separate ? page.accentCont : "transparent"
                                                    border.color: libraryCol.separate ? page.accent : (segSepMouse.containsMouse ? page.accentDim : page.outline)
                                                    Layout.row: 0; Layout.column: 1
                                                    Text { id: segSep; anchors.centerIn: parent; textFormat: Text.PlainText; text: "A SEPARATE FOLDER"; color: libraryCol.separate || segSepMouse.containsMouse ? page.accent : page.textHi; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                                    MouseArea { id: segSepMouse; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: page.setv("library_source", "separate") }
                                                }
                                                SText {
                                                    objectName: "libFolderField"
                                                    visible: libraryCol.separate
                                                    Layout.fillWidth: true
                                                    Layout.row: libFolderRow.roomy ? 0 : 1; Layout.column: libFolderRow.roomy ? 2 : 0
                                                    Layout.columnSpan: libFolderRow.roomy ? 1 : 3
                                                    text: libraryCol.folderE
                                                    onEdited: function(t){ page.setv("library_folder", t) }
                                                }
                                                Rectangle {
                                                    visible: libraryCol.separate
                                                    // Green so it reads as the next step; once a folder is
                                                    // chosen it calms down but stays green (still clickable).
                                                    implicitWidth: brwTxt.implicitWidth + page.btnPadH * 2; implicitHeight: brwTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                                    color: page.accentCont; border.color: page.accent
                                                    opacity: libraryCol.folderE !== "" ? 0.6 : 1
                                                    Behavior on opacity { NumberAnimation { duration: 160 } }
                                                    Layout.row: libFolderRow.roomy ? 0 : 1; Layout.column: 3
                                                    Text { id: brwTxt; anchors.centerIn: parent; textFormat: Text.PlainText; text: "BROWSE"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                                    MouseArea {
                                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                        onClicked: {
                                                            var du = page.pathUrl(libraryCol.folderE)
                                                            if (du !== "") libFolderDlg.currentFolder = du
                                                            libFolderDlg.open()
                                                        }
                                                    }
                                                }
                                                // Download mode has no field/Browse (and a tight row puts
                                                // them below): this spacer keeps Rescan pinned right
                                                // instead of leaving a gap.
                                                Item {
                                                    visible: !libraryCol.separate || !libFolderRow.roomy; Layout.fillWidth: true
                                                    Layout.row: 0; Layout.column: libFolderRow.roomy ? 4 : 2
                                                }
                                                Rectangle {
                                                    id: scanBtn
                                                    Layout.row: 0; Layout.column: libFolderRow.roomy ? 5 : 3
                                                    // Green while it can act and while a scan runs (a scan in
                                                    // progress is the button at work, not a button that is off);
                                                    // gray only while it waits on SAVE CHANGES or a folder.
                                                    readonly property bool lit: libraryCol.scannable || libraryCol.scanning
                                                    implicitWidth: startTxt.implicitWidth + page.btnPadH * 2; implicitHeight: startTxt.implicitHeight + page.btnPadV * 2; radius: page.btnRad
                                                    color: lit ? page.accentCont : page.surface2
                                                    border.color: lit ? page.accent : page.border1
                                                    opacity: lit ? 1 : 0.55
                                                    clip: true
                                                    // While scanning, a soft band of light crosses the button
                                                    // every 1.6 s (0.7 of it moving, then a rest). Only while
                                                    // Settings is on screen: a scan can run for most of an hour.
                                                    property real sweepT: 0
                                                    SequentialAnimation on sweepT {
                                                        running: libraryCol.scanning && page.active && scanBtn.visible
                                                        loops: Animation.Infinite
                                                        NumberAnimation { from: 0; to: 1; duration: 1120 }
                                                        PauseAnimation { duration: 480 }
                                                    }
                                                    Rectangle {
                                                        visible: libraryCol.scanning
                                                        width: scanBtn.width * 0.45; height: scanBtn.height * 2; rotation: 18
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        x: -width + (scanBtn.width + width * 2) * scanBtn.sweepT
                                                        gradient: Gradient {
                                                            orientation: Gradient.Horizontal
                                                            GradientStop { position: 0.0; color: "transparent" }
                                                            GradientStop { position: 0.5; color: Qt.rgba(0.24, 1, 0.43, 0.22) }
                                                            GradientStop { position: 1.0; color: "transparent" }
                                                        }
                                                    }
                                                    Text {
                                                        textFormat: Text.PlainText
                                                        id: startTxt; anchors.centerIn: parent
                                                        text: libraryCol.scanning ? "SCANNING" : "RESCAN"
                                                        color: scanBtn.lit ? page.accent : page.textLo; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                                    }
                                                    MouseArea {
                                                        anchors.fill: parent
                                                        enabled: libraryCol.scannable
                                                        visible: libraryCol.scannable
                                                        cursorShape: Qt.PointingHandCursor
                                                        // Acts on the SAVED configuration only (scannable gates
                                                        // on no staged library edits); the first scan of a new
                                                        // configuration belongs to SAVE CHANGES.
                                                        onClicked: waves.rescanLibrary()
                                                    }
                                                }
                                            }

                                            // Grayed-button hint while Rescan cannot act: staged edits wait
                                            // for SAVE CHANGES (which also starts their scan); otherwise a
                                            // folder is still missing. Hidden with the switch off, the gray
                                            // row already says the feature is off.
                                            Text {
                                                textFormat: Text.PlainText
                                                visible: libraryCol.enabledE && !libraryCol.scannable && !libraryCol.scanning
                                                width: parent.width; wrapMode: Text.WordWrap
                                                text: libraryCol.libDirty && libraryCol.root !== ""
                                                      ? "SAVE CHANGES applies this and starts the scan."
                                                      : (libraryCol.separate ? "Choose a folder, then SAVE CHANGES." : "Set a download folder in Downloads, then SAVE CHANGES.")
                                                // Near-white, not the dim gray the card's static help uses:
                                                // this line is the one telling you the card is waiting on
                                                // YOU, so it has to out-read the prose above it.
                                                color: page.textHi; font.pixelSize: 12
                                            }
                                            // Live scan state: progress while a (possibly minutes-long, NAS)
                                            // scan runs, or why a badge is blank (folder unreadable or offline).
                                            // Only visible while scanning or on an error, so no space is wasted.
                                            // Walk-phase note and the error explanations. Once the read
                                            // phase starts, the note moves into the bar's own header row
                                            // below (left of the bar, with the reading line at its right).
                                            Text {
                                                visible: (libraryCol.scanning && !page.libScanReading)
                                                         || page.libraryScanStatus === "unreadable"
                                                         || page.libraryScanStatus === "error"
                                                         || (page.libraryScanStatus === "missing" && libraryCol.root !== "")
                                                         || (page.libTruncationShown && libraryCol.root !== "")
                                                width: parent.width; wrapMode: Text.WordWrap
                                                textFormat: Text.PlainText
                                                font.pixelSize: 13; font.weight: Font.Medium
                                                color: page.gold
                                                text: libraryCol.scanning
                                                      ? page.libScanNote()
                                                      : page.libraryScanStatus === "unreadable"
                                                      ? (Qt.platform.os === "osx"
                                                         ? "Waves doesn't have permission to read this folder. It is likely on a network share or external drive that macOS protects. Allow access when macOS asks, or if you already declined, turn Waves on under System Settings, Privacy & Security, then reopen Settings."
                                                         : "Waves doesn't have permission to read this folder. Check the folder's permissions (and, on a network share, that the mount allows this user to read it), then reopen Settings.")
                                                      : page.libraryScanStatus === "missing"
                                                      ? "This folder isn't available right now. If it lives on an external drive or NAS, connect it and reopen Settings."
                                                      : page.libraryScanStatus === "error"
                                                      ? "The last scan did not finish. Try Rescan again; if it keeps failing, export diagnostics from the Diagnostics section."
                                                      : page.libTruncationNote()
                                            }
                                            // The download buttons' dot matrix in the download buttons'
                                            // clothes: the same green-filled, green-edged pill a running
                                            // download wears (see DownloadButton's running state), with
                                            // the matrix filling it and the percentage pinned right.
                                            // Not a button, just dressed like one. Centered with a width
                                            // cap so resizing the window (an ultrawide) widens the card,
                                            // not the bar; the notes above and below center with it.
                                            Column {
                                                visible: page.libScanReading
                                                anchors.horizontalCenter: parent.horizontalCenter
                                                width: Math.min(parent.width, 760)
                                                spacing: 6
                                                // Header row on the bar's own width: progress note at the
                                                // left edge, the album being read at the right edge.
                                                Item {
                                                    width: parent.width
                                                    height: scanNote.implicitHeight
                                                    Text {
                                                        id: scanNote
                                                        textFormat: Text.PlainText
                                                        anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                                                        font.pixelSize: 13; font.weight: Font.Medium
                                                        color: page.gold
                                                        text: page.libScanNote()
                                                    }
                                                    Text {
                                                        visible: (page.libraryScanProgress.artist || "") !== "" || (page.libraryScanProgress.album || "") !== ""
                                                        textFormat: Text.PlainText
                                                        anchors.left: scanNote.right; anchors.leftMargin: 16
                                                        anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                                        horizontalAlignment: Text.AlignRight
                                                        // Middle elide as the last resort: a too-long title
                                                        // keeps its artist AND its ending.
                                                        elide: Text.ElideMiddle; maximumLineCount: 1
                                                        font.pixelSize: 12; font.family: page.mono
                                                        color: page.textLo
                                                        text: (page.libraryScanProgress.artist || "?")
                                                              + ((page.libraryScanProgress.album || "") !== "" ? " · " + page.libraryScanProgress.album : "")
                                                    }
                                                }
                                                // The bar itself, in the running download button's clothes.
                                                Rectangle {
                                                    width: parent.width
                                                    height: 30; radius: page.btnRad
                                                    color: page.accentCont
                                                    border.width: 1; border.color: page.accentDim
                                                    Text {
                                                        id: scanDotsPct
                                                        textFormat: Text.PlainText
                                                        text: page.libScanReading
                                                              ? Math.floor(100 * page.libraryScanProgress.done / Math.max(1, page.libraryScanProgress.total)) + "%" : ""
                                                        color: page.accent; font.family: page.mono; font.pixelSize: 11; font.bold: true
                                                        anchors.right: parent.right; anchors.rightMargin: 12
                                                        anchors.verticalCenter: parent.verticalCenter
                                                    }
                                                    DotBar {
                                                        id: scanDots
                                                        anchors.left: parent.left; anchors.leftMargin: 12
                                                        anchors.right: scanDotsPct.left; anchors.rightMargin: 8
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        onColor: page.accent
                                                        pct: page.libScanReading
                                                             ? 100 * page.libraryScanProgress.done / Math.max(1, page.libraryScanProgress.total) : 0
                                                    }
                                                }
                                            }

                                            // The card's options, as the same bordered tiles the flag
                                            // grid uses everywhere else on this page (they were loose
                                            // switches stacked under the folder row, which read as
                                            // leftovers rather than as settings). Two across in an even
                                            // grid, all the same width: never one stretched over the row.
                                            // Left: the bulk claim gate, on by default, so a discography
                                            // or an album leaves out what the scan already claims.
                                            // Right: MusicBrainz arbitration, off by default, and its
                                            // help says exactly what leaves the machine when it is on.
                                            // Third: the MusicBrainz library lookup, off by default,
                                            // its help saying what leaves the machine when it is on.
                                            // Fourth: the Releases auto-follow, on by default, here
                                            // because this is where the scan it feeds on is set up.
                                            // All gray and go inert with the master switch, since
                                            // none means anything while no library is scanned, and
                                            // all stage into editMap and commit on SAVE CHANGES.
                                            Flow {
                                                id: libFlagFlow
                                                width: parent.width; spacing: 10
                                                // The most lines any tile's label wraps to (see FlagTile).
                                                readonly property int labelLines: {
                                                    var m = 1
                                                    for (var i = 0; i < children.length; i++)
                                                        if (children[i].labelLines !== undefined) m = Math.max(m, children[i].labelLines)
                                                    return m
                                                }
                                                readonly property int helpLines: {
                                                    var m = 1
                                                    for (var i = 0; i < children.length; i++)
                                                        if (children[i].helpNatural !== undefined) m = Math.max(m, children[i].helpNatural)
                                                    return m
                                                }
                                                enabled: libraryCol.enabledE
                                                opacity: libraryCol.enabledE ? 1 : 0.4
                                                Behavior on opacity { NumberAnimation { duration: 160 } }
                                                FlagTile {
                                                    width: (libFlagFlow.width - 10) / 2
                                                    helpLines: 3
                                                    rowLabelLines: libFlagFlow.labelLines
                                                    rowHelpLines: libFlagFlow.helpLines
                                                    keyName: "library_bulk_skip"
                                                    checked: libraryCol.bulkE
                                                    label: "Skip what you already have"
                                                    help: "Discographies, albums and playlists leave out the copies the scan matched."
                                                }
                                                FlagTile {
                                                    width: (libFlagFlow.width - 10) / 2
                                                    helpLines: 3
                                                    rowLabelLines: libFlagFlow.labelLines
                                                    rowHelpLines: libFlagFlow.helpLines
                                                    keyName: "library_mb_arbiter"
                                                    checked: libraryCol.mbE
                                                    label: "Ask MusicBrainz when unsure"
                                                    help: "Sends artist and album titles to musicbrainz.org for matches the scan cannot prove on its own."
                                                }
                                                FlagTile {
                                                    width: (libFlagFlow.width - 10) / 2
                                                    helpLines: 3
                                                    rowLabelLines: libFlagFlow.labelLines
                                                    rowHelpLines: libFlagFlow.helpLines
                                                    keyName: "library_mb_lookup"
                                                    checked: libraryCol.lookupE
                                                    label: "Look up your artists on MusicBrainz"
                                                    help: "Sends your library's artist names to musicbrainz.org and keeps their genres, tags and albums."
                                                }
                                                FlagTile {
                                                    width: (libFlagFlow.width - 10) / 2
                                                    helpLines: 3
                                                    rowLabelLines: libFlagFlow.labelLines
                                                    rowHelpLines: libFlagFlow.helpLines
                                                    keyName: "releases_auto_import"
                                                    checked: libraryCol.followE
                                                    label: "Follow your artists"
                                                    help: "Follows your library's artists in Releases as the scan finds them on TIDAL. Close names wait on the Releases tab."
                                                }
                                            }
                                            // The follow floor, under the tiles: the fewest tracks an
                                            // artist needs in the library before an import follows it,
                                            // so a stray single or a guest spot follows nobody. Grays
                                            // with the master switch like the tiles and stages into
                                            // editMap; not part of libDirty (no scan configuration).
                                            RowLayout {
                                                id: libFloorRow
                                                width: parent.width; spacing: 14
                                                enabled: libraryCol.enabledE
                                                opacity: libraryCol.enabledE ? 1 : 0.4
                                                Behavior on opacity { NumberAnimation { duration: 160 } }
                                                ColumnLayout {
                                                    Layout.fillWidth: true; spacing: 2
                                                    Text { textFormat: Text.PlainText; text: "Follow artists with at least " + libraryCol.minTracksE + (libraryCol.minTracksE === 1 ? " track" : " tracks"); color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                                    Text { textFormat: Text.PlainText; text: "Artists with fewer tracks in your library are left out of the import. You can still follow them by hand."; color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                                }
                                                SStepper {
                                                    objectName: "libFloorStepper"
                                                    Layout.alignment: Qt.AlignVCenter
                                                    value: libraryCol.minTracksE
                                                    minimum: 1; maximum: 999; step: 1; decimals: 0
                                                    onChanged: function(v){ page.setv("releases_import_min_tracks", v) }
                                                }
                                                Text { textFormat: Text.PlainText; Layout.alignment: Qt.AlignVCenter; text: "tracks"; color: page.textLo; font.pixelSize: 13 }
                                            }
                                            // Follows from earlier imports that the saved floor would
                                            // leave out today: offered for unfollowing in one go, so a
                                            // floor raised after a big import can still take effect.
                                            RowLayout {
                                                id: libFloorExcessRow
                                                visible: page.libraryFloorExcess > 0 && libraryCol.enabledE
                                                width: parent.width; spacing: 14
                                                Text {
                                                    Layout.fillWidth: true; textFormat: Text.PlainText; wrapMode: Text.WordWrap
                                                    text: page.libraryFloorExcess + (page.libraryFloorExcess === 1 ? " artist" : " artists")
                                                          + " followed from your library " + (page.libraryFloorExcess === 1 ? "has" : "have")
                                                          + " fewer than " + page.libraryMinTracksLive + " tracks."
                                                    color: page.textLo; font.pixelSize: 12
                                                }
                                                // The Browse… button's clothes: this page has no SpecBtn.
                                                Rectangle {
                                                    objectName: "libFloorUnfollow"
                                                    Layout.preferredHeight: 32; implicitWidth: libFloorTxt.width + 24; radius: 8
                                                    color: page.surface2; border.color: page.border1
                                                    Text {
                                                        id: libFloorTxt; anchors.centerIn: parent; textFormat: Text.PlainText
                                                        text: "UNFOLLOW " + (page.libraryFloorExcess === 1 ? "IT" : "THEM")
                                                        color: page.textLo; font.family: page.mono; font.pixelSize: 11; font.bold: true; font.letterSpacing: 1.2
                                                    }
                                                    MouseArea {
                                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                        onClicked: waves.unfollowUnderFloor()
                                                    }
                                                }
                                            }
                                            }
                                        }

                                        // String / path: label + help, then text field (+ browse) below
                                        Column {
                                            id: strCol
                                            visible: modelData.type === "str" && modelData.inline !== true
                                            width: parent.width; spacing: 6
                                            Row {
                                                width: parent.width; spacing: 10
                                                Text {
                                                    textFormat: Text.PlainText
                                                    id: strLabel
                                                    text: modelData.label; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium
                                                }
                                                // Puts just THIS field back to what a fresh install
                                                // ships with, so a mangled template doesn't cost you
                                                // every other setting. Always shown (otherwise nobody
                                                // discovers it), but inert and faded while the field
                                                // already matches: it reads as the field's status.
                                                Text {
                                                    id: strDefaultLink
                                                    readonly property bool changed: modelData.default_value !== undefined
                                                                                    && String(page.val(modelData)) !== String(modelData.default_value)
                                                    visible: modelData.default_value !== undefined
                                                    anchors.verticalCenter: strLabel.verticalCenter
                                                    textFormat: Text.PlainText
                                                    text: changed ? "Restore default" : "Default"
                                                    color: !changed ? page.textDim
                                                                    : strDefaultMa.containsMouse ? page.accent : page.accentDim
                                                    opacity: changed ? 1 : 0.5
                                                    font.pixelSize: 12
                                                    font.underline: changed && strDefaultMa.containsMouse
                                                    MouseArea {
                                                        id: strDefaultMa
                                                        anchors.fill: parent; anchors.margins: -4
                                                        enabled: strDefaultLink.changed
                                                        hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                                        // Set the edit map, then RE-BIND the box rather than
                                                        // writing its text. (Typing alone does NOT break a
                                                        // TextField's text binding on this Qt, verified with
                                                        // real key events; only an imperative JS write to
                                                        // `text` destroys it, permanently.) setv alone would
                                                        // not repaint a field the user had edited (val()
                                                        // reads the edit map only on re-evaluation), and a
                                                        // plain write would kill the binding while delegates
                                                        // stay alive across close/reopen (refreshSchema only
                                                        // runs after a save), so Restore default followed by
                                                        // Cancel could leave the box showing the default
                                                        // while the config still used the custom value.
                                                        // Qt.binding shows the default now and keeps the
                                                        // display live afterwards.
                                                        onClicked: {
                                                            page.setv(modelData.key, modelData.default_value)
                                                            strField.text = Qt.binding(function() { return page.val(modelData) })
                                                        }
                                                    }
                                                }
                                            }
                                            Text { textFormat: Text.PlainText; visible: modelData.help !== ""; text: modelData.help; color: page.textDim; font.pixelSize: 12; width: parent.width; wrapMode: Text.WordWrap }
                                            Row {
                                                width: parent.width; spacing: 8
                                                SText {
                                                    id: strField
                                                    width: modelData.browse ? parent.width - browseBtn.width - 8 : parent.width
                                                    text: page.val(modelData)
                                                    onEdited: function(t){ page.setv(modelData.key, t) }
                                                }
                                                Rectangle {
                                                    id: browseBtn
                                                    visible: modelData.browse !== ""
                                                    // SAVE CHANGES button vocabulary: accent-container fill,
                                                    // accentDim border, uppercase accent label. Always full
                                                    // strength: an earlier fade-once-set read as disabled,
                                                    // and re-picking the folder is a legitimate action at any
                                                    // time (it is also how macOS re-grants network-volume
                                                    // access), so the button must always look clickable.
                                                    width: browseTxt.width + page.btnPadH * 2; height: browseTxt.height + page.btnPadV * 2; radius: page.btnRad
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    color: page.accentCont
                                                    border.color: page.accentDim; border.width: 1
                                                    Text { id: browseTxt; anchors.centerIn: parent; text: "BROWSE"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                                                    MouseArea {
                                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                        onClicked: {
                                                            // Open the picker where the field already points: a dir
                                                            // field's value IS the folder, a file field opens beside
                                                            // the binary it names.
                                                            var v = page.val(modelData)
                                                            if (modelData.browse === "dir") {
                                                                folderDlg.targetKey = modelData.key
                                                                // A folder that is gone right now (an unmounted
                                                                // share) opens at its nearest existing ancestor
                                                                // instead of wherever the picker last was.
                                                                var live = waves.existingFolder(v)
                                                                var du = page.pathUrl(live !== "" ? live : v)
                                                                if (du !== "") folderDlg.currentFolder = du
                                                                folderDlg.open()
                                                            } else {
                                                                fileDlg.targetKey = modelData.key
                                                                var fu = page.dirUrlOf(v)
                                                                if (fu !== "") fileDlg.currentFolder = fu
                                                                fileDlg.open()
                                                            }
                                                        }
                                                    }
                                                }
                                            }
                                            // Path-template fields get a live example of the
                                            // final file location, re-resolved on every edit.
                                            PathPreviewLine {
                                                visible: modelData.key.indexOf("format_") === 0
                                                width: parent.width
                                                path: visible ? waves.previewPathTemplate(modelData.key.substring(7), String(page.val(modelData))) : ""
                                            }
                                        }
                                    }
                                }
                            }
                            Repeater {
                                model: card.modelData.id === "files"
                                     ? page.rowFields(card.modelData.fields).filter(function(f) { return f.key.indexOf("format_") === 0 })
                                     : page.rowFields(card.modelData.fields)
                                delegate: rowFieldComp
                            }

                            // Template-token reference, Files & Naming card
                            // only: a progressive-disclosure table of every
                            // {token}, grouped by category, each with a copy
                            // button and a sample value from the real formatter.
                            Column {
                                id: tokRef
                                visible: card.modelData.id === "files"
                                width: inner.width; spacing: 10
                                property bool expanded: false
                                property var groups: []
                                Text {
                                    textFormat: Text.PlainText
                                    text: (tokRef.expanded ? "▾  " : "▸  ") + "Want to know more about these paths and tags?"
                                    color: page.accent; font.pixelSize: 12
                                    MouseArea {
                                        anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            if (!tokRef.expanded && tokRef.groups.length === 0)
                                                tokRef.groups = waves.pathTemplateTokens()
                                            tokRef.expanded = !tokRef.expanded
                                        }
                                    }
                                }
                                Column {
                                    visible: tokRef.expanded
                                    width: parent.width; spacing: 0
                                    Repeater {
                                        model: tokRef.groups
                                        delegate: Column {
                                            id: tokGroup
                                            required property var modelData
                                            required property int index
                                            width: parent.width
                                            Item { visible: tokGroup.index > 0; width: 1; height: 10 }
                                            // Category band; the column labels ride on
                                            // every band so the columns stay labeled
                                            // wherever the user has scrolled.
                                            Rectangle {
                                                width: parent.width; height: 24; radius: 6
                                                color: page.surface3
                                                Rectangle { x: 8; width: 3; height: 11; radius: 1.5; anchors.verticalCenter: parent.verticalCenter; color: page.accent }
                                                Text {
                                                    x: 19; anchors.verticalCenter: parent.verticalCenter
                                                    text: tokGroup.modelData.group.toUpperCase()
                                                    textFormat: Text.PlainText
                                                    color: page.textHi; font.family: page.mono; font.pixelSize: 12; font.letterSpacing: 1
                                                }
                                                Text {
                                                    x: 256; anchors.verticalCenter: parent.verticalCenter
                                                    text: "WHAT IT IS"
                                                    color: page.textLo; font.family: page.mono; font.pixelSize: 10; font.letterSpacing: 1
                                                }
                                                Text {
                                                    anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                                                    text: "EXAMPLE"
                                                    color: page.textLo; font.family: page.mono; font.pixelSize: 10; font.letterSpacing: 1
                                                }
                                            }
                                            Repeater {
                                                model: tokGroup.modelData.tokens
                                                delegate: Item {
                                                    id: tokRow
                                                    required property var modelData
                                                    required property int index
                                                    property bool copied: false
                                                    width: parent.width; height: 24
                                                    Timer { id: tokCopiedTimer; interval: 1400; onTriggered: tokRow.copied = false }
                                                    // Hover wash so the row's three columns
                                                    // read as one line across the wide gap.
                                                    MouseArea {
                                                        id: tokRowMa
                                                        anchors.fill: parent
                                                        hoverEnabled: true
                                                        acceptedButtons: Qt.NoButton
                                                    }
                                                    Rectangle {
                                                        visible: tokRowMa.containsMouse || tokCopyMa.containsMouse
                                                        anchors.fill: parent; anchors.topMargin: 1
                                                        radius: 4; color: "#1b1f26"
                                                    }
                                                    Text {
                                                        id: tokName
                                                        x: 8; anchors.verticalCenter: parent.verticalCenter
                                                        width: Math.min(implicitWidth, 218)
                                                        text: tokRow.modelData.token
                                                        textFormat: Text.PlainText
                                                        color: page.accent; font.family: page.mono; font.pixelSize: 11; elide: Text.ElideRight
                                                    }
                                                    // Copy button beside the token: copies the
                                                    // {token} and flashes a confirmation tick.
                                                    Item {
                                                        x: tokName.x + tokName.width + 6; width: 16; height: 16
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        // Two offset outline squares: the classic
                                                        // copy glyph, no icon font needed.
                                                        Rectangle {
                                                            visible: !tokRow.copied
                                                            x: 4; y: 2; width: 9; height: 9; radius: 2
                                                            color: "transparent"; border.width: 1
                                                            border.color: tokCopyMa.containsMouse ? page.accent : page.textDim
                                                        }
                                                        Rectangle {
                                                            visible: !tokRow.copied
                                                            x: 1; y: 5; width: 9; height: 9; radius: 2
                                                            color: page.surface
                                                            border.width: 1
                                                            border.color: tokCopyMa.containsMouse ? page.accent : page.textDim
                                                        }
                                                        Text {
                                                            visible: tokRow.copied
                                                            anchors.centerIn: parent
                                                            text: "✓"; color: page.accent; font.family: page.mono; font.pixelSize: 12
                                                        }
                                                        MouseArea {
                                                            id: tokCopyMa
                                                            anchors.fill: parent; anchors.margins: -4
                                                            hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                                            onClicked: {
                                                                page.copyText(tokRow.modelData.token)
                                                                tokRow.copied = true
                                                                tokCopiedTimer.restart()
                                                            }
                                                        }
                                                    }
                                                    Text {
                                                        x: 256; width: parent.width - 256 - 200 - 16; anchors.verticalCenter: parent.verticalCenter
                                                        text: tokRow.modelData.desc
                                                        textFormat: Text.PlainText
                                                        color: page.textDim; font.pixelSize: 11; elide: Text.ElideRight
                                                    }
                                                    Text {
                                                        width: 200; anchors.right: parent.right; anchors.rightMargin: 8; anchors.verticalCenter: parent.verticalCenter
                                                        text: tokRow.modelData.sample
                                                        textFormat: Text.PlainText
                                                        horizontalAlignment: Text.AlignRight
                                                        color: page.textLo; font.family: page.mono; font.pixelSize: 11; elide: Text.ElideRight
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }
                            }

                            // The Files & Naming card's non-template value rows
                            // (padding, delimiters) continue under the reference.
                            Repeater {
                                model: card.modelData.id === "files"
                                     ? page.rowFields(card.modelData.fields).filter(function(f) { return f.key.indexOf("format_") !== 0 })
                                     : []
                                delegate: rowFieldComp
                            }

                            // Short-value fields three per line.
                            Flow {
                                visible: page.thirdFields(card.modelData.fields).length > 0
                                width: parent.width; spacing: 10
                                Repeater {
                                    model: page.thirdFields(card.modelData.fields)
                                    delegate: rowFieldComp
                                }
                            }

                            // The per-character stand-in table, directly under
                            // the general stand-in it overrides.
                            Repeater {
                                model: page.mapFields(card.modelData.fields)
                                delegate: charMapComp
                            }

                            // On/off switches as a 2-column tile grid (keeps the look).
                            Flow {
                                id: flagFlow
                                readonly property int flagCount: page.boolFields(card.modelData.fields).length
                                // Three across when they divide evenly that way (a
                                // trio of switches earns one line, not one and a
                                // half), otherwise the usual two.
                                readonly property int cols: flagCount % 3 === 0 ? 3 : 2
                                // The most lines any tile's label wraps to. Every tile
                                // makes room for it, so a two-line label adds height
                                // instead of taking a line from its help, and the tiles
                                // of a row keep one height.
                                readonly property int labelLines: {
                                    var m = 1
                                    for (var i = 0; i < flagRep.count; i++) {
                                        var t = flagRep.itemAt(i)
                                        if (t) m = Math.max(m, t.labelLines)
                                    }
                                    return m
                                }
                                // The most lines any tile's help takes (at most the
                                // tile's room), so a wide window's short helps get
                                // short tiles, every tile still one height.
                                readonly property int helpLines: {
                                    var m = 1
                                    for (var i = 0; i < flagRep.count; i++) {
                                        var t = flagRep.itemAt(i)
                                        if (t) m = Math.max(m, t.helpNatural)
                                    }
                                    return m
                                }
                                // A tile with a sub-option shows it in a strip along its
                                // bottom edge. Every tile keeps the strip's room whether
                                // it shows or not, so turning the parent on never grows
                                // the row, and the help keeps all its lines.
                                readonly property real childExtra: {
                                    for (var i = 0; i < flagRep.count; i++) {
                                        var t = flagRep.itemAt(i)
                                        if (t && t.hasChild) return page.tileStripH
                                    }
                                    return 0
                                }
                                visible: flagCount > 0
                                width: parent.width; spacing: 10
                                Repeater {
                                    id: flagRep
                                    model: page.boolFields(card.modelData.fields)
                                    delegate: Rectangle {
                                        id: flagTile
                                        required property var modelData
                                        required property int index
                                        // Flags that need FFmpeg are grayed and inert while it's missing.
                                        readonly property bool ffBlocked: modelData.requires_ffmpeg === true && page.ff.stateKey === "missing"
                                        readonly property bool depBlocked: modelData.requires_any !== undefined && !Object.keys(modelData.requires_any).some(function(k) {
                                            return (page.editMap[k] !== undefined ? page.editMap[k] : modelData.requires_any[k]) === true
                                        })
                                        readonly property bool blocked: ffBlocked || depBlocked
                                        // A tile may carry a sub-option (e.g. Save cover.jpg ->
                                        // "Also save for single tracks"): a strip that fades up into
                                        // the room along the tile's bottom edge while the parent flag
                                        // is on, so the tile never grows or shifts its neighbors.
                                        readonly property bool hasChild: modelData.child_key !== undefined
                                        // While a prerequisite is missing (ffmpeg, or a required flag)
                                        // the feature can't run, so the switch reads OFF (grayed)
                                        // regardless of the saved value; it animates back to the real
                                        // preference once the prerequisite arrives.
                                        readonly property bool on: page.val(modelData) === true && !blocked
                                        readonly property bool childOn: hasChild && on
                                        readonly property bool childVal: page.valChild(modelData) === true
                                        readonly property string hint: ffBlocked ? "Requires FFmpeg"
                                            : depBlocked ? (modelData.requires_hint !== undefined ? modelData.requires_hint : "Requires another option") : ""
                                        readonly property bool dirtyHere: page.fieldDirty(modelData)
                                        function flip() { page.setv(modelData.key, !(page.val(modelData) === true)) }
                                        function flipChild() { page.setv(modelData.child_key, !childVal) }
                                        // The strip's two clocks: `stripOpen` on the switch's,
                                        // `stripK` on the key's.
                                        property real stripOpen: childOn ? 1 : 0
                                        Behavior on stripOpen { NumberAnimation { duration: page.openMs; easing.type: Easing.OutCubic } }
                                        property real stripK: childVal ? 1 : 0
                                        Behavior on stripK { NumberAnimation { duration: page.keyMs; easing.type: Easing.OutCubic } }
                                        // Every tile the same width, never one stretched
                                        // over a row of its own: each section's switch
                                        // count divides into full rows (a test checks).
                                        width: (inner.width - flagFlow.spacing * (flagFlow.cols - 1)) / flagFlow.cols
                                        opacity: blocked ? 0.45 : 1
                                        // Ease the tile back to full strength when ffmpeg
                                        // arrives, in step with the switch animating on.
                                        Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                                        // Too narrow for words beside the switch (three across
                                        // on a small window): the switch moves up beside the
                                        // label and the help takes the tile's full width.
                                        readonly property bool compact: width < page.tileCompactW
                                        // A third of the row is narrower, so the same
                                        // help needs one more line to avoid eliding.
                                        readonly property int labelLines: flBody.titleLines
                                        readonly property int helpNatural: flBody.helpNatural
                                        readonly property int helpCap: flBody.helpCap
                                        // A third of the row is narrower, so the same help needs
                                        // one more line to avoid eliding. Compact tiles take one
                                        // more again (the words are narrower still), and a taller
                                        // head row for the switch beside the label.
                                        // Room for a nested key too (flagFlow.childExtra), so
                                        // showing it never grows the tile.
                                        height: page.tileHeight(flagFlow.cols === 3, compact, flagFlow.labelLines, flagFlow.helpLines) + flagFlow.childExtra
                                        radius: 10
                                        color: page.surface
                                        border.color: (tileMouse.containsMouse && !blocked) ? page.outline : page.surface
                                        Behavior on border.color { ColorAnimation { duration: 120 } }
                                        // An unsaved edit grows a green strip on the tile's left edge.
                                        Rectangle {
                                            x: 0; z: 2; width: 3; radius: 1.5; anchors.verticalCenter: parent.verticalCenter
                                            height: flagTile.dirtyHere ? parent.height - 28 : 0
                                            color: page.accent
                                            Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                                        }
                                        // Whole-tile click toggles the parent flag; the child key
                                        // sits above this and swallows its own clicks.
                                        MouseArea {
                                            id: tileMouse
                                            anchors.fill: parent; hoverEnabled: true
                                            enabled: !flagTile.blocked
                                            cursorShape: flagTile.blocked ? Qt.ArrowCursor : Qt.PointingHandCursor
                                            onClicked: flagTile.flip()
                                        }
                                        TileBody {
                                            id: flBody
                                            padL: flagTile.compact ? 14 : 16; padR: flagTile.compact ? 12 : 14
                                            title: flagTile.modelData.label; checked: flagTile.on
                                            compact: flagTile.compact
                                            help: flagTile.modelData.help
                                            helpShown: flagTile.modelData.help !== "" && !flagTile.blocked
                                            helpLines: page.tileHelpLines(flagFlow.cols === 3, flagTile.compact)
                                            Text { visible: flagTile.hint !== ""; textFormat: Text.PlainText; text: flagTile.hint; color: page.gold; font.pixelSize: 11; Layout.fillWidth: true }
                                        }
                                        // The sub-option strip, flush along the bottom edge inside
                                        // the tile's outline: it fades in and settles 8 px, the
                                        // card's shade with it, all on the switch's clock.
                                        Item {
                                            objectName: "tileStrip"
                                            visible: flagTile.hasChild && flagTile.stripOpen > 0.001
                                            x: 1; width: parent.width - 2
                                            y: parent.height - 1 - page.tileStripH; height: page.tileStripH
                                            opacity: flagTile.stripOpen
                                            Rectangle {
                                                id: stripFace
                                                width: parent.width; height: parent.height; radius: 9
                                                y: 8 * (1 - flagTile.stripOpen)
                                                color: page.mix(page.surface0, page.accentCont, flagTile.stripK)
                                                // Square top corners: the strip runs into the card.
                                                Rectangle { width: parent.width; height: 9; color: parent.color }
                                                Row {
                                                    x: flagTile.compact ? 13 : 15; spacing: 8
                                                    anchors.verticalCenter: parent.verticalCenter; anchors.verticalCenterOffset: 1
                                                    RoundCheck { anchors.verticalCenter: parent.verticalCenter; k: flagTile.stripK }
                                                    Text {
                                                        anchors.verticalCenter: parent.verticalCenter
                                                        width: stripFace.width - (flagTile.compact ? 26 : 30) - 21
                                                        textFormat: Text.PlainText; elide: Text.ElideRight
                                                        text: flagTile.modelData.child_label !== undefined ? flagTile.modelData.child_label : ""
                                                        color: page.mix(page.textLo, page.accentContTx, flagTile.stripK)
                                                        font.pixelSize: 12; font.weight: Font.Medium
                                                    }
                                                }
                                            }
                                            // The card's soft shade on the strip, right under its edge.
                                            Rectangle {
                                                width: parent.width; height: 8
                                                gradient: Gradient {
                                                    GradientStop { position: 0; color: "#80000000" }
                                                    GradientStop { position: 1; color: "#00000000" }
                                                }
                                            }
                                            MouseArea {
                                                anchors.fill: parent
                                                enabled: flagTile.childOn
                                                cursorShape: Qt.PointingHandCursor
                                                onClicked: flagTile.flipChild()
                                            }
                                        }
                                    }
                                }

                                // An odd tile count simply ends the grid mid-row. A
                                // decorative wave tile used to fill that slot; it read
                                // as a dead panel rather than finished surface, so the
                                // row now just stops.
                            }

                            // FLAC signature check, FFmpeg & FLAC section only.
                            Loader {
                                active: card.modelData.id === "processing"
                                visible: active
                                width: inner.width
                                height: (active && item) ? item.implicitHeight : 0
                                sourceComponent: flacCardComp
                                onItemChanged: if (item) page.flacCardItem = item
                            }

                            // Reset actions, Diagnostics section only, at the very
                            // bottom: put settings back to factory defaults, or
                            // wipe the whole app state. Both confirm first (the
                            // dialogs live in Main.qml, full-window overlays).
                            Loader {
                                active: card.modelData.id === "diagnostics"
                                visible: active
                                width: inner.width
                                height: (active && item) ? item.implicitHeight : 0
                                sourceComponent: resetRowComp
                            }
                        }
                    }
                }
            }

        }
    }
    }

    // Fading confirmation toast shown after Save.
    Rectangle {
        id: savedToast
        z: 100
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom; anchors.bottomMargin: 42
        radius: 10; color: page.surfaceHi; border.color: page.outline; border.width: 1
        implicitWidth: toastRow.width + 32; implicitHeight: 44
        opacity: page.savedFlash ? 1 : 0
        visible: opacity > 0
        Behavior on opacity { NumberAnimation { duration: 300 } }
        Row {
            id: toastRow; anchors.centerIn: parent; spacing: 8
            Ico { name: "check"; color: page.green; size: 16 }
            Text { text: "Changes saved"; color: page.green; font.pixelSize: 14; font.bold: true }
        }
    }

    // ── heart-gib overlay (easter egg), the Canvas the footer heart bursts
    // into. It covers the page, but every gib is boxed into the rail: they
    // fly out, bounce off the rail's edges, and most splat where they hit a
    // wall, creeping down it and dripping; the rest land on the rail's floor.
    // After a few seconds the mess fades away where it lies, and the credit
    // types itself back in with a whole heart. Transparent and
    // non-interactive (no MouseArea), so it never blocks the UI.
    Canvas {
        id: gibOverlay
        objectName: "settingsGibs"
        anchors.fill: parent
        z: 2000
        // Each gib: x, y, velocity, size, color, st (0 flying, 1 landed,
        // 2 stuck to a wall), a (alpha), wall (-1 left, 1 right), fade (its
        // delay once the clean-up starts).
        property var gibs: []
        property var drips: []
        property bool running: false
        // The box, the rail's viewport in this Canvas's coordinates, taken
        // when the heart bursts.
        property real bx0: 0
        property real by0: 0
        property real bx1: 0
        property real by1: 0
        property real bleedTime: 0     // seconds since the burst
        property real drainT: -1       // seconds since the clean-up began (-1: not yet)
        readonly property real settleAt: 3.5

        function explode(ox, oy, model, hpx) {
            var r = page.railScroller
            var o = r.mapToItem(gibOverlay, 0, 0)
            bx0 = o.x + 1; by0 = o.y + 1; bx1 = o.x + r.width - 1; by1 = o.y + r.height - 1
            var cs = HeartGib.cells(model, ox, oy, hpx)
            var hw = HeartGib.pxW(model, hpx), hh = HeartGib.pxH(model, hpx)
            var ccx = ox + hw / 2, ccy = oy + hh / 2
            var g = []
            for (var i = 0; i < cs.length; i++) {
                var pc = cs[i]; var dx = pc.x - ccx, dy = pc.y - ccy
                var d = Math.max(6, Math.sqrt(dx * dx + dy * dy)); var sp = 160 + Math.random() * 320
                g.push({ x: pc.x, y: pc.y, vx: dx / d * sp + (Math.random() - 0.5) * 220,
                         vy: dy / d * sp - (260 + Math.random() * 380),
                         size: hpx + Math.floor(Math.random() * 2), color: pc.color,
                         st: 0, a: 1, wall: 0, fade: Math.random() * 1.2 })
            }
            gibs = g; drips = []
            bleedTime = 0; drainT = -1; running = true
            requestPaint()
        }
        // Start the clean-up now (Settings closing, or the credit leaving view).
        function release() { if (running && drainT < 0) drainT = 0 }
        // The last of the mess is gone: hand the credit back.
        function finish() {
            gibs = []; drips = []; running = false; drainT = -1; requestPaint()
            footer.rearmCredit()
        }

        FrameAnimation { running: gibOverlay.running
            onTriggered: { gibOverlay.step(Math.min(frameTime, 0.032)); gibOverlay.requestPaint() } }

        function step(dt) {
            var grav = 2000, bounce = 0.45
            bleedTime += dt
            if (drainT >= 0) drainT += dt
            else if (bleedTime > settleAt) drainT = 0
            var live = []
            for (var i = 0; i < gibs.length; i++) {
                var p = gibs[i]
                if (p.st === 0) {                                // flying
                    p.vy += grav * dt; p.x += p.vx * dt; p.y += p.vy * dt
                    var hit = false
                    if (p.x < bx0) { p.x = bx0; p.vx = -p.vx * bounce; hit = true; p.wall = -1 }
                    else if (p.x + p.size > bx1) { p.x = bx1 - p.size; p.vx = -p.vx * bounce; hit = true; p.wall = 1 }
                    if (p.y < by0) { p.y = by0; p.vy = -p.vy * bounce; hit = true }
                    if (hit && Math.random() < 0.7) { p.st = 2; p.vx = 0; p.vy = 0 }      // splat on the wall
                    else if (p.y + p.size >= by1 && p.vy > 0) { p.y = by1 - p.size; p.st = 1; p.vx = 0; p.vy = 0 }
                } else if (p.st === 2 && drainT < 0) {          // stuck: creeps down, dripping
                    p.y = Math.min(by1 - p.size, p.y + 6 * dt)
                    if (Math.random() < dt * 0.6)
                        drips.push({ x: p.x + (p.wall < 0 ? 1 : 0), y: p.y + p.size, vy: 20, color: p.color })
                }
                // Clean-up: each settled gib fades (and sinks a little) after
                // its own short delay; one still flying lands first.
                if (drainT >= 0 && p.st !== 0 && drainT > p.fade) { p.a -= dt * 2.2; p.y += 40 * dt }
                if (p.a <= 0) continue
                live.push(p)
            }
            gibs = live
            var dl = []
            for (i = 0; i < drips.length; i++) {
                var dd = drips[i]; dd.vy += 300 * dt; dd.y += dd.vy * dt
                if (dd.y >= by1) continue
                dl.push(dd)
            }
            drips = dl
            // Done once everything is gone, and at the latest 6 s into the clean-up.
            if (drainT > 0.2 && gibs.length === 0 && drips.length === 0) finish()
            else if (drainT > 6) finish()
        }
        onPaint: {
            var ctx = getContext('2d'); ctx.clearRect(0, 0, width, height)
            if (!running) return
            ctx.save(); ctx.beginPath(); ctx.rect(bx0, by0, bx1 - bx0, by1 - by0); ctx.clip()
            for (var i = 0; i < gibs.length; i++) { var p = gibs[i]
                ctx.globalAlpha = Math.max(0, p.a)
                ctx.fillStyle = p.color; ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size, p.size) }
            ctx.globalAlpha = 1
            for (i = 0; i < drips.length; i++) { var d = drips[i]
                ctx.fillStyle = d.color; ctx.fillRect(Math.round(d.x), Math.round(d.y), 2, 4) }
            ctx.restore()
        }
    }
}
