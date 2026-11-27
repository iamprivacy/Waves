import QtQuick

// The progress pill for update, FFmpeg and Lavender downloads: the message on
// the left, a slim capsule on the right, and the percent riding just past the
// capsule's head. The track resumes after the number so it never runs behind
// it. Nothing animates: the bar only repaints when the percent moves. The
// capsule is laid out against the widest the words get ("100%"), so it never
// shifts sideways as the number grows. Too narrow for the words, it shows
// the percent alone.
Rectangle {
    id: bar

    property real pct: 0        // 0..100
    property string label: ""   // "message · NN%"; the part after the last " · " rides the capsule
    property string mono: ""    // monospace family for the words
    property real endInset: 0   // room kept clear at the right end (a site's own button)

    // Waves palette (defaults mirror the app tokens; override if they diverge)
    property color accent:     "#3dff6e"
    property color accentCont: "#06210f"
    property color accentDim:  "#22a64a"

    implicitHeight: 30; radius: 8
    color: accentCont; border.width: 1; border.color: accentDim
    clip: true

    readonly property real p: Math.max(0, Math.min(100, pct)) / 100
    readonly property int cut: label.lastIndexOf(" · ")
    readonly property string msg: cut < 0 ? label : label.slice(0, cut)
    readonly property string num: cut < 0 ? "" : label.slice(cut + 3)
    readonly property color well: Qt.alpha(accent, 0.14)
    readonly property color lit: Qt.tint(accentCont, Qt.alpha(accent, 0.8))

    TextMetrics { id: tmMsg; font: msgTxt.font; text: bar.msg }
    TextMetrics { id: tmNum; font: numTxt.font; text: "100%" }
    // Too narrow for the words and a readable capsule: the percent alone.
    readonly property bool showMsg: msg !== "" && width - endInset - 20 - (tmMsg.advanceWidth + 12) - tmNum.advanceWidth - 8 >= 32
    readonly property real rx: showMsg ? Math.round(10 + tmMsg.advanceWidth + 12) : 10
    readonly property real rw: width - endInset - 10 - rx

    Text {
        id: msgTxt
        visible: bar.showMsg
        x: 10; anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText; text: bar.msg
        color: bar.accent; font.family: bar.mono; font.pixelSize: 11; font.bold: true
    }
    Item {
        id: cap
        x: bar.rx; width: bar.rw - tmNum.advanceWidth - 8; height: 8
        anchors.verticalCenter: parent.verticalCenter
        Rectangle {
            id: capFill
            width: Math.max(cap.height, Math.round(cap.width * bar.p)); height: cap.height
            radius: cap.height / 2; color: bar.lit
        }
        Rectangle {
            visible: width > 0
            x: capFill.width + tmNum.advanceWidth + 16
            width: bar.rw - x; height: cap.height; radius: cap.height / 2
            color: bar.well
        }
    }
    Text {
        id: numTxt
        x: cap.x + capFill.width + 8; anchors.verticalCenter: parent.verticalCenter
        textFormat: Text.PlainText; text: bar.num
        color: bar.accent; font.family: bar.mono; font.pixelSize: 10; font.bold: true
    }
}
