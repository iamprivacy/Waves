"""Reachability-gate liveness + held-download replay + makedirs retry.

The 2026-07-13 diagnostics bundle showed the old gate misreading a busy SMB
share as dead: the 4s write probe timed out behind saturated download I/O and
every click bounced into the "isn't reachable" retry dialog (11 of 20 queue
ids that session never started). These tests pin the new behavior:

* a recent real write to the base skips the probe entirely,
* a probe timeout while downloads are running reads as busy, not dead,
* a probe timeout with nothing running still raises the dialog,
* held downloads accumulate per item and ALL replay on "Try again",
* destination-folder creation retries transient errors instead of failing
  the whole album on one spurious EACCES.

No Qt: the gate methods are borrowed unbound onto a plain host object.
"""

from __future__ import annotations

import time
from threading import Lock
from types import SimpleNamespace

import pytest

from tidaler.download import Download
from tidaler.waves_ui.backend import WavesBridge


class GateHost:
    """Just enough bridge surface for the gate methods to run on."""

    _BASE_OK_TTL_SEC = WavesBridge._BASE_OK_TTL_SEC
    _gate_reachability = WavesBridge._gate_reachability
    _note_download_base_ok = WavesBridge._note_download_base_ok
    _downloads_running = WavesBridge._downloads_running
    _stash_pending_download = WavesBridge._stash_pending_download
    _run_pending_downloads = WavesBridge._run_pending_downloads
    _probe_download_base = WavesBridge._probe_download_base
    _probe_folder_verdict = staticmethod(WavesBridge._probe_folder_verdict)

    def __init__(self, base: str = "/Volumes/Share/Music") -> None:
        self.saved = 0

        def save() -> None:
            self.saved += 1

        self.settings = SimpleNamespace(data=SimpleNamespace(download_base_path=base), save=save)
        self._base_ok = ("", 0.0)
        self._pending_downloads = []
        self._pending_lock = Lock()
        self._queue: list[dict] = []
        self.statuses: list[str] = []
        self.unreachable_emits: list[str] = []
        self.downloadFolderUnreachable = SimpleNamespace(emit=self.unreachable_emits.append)

    def _set_status(self, text: str) -> None:
        self.statuses.append(text)


def _probe_stub(host: GateHost, verdict: str, live: str | None = None, calls: list | None = None):
    def fake(timeout_s: float = 8.0):
        if calls is not None:
            calls.append(verdict)
        return (verdict, live if live is not None else host.settings.data.download_base_path)

    return fake


# ---- liveness window -------------------------------------------------------


def test_recent_base_write_skips_the_probe():
    host = GateHost()
    host._note_download_base_ok()
    calls: list = []
    host._probe_download_base = _probe_stub(host, "dead", calls=calls)
    assert host._gate_reachability(lambda: None, "m1") is True
    assert calls == [], "a fresh liveness mark must answer without probing"


def test_stale_liveness_mark_probes_again():
    host = GateHost()
    host._base_ok = (host.settings.data.download_base_path, time.monotonic() - host._BASE_OK_TTL_SEC - 1)
    calls: list = []
    host._probe_download_base = _probe_stub(host, "ok", calls=calls)
    assert host._gate_reachability(lambda: None, "m1") is True
    assert calls == ["ok"]


def test_liveness_mark_for_another_base_path_does_not_count():
    host = GateHost()
    host._base_ok = ("/Volumes/OtherShare", time.monotonic())
    calls: list = []
    host._probe_download_base = _probe_stub(host, "ok", calls=calls)
    assert host._gate_reachability(lambda: None, "m1") is True
    assert calls == ["ok"], "changing the download folder must invalidate the old mark"


def test_successful_probe_refreshes_the_liveness_mark():
    host = GateHost()
    host._probe_download_base = _probe_stub(host, "ok")
    host._gate_reachability(lambda: None, "m1")
    path, stamp = host._base_ok
    assert path == host.settings.data.download_base_path
    assert time.monotonic() - stamp < 5


# ---- timeout is busy-vs-dead, not always dead ------------------------------


def test_probe_timeout_with_running_downloads_proceeds():
    host = GateHost()
    host._queue = [{"qid": 1, "status": "running"}]
    host._probe_download_base = _probe_stub(host, "timeout")
    assert host._gate_reachability(lambda: None, "m1") is True
    assert host.unreachable_emits == []


def test_probe_timeout_with_idle_queue_raises_the_dialog():
    host = GateHost()
    host._queue = [{"qid": 1, "status": "done"}]
    host._probe_download_base = _probe_stub(host, "timeout")
    assert host._gate_reachability(lambda: None, "m1") is False
    assert host.unreachable_emits == [host.settings.data.download_base_path]
    assert len(host._pending_downloads) == 1


def test_probe_dead_raises_the_dialog_even_while_downloads_run():
    host = GateHost()
    host._queue = [{"qid": 1, "status": "running"}]
    host._probe_download_base = _probe_stub(host, "dead")
    assert host._gate_reachability(lambda: None, "m1") is False
    assert host.unreachable_emits == [host.settings.data.download_base_path]


def test_healed_probe_follows_the_live_mount_and_marks_liveness():
    host = GateHost()
    host._probe_download_base = _probe_stub(host, "healed", live="/Volumes/Share 1/Music")
    assert host._gate_reachability(lambda: None, "m1") is True
    assert host.settings.data.download_base_path == "/Volumes/Share 1/Music"
    assert host.saved == 1
    assert host._base_ok[0] == "/Volumes/Share 1/Music"


def test_probe_deadline_reports_timeout_not_dead():
    host = GateHost()

    def slow_probe(path):
        time.sleep(0.5)
        return ("ok", path)

    host._probe_folder_verdict = slow_probe
    verdict, path = host._probe_download_base(timeout_s=0.05)
    assert verdict == "timeout"
    assert path == host.settings.data.download_base_path


# ---- held downloads: all clicks survive and replay -------------------------


def test_every_gated_click_survives_and_replays_in_order():
    host = GateHost()
    ran: list[str] = []
    host._stash_pending_download("a", lambda: ran.append("a"))
    host._stash_pending_download("b", lambda: ran.append("b"))
    host._stash_pending_download("c", lambda: ran.append("c"))
    host._run_pending_downloads()
    assert ran == ["a", "b", "c"]
    assert host._pending_downloads == []


def test_reclick_of_the_same_item_replaces_its_held_copy():
    host = GateHost()
    ran: list[str] = []
    host._stash_pending_download("a", lambda: ran.append("a-old"))
    host._stash_pending_download("b", lambda: ran.append("b"))
    host._stash_pending_download("a", lambda: ran.append("a-new"))
    host._run_pending_downloads()
    assert ran == ["b", "a-new"], "same item queues once; different items all run"


# ---- destination makedirs retries transient failures -----------------------


class _MakedirsHost:
    _FILE_OPERATION_RETRIES = Download._FILE_OPERATION_RETRIES
    _makedirs_with_retry = Download._makedirs_with_retry
    fn_logger = SimpleNamespace(debug=lambda *a, **k: None)

    @staticmethod
    def _file_operation_retry_delay(attempt: int) -> float:
        return 0.0  # no real sleeps in tests


def test_makedirs_survives_a_transient_permission_error(tmp_path, monkeypatch):
    import tidaler.download as dl_mod

    real_makedirs = dl_mod.os.makedirs
    failures = {"left": 2}

    def flaky(path, exist_ok=False):
        if failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(13, "Permission denied", str(path))
        real_makedirs(path, exist_ok=exist_ok)

    monkeypatch.setattr(dl_mod.os, "makedirs", flaky)
    target = tmp_path / "Artist" / "Album"
    _MakedirsHost()._makedirs_with_retry(target)
    assert target.is_dir()


def test_makedirs_still_raises_when_the_error_is_permanent(tmp_path, monkeypatch):
    import tidaler.download as dl_mod

    def always_denied(path, exist_ok=False):
        raise PermissionError(13, "Permission denied", str(path))

    monkeypatch.setattr(dl_mod.os, "makedirs", always_denied)
    with pytest.raises(PermissionError):
        _MakedirsHost()._makedirs_with_retry(tmp_path / "nope")
