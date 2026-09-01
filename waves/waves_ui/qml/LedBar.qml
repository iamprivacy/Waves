import QtQuick
import QtQuick.Effects

// LED dot-matrix progress pill, extracted from the Settings updater card so the
// FFmpeg surfaces share the exact same bar (Progress Lab verdict: fill past the
// corners, never show a gap). Cells brighten column-by-column, bottom-up, and
// the next unlit cell pulses while work is in flight.
Rectangle {
    id: bar

    property real pct: 0        // 0..100
    property string label: ""   // centered status text ("message · NN%")
    property string mono: ""    // monospace family for the label

    // Waves palette (defaults mirror the app tokens; override if they diverge)
    property color accent:     "#3dff6e"
    property color accentCont: "#06210f"
    property color accentDim:  "#22a64a"

    implicitHeight: 30; radius: 8
    color: accentCont; border.width: 1; border.color: accentDim
    clip: true

    // Internal 20 Hz "breathe" clock for the next-to-fill cell (same fix as the
    // app's other LED matrices): a per-frame SequentialAnimation per cell marks
    // the window dirty every vsync and, with the wave-loop video behind it,
    // recomposites the whole scene at the display refresh while a download runs.
    // Stepping one value at 20 Hz keeps the pulse identical for ~6x fewer
    // repaints. Only ticks while the bar is actually filling.
    property real pulse: 0.85
    Timer {
        running: bar.visible && bar.pct < 100
        interval: 50; repeat: true
        property real phase: 0
        onTriggered: {
            phase = (phase + 0.05 / 1.04) % 1   // 1.04s breathe = 2 x 520ms
            bar.pulse = 0.28 + 0.57 * (0.5 + 0.5 * Math.cos(2 * Math.PI * phase))
        }
    }

    Item {
        id: ledGrid
        anchors.fill: parent
        readonly property int grows: 6
        readonly property real ggap: 1.5
        readonly property real cellH: (height - (grows - 1) * ggap) / grows
        // Aim for the lab's ~25px cells but always overfill: the last column
        // bleeds off the right edge instead of leaving a gap.
        readonly property int gcols: Math.max(1, Math.ceil((width + ggap) / (25 + ggap)))
        readonly property real cellW: 25
        readonly property int total: gcols * grows
        readonly property int lit: Math.round(Math.max(0, Math.min(100, bar.pct)) / 100 * total)
        opacity: 0.5
        // Item clip is square; mask the grid to the pill's rounded shape so
        // edge-to-edge cells never poke past the corners.
        layer.enabled: true
        layer.effect: MultiEffect {
            maskEnabled: true
            maskSource: ShaderEffectSource { sourceItem: ledMask; hideSource: false }
        }
        Repeater {
            model: ledGrid.total
            delegate: Rectangle {
                required property int index
                readonly property int col: index % ledGrid.gcols
                readonly property int rowTop: Math.floor(index / ledGrid.gcols)
                // column-major, bottom-up, mirroring DotMatrix's rising fill
                readonly property int fillIndex: col * ledGrid.grows + (ledGrid.grows - 1 - rowTop)
                readonly property bool litCell: fillIndex < ledGrid.lit
                readonly property bool pulsing: fillIndex === ledGrid.lit && ledGrid.lit < ledGrid.total
                x: col * (ledGrid.cellW + ledGrid.ggap)
                y: rowTop * (ledGrid.cellH + ledGrid.ggap)
                width: ledGrid.cellW; height: ledGrid.cellH; radius: 0   // sharp LED cells
                color: bar.accent
                // Breathe off the bar's internal 20 Hz clock (bar.pulse) rather than
                // a per-frame animation, so an in-flight ffmpeg/update download does
                // not repaint the window every vsync. See bar.pulse.
                opacity: pulsing ? bar.pulse : (litCell ? 1.0 : 0.16)
            }
        }
    }
    Item {
        id: ledMask
        anchors.fill: parent
        visible: false
        Rectangle { anchors.fill: parent; radius: bar.radius - 1; color: "#ffffff" }
    }
    // Dark plate behind the label: the label is accent-on-accent once the
    // cells under it light up, so without a backing it washes out as the bar
    // fills. The plate hugs the text and rides above the masked grid.
    Rectangle {
        anchors.centerIn: parent
        width: ledLabel.implicitWidth + 14; height: ledLabel.implicitHeight + 4
        radius: 4; color: "#0a120c"; opacity: 0.78
        visible: bar.label !== ""
    }
    Text {
        id: ledLabel
        anchors.centerIn: parent
        textFormat: Text.PlainText
        text: bar.label
        color: bar.accent; font.family: bar.mono; font.pixelSize: 11; font.bold: true
        style: Text.Outline; styleColor: "#a0060f09"
    }
}
