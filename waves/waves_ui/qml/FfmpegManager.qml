import QtQuick

// Headless FFmpeg state holder: a thin, reactive cache over the backend's
// FFmpeg manager (status + install lifecycle) plus the helper calls. Non-visual
// so it can be dropped in wherever the FFmpeg state is needed, the first-run
// setup step and (in future) the Settings page, without duplicating the
// signal wiring. `waves` is the app-wide backend context property.
QtObject {
    id: mgr

    property var status: ({})        // last waves.ffmpegStatus()
    property string lifeState: ""    // "" | downloading | verifying | installing | done | failed | cancelled
    property string message: ""      // last lifecycle message
    // What a "failed" lifeState failed at, as the failure line's lead-in: a
    // Remove that could not land is not an install failure.
    property string failPrefix: "Install failed: "
    property real pct: 0             // download/extract progress
    property bool updateAvailable: false
    property bool checking: false    // a user-initiated update check is in flight
    property bool upToDate: false    // transient "✓ up to date" after a check
    property bool checkFailed: false // transient "could not check" after a check that got no answer

    readonly property string stateKey: status.state ? status.state : "missing"
    readonly property bool busy: lifeState === "downloading" || lifeState === "verifying" || lifeState === "installing"
    readonly property bool ready: stateKey === "managed" || stateKey === "path"

    function refresh() { mgr.status = waves.ffmpegStatus() }
    function install() { waves.installFfmpeg() }
    function cancel() { waves.cancelFfmpeg() }
    function remove() { mgr.updateAvailable = false; waves.removeFfmpeg() }
    function checkUpdates() {
        if (mgr.busy || mgr.checking) return
        mgr.checking = true; mgr.upToDate = false; mgr.checkFailed = false; waves.checkFfmpegUpdate()
    }

    property Timer upToDateTimer: Timer { interval: 4000; onTriggered: mgr.upToDate = false }
    property Timer checkFailedTimer: Timer { interval: 4000; onTriggered: mgr.checkFailed = false }

    property Connections conn: Connections {
        target: waves
        function onFfmpegStateChanged(state, msg) {
            mgr.failPrefix = state === "remove_failed" ? "Remove failed: " : "Install failed: "
            if (state === "remove_failed") state = "failed"
            mgr.lifeState = state; mgr.message = msg
            if (state === "done" || state === "failed" || state === "cancelled") mgr.pct = 0
            // A completed install brings us to the latest build, so clear any
            // pending "Update" flag, otherwise the UI shows a perpetual update
            // and re-downloads the same build on the next check.
            if (state === "done") mgr.updateAvailable = false
        }
        function onFfmpegProgress(p) { mgr.pct = p }
        function onFfmpegStatusChanged() { mgr.refresh() }
        function onFfmpegUpdateChecked(available, current, latest) {
            mgr.checking = false; mgr.updateAvailable = available
            // A check that threw (offline, API error) arrives as
            // available=false with an EMPTY latest: "could not check",
            // never "up to date".
            if (available) return
            if (latest === "") { mgr.checkFailed = true; mgr.checkFailedTimer.restart() }
            else { mgr.upToDate = true; mgr.upToDateTimer.restart() }
        }
    }

    Component.onCompleted: refresh()
}
