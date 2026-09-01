"""Restore default must RE-BIND the field, never overwrite its text.

THE BUG WE ARE FENCING OFF
--------------------------
The per-field "Restore default" link assigned the box directly:

    strField.text = modelData.default_value
    page.setv(modelData.key, modelData.default_value)

An imperative write to a TextField's ``text`` destroys the
``text: page.val(modelData)`` binding declared on it, and the SettingsPage is a
permanent child of Main.qml whose Repeater delegates outlive a close/reopen
(``refreshSchema`` only re-runs after an actual save). So Restore default
followed by CANCEL left the box displaying the shipped default for the rest of
the session while the persisted setting, and every download, still used the
user's custom value. The link beside it still read "Restore default" (its own
binding was alive and re-evaluated against the real value), and SAVE was greyed
out, so the page showed a contradiction the user could not resolve.

``Qt.binding`` does both jobs: it shows the default immediately AND repairs a
binding the user's own typing had already broken.

The page's Browse-dialog path documents the same hazard and takes the same care
(see the comment above ``folderDlg``), so this is checked structurally: any
assignment to a settings field's ``text`` must go through ``Qt.binding``.
"""

from __future__ import annotations

import re
from pathlib import Path

SETTINGS_QML = Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml" / "SettingsPage.qml"


def _source() -> str:
    return SETTINGS_QML.read_text(encoding="utf-8")


def test_restore_default_sets_the_edit_map():
    """The value has to reach editMap, or Save would never persist it."""
    src = _source()
    assert "page.setv(modelData.key, modelData.default_value)" in src


def test_no_bare_imperative_write_to_a_settings_field_text():
    """Every `<field>.text = ...` in the page must hand over a Qt.binding.

    A bare assignment silently kills the declarative binding, and because the
    delegates are kept alive across close/reopen the field then lies about the
    persisted value until some unrelated save rebuilds the page.
    """
    offenders = []
    for lineno, line in enumerate(_source().splitlines(), 1):
        m = re.search(r"\b(\w*[Ff]ield|tf)\.text\s*=\s*(.+)$", line.strip())
        if not m:
            continue
        if "Qt.binding" in m.group(2):
            continue
        offenders.append(f"SettingsPage.qml:{lineno}: {line.strip()}")
    assert not offenders, "imperative writes destroy the field's binding, use Qt.binding:\n" + "\n".join(offenders)


def test_restore_default_rebinds_rather_than_overwrites():
    """Pin the shape of the handler itself, so the pair cannot drift apart."""
    src = _source()
    handler = re.search(
        r"onClicked:\s*\{[^}]*default_value[^}]*strField\.text[^}]*\}",
        src,
        re.DOTALL,
    )
    assert handler is not None, "the Restore default click handler moved or was renamed"
    body = handler.group(0)
    assert "page.setv(" in body
    assert "Qt.binding(" in body, "Restore default must re-establish the binding, not overwrite the text"


def test_the_character_table_restores_through_the_same_care():
    """The per-character table has many boxes, so its Restore default cannot
    re-bind one field inline. It stages the whole table through ``mapStage``,
    which emits ``mapRestaged``; each box re-binds on that signal. Different
    mechanism, same rule: no bare write to a box's text.
    """
    src = _source()
    assert "signal mapRestaged(string key)" in src
    stage = re.search(r"function mapStage\(f, m\)\s*\{.*?\n    \}", src, re.DOTALL)
    assert stage is not None, "mapStage moved or was renamed"
    assert "setv(f.key, copy)" in stage.group(0), "the staged table must reach editMap"
    assert "mapRestaged(f.key)" in stage.group(0), "the boxes never hear about it otherwise"
    listener = re.search(r"function onMapRestaged\(key\)\s*\{.*?\n\s*\}", src, re.DOTALL)
    assert listener is not None, "no box listens for a staged table"
    assert "Qt.binding(" in listener.group(0)
