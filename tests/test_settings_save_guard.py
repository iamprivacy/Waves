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
from threading import Lock
from typing import ClassVar

from tidaler.model.cfg import Settings as CfgSettings
from tidaler.waves_ui.backend import WavesBridge

# The managed binary sits under the account's own Application Support folder, so
# the path is identity-bearing. That is the whole reason it must never reach the
# settings file, which the bug template asks users to paste in public.
_MANAGED = "/Users/testuser/Library/Application Support/Waves/bin/ffmpeg"


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
    stub.settings.data.path_binary_ffmpeg = _MANAGED

    stub._restore_ffmpeg_flags = _bind(stub, "_restore_ffmpeg_flags")
    stub._restore_ffmpeg_path = _bind(stub, "_restore_ffmpeg_path")
    stub._save_settings = _bind(stub, "_save_settings")
    stub._settings_save_lock = Lock()

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


def test_the_save_leaves_the_live_settings_alone():
    """THE SECOND BUG. The restores used to run on the singleton itself, which
    every in-flight Download holds and re-reads on every track, and nothing put
    the values back: only _resolve_ffmpeg injects, and no save site calls it. So
    a "Don't ask again" tick during an album download stripped the managed
    ffmpeg path for the rest of that album. From that track on the m4a duration
    remux was skipped and a FLAC extraction ran with an empty executable.

    What goes to disk is the user's real preference; what stays in memory is
    what the running download was built with. Both, not one or the other."""
    stub = _bridge()
    live = stub.settings.data

    stub._save_settings()

    assert _last_save(stub)["path_binary_ffmpeg"] == "", "the machine path must not reach disk"
    assert stub.settings.data is live, "the singleton's data object was swapped out and not put back"
    assert live.path_binary_ffmpeg == _MANAGED, "a running download just lost the ffmpeg binary it was built with"
    assert live.extract_flac is False, "the in-memory force-off was overwritten under a running download"
    assert live.video_convert_mp4 is False


def test_two_saves_at_once_cannot_strand_a_copy():
    """Saves fire from the GUI thread, from download workers (a share's first
    landing) and from the keep-warm daemon. Two overlapping swaps that restored
    each other's copy would leave the singleton holding a sanitised one for
    good, which is the original bug by another route."""
    import threading

    stub = _bridge()
    live = stub.settings.data
    barrier = threading.Barrier(4)

    def _save():
        barrier.wait()
        stub._save_settings()

    threads = [threading.Thread(target=_save) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert stub.settings.data is live
    assert live.path_binary_ffmpeg == _MANAGED
    assert len(stub.settings.saved) == 4
    assert all(w["path_binary_ffmpeg"] == "" for w in stub.settings.saved)


def test_no_bare_settings_save_outside_the_guarded_helper():
    """The audit guard: a newly added bare save silently re-opens this bug.

    Exactly five call sites of ``self.settings.save()`` are allowed: inside
    ``_save_settings`` itself; inside ``applySettings``, which does the restores
    explicitly because it must compute ``ffmpeg_source`` from the restored value
    before saving; inside ``_apply_first_run_defaults``, which runs from
    ``__init__`` before ffmpeg is resolved, so nothing is injected yet; and two
    more in ``__init__`` under the same nothing-injected-yet reasoning: the
    video-template migration, and the one-time video_download force-off.
    """
    source = inspect.getsource(WavesBridge)
    bare = len(re.findall(r"self\.settings\.save\(\)", source))
    assert bare == 5, (
        f"found {bare} bare self.settings.save() calls, expected 5 "
        "(_save_settings, applySettings, _apply_first_run_defaults, and __init__'s "
        "video-template migration + video_download force-off). Route new saves "
        "through _save_settings, or this writes the transient ffmpeg flags and "
        "path to disk."
    )

    for name in ("_save_settings", "applySettings", "_apply_first_run_defaults", "__init__"):
        method_src = inspect.getsource(getattr(WavesBridge, name))
        assert "self.settings.save()" in method_src, f"{name} no longer saves; update this guard"


def _apply_bridge(save_hook=None):
    """A stub carrying the REAL ``applySettings`` (and ``_save_settings``) over a
    real dataclass, in the same ffmpeg-missing state ``_bridge`` builds: the
    user's real preferences remembered, both flags forced off in memory, and the
    managed path injected.

    ``save_hook`` runs at the top of every ``settings.save()``. That is the seam
    the two tests below use to place one writer inside the other's window.
    """
    stub = _Stub()

    class _Cfg:
        data = CfgSettings()
        saved: ClassVar[list[dict]] = []

        def save(self):
            if save_hook is not None:
                save_hook()
            self.saved.append(
                {
                    "extract_flac": self.data.extract_flac,
                    "video_convert_mp4": self.data.video_convert_mp4,
                    "path_binary_ffmpeg": self.data.path_binary_ffmpeg,
                }
            )

    stub.settings = _Cfg()
    stub._ffmpeg_flag_prefs = {"extract_flac": True, "video_convert_mp4": True}
    stub._ffmpeg_user_path = ""
    stub.settings.data.extract_flac = False
    stub.settings.data.video_convert_mp4 = False
    stub.settings.data.path_binary_ffmpeg = _MANAGED

    for name in (
        "_restore_ffmpeg_flags",
        "_restore_ffmpeg_path",
        "_ffmpeg_source_label",
        "_user_ffmpeg_path",
        "_waves_pref_bool",
        "_save_settings",
        "applySettings",
    ):
        setattr(stub, name, _bind(stub, name))
    stub._settings_save_lock = Lock()

    class _Signal:
        def __init__(self):
            self.emits: list = []

        def emit(self, *a):
            self.emits.append(a)

    stub.settingsPersistedExternally = _Signal()
    stub.librarySourceChanged = _Signal()
    stub.ownershipChanged = _Signal()
    stub.confirmCategoryDlChanged = _Signal()
    stub.ffmpegStatusChanged = _Signal()
    # applySettings reads these three to decide whether a library setting moved.
    stub._waves_prefs = {"library_enabled": False, "library_source": "", "library_folder": ""}
    stub._ffmpeg = type("_F", (), {"is_installed": staticmethod(lambda: True)})()
    stub._library_root = lambda: None
    stub._logged_in = False  # keeps _init_download out of it
    stub._set_status = lambda *a, **k: None
    return stub


def test_apply_settings_waits_for_a_worker_save_before_it_restores():
    """applySettings does the ffmpeg restores explicitly rather than through
    _save_settings, so it has to take the same lock. Held elsewhere, it must
    wait: its restore and its write are separate statements, and a worker save
    completing between them re-borrows the managed path (its finally puts the
    borrowed value back), which this write would then serialise."""
    import threading

    wrote = threading.Event()
    stub = _apply_bridge(save_hook=wrote.set)

    stub._settings_save_lock.acquire()
    t = threading.Thread(target=lambda: stub.applySettings({}))
    t.start()
    try:
        assert not wrote.wait(0.3), (
            "applySettings wrote the settings file while _settings_save_lock was "
            "held: its restore-and-save region is not under the lock"
        )
    finally:
        stub._settings_save_lock.release()
    t.join(5)
    assert not t.is_alive(), "applySettings never finished after the lock was released"

    written = _last_save(stub)
    assert written["path_binary_ffmpeg"] == "", "a machine path (with the account name) was persisted"
    assert written["extract_flac"] is True
    assert written["video_convert_mp4"] is True


def test_a_worker_write_back_cannot_reach_the_apply_settings_write():
    """THE LEAK, forced end to end. _save_settings borrows the live values for
    the length of one write and puts them back in a finally. Order it worker
    borrow, worker write, GUI restore, worker put-back, GUI write and the GUI's
    save serialises the managed ffmpeg path: an absolute path carrying the
    account name, into the file a diagnostics bundle ships, with both ffmpeg
    flags written False against a real preference of True.

    Both writers are real. applySettings is a GUI-thread slot; _save_settings
    runs on download workers (a share's first landing, a folder auto-heal) and on
    the keep-warm daemon."""
    import threading

    worker_in_write = threading.Event()
    gui_restored = threading.Event()
    worker_wrote_back = threading.Event()

    def _park():
        # Only the worker's own write parks here; the GUI's write comes later.
        if not worker_in_write.is_set():
            worker_in_write.set()
            gui_restored.wait(0.12)

    stub = _apply_bridge(save_hook=_park)
    live = stub.settings.data

    def _worker():
        stub._save_settings()
        worker_wrote_back.set()

    def _label_seam():
        # The real applySettings calls _ffmpeg_source_label between the restore
        # and the save, so standing here is standing in the window.
        gui_restored.set()
        worker_wrote_back.wait(0.12)
        return "managed"

    stub._ffmpeg_source_label = _label_seam

    w = threading.Thread(target=_worker)
    w.start()
    assert worker_in_write.wait(5), "the worker save never reached its write"
    g = threading.Thread(target=lambda: stub.applySettings({}))
    g.start()
    for t in (w, g):
        t.join(10)
        assert not t.is_alive(), "a writer deadlocked"

    assert len(stub.settings.saved) == 2, "both writes should have happened"
    for written in stub.settings.saved:
        assert written["path_binary_ffmpeg"] == "", (
            "a worker's borrowed ffmpeg path reached the settings file: that path is "
            "absolute and carries the account name, and a diagnostics bundle ships it"
        )
        assert written["extract_flac"] is True, "the user's real FLAC preference was written False"
        assert written["video_convert_mp4"] is True
    # applySettings leaves the restored value in memory on purpose and re-injects
    # via _init_download when logged in (this stub is not), so the user's value is
    # what should be standing here. The managed path standing here instead would
    # mean the worker's put-back landed after the GUI's restore, which is the
    # ordering that produced the leak.
    assert live.path_binary_ffmpeg == "", "the worker's put-back landed inside the apply window"
