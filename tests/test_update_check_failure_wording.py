"""A failed update check says "could not check", never "up to date".

Both update checks (the app's and the managed FFmpeg's) run off the GUI
thread and, when the probe throws (offline, API error), emit
``(False, "", "")``. Three consumers used to read that as "nothing newer"
and flashed "up to date": the Settings updater card, the FFmpeg card
through FfmpegManager, and the status-bar version mark (front-end audit
2026-09-17, G7). The contract is now explicit: a real answer always names
the release or build, so an EMPTY ``latest`` means the check got no answer.
Pinned on the source so a tidy-up cannot fold the two branches back into one.
"""

from __future__ import annotations

import pathlib

QML = pathlib.Path(__file__).resolve().parent.parent / "waves" / "waves_ui" / "qml"


def _handler(src: str, name: str) -> str:
    start = src.index(f"function {name}(")
    return src[start : src.index("\n        }", start)]


def test_settings_updater_card_splits_failed_from_up_to_date():
    src = (QML / "SettingsPage.qml").read_text()
    body = _handler(src, "onAppUpdateChecked")
    assert 'latest === ""' in body
    assert "page.auCheckFailed = true" in body
    assert "page.auUpToDate = true" in body
    assert 'text: "✗ Could not check"' in src


def test_ffmpeg_manager_splits_failed_from_up_to_date():
    src = (QML / "FfmpegManager.qml").read_text()
    body = _handler(src, "onFfmpegUpdateChecked")
    assert 'latest === ""' in body
    assert "mgr.checkFailed = true" in body
    assert "mgr.upToDate = true" in body


def test_status_bar_version_mark_splits_failed_from_up_to_date():
    src = (QML / "Main.qml").read_text()
    assert '"COULD NOT CHECK"' in src
    idx = src.index('if (statusMark.verState !== "checking") return')
    body = src[idx : idx + 600]
    assert 'latest === "" ? "failed" : "current"' in body
