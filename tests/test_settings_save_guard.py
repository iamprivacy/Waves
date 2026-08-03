"""Every settings save must undo the transient ffmpeg injections first.

THE BUG
-------
``Download`` force-disables ``video_convert_mp4`` and ``extract_flac`` **in
memory** when ffmpeg is absent, and ``_resolve_ffmpeg`` injects the managed
binary path into ``path_binary_ffmpeg``. ``Settings`` is a singleton and
``save()`` serialises the whole dataclass, so any bare ``settings.save()``
writes those transient values to disk.

``applySettings`` and ``setVideoQuality`` knew this and restored first. Three
other save sites did not: ``keepDownloadFolder`` (answering the
download-folder nudge with "keep it"), ``muteCategoryDlConfirm`` (a plain
"Don't ask again" click) and the download-folder auto-heal. Answering a nudge
about folders therefore silently turned FLAC extraction and video conversion
off on disk, invisibly until the next launch (the settings page renders the
pre-damage snapshot), and with ffmpeg present it also persisted a machine path
containing the username into settings.json, the file the bug template asks
users to paste publicly.

THE FIX routes every save through ``_save_settings``, which restores both
before saving. This test fences the invariant at the helper AND at the slots
that regressed, so a newly added save site is caught by the audit test below.
"""

from __future__ import annotations

import inspect
import re
from typing import ClassVar

from tidaler.model.cfg import Settings as CfgSettings
from tidaler.waves_ui.backend import WavesBridge


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _bridge():
    """A stub carrying the real save/restore methods over a real dataclass, in
    the exact state the bug needs: ffmpeg missing (flags forced off in memory,
    user's real preference remembered) and a managed path injected."""
    stub = _Stub()

    class _Cfg:
        data = CfgSettings()
        saved: ClassVar[list[dict]] = []

        def save(self):
            # Capture what would hit disk.
            self.saved.append(
                {
                    "extract_flac": self.data.extract_flac,
                    "video_convert_mp4": self.data.video_convert_mp4,
                    "path_binary_ffmpeg": self.data.path_binary_ffmpeg,
                }
            )

    stub.settings = _Cfg()

    # The user's real preferences: both features ON, no explicit ffmpeg path.
    stub._ffmpeg_flag_prefs = {"extract_flac": True, "video_convert_mp4": True}
    stub._ffmpeg_user_path = ""

    # What Download/_resolve_ffmpeg did to the live object in memory.
    stub.settings.data.extract_flac = False
    stub.settings.data.video_convert_mp4 = False
    stub.settings.data.path_binary_ffmpeg = "/Users/testuser/Library/Application Support/Waves/bin/ffmpeg"

    stub._restore_ffmpeg_flags = _bind(stub, "_restore_ffmpeg_flags")
    stub._restore_ffmpeg_path = _bind(stub, "_restore_ffmpeg_path")
    stub._save_settings = _bind(stub, "_save_settings")

    class _Signal:
        emits: ClassVar[list] = []

        def emit(self, *a):
            self.emits.append(a)

    # _save_settings tells the Settings page a backend path persisted values.
    stub.settingsPersistedExternally = _Signal()
    return stub


def _last_save(stub) -> dict:
    assert stub.settings.saved, "nothing was saved"
    return stub.settings.saved[-1]


def test_keep_download_folder_does_not_persist_transient_ffmpeg_flags():
    """Answering the folder nudge must not disable FLAC extraction on disk."""
    stub = _bridge()
    stub._run_pending_downloads = lambda: None

    _bind(stub, "keepDownloadFolder")()

    written = _last_save(stub)
    assert written["extract_flac"] is True, "answering the folder nudge disabled FLAC extraction on disk"
    assert written["video_convert_mp4"] is True
    assert written["path_binary_ffmpeg"] == "", "a machine path (with the username) was persisted"
    assert stub.settings.data.download_folder_prompted is True  # the decision still stuck


def test_mute_category_confirm_does_not_persist_transient_ffmpeg_flags():
    """'Don't ask again' on the bulk-download confirm shares the trap."""
    stub = _bridge()
    stub.confirmCategoryDlChanged = type("_S", (), {"emit": staticmethod(lambda: None)})()

    _bind(stub, "muteCategoryDlConfirm")()

    written = _last_save(stub)
    assert written["extract_flac"] is True
    assert written["video_convert_mp4"] is True
    assert written["path_binary_ffmpeg"] == ""
    assert stub.settings.data.confirm_category_download is False  # the opt-out still stuck


def test_save_settings_restores_both_injections():
    """The helper itself is the invariant; test it directly."""
    stub = _bridge()

    stub._save_settings()

    written = _last_save(stub)
    assert written == {
        "extract_flac": True,
        "video_convert_mp4": True,
        "path_binary_ffmpeg": "",
    }


def test_no_bare_settings_save_outside_the_guarded_helper():
    """The audit guard: a newly added bare save silently re-opens this bug.

    Exactly three call sites of ``self.settings.save()`` are allowed: inside
    ``_save_settings`` itself; inside ``applySettings``, which does the restores
    explicitly because it must compute ``ffmpeg_source`` from the restored value
    before saving; and inside ``_apply_first_run_defaults``, which runs from
    ``__init__`` before ffmpeg is resolved, so nothing is injected yet.
    """
    source = inspect.getsource(WavesBridge)
    bare = len(re.findall(r"self\.settings\.save\(\)", source))
    assert bare == 3, (
        f"found {bare} bare self.settings.save() calls, expected 3 "
        "(_save_settings, applySettings, _apply_first_run_defaults). Route new saves "
        "through _save_settings, or this writes the transient ffmpeg flags and path to disk."
    )

    for name in ("_save_settings", "applySettings", "_apply_first_run_defaults"):
        method_src = inspect.getsource(getattr(WavesBridge, name))
        assert "self.settings.save()" in method_src, f"{name} no longer saves; update this guard"
