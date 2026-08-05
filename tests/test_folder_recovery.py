"""Unreachable-folder recovery watch: the app notices a returned drive itself.

A user with a network drive had to re-pick the folder via Browse every time
the share dropped and came back: nothing re-checked reachability until the
next click. Now a failed gate starts a watch (10s re-probe backbone, plus a
/Volumes watcher on macOS) that resumes the held downloads and dismisses the
gate dialog the moment the folder answers again.

Hermetic: the recovery methods are borrowed unbound onto a plain host.
"""

from __future__ import annotations

import pathlib
import time
from threading import Lock
from types import SimpleNamespace

from tidaler.waves_ui.backend import WavesBridge

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAIN_QML = (ROOT / "tidaler" / "waves_ui" / "qml" / "Main.qml").read_text()
SETTINGS_QML = (ROOT / "tidaler" / "waves_ui" / "qml" / "SettingsPage.qml").read_text()


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args)


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


class _Timer:
    def __init__(self):
        self.running = False

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class RecoveryHost:
    _BASE_OK_TTL_SEC = WavesBridge._BASE_OK_TTL_SEC
    _WARMUP_DIALOG_DELAY_SEC = WavesBridge._WARMUP_DIALOG_DELAY_SEC
    _keepwarm_tick = WavesBridge._keepwarm_tick
    _gate_reachability = WavesBridge._gate_reachability
    _note_download_base_ok = WavesBridge._note_download_base_ok
    _downloads_running = WavesBridge._downloads_running
    _stash_pending_download = WavesBridge._stash_pending_download
    _run_pending_downloads = WavesBridge._run_pending_downloads
    _recovery_probe = WavesBridge._recovery_probe
    _on_folder_recovered = WavesBridge._on_folder_recovered
    # Proof of life records the share's origin (real method, real state: no
    # mount to statfs here, so it stays a quiet no-op in these tests).
    _remember_share_origin = WavesBridge._remember_share_origin
    # A watch that has run past the wedge window force-remounts the share;
    # real method + real state (no origins recorded here, so it's a no-op,
    # and _recovery_started is stamped fresh so the window never elapses).
    _remount_download_share = WavesBridge._remount_download_share
    _REMOUNT_COOLDOWN_SEC = WavesBridge._REMOUNT_COOLDOWN_SEC
    _WEDGE_FORCE_SEC = WavesBridge._WEDGE_FORCE_SEC
    _save_settings = WavesBridge._save_settings
    _restore_ffmpeg_flags = WavesBridge._restore_ffmpeg_flags
    _restore_ffmpeg_path = WavesBridge._restore_ffmpeg_path

    def __init__(self, base: str = "/Volumes/Share/Music") -> None:
        self.saved = 0

        def save() -> None:
            self.saved += 1

        self.settings = SimpleNamespace(
            data=SimpleNamespace(download_base_path=base, path_binary_ffmpeg="", network_mount_origins={}),
            save=save,
        )
        self._share_origin_noted: set = set()
        self._remount_lock = Lock()
        self._remount_last = -1e9
        self._recovery_started = time.monotonic()
        self._ffmpeg_flag_prefs: dict = {}
        self._ffmpeg_user_path = ""
        self._base_ok = ("", 0.0)
        self._pending_downloads: list = []
        self._pending_lock = Lock()
        self._queue: list[dict] = []
        self._recovery_poll = _Timer()
        self._recovery_inflight = False
        self._recovery_dialog_shown = True
        self._recovery_dialog_deadline = 0.0
        self.threadpool = _InlinePool()
        self.statuses: list[str] = []
        self.downloadFolderUnreachable = _Signal()
        self.downloadFolderRecovered = _Signal()
        self._recoveryWatchWanted = _Signal()
        self.settingsPersistedExternally = SimpleNamespace(emit=lambda *a: None)

    def _set_status(self, text: str) -> None:
        self.statuses.append(text)


def _probe(verdict: str, live: str | None = None):
    def fake(self, timeout_s: float = 8.0):
        return (verdict, live if live is not None else self.settings.data.download_base_path)

    return fake


def test_failed_gate_requests_the_recovery_watch():
    host = RecoveryHost()
    RecoveryHost._probe_download_base = _probe("dead")
    try:
        assert host._gate_reachability(lambda: None, "a1") is False
    finally:
        del RecoveryHost._probe_download_base
    assert host._recoveryWatchWanted.emits == [()]


def test_probe_with_nothing_held_stops_the_watch():
    host = RecoveryHost()
    host._recovery_poll.running = True
    host._recovery_probe()
    assert host._recovery_poll.running is False
    assert host.downloadFolderRecovered.emits == []


def test_probe_ok_resumes_and_notes_liveness():
    host = RecoveryHost()
    host._pending_downloads = [("a1", lambda: None)]
    RecoveryHost._probe_download_base = _probe("ok")
    try:
        host._recovery_probe()
    finally:
        del RecoveryHost._probe_download_base
    assert host.downloadFolderRecovered.emits == [()]
    assert host._base_ok[0] == host.settings.data.download_base_path
    assert time.monotonic() - host._base_ok[1] < 5
    assert host._recovery_inflight is False


def test_probe_healed_persists_the_live_mount():
    host = RecoveryHost()
    host._pending_downloads = [("a1", lambda: None)]
    RecoveryHost._probe_download_base = _probe("healed", "/Volumes/Share 1/Music")
    try:
        host._recovery_probe()
    finally:
        del RecoveryHost._probe_download_base
    assert host.settings.data.download_base_path == "/Volumes/Share 1/Music"
    assert host.saved == 1
    assert host.downloadFolderRecovered.emits == [()]


def test_probe_still_dead_stays_quiet_and_keeps_watching():
    host = RecoveryHost()
    host._pending_downloads = [("a1", lambda: None)]
    host._recovery_poll.running = True
    RecoveryHost._probe_download_base = _probe("dead")
    try:
        host._recovery_probe()
    finally:
        del RecoveryHost._probe_download_base
    assert host.downloadFolderRecovered.emits == []
    assert host._recovery_poll.running is True
    assert host._recovery_inflight is False


def test_overlapping_probes_collapse_to_one():
    host = RecoveryHost()
    host._pending_downloads = [("a1", lambda: None)]
    host._recovery_inflight = True
    calls: list = []
    RecoveryHost._probe_download_base = lambda self, timeout_s=8.0: calls.append(1) or ("ok", "")
    try:
        host._recovery_probe()
    finally:
        del RecoveryHost._probe_download_base
    assert calls == []


def test_recovered_replays_every_held_download():
    host = RecoveryHost()
    ran: list[str] = []
    host._pending_downloads = [("a1", lambda: ran.append("a1")), ("b2", lambda: ran.append("b2"))]
    host._recovery_poll.running = True
    host._on_folder_recovered()
    assert ran == ["a1", "b2"]
    assert host._pending_downloads == []
    assert host._recovery_poll.running is False


def test_warmup_stays_quiet_before_the_deadline():
    """A cold share that has not answered yet, still inside the warm-up
    window: keep watching, raise nothing."""
    host = RecoveryHost()
    host._pending_downloads = [("a1", lambda: None)]
    host._recovery_dialog_shown = False
    host._recovery_dialog_deadline = time.monotonic() + 60
    RecoveryHost._probe_download_base = _probe("timeout")
    try:
        host._recovery_probe()
    finally:
        del RecoveryHost._probe_download_base
    assert host.downloadFolderUnreachable.emits == []
    assert host._recovery_dialog_shown is False


def test_warmup_deadline_raises_the_dialog_once():
    host = RecoveryHost()
    host._pending_downloads = [("a1", lambda: None)]
    host._recovery_dialog_shown = False
    host._recovery_dialog_deadline = time.monotonic() - 1
    RecoveryHost._probe_download_base = _probe("timeout")
    try:
        host._recovery_probe()
        host._recovery_probe()
    finally:
        del RecoveryHost._probe_download_base
    assert host.downloadFolderUnreachable.emits == [(host.settings.data.download_base_path,)]
    assert host._recovery_dialog_shown is True
    assert "Download folder isn't reachable" in host.statuses


def test_keepwarm_touches_only_network_volumes(monkeypatch):
    import tidaler.waves_ui.backend as backend_mod

    listed: list[str] = []
    monkeypatch.setattr(backend_mod.os, "listdir", listed.append)

    class _InlineThread:
        def __init__(self, target, daemon):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(backend_mod, "Thread", _InlineThread)
    host = RecoveryHost(base="/Users/someone/Music")
    host._keepwarm_inflight = False
    host._keepwarm_tick()
    assert listed == []
    nas = RecoveryHost(base="/Volumes/Share/Music")
    nas._keepwarm_inflight = False
    nas._keepwarm_tick()
    assert listed == ["/Volumes/Share/Music"]
    assert nas._keepwarm_inflight is False


def test_keepwarm_collapses_while_a_touch_is_hung(monkeypatch):
    import tidaler.waves_ui.backend as backend_mod

    started: list = []
    monkeypatch.setattr(
        backend_mod, "Thread", lambda target, daemon: started.append(target) or SimpleNamespace(start=lambda: None)
    )
    host = RecoveryHost(base="/Volumes/Share/Music")
    host._keepwarm_inflight = True
    host._keepwarm_tick()
    assert started == []


def test_qml_gate_dialog_dismisses_on_recovery():
    assert "function onDownloadFolderRecovered()" in MAIN_QML
    assert "resumes on its own" in MAIN_QML


def test_browse_button_never_fades():
    """The Browse button used to drop to 40% once a path was set, reading as
    disabled even though re-picking is always a legitimate action."""
    assert "needsValue" not in SETTINGS_QML
