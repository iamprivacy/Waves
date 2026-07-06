import QtQuick
import QtQuick.Shapes

// Expand/collapse indicator: a rounded-cap line chevron that smoothly rotates
// between a closed and an open angle. Self-contained and resizable so it can be
// reused anywhere something opens, settings sections, the download queue, and
// the dropdown menus. The motion is the Material "standard" curve, kept short
// and gentle. Colours default to the Waves accent but are overridable.
//
// Every use shares one motion so all chevrons in the app spin the same way:
// closed ▸ (-90°) → open ▾ (0°), a clockwise quarter-turn (the default angles).
//   • Sections: keep the soft tile that tints accent when open / washes on hover
//     (showTile: true).
//   • Dropdowns: set showTile: false so it reads as a bare caret; it still does
//     the same clockwise ▸→▾ turn while the popup is open.
Item {
    id: chev
    property bool open: false
    property bool hovered: false
    property real tile: 30
    property real glyph: 19
    property bool showTile: true       // soft background tile (off for bare carets)
    property real closedAngle: -90     // glyph rotation while closed
    property real openAngle: 0         // glyph rotation while open
    property color stroke: "#3dff6e"   // accent
    property color openBg: "#06210f"   // accent container (open tint)
    property color hoverBg: "#1d2128"  // surface3 (hover wash while collapsed)
    implicitWidth: tile; implicitHeight: tile

    Rectangle {
        visible: chev.showTile
        anchors.fill: parent; radius: parent.width * 0.3
        color: chev.open ? chev.openBg : (chev.hovered ? chev.hoverBg : "transparent")
        Behavior on color { ColorAnimation { duration: 220; easing.type: Easing.Bezier; easing.bezierCurve: [0.4, 0.0, 0.2, 1.0, 1.0, 1.0] } }
    }
    Shape {
        anchors.centerIn: parent
        width: 19; height: 19
        antialiasing: true
        scale: chev.glyph / 19
        rotation: chev.open ? chev.openAngle : chev.closedAngle
        Behavior on rotation {
            NumberAnimation { duration: 260; easing.type: Easing.Bezier; easing.bezierCurve: [0.4, 0.0, 0.2, 1.0, 1.0, 1.0] }
        }
        ShapePath {
            // Gate the stroke on effective visibility: a hidden QQuickShape's
            // scene-graph node can keep painting after its ancestors hide
            // (observed: the search tier's sort caret bleeding over Browse /
            // My Tidal after the tier collapsed). Item.visible reads as the
            // EFFECTIVE value in QML, so this reliably blanks the orphan node.
            strokeColor: chev.visible ? chev.stroke : "transparent"
            strokeWidth: 2.1; fillColor: "transparent"
            capStyle: ShapePath.RoundCap; joinStyle: ShapePath.RoundJoin
            startX: 4; startY: 7
            PathLine { x: 9.5; y: 12.5 }
            PathLine { x: 15; y: 7 }
        }
    }
}
