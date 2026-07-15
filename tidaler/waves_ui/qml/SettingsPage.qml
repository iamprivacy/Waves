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
    signal closed()
    // Advanced-section reset actions. The page only asks; Main.qml shows the
    // confirmation dialog and calls the backend, then (for the settings
    // reset) tells the page to re-read the freshly-defaulted schema.
    signal resetSettingsRequested()
    signal factoryResetRequested()
    function externalReset() { editMap = ({}); dirty = false; refreshSchema() }

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
    readonly property real   btnTrack: 0
    readonly property int    btnRad:   8
    readonly property int    btnPadH:  12
    readonly property int    btnPadV:  7

    property var groups: []
    property var editMap: ({})
    property bool dirty: false
    property bool savedFlash: false
    Timer { id: flashTimer; interval: 2200; onTriggered: page.savedFlash = false }
    Timer { id: auUpToDateTimer; interval: 4000; onTriggered: page.auUpToDate = false }
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
    // Latches true once FFmpeg has been seen missing while the page is open, so
    // the Processing section's auto-open survives a successful install (which
    // flips the live state missing → managed). Synced on open and on any change.
    property bool ffEverMissing: false
    function ffSyncEverMissing() { if (page.ff && page.ff.stateKey === "missing") page.ffEverMissing = true }
    Connections { target: page.ff; function onStateKeyChanged() { page.ffSyncEverMissing() } }

    // ---- In-app updater state ----
    property var appUp: ({})             // last waves.appUpdateStatus()
    property string auState: ""          // install lifecycle: "" | downloading | done | failed | cancelled
    property string auMsg: ""
    property real auPct: 0
    property bool auUpdate: false        // a newer release is available
    property string auLatest: ""         // latest version tag when available
    property bool auChecking: false      // a user-initiated check is in flight
    property bool auUpToDate: false      // transient "✓ up to date" after a check
    readonly property bool auBusy: auState === "downloading" || auState === "verifying" || auState === "installing"
    readonly property bool auDone: auState === "done"
    function auRefresh() { page.appUp = waves.appUpdateStatus() }

    // ---- Diagnostics export state ----
    property bool diagBusy: false        // an export is being written
    property string diagPath: ""         // last exported bundle path ("" = none yet)
    property bool diagFailed: false
    function auCheck() {
        if (page.auBusy || page.auChecking) return
        page.auChecking = true; page.auUpToDate = false; waves.checkAppUpdate(true)
    }

    // Deep-link: land the view on a section card, e.g. arriving from the
    // status bar's update notice. Deliberately a jump, not an animated
    // scroll: the user should drop in where they need to be, not watch the
    // page fly past. The card itself is already expanded by its own open
    // rule (the Updates section auto-opens while an update is available).
    function jumpToCard(cardId) {
        for (var i = 0; i < secRep.count; i++) {
            var it = secRep.itemAt(i)
            // Match a special card key (ffmpeg/updates) or a plain section id
            // (e.g. "downloads"), so the folder gate can deep-link a regular
            // section the same way the update notice targets its card.
            if (it && (it.modelData.card === cardId || it.modelData.id === cardId)) {
                settingsFlick.contentY = Math.max(0, Math.min(it.y, settingsFlick.contentHeight - settingsFlick.height))
                return
            }
        }
    }

    function val(f) { return editMap[f.key] !== undefined ? editMap[f.key] : f.value }
    // Secondary value for a composite field (the cover_sizes "separate file"
    // dropdown), read/written under its own file_key.
    function val2(f) { return editMap[f.file_key] !== undefined ? editMap[f.file_key] : f.file_value }
    // Child toggle value for the cover_scope composite, under its own child_key.
    function valChild(f) { return editMap[f.child_key] !== undefined ? editMap[f.child_key] : f.child_value }
    function setv(key, v) { var e = Object.assign({}, editMap); e[key] = v; editMap = e; dirty = true }
    // Within a section, on/off switches render as a tile grid and everything
    // else as labelled rows.
    function boolFields(fields) { return fields.filter(function(f){ return f.type === "bool" && f.embedded !== true }) }
    function rowFields(fields)  { return fields.filter(function(f){ return f.type !== "bool" && f.embedded !== true }) }
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
        return s
    }
    // Inverse of urlToPath for a path's parent directory: file URL for the
    // folder containing `p`, or "" when there is no usable directory. Lets a
    // Browse… dialog open right where the current value already points.
    function pathUrl(p) {
        var s = String(p || "").trim()
        if (s === "") return ""
        // Windows drive-letter paths need the extra slash (file:///C:/…).
        return (/^[A-Za-z]:/.test(s) ? "file:///" : "file://") + s
    }
    function dirUrlOf(p) {
        var s = String(p || "").trim()
        var cut = Math.max(s.lastIndexOf("/"), s.lastIndexOf("\\"))
        return cut > 0 ? pathUrl(s.substring(0, cut)) : ""
    }
    function refreshSchema() { groups = waves.settingsSchema(); needsRefresh = false }

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
        case "updates":     return "M8 3V12.4 M4.6 6.4L8 3L11.4 6.4"
        // Pulse/heartbeat trace: diagnostics watch the app's vitals.
        case "diagnostics": return "M2.6 8H5.4L6.8 4.6L9.2 11.4L10.6 8H13.4"
        default:            return "M3 5.4H13 M3 10.6H13 M5.4 5.4A1.5 1.5 0 1 0 8.4 5.4A1.5 1.5 0 1 0 5.4 5.4Z M7.6 10.6A1.5 1.5 0 1 0 10.6 10.6A1.5 1.5 0 1 0 7.6 10.6Z"
        }
    }

    // Front-load the build at startup (behind the login overlay) so every
    // open of the page is instant. The startup update check is opt-in and
    // throttled in the backend, it no-ops unless the user enabled it.
    Component.onCompleted: { refreshSchema(); auRefresh(); waves.startupUpdateCheck(); waves.startupFfmpegUpdateCheck() }

    onActiveChanged: {
        if (active) {
            editMap = ({})
            dirty = false
            if (needsRefresh) refreshSchema()
            // Refresh local status only (no network). Update checks are
            // user-initiated via the "Check for updates" button.
            if (page.ff) page.ff.refresh()
            ffSyncEverMissing()
            auRefresh()
        }
    }

    // In-app updater signals from the backend. (FFmpeg signals are handled by
    // the shared FfmpegManager, `page.ff`.)
    Connections {
        target: waves
        function onAppUpdateStateChanged(state, msg) {
            page.auState = state; page.auMsg = msg
            if (state === "done" || state === "failed" || state === "cancelled") page.auPct = 0
        }
        function onAppUpdateProgress(pct) { page.auPct = pct }
        function onAppUpdateStatusChanged() { page.auRefresh() }
        function onAppUpdateChecked(available, current, latest) {
            var wasManual = page.auChecking
            page.auChecking = false
            page.auUpdate = available
            page.auLatest = latest
            // Only flash "up to date" for a user-initiated check, never the
            // silent startup one.
            if (!available && wasManual) { page.auUpToDate = true; auUpToDateTimer.restart() }
        }
        function onDiagnosticsExported(path) {
            page.diagBusy = false
            page.diagPath = path
            page.diagFailed = (path === "")
        }
    }

    // ---- Controls -------------------------------------------------------
    // Pure visual, the whole flag tile is the click target (see below).
    // Material pill switch (track + sliding knob). Pure-visual; the enclosing
    // flag tile's MouseArea drives `checked` via page.val/page.setv.
    component SToggle: Rectangle {
        property bool checked: false
        width: 46; height: 26; radius: 13
        color: checked ? page.accentCont : page.surface3
        border.color: checked ? page.accent : page.outline; border.width: 2
        // Animate the track too, so when ffmpeg lands and a gated toggle flips
        // on, the whole pill (track + border + knob) eases on rather than snaps.
        Behavior on color { ColorAnimation { duration: 160 } }
        Behavior on border.color { ColorAnimation { duration: 160 } }
        Rectangle {
            width: parent.checked ? 16 : 12; height: width; radius: width / 2
            anchors.verticalCenter: parent.verticalCenter
            // Target the ON knob's FINAL width (16), not the live `width`. Otherwise
            // the slide-on chases the simultaneously-animating width and lags; the
            // slide-off already targeted a constant (5), which is why it stayed smooth.
            x: parent.checked ? parent.width - 16 - 4 : 5
            color: parent.checked ? page.accent : page.textDim
            Behavior on x { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
            Behavior on width { NumberAnimation { duration: 140 } }
            Behavior on color { ColorAnimation { duration: 160 } }
        }
    }

    // Segmented cadence control with a spring-slid thumb; shared by the
    // Updates and FFmpeg auto-check tiles (AutoCheckTile below) so the two
    // controls stay identical. `field` is the embedded enum descriptor.
    component CadenceSeg: Rectangle {
        id: seg
        property var field: null
        readonly property var opts: field ? field.options : []
        readonly property string cad: field ? String(page.val(field)) : "daily"
        readonly property bool onSecond: opts.length > 1 && cad === opts[1].value
        readonly property real pad: 2
        readonly property real seg1W: segT1.implicitWidth + 16
        readonly property real seg2W: segT2.implicitWidth + 16
        radius: 6; implicitHeight: 26
        implicitWidth: seg1W + seg2W + pad * 2
        color: page.surface3; border.color: page.outline
        Rectangle {
            y: seg.pad
            height: parent.height - seg.pad * 2
            radius: 5
            color: page.accentCont; border.color: page.accentDim
            x: seg.onSecond ? seg.pad + seg.seg1W : seg.pad
            width: seg.onSecond ? seg.seg2W : seg.seg1W
            Behavior on x { NumberAnimation { duration: 260; easing.type: Easing.OutBack; easing.overshoot: 1.6 } }
            Behavior on width { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } }
        }
        RowLayout {
            anchors.fill: parent; anchors.margins: seg.pad; spacing: 0
            Item {
                Layout.fillHeight: true; implicitWidth: seg.seg1W
                Text {
                    id: segT1; anchors.centerIn: parent
                    text: seg.opts.length > 0 ? String(seg.opts[0].label).toUpperCase() : ""
                    font.family: page.uiFont; font.bold: true; font.pixelSize: 10
                    color: !seg.onSecond ? page.accent : page.textLo
                    Behavior on color { ColorAnimation { duration: 160 } }
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: if (seg.field && seg.opts.length > 0) page.setv(seg.field.key, seg.opts[0].value)
                }
            }
            Item {
                Layout.fillHeight: true; implicitWidth: seg.seg2W
                Text {
                    id: segT2; anchors.centerIn: parent
                    text: seg.opts.length > 1 ? String(seg.opts[1].label).toUpperCase() : ""
                    font.family: page.uiFont; font.bold: true; font.pixelSize: 10
                    color: seg.onSecond ? page.accent : page.textLo
                    Behavior on color { ColorAnimation { duration: 160 } }
                }
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: if (seg.field && seg.opts.length > 1) page.setv(seg.field.key, seg.opts[1].value)
                }
            }
        }
    }

    // Right-hand auto-check tile (pill toggle + cadence segment), shared by
    // the Updates and FFmpeg cards so the toggle and segment are identical by
    // construction. `autoField`/`cadField` are the card's embedded schema
    // descriptors; background/border colors are overridable at the use site.
    component AutoCheckTile: Rectangle {
        id: act
        property var autoField: null
        property var cadField: null
        readonly property bool autoOn: autoField ? page.val(autoField) === true : false
        // For the host card's implicitHeight formula (the tile itself is
        // anchored top/bottom, so its own implicitHeight is not used).
        readonly property real rowImplicitHeight: actRow.implicitHeight
        radius: 10; color: page.surface; border.color: page.border1
        RowLayout {
            id: actRow
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 13
            SToggle {
                Layout.alignment: Qt.AlignVCenter
                checked: act.autoOn
                MouseArea {
                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                    onClicked: if (act.autoField) page.setv(act.autoField.key, !act.autoOn)
                }
            }
            ColumnLayout {
                Layout.fillWidth: true; spacing: 3
                Text { text: act.autoField ? act.autoField.label : ""; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true }
                Text {
                    text: "Notifies only; nothing downloads until you click Update. The check sends none of your data."
                    color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                    wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true
                }
            }
            ColumnLayout {
                Layout.alignment: Qt.AlignVCenter
                visible: act.autoOn
                spacing: 4
                Text { text: "CADENCE"; color: page.textDim; font.family: page.mono; font.pixelSize: 9; font.letterSpacing: 1.2; Layout.alignment: Qt.AlignHCenter }
                CadenceSeg { field: act.cadField }
            }
        }
    }

    // Numeric stepper. Integer by default; set `step`/`decimals` for decimals
    // (e.g. step 0.5 / decimals 1 for the second-scale Advanced delays).
    component SStepper: Row {
        property real value: 0
        property real minimum: 1
        property real maximum: 9999
        property real step: 1
        property int decimals: 0
        signal changed(real v)
        spacing: 6
        // Clamp/round and emit ONLY, never assign `value` here. `value` is a
        // declarative binding to page.val(modelData); the parent's onChanged
        // routes through page.setv, which updates editMap and re-evaluates that
        // binding, so the display follows. Assigning `value` would break the
        // binding, and since delegates are kept alive across close/reopen a
        // cancelled edit would then persist visually with Save disabled.
        function apply(v) {
            var r = Math.pow(10, decimals)
            changed(Math.round(Math.max(minimum, Math.min(maximum, v)) * r) / r)
        }
        Rectangle {
            width: 32; height: 32; radius: 8; color: page.surface2; border.color: page.outline
            Ico { anchors.centerIn: parent; name: "minus"; color: page.accent; size: 17 }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: parent.parent.apply(parent.parent.value - parent.parent.step) }
        }
        Rectangle {
            width: 52; height: 32; radius: 8; color: page.surface2; border.color: page.outline
            Text { anchors.centerIn: parent; text: parent.parent.value.toFixed(parent.parent.decimals); color: page.textHi; font.family: page.mono; font.pixelSize: 16; font.bold: true }
        }
        Rectangle {
            width: 32; height: 32; radius: 8; color: page.surface2; border.color: page.outline
            Ico { anchors.centerIn: parent; name: "plus"; color: page.accent; size: 16 }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: parent.parent.apply(parent.parent.value + parent.parent.step) }
        }
    }

    component SCombo: ComboBox {
        id: cb
        implicitHeight: 44
        implicitWidth: 240
        textRole: "label"   // model items are {value, label}; store value, show label
        background: Rectangle { radius: 8; color: page.surface2; border.color: cb.pressed || cb.popup.visible ? page.accent : page.outline }
        contentItem: Text { text: cb.displayText; color: page.textHi; font.pixelSize: 14; leftPadding: 12; rightPadding: 26; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        indicator: ExpandChevron {
            x: cb.width - 24; y: (cb.height - 16) / 2; tile: 16; glyph: 12
            showTile: false; closedAngle: -90; openAngle: 0
            stroke: page.accent; open: cb.popup.visible
        }
        delegate: ItemDelegate {
            width: cb.width
            contentItem: Text { text: (modelData && modelData.label !== undefined) ? modelData.label : modelData; color: page.textHi; font.pixelSize: 14; verticalAlignment: Text.AlignVCenter }
            background: Rectangle { color: highlighted ? page.surface3 : page.surface2 }
            highlighted: cb.highlightedIndex === index
        }
        popup: Popup {
            y: cb.height + 4; width: cb.width; implicitHeight: Math.min(contentItem.implicitHeight + 8, 280); padding: 4
            background: Rectangle { radius: 8; color: page.surface2; border.color: page.outline }
            contentItem: ListView {
                clip: true; implicitHeight: contentHeight
                model: cb.popup.visible ? cb.delegateModel : null
                ScrollBar.vertical: ScrollBar {}
            }
        }
    }

    component SText: Rectangle {
        property alias text: tf.text
        property bool focused: tf.activeFocus
        radius: 8; color: page.surface2; border.color: tf.activeFocus ? page.accent : page.outline
        implicitHeight: 36
        property var onEdited: (function(t){})
        TextField {
            id: tf
            anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
            verticalAlignment: TextInput.AlignVCenter
            color: page.textHi; font.family: page.mono; font.pixelSize: 13
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

    // Per-section line-art icon (see `iconPath`). Stroke-only, accent-coloured,
    // drawn in a 16-unit box and scalable via `px`.
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
    // and because delegates are kept alive across close/reopen a cancelled Browse
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
                    Text { id: cancelTxt; anchors.centerIn: parent; text: "CANCEL"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: page.closed() }
                }
                Rectangle {
                    id: saveBtn
                    width: saveTxt.width + page.btnPadH * 2; height: saveTxt.height + page.btnPadV * 2; radius: page.btnRad
                    color: page.accentCont; opacity: page.dirty ? 1 : 0.4
                    border.color: page.accentDim; border.width: 1
                    Text { id: saveTxt; anchors.centerIn: parent; text: "SAVE CHANGES"; color: page.accent; font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack }
                    MouseArea {
                        anchors.fill: parent; enabled: page.dirty
                        cursorShape: page.dirty ? Qt.PointingHandCursor : Qt.ArrowCursor
                        // Keep editMap so the controls keep showing the values
                        // we just saved (clearing it would revert them to the
                        // now-stale schema defaults); it's reset on next open,
                        // when needsRefresh pulls the fresh persisted values.
                        onClicked: { waves.applySettings(page.editMap); page.dirty = false; page.needsRefresh = true; page.savedFlash = true; flashTimer.restart() }
                    }
                }
            }
        }

        Flickable {
            id: settingsFlick
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: col.height + 28
            ScrollBar.vertical: ScrollBar {}
            boundsBehavior: Flickable.StopAtBounds

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
                                Text { textFormat: Text.PlainText; id: updT; anchors.centerIn: parent; text: "UPDATE"; color: page.gold; font.family: page.mono; font.pixelSize: 10; font.bold: true }
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
                            visible: page.ff.lifeState === "failed"
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: "Install failed: " + page.ff.message
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
                                    Text { textFormat: Text.PlainText; id: ffUpdT; anchors.centerIn: parent; text: "UPDATE"; color: page.gold; font.family: page.mono; font.pixelSize: 10; font.bold: true }
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
                                visible: page.ff.lifeState === "failed"
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Install failed: " + page.ff.message
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
                                Text { text: "Waves " + (auCard.cur || ""); color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                Text {
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                    // A pending update is the one state worth shouting about:
                                    // gold and full-size, not the dim caption grey.
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
                                visible: page.auState === "failed"
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: "Update failed: " + page.auMsg
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

                // Reset row (Advanced section footer): explainer on the left,
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
                                        id: dgPrimTxt; anchors.centerIn: parent
                                        text: (page.diagBusy ? "Exporting…" : "Export report").toUpperCase()
                                        color: page.accent
                                        font.pixelSize: 13; font.family: page.uiFont; font.bold: true; font.letterSpacing: page.btnTrack
                                    }
                                    MouseArea {
                                        anchors.fill: parent; enabled: !page.diagBusy
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            // Push the two prefs as they stand in the UI so the
                                            // export honours an unsaved checkbox change.
                                            waves.setWavesPref("verbose_diagnostics", dgCard.vbOn)
                                            waves.setWavesPref("diagnostics_redact_content", dgCard.rdOn)
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

                    // ---- Right tile: verbose + redact-content toggles -------
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
                                SToggle {
                                    Layout.alignment: Qt.AlignVCenter
                                    checked: dgCard.vbOn
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            var v = !dgCard.vbOn
                                            page.setv("verbose_diagnostics", v)
                                            // Applies live: the watchdog and detail level flip now,
                                            // not on Save, so "turn on, reproduce, export" just works.
                                            waves.setWavesPref("verbose_diagnostics", v)
                                        }
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 3
                                    Text { text: dgCard.dfVerbose ? dgCard.dfVerbose.label : ""; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text {
                                        text: "Logs detailed activity to help diagnose slowdowns, freezes and crashes. Turn on, reproduce the problem, then export."
                                        color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                                        wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                }
                            }
                            RowLayout {
                                Layout.fillWidth: true; spacing: 13
                                SToggle {
                                    Layout.alignment: Qt.AlignVCenter
                                    checked: dgCard.rdOn
                                    MouseArea {
                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            var v = !dgCard.rdOn
                                            page.setv("diagnostics_redact_content", v)
                                            waves.setWavesPref("diagnostics_redact_content", v)
                                        }
                                    }
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true; spacing: 3
                                    Text { text: dgCard.dfRedact ? dgCard.dfRedact.label : ""; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true }
                                    Text {
                                        text: "Exports always remove usernames, paths, addresses and tokens. This also hides searches and titles, which can make bugs harder to reproduce."
                                        color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                                        wrapMode: Text.WordWrap; maximumLineCount: 3; elide: Text.ElideRight; Layout.fillWidth: true
                                    }
                                }
                            }
                        }
                    }
                    }
                }

                // Settings sections, schema-driven, collapsible
            Repeater {
                id: secRep
                model: page.groups
                delegate: Rectangle {
                    id: card
                    required property var modelData
                    width: col.width
                    radius: 12; clip: true
                    color: page.surface
                    border.width: 1
                    border.color: card.open ? "#30343c" : page.border1
                    implicitHeight: hd.implicitHeight + bodyWrap.height
                    Behavior on border.color { ColorAnimation { duration: 200 } }
                    // Downloads opens by default; Processing opens when FFmpeg is
                    // missing so the install button is visible. The rest start
                    // collapsed. The Processing trigger latches on `ffEverMissing`
                    // (not the live state) so a successful install, which flips
                    // the state missing → managed, doesn't snap the card shut.
                    property bool open: modelData.open === true
                        || (modelData.card === "ffmpeg" && page.ffEverMissing)
                        || (modelData.card === "updates" && page.auUpdate)

                    // Card header, icon + title + count chip + rotating chevron.
                    // The whole row is the click target.
                    Item {
                        id: hd
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                        implicitHeight: Math.max(30, hdRow.implicitHeight) + 28
                        RowLayout {
                            id: hdRow
                            anchors.left: parent.left; anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 15; anchors.rightMargin: 15
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
                                // still reads from the glyph colour (red/gold/green) below.
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
                                    text: card.modelData.group; color: page.textHi
                                    font.pixelSize: 15; font.weight: Font.DemiBold
                                    Layout.fillWidth: true; elide: Text.ElideRight
                                }
                                Text {
                                    visible: text !== ""
                                    text: card.modelData.desc !== undefined ? card.modelData.desc : ""
                                    color: page.textDim; font.pixelSize: 12
                                    wrapMode: Text.WordWrap; Layout.fillWidth: true
                                }
                            }
                            Rectangle {
                                radius: 4; color: "transparent"; border.color: page.border1
                                Layout.preferredHeight: 18; Layout.preferredWidth: cntT.implicitWidth + 16
                                Layout.alignment: Qt.AlignVCenter
                                Text { id: cntT; anchors.centerIn: parent; text: card.modelData.fields.length; color: page.textDim; font.family: page.mono; font.pixelSize: 11 }
                            }
                            ExpandChevron { open: card.open; hovered: hdHover.containsMouse; Layout.alignment: Qt.AlignVCenter }
                        }
                        MouseArea {
                            id: hdHover
                            anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                            onClicked: card.open = !card.open
                        }
                    }

                    // Collapsible body. Driving a real (animated) height keeps the
                    // Flickable's contentHeight binding re-measuring as it opens/closes.
                    Item {
                        id: bodyWrap
                        anchors.left: parent.left; anchors.right: parent.right; anchors.top: hd.bottom
                        clip: true
                        height: card.open ? inner.implicitHeight + 26 : 0
                        Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }

                        Rectangle { id: bodyDiv; anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top; height: 1; color: page.divider }

                        Column {
                            id: inner
                            x: 15; y: 12; width: parent.width - 30; spacing: 10
                            opacity: card.open ? 1 : 0
                            // Fade in quickly on open; on close, outlast the height
                            // collapse (220ms) so content doesn't vanish early.
                            Behavior on opacity { NumberAnimation { duration: card.open ? 160 : 220; easing.type: Easing.OutQuad } }

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

                            // Value-bearing settings (str / enum / int / float) as
                            // labelled rows. One shared delegate feeds two Repeaters:
                            // the File organization card renders its path-template
                            // rows first, the token reference right under them, then
                            // its remaining value rows; every other card renders all
                            // of its rows in the first Repeater.
                            Component {
                                id: rowFieldComp
                                Rectangle {
                                    required property var modelData
                                    visible: page.depOK(modelData)
                                    width: inner.width
                                    radius: 10; color: page.surface; border.color: page.border1
                                    implicitHeight: body.implicitHeight + 20
                                    Item {
                                        id: body
                                        x: 14; y: 10; width: parent.width - 28
                                        implicitHeight: modelData.type === "str" ? strCol.implicitHeight
                                                      : modelData.type === "cover_sizes" ? coverCol.implicitHeight
                                                      : inlineRow.implicitHeight

                                        // Enum / int / float: label + help on the left, control on the right
                                        RowLayout {
                                            id: inlineRow
                                            visible: modelData.type !== "str" && modelData.type !== "cover_sizes"
                                            width: parent.width; spacing: 14
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 2
                                                Text { text: modelData.label; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                                Text { visible: modelData.help !== ""; text: modelData.help; color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                            }
                                            SCombo {
                                                visible: modelData.type === "enum"; Layout.alignment: Qt.AlignVCenter
                                                model: modelData.type === "enum" ? modelData.options : []
                                                currentIndex: modelData.type === "enum" ? page.enumIndex(modelData.options, page.val(modelData)) : 0
                                                onActivated: page.setv(modelData.key, modelData.options[currentIndex].value)
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
                                            RowLayout {
                                                width: parent.width; spacing: 14
                                                ColumnLayout {
                                                    Layout.fillWidth: true; spacing: 2
                                                    Text { text: modelData.label; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                                    Text { visible: modelData.help !== ""; text: modelData.help; color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                                }
                                                SCombo {
                                                    Layout.alignment: Qt.AlignVCenter
                                                    model: modelData.options ? modelData.options : []
                                                    currentIndex: page.enumIndex(modelData.options, page.val(modelData))
                                                    onActivated: page.setv(modelData.key, modelData.options[currentIndex].value)
                                                }
                                            }
                                            Text {
                                                textFormat: Text.PlainText
                                                text: (coverCol.expanded ? "▾  " : "▸  ") + "Separate cover.jpg size"
                                                color: page.accent; font.pixelSize: 12
                                                MouseArea { anchors.fill: parent; anchors.margins: -4; cursorShape: Qt.PointingHandCursor; onClicked: coverCol.expanded = !coverCol.expanded }
                                            }
                                            RowLayout {
                                                visible: coverCol.expanded
                                                width: parent.width; spacing: 14
                                                ColumnLayout {
                                                    Layout.fillWidth: true; spacing: 2
                                                    Text { text: modelData.file_label ? modelData.file_label : "Separate cover.jpg size"; color: page.textHi; font.pixelSize: 13; font.weight: Font.Medium }
                                                    Text { text: "Size of the saved cover.jpg. \"Same as embedded\" matches the size above."; color: page.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                                                }
                                                SCombo {
                                                    Layout.alignment: Qt.AlignVCenter
                                                    model: modelData.file_options ? modelData.file_options : []
                                                    currentIndex: page.enumIndex(modelData.file_options, page.val2(modelData))
                                                    onActivated: page.setv(modelData.file_key, modelData.file_options[currentIndex].value)
                                                }
                                            }
                                        }

                                        // String / path: label + help, then text field (+ browse) below
                                        Column {
                                            id: strCol
                                            visible: modelData.type === "str"
                                            width: parent.width; spacing: 6
                                            Text { text: modelData.label; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium }
                                            Text { visible: modelData.help !== ""; text: modelData.help; color: page.textDim; font.pixelSize: 12; width: parent.width; wrapMode: Text.WordWrap }
                                            Row {
                                                width: parent.width; spacing: 8
                                                SText {
                                                    width: modelData.browse ? parent.width - browseBtn.width - 8 : parent.width
                                                    text: page.val(modelData)
                                                    onEdited: function(t){ page.setv(modelData.key, t) }
                                                }
                                                Rectangle {
                                                    id: browseBtn
                                                    visible: modelData.browse !== ""
                                                    // SAVE CHANGES button vocabulary: accent-container fill,
                                                    // accentDim border, uppercase accent label; full strength
                                                    // while the field still needs a value (Browse IS the action
                                                    // to take), faded once one is set. String(): this binding
                                                    // also evaluates in the hidden non-str branches of the
                                                    // delegate, where val() is a number or bool.
                                                    readonly property bool needsValue: String(page.val(modelData) ?? "").trim() === ""
                                                    width: browseTxt.width + page.btnPadH * 2; height: browseTxt.height + page.btnPadV * 2; radius: page.btnRad
                                                    anchors.verticalCenter: parent.verticalCenter
                                                    color: page.accentCont
                                                    border.color: page.accentDim; border.width: 1
                                                    opacity: needsValue ? 1 : 0.4
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
                                                                var du = page.pathUrl(v)
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

                            // Template-token reference, File organization card
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
                                            // every band so the columns stay labelled
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

                            // The File organization card's non-template value rows
                            // (padding, delimiters) continue under the reference.
                            Repeater {
                                model: card.modelData.id === "files"
                                     ? page.rowFields(card.modelData.fields).filter(function(f) { return f.key.indexOf("format_") !== 0 })
                                     : []
                                delegate: rowFieldComp
                            }

                            // On/off switches as a 2-column tile grid (keeps the look).
                            Flow {
                                id: flagFlow
                                readonly property int flagCount: page.boolFields(card.modelData.fields).length
                                visible: flagCount > 0
                                width: parent.width; spacing: 10
                                Repeater {
                                    model: page.boolFields(card.modelData.fields)
                                    delegate: Rectangle {
                                        id: flagTile
                                        required property var modelData
                                        // Flags that need FFmpeg are greyed and inert while it's missing.
                                        readonly property bool ffBlocked: modelData.requires_ffmpeg === true && page.ff.stateKey === "missing"
                                        // A tile may carry a nested child (e.g. Save cover.jpg ->
                                        // "Also save for single tracks"): a compact checkbox that appears
                                        // UNDER the description while the parent flag is on. The box keeps
                                        // its fixed size; the column just re-centres to make room, so the
                                        // tile never grows or shifts its neighbours.
                                        readonly property bool hasChild: modelData.child_key !== undefined
                                        readonly property bool childOn: flagTile.hasChild && !flagTile.ffBlocked && (page.val(flagTile.modelData) === true)
                                        width: (inner.width - 10) / 2
                                        height: 92
                                        radius: 10; border.color: page.border1
                                        opacity: ffBlocked ? 0.45 : 1
                                        // Ease the tile back to full strength when ffmpeg
                                        // arrives, in step with the toggle animating on.
                                        Behavior on opacity { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                                        color: (tileMouse.containsMouse && !ffBlocked) ? page.surface2 : page.surface

                                        // Whole-tile click toggles the parent flag; the child checkbox
                                        // sits above this and swallows its own clicks.
                                        MouseArea {
                                            id: tileMouse
                                            anchors.fill: parent; hoverEnabled: true
                                            enabled: !flagTile.ffBlocked
                                            cursorShape: flagTile.ffBlocked ? Qt.ArrowCursor : Qt.PointingHandCursor
                                            onClicked: page.setv(flagTile.modelData.key, !page.val(flagTile.modelData))
                                        }
                                        RowLayout {
                                            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 14; spacing: 13
                                            SToggle {
                                                Layout.alignment: Qt.AlignVCenter
                                                // While ffmpeg is missing the feature can't run, so the
                                                // toggle reads OFF (greyed) regardless of the saved value;
                                                // when ffmpeg lands it animates back to the real preference.
                                                checked: page.val(flagTile.modelData) && !flagTile.ffBlocked
                                            }
                                            ColumnLayout {
                                                Layout.fillWidth: true; spacing: 3
                                                Text { text: flagTile.modelData.label; color: page.textHi; font.pixelSize: 14; font.weight: Font.Medium; elide: Text.ElideRight; Layout.fillWidth: true }
                                                Text {
                                                    visible: flagTile.modelData.help !== "" && !flagTile.ffBlocked
                                                    text: flagTile.modelData.help; color: page.textDim; font.pixelSize: 12; lineHeight: 1.15
                                                    // Trim the helper to two lines while the child checkbox is
                                                    // showing, so both fit the fixed box.
                                                    wrapMode: Text.WordWrap; maximumLineCount: flagTile.childOn ? 2 : 3; elide: Text.ElideRight; Layout.fillWidth: true
                                                }
                                                Text {
                                                    visible: flagTile.ffBlocked
                                                    text: "Requires FFmpeg"; color: page.gold; font.pixelSize: 11; Layout.fillWidth: true
                                                }
                                                // Nested child: a compact checkbox + label, shown only while
                                                // the parent flag is on (layouts skip it when hidden, so other
                                                // tiles are unchanged).
                                                Item {
                                                    visible: flagTile.childOn
                                                    Layout.fillWidth: true; implicitHeight: childRowL.implicitHeight
                                                    MouseArea {
                                                        anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                                        onClicked: page.setv(flagTile.modelData.child_key, !(page.valChild(flagTile.modelData) === true))
                                                    }
                                                    RowLayout {
                                                        id: childRowL
                                                        anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                                                        spacing: 8
                                                        Rectangle {
                                                            Layout.alignment: Qt.AlignVCenter
                                                            width: 18; height: 18; radius: 5
                                                            readonly property bool on: page.valChild(flagTile.modelData) === true
                                                            color: on ? page.accentCont : page.surface3
                                                            border.color: on ? page.accent : page.outline; border.width: 2
                                                            Behavior on color { ColorAnimation { duration: 140 } }
                                                            Behavior on border.color { ColorAnimation { duration: 140 } }
                                                            Ico { anchors.centerIn: parent; visible: parent.on; name: "check"; color: page.accent; size: 12 }
                                                        }
                                                        Text {
                                                            text: flagTile.modelData.child_label !== undefined ? flagTile.modelData.child_label : ""
                                                            color: page.textLo; font.pixelSize: 12; elide: Text.ElideRight; Layout.fillWidth: true
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }
                                }

                                // An odd tile count would leave a blank half-row.
                                // Fill the empty slot with a calm, non-interactive
                                // wave tile (the WaveMark's glyph patterns, static
                                // and dim) so the grid reads as finished surface,
                                // not a hole. Placeholder until these sections get
                                // a fuller redesign.
                                Rectangle {
                                    visible: flagFlow.flagCount % 2 === 1
                                    width: (inner.width - 10) / 2; height: 92
                                    radius: 10; color: page.surface; border.color: page.border1
                                    Item {
                                        anchors.fill: parent; anchors.margins: 3; clip: true
                                        Repeater {
                                            model: [
                                                { yf: 0.10, px: 7,  op: 0.10, pat: "   '    .     *   :   .   " },
                                                { yf: 0.26, px: 8,  op: 0.13, pat: ".~-~..-~-.~..-~-." },
                                                { yf: 0.42, px: 8,  op: 0.16, pat: "-.~-..~.-~-..~.-" },
                                                { yf: 0.58, px: 10, op: 0.20, pat: "_.-~-._.,-~-._.-" },
                                                { yf: 0.74, px: 11, op: 0.24, pat: ".-~^-._,.~-^._.-~" }
                                            ]
                                            delegate: Text {
                                                required property var modelData
                                                textFormat: Text.PlainText
                                                y: Math.round(86 * modelData.yf)
                                                text: modelData.pat.repeat(24)
                                                font.family: page.mono; font.pixelSize: modelData.px
                                                color: page.accent; opacity: modelData.op
                                                font.letterSpacing: -0.5
                                            }
                                        }
                                    }
                                }
                            }

                            // Reset actions, Advanced section only, at the very
                            // bottom: put settings back to factory defaults, or
                            // wipe the whole app state. Both confirm first (the
                            // dialogs live in Main.qml, full-window overlays).
                            Loader {
                                active: card.modelData.id === "advanced"
                                visible: active
                                width: inner.width
                                height: (active && item) ? item.implicitHeight : 0
                                sourceComponent: resetRowComp
                            }
                        }
                    }
                }
            }

            // ── footer credit (easter egg), "made with [♥] by iamprivacy".
            // Types itself out when scrolled into view, backspaces away when
            // scrolled off; the heart lub-dubs on hover and bursts into colour
            // pixels when clicked (the burst sim is the page-root `gibOverlay`).
            Item {
                id: footer
                width: col.width; height: 72

                readonly property int total: 25   // "made with "10 + heart 1 + " by "4 + "iamprivacy"10
                property int n: 0
                property bool inView: false
                property bool cursorPresent: false
                property bool heartGibbed: false
                property bool creditHidden: false
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
                function recomputeInView() {
                    var p = footer.mapToItem(settingsFlick, 0, footer.height / 2)
                    var nowIn = (p.y < settingsFlick.height - 6) && (p.y > -footer.height)
                    if (nowIn !== footer.inView) footer.inView = nowIn
                }
                function checkGibMeet() {
                    if (!footer.heartGibbed || footer.creditHidden) return
                    var tb = fCredit.mapToItem(page, 0, fCredit.height / 2 + 6).y
                    if (tb >= gibOverlay.groundY) {
                        footer.heartGibbed = false
                        gibOverlay.releaseWithText(fCredit)
                    }
                }
                Component.onCompleted: Qt.callLater(footer.recomputeInView)
                Connections { target: settingsFlick
                    function onContentYChanged() { footer.recomputeInView(); footer.checkGibMeet() }
                    function onContentHeightChanged() { footer.recomputeInView() }
                    function onHeightChanged() { footer.recomputeInView() }
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
                        if (footer.creditHidden) {
                            // already handed off to the falling overlay, leave it
                        } else if (footer.heartGibbed) {
                            fTypeBack.stop()
                            footer.heartGibbed = false
                            gibOverlay.releaseWithText(fCredit)
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
                    opacity: footer.creditHidden ? 0 : 1
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
                        Text { anchors.verticalCenter: parent.verticalCenter
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
                            // stays until the credit is released on scroll-away
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
                        Text { anchors.verticalCenter: parent.verticalCenter
                               text: " by ".substring(0, footer.visFor(11, 4)); color: page.accent; font.family: page.mono; font.pixelSize: 11 }
                        Item { implicitWidth: fGh.implicitWidth; implicitHeight: fGh.implicitHeight; anchors.verticalCenter: parent.verticalCenter
                            Text { id: fGh; text: "iamprivacy".substring(0, footer.visFor(15, 10))
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

    // ── heart-gib overlay (easter egg), viewport-fixed Canvas the footer heart
    // bursts into. Lives at the page root so the gibs + drips fall down the whole
    // settings view rather than being clipped in the scroll. Transparent and
    // non-interactive (no MouseArea) so it never blocks the UI.
    Canvas {
        id: gibOverlay
        anchors.fill: parent
        z: 2000
        property var gibs: []
        property var splats: []
        property var drips: []
        property real groundY: 0
        property real bleedTime: 0
        property real dripAccum: 0
        property bool running: false
        property bool releasing: false   // when true the ledge is gone → everything falls off-screen

        function explode(ox, oy, model, hpx) {
            var cs = HeartGib.cells(model, ox, oy, hpx)
            var hw = HeartGib.pxW(model, hpx), hh = HeartGib.pxH(model, hpx)
            var ccx = ox + hw / 2, ccy = oy + hh / 2
            var g = []
            for (var i = 0; i < cs.length; i++) {
                var pc = cs[i]; var dx = pc.x - ccx, dy = pc.y - ccy
                var d = Math.max(6, Math.sqrt(dx * dx + dy * dy)); var sp = 120 + Math.random() * 220
                g.push({ x: pc.x, y: pc.y, vx: dx / d * sp + (Math.random() - 0.5) * 130,
                         vy: dy / d * sp - (130 + Math.random() * 170),
                         size: hpx + Math.floor(Math.random() * 2), color: pc.color })
            }
            gibs = g; splats = []; drips = []
            groundY = oy + hh + 14; bleedTime = 0; dripAccum = 0; releasing = false; running = true
            requestPaint()
        }
        // scroll-away: drop every settled splat into a falling gib and dissolve the
        // ledge, so the giblets slide off the bottom as the page scrolls.
        function release() {
            var g = gibs.slice()
            for (var i = 0; i < splats.length; i++) {
                var s = splats[i]
                g.push({ x: s.x, y: groundY - s.size, vx: (Math.random() - 0.5) * 40,
                         vy: 30 + Math.random() * 70, size: s.size, color: s.color })
            }
            gibs = g; splats = []
            releasing = true; running = true
            requestPaint()
        }
        // release() the giblets AND hand off the credit text, snapshot its viewport
        // position so a matching falling copy drops with the giblets while the real
        // (content-space) credit hides, then re-arms.
        function releaseWithText(creditItem) {
            release()
            var p = creditItem.mapToItem(gibOverlay, 0, 0)
            fallText.x = p.x; fallText.y = p.y
            fallText.vy = 8; fallText.active = true; fallText.visible = true
            footer.creditHidden = true
            running = true
        }
        function reset() { gibs = []; splats = []; drips = []; running = false; releasing = false
                           fallText.active = false; fallText.visible = false; requestPaint() }

        // the falling credit copy (viewport space). Mirrors the footer layout
        // (heart-width gap where the gibbed heart was) so the hand-off is seamless.
        Item {
            id: fallText
            visible: false
            property bool active: false
            property real vy: 0
            Row {
                spacing: 0
                Text { text: "made with "; color: page.accent; font.family: page.mono; font.pixelSize: 11 }
                Item { width: footer.lovedW; height: footer.heartH
                    Text { anchors.centerIn: parent; text: "[love]"; color: page.accent; font.family: page.mono; font.pixelSize: 11 } }
                Text { text: " by ";       color: page.accent; font.family: page.mono; font.pixelSize: 11 }
                Text { text: "iamprivacy";  color: page.gold; font.family: page.mono; font.pixelSize: 11 }
            }
        }

        FrameAnimation { running: gibOverlay.running
            onTriggered: { gibOverlay.step(Math.min(frameTime, 0.032)); gibOverlay.requestPaint() } }

        function step(dt) {
            var g = 2400, W = width, H = height
            bleedTime += dt
            var live = []
            for (var i = 0; i < gibs.length; i++) {
                var p = gibs[i]
                p.vy += g * dt; p.x += p.vx * dt; p.y += p.vy * dt
                if (p.x < 0) { p.x = 0; p.vx = -p.vx * 0.4 } else if (p.x > W) { p.x = W; p.vx = -p.vx * 0.4 }
                if (!releasing && p.y >= groundY && p.vy > 0) {       // ledge only holds while not releasing
                    if (Math.abs(p.vy) < 65) { splats.push({ x: p.x, size: p.size, color: p.color }); continue }
                    p.y = groundY; p.vy = -p.vy * 0.32; p.vx *= 0.6
                }
                if (p.y > H) continue
                live.push(p)
            }
            gibs = live
            if (!releasing && splats.length > 0) {
                var rate = 40 * Math.exp(-bleedTime / 7.0)
                dripAccum += dt * rate
                while (dripAccum >= 1) {
                    dripAccum -= 1
                    var s = splats[Math.floor(Math.random() * splats.length)]
                    drips.push({ x: s.x, y: groundY, vy: 30 + Math.random() * 50, color: s.color })
                }
            }
            var dl = []
            for (i = 0; i < drips.length; i++) {
                var d = drips[i]; d.vy += g * 0.25 * dt; d.y += d.vy * dt
                if (d.y >= H) continue
                dl.push(d)
            }
            drips = dl
            if (fallText.active) {
                fallText.vy += g * 0.7 * dt
                fallText.y += fallText.vy * dt
                if (fallText.y > H) {
                    fallText.active = false; fallText.visible = false
                    footer.creditHidden = false
                    footer.n = 0; footer.cursorPresent = false
                }
            }
            if (gibs.length === 0 && drips.length === 0 && !fallText.active && (releasing || bleedTime > 9)) running = false
        }
        onPaint: {
            var ctx = getContext('2d'); ctx.clearRect(0, 0, width, height)
            for (var i = 0; i < splats.length; i++) { var s = splats[i]
                ctx.fillStyle = s.color; ctx.fillRect(Math.round(s.x), Math.round(groundY - s.size), s.size, s.size) }
            for (i = 0; i < gibs.length; i++) { var p = gibs[i]
                ctx.fillStyle = p.color; ctx.fillRect(Math.round(p.x), Math.round(p.y), p.size, p.size) }
            for (i = 0; i < drips.length; i++) { var d = drips[i]
                ctx.fillStyle = d.color; ctx.fillRect(Math.round(d.x), Math.round(d.y), 2, 4) }
        }
    }
}
