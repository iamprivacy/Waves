// The tray icon for a window closed to the background, on Windows and Linux.
// macOS never loads this file: there the Dock icon is the way back (Main.qml
// keeps the Loader off), so the Mac bundle ships without Qt.labs.platform.
import QtQuick
import Qt.labs.platform as Platform

Item {
    id: host
    // Set by Main.qml: true while the window is closed to the background.
    property bool shown: false
    // Whether this desktop has a tray at all. Without one the close button
    // exits, as it always did.
    readonly property bool available: tray.available

    onAvailableChanged: waves.setTrayAvailable(available)
    Component.onCompleted: waves.setTrayAvailable(available)

    Platform.SystemTrayIcon {
        id: tray
        visible: host.shown && available
        icon.source: waves.trayIconUrl()
        tooltip: "Waves"
        // A click or a double click opens the window; the context click is
        // the menu's.
        onActivated: function (reason) {
            if (reason !== Platform.SystemTrayIcon.Context) waves.requestShowWindow()
        }
        menu: Platform.Menu {
            Platform.MenuItem { text: "Open Waves"; onTriggered: waves.requestShowWindow() }
            Platform.MenuSeparator {}
            Platform.MenuItem { text: "Quit Waves"; onTriggered: waves.requestQuit() }
        }
    }
}
