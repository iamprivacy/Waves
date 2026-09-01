import QtQuick

// The download buttons' rising dot matrix (Main.qml's inline DotMatrix), as a
// standalone file so surfaces outside Main.qml (the Settings library scan) can
// draw the exact same bar. Kept a copy rather than the shared definition for
// the same reason LedBar is: Main's inline component breathes off root's
// shared 20 Hz clocks (ledPulse, shimmerPhase) so hundreds of instances cost
// one timer, while a lone Settings bar can afford its own. Cells brighten
// column-major, bottom-up, and the next unlit cell pulses while work runs.
Item {
    id: dm
    property real pct: 0
    property int rows: 4
    property real dot: 3
    property real gap: 2
    property int maxCols: 0
    property bool pulse: true
    property color onColor: "#3dff6e"
    readonly property int cols: {
        var c = Math.max(1, Math.floor((width + gap) / (dot + gap)))
        return (maxCols > 0 && c > maxCols) ? maxCols : c
    }
    readonly property int total: rows * cols
    readonly property int litCount: Math.round(Math.max(0, Math.min(100, pct)) / 100 * total)
    implicitHeight: rows * dot + (rows - 1) * gap

    // Own 20 Hz breathe clock (see LedBar for why not a per-cell animation):
    // only ticks while the bar is visible and still filling.
    property real pulseLevel: 0.85
    Timer {
        running: dm.visible && dm.pulse && dm.pct < 100
        interval: 50; repeat: true
        property real phase: 0
        onTriggered: {
            phase = (phase + 0.05 / 1.04) % 1   // 1.04s breathe = 2 x 520ms
            dm.pulseLevel = 0.28 + 0.57 * (0.5 + 0.5 * Math.cos(2 * Math.PI * phase))
        }
    }

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
            // the single next block pulses while the work is in progress
            readonly property bool pulsing: dm.pulse && fillIndex === dm.litCount && dm.litCount < dm.total
            x: col * (dm.dot + dm.gap)
            y: rowTop * (dm.dot + dm.gap)
            width: dm.dot; height: dm.dot; radius: 0   // sharp LED cells
            color: dm.onColor
            opacity: pulsing ? dm.pulseLevel : (lit ? 1.0 : 0.16)
        }
    }
}
