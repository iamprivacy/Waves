import QtQuick

// The input shield for a developer note (or any note laid over the app on
// Overlay.overlay): while active, nothing behind it reacts. Every mouse
// button, the wheel and hover stop here, and the keyboard is held, so
// typing, Tab and Space never reach the page. A Shortcut still fires (it
// runs before keys are delivered), which is how Esc closes the note; every
// other window Shortcut stands down on root.noteUp. Focus goes back where
// it was when the shield lets go, unless something else (a dialog the note
// stepped aside for) has taken it meanwhile.
//
// Use one over the backdrop (backdropClicked closes the note on a left
// click) and one, holdsKeys: false, filling the note's card so a click on
// the card neither closes it nor falls through.
Item {
    id: shield
    property bool active: true
    property bool holdsKeys: true
    signal backdropClicked()

    property Item focusBefore: null
    onActiveChanged: {
        if (!holdsKeys) return
        if (active) {
            focusBefore = Window.activeFocusItem
            keys.forceActiveFocus()
        } else {
            if (keys.activeFocus && focusBefore) focusBefore.forceActiveFocus()
            focusBefore = null
        }
    }

    MouseArea {
        anchors.fill: parent
        enabled: shield.active
        acceptedButtons: Qt.AllButtons; hoverEnabled: true
        onWheel: function(w) { w.accepted = true }
        onClicked: function(m) { if (m.button === Qt.LeftButton) shield.backdropClicked() }
    }
    Item {
        id: keys
        Keys.onPressed: function(e) { e.accepted = true }
    }
}
