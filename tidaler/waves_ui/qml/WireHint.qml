import QtQuick

// The loading hint: what a page shows while its payload is still on the wire.
// The Browse landing, a drilled Browse page and a fresh search all render this
// ONE component over the ambient water (the panes behind it are transparent on
// purpose, so the living water stays in view and the finished page then fades
// in over it), so a change here changes every loading state at once.
//
// The words hold still and the motion moves underneath: a swell travels along
// a row of LED cells, bright at its head with a long wake behind it, in the
// cell language the download buttons and queue rows already speak (DotBar:
// sharp cells, unlit ones left at a low glow rather than removed). A depth
// sounder's ping, which is what a page fetch is.
//
// The call sites set `active` (their own loading flag), `width` (their column),
// `tint` (root.textLo) and `onScreen` (root.onScreen); a drilled page lowers
// `topPad` under its skeleton header. Everything about the look lives here.
//
// TRANSITION. `height` follows `active` with no animation, so the finished page
// takes the hint's space the instant it is ready, while the visual itself fades
// out over the arriving content (this Item does not clip, so the fading hint
// draws past its own collapsed box). In, out and the page's own reveal are one
// cross-fade instead of a cut.
//
// MOTION BUDGET. The swell steps off ONE 20 Hz timer (`tick`) instead of a
// per-frame animation. With the wave-loop video behind it, a per-frame
// animation repaints the whole scene at the display's refresh, which is the
// trap the WaveMark logo and the LED matrices already hit (see Main.qml's
// shared ledPulse/marchTick clock). `onScreen` parks the clock when nobody can
// see it, the same gate the decorative clocks use.
Item {
    id: hint

    property bool active: false
    property color tint: "#a8acb4"        // root.textLo
    property real topPad: 96
    property bool onScreen: true
    // The palette default mirrors the app token, like ExpandChevron and LedBar
    // do, so the component stands alone in a lab without a root to read.
    property color accent: "#3dff6e"

    // The copy, in one place.
    readonly property string phrase: "Reading the wire…"

    implicitHeight: topPad + body.height
    height: active ? implicitHeight : 0
    visible: shown > 0

    // The cross-fade. Slightly slower in than out, so the hint arrives calmly
    // and gets out of the finished page's way promptly.
    property real shown: active ? 1 : 0
    Behavior on shown {
        NumberAnimation { duration: hint.active ? 260 : 190; easing.type: Easing.InOutSine }
    }

    // The stepped clock the swell derives its motion from. Restarted whenever
    // the hint appears, so each load begins at the top of the animation rather
    // than wherever the last one left off.
    property int tick: 0
    onActiveChanged: if (active) tick = 0
    Timer {
        running: hint.visible && hint.onScreen
        interval: 50; repeat: true
        onTriggered: hint.tick = (hint.tick + 1) % 100000
    }

    Column {
        id: body
        y: hint.topPad
        anchors.left: parent.left; anchors.right: parent.right
        opacity: hint.shown
        spacing: 20

        Text {
            width: parent.width; horizontalAlignment: Text.AlignHCenter
            textFormat: Text.PlainText; text: hint.phrase
            color: hint.tint; font.pixelSize: 22; opacity: 0.88
        }

        Row {
            id: strip
            anchors.horizontalCenter: parent.horizontalCenter
            spacing: 3
            readonly property int cells: 30
            // One pass every ~3.9s, entering left and leaving right (the head
            // starts and ends off the strip, so the swell arrives and departs
            // instead of blinking into existence at cell 0).
            readonly property real head: {
                var span = cells + 14
                return (hint.tick % 78) / 78 * span - 7
            }
            Repeater {
                model: strip.cells
                delegate: Rectangle {
                    required property int index
                    readonly property real d: index - strip.head
                    // Sharp face, long wake: ahead of the head the light falls
                    // off fast, behind it a wake decays slowly.
                    readonly property real lit: d > 0 ? Math.exp(-(d * d) / 2.4)
                                                      : Math.exp(-(d * d) / 30.0)
                    // A shade larger than the download bars' 3px cells: this
                    // one carries a page, not a button.
                    width: 4; height: 4; radius: 0   // sharp LED cells
                    color: hint.accent
                    opacity: 0.14 + 0.86 * lit
                }
            }
        }
    }
}
