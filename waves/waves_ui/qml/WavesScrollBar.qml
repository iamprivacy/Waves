import QtQuick
import QtQuick.Controls.Basic

// The app's vertical scrollbar (Scrollbar Lab pick G): an LED column of mono
// dot cells down a 12 px gutter, the thumb a run of lit cells. Dragged, a
// bubble beside the lit run names the section at the top of the view (a page
// that has sections binds `section`; one that has none leaves it empty and
// shows no bubble).
//
// Attach it as `ScrollBar.vertical: WavesScrollBar {}`. A list whose rows
// run to its own right edge (inset from the window or a card by a margin)
// sets `outside: true`: the bar then moves into that margin, its right edge
// `reach` px right of the list's, beside the rows instead of over them and
// out from under the window's edge fades. It moves into the list's parent,
// or into `beside` when that parent is a Layout (which would place it).
ScrollBar {
    id: gb
    objectName: "wavesScrollBar"

    property string section: ""
    // Sits in the margin right of the host list (see above).
    property bool outside: false
    property real reach: 22
    property Item beside: null

    // Waves palette (defaults mirror the app tokens; override if they diverge)
    property color accent:     "#3dff6e"
    property color accentSoft: "#9dffbe"
    property color surfaceHi:  "#22262e"
    property color outline:    "#3a3f49"
    property color textHi:     "#e6e8ec"
    property string mono:      monoFont

    readonly property int gutter: 12
    readonly property int fadeMs: 150

    // The list it scrolls, once it has moved out beside it.
    property Item host: null
    Component.onCompleted: {
        if (!gb.outside || !gb.parent) return
        // The attached bar is laid out only while its parent is the list, so
        // once moved it keeps these.
        var h = gb.parent, to = gb.beside || h.parent
        gb.host = h
        gb.parent = to
        // mapToItem is not notifiable: the leading terms are its dependencies.
        gb.x = Qt.binding(function () { return (to.width, h.x, h.width, h.mapToItem(to, 0, 0).x) + h.width + gb.reach - gb.width })
        gb.y = Qt.binding(function () { return (to.height, h.y, h.height, h.mapToItem(to, 0, 0).y) })
        gb.height = Qt.binding(function () { return h.height })
    }

    // Nothing to scroll, nothing to show (and a hidden page's bar builds no cells).
    visible: policy !== ScrollBar.AlwaysOff && size < 1.0 && (!outside || host === null || host.visible)

    implicitWidth: gutter
    padding: 0
    minimumSize: 0.04
    readonly property int pitch: 7
    readonly property int cells: Math.max(1, Math.floor(height / pitch))
    readonly property bool awake: gb.active || gb.hovered || gb.pressed
    background: Item {
        opacity: gb.awake ? 1 : 0.5
        Behavior on opacity { NumberAnimation { duration: gb.fadeMs } }
        Repeater {
            model: gb.visible ? gb.cells : 0
            delegate: Text {
                required property int index
                readonly property real mid: (index + 0.5) / gb.cells
                readonly property bool lit: mid >= gb.position && mid <= gb.position + gb.size
                x: gb.width - 3 - width / 2 - 1
                y: Math.round((index + 0.5) * gb.pitch - height / 2)
                textFormat: Text.PlainText
                text: "·"
                color: lit && gb.pressed ? gb.accentSoft : gb.accent
                font.family: gb.mono; font.pixelSize: 14; font.bold: true
                opacity: lit ? 1.0 : 0.16
            }
        }
    }
    contentItem: Item {
        implicitWidth: 6
        Rectangle {
            objectName: "sectionBubble"
            anchors.right: parent.left; anchors.rightMargin: 4
            anchors.verticalCenter: parent.verticalCenter
            width: gbText.implicitWidth + 16; height: gbText.implicitHeight + 8
            radius: 4
            color: gb.surfaceHi
            border.width: 1; border.color: gb.outline
            opacity: gb.pressed && gb.section !== "" ? 1 : 0
            visible: opacity > 0
            Behavior on opacity { NumberAnimation { duration: gb.fadeMs } }
            Text {
                id: gbText
                anchors.centerIn: parent
                textFormat: Text.PlainText
                text: gb.section
                color: gb.textHi; font.family: gb.mono; font.pixelSize: 11; font.bold: true
                font.letterSpacing: 0.6
            }
        }
    }
    // The bar's own cursor: MomentumGuard wears the cursor of the item
    // under the pointer, so the bar names one.
    MouseArea {
        anchors.fill: parent
        acceptedButtons: Qt.NoButton
        cursorShape: Qt.ArrowCursor
    }
}
