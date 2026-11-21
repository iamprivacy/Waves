import QtQuick

// Buy Me a Coffee: opens the page in the browser. No box: a white ceramic
// mug with the coffee showing through an open rim, and the label in Buy Me a
// Coffee's own yellow. The mug steams while `steaming` is set, and the steam
// climbs higher and quicker while hovered. The mug's body sits on the middle
// of the label's capitals, not on the canvas middle (the steam headroom
// would push it low). Shared by the Settings sidebar foot and the launch
// note, so the link lives here and nowhere else.
Item {
    id: coffee
    property string uiFont: ""
    property real padV: 5
    // The host says when it is on screen; the steam clock stops otherwise.
    property bool steaming: true
    // The launch note asks in its own words; the Settings foot keeps this.
    property string label: "Buy me a coffee"
    signal opened()
    readonly property string url: "https://www.buymeacoffee.com/iamprivacy"
    readonly property color yellow:    "#ffdd00"
    readonly property color yellowHot: "#ffe94d"
    readonly property color ceramic:   "#f6f1e8"   // lip, outline and handle
    readonly property color glaze:     "#ece4d6"   // body
    readonly property color brew:      "#6b4024"
    readonly property color steam:     "#d9d4cc"
    readonly property real  ms: 1.1                // mug scale over its 22-unit drawing
    width: mug.width + 7 + coffeeTxt.implicitWidth
    height: Math.max(coffeeTxt.implicitHeight + coffee.padV * 2 + 2, 30)
    implicitWidth: width; implicitHeight: height
    // 0 at rest, 1 hovered; eases so the steam speeds up smoothly.
    property real h: coffeeMa.containsMouse ? 1 : 0
    Behavior on h { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    // Steam clock, in seconds.
    property real t: 0
    FrameAnimation {
        running: coffee.visible && coffee.steaming
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
        text: coffee.label
        color: Qt.tint(coffee.yellow, Qt.rgba(coffee.yellowHot.r, coffee.yellowHot.g, coffee.yellowHot.b, coffee.h))
        font.family: coffee.uiFont; font.pixelSize: 14; font.bold: true
    }
    MouseArea {
        id: coffeeMa
        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
        onClicked: { Qt.openUrlExternally(coffee.url); coffee.opened() }
    }
}
