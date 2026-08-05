"""A network share macOS quietly ejected gets mounted back, not just watched.

The saga this pins down: the saved download folder lives on an SMB share,
macOS silently ejects the volume (sleep, network blip), and every probe the
app runs then checks a mount point that cannot come back by itself. Finder
"fixes" it because navigating to the share IS a mount request. These tests
prove the app now makes that same request: the share's origin is recorded
while healthy, and a dead probe whose volume is gone triggers a remount and
a second probe.
"""

from __future__ import annotations

import sys
import types
from threading import Lock

import tidaler.waves_ui.backend as backend_mod
from tidaler.waves_ui import netmount
from tidaler.waves_ui.backend import WavesBridge


def _bridge(base_path="", origins=None):
    b = WavesBridge.__new__(WavesBridge)
    b.settings = types.SimpleNamespace(
        data=types.SimpleNamespace(
            download_base_path=base_path,
            network_mount_origins=dict(origins or {}),
        )
    )
    b._share_origin_noted = set()
    b._remount_lock = Lock()
    b._remount_last = -1e9
    b._saved = 0
    b._save_settings = lambda: setattr(b, "_saved", b._saved + 1)
    return b


# ---- origin_url: statfs from-name -> mountable URL --------------------------


def test_origin_url_builds_smb_url():
    assert netmount.origin_url("smbfs", "//carol@nas/Media") == "smb://carol@nas/Media"


def test_origin_url_percent_encodes_share_segments():
    assert netmount.origin_url("smbfs", "//nas/My Music/sub dir") == "smb://nas/My%20Music/sub%20dir"


def test_origin_url_rejects_local_disks_and_junk():
    assert netmount.origin_url("apfs", "/dev/disk3s1") == ""
    assert netmount.origin_url("smbfs", "not-a-share") == ""
    assert netmount.origin_url("smbfs", "//") == ""


def test_mount_origin_answers_for_the_root_volume():
    if sys.platform != "darwin":
        return  # statfs layout is macOS-specific; elsewhere the helper no-ops
    fstype, from_name = netmount.mount_origin("/")
    assert fstype != ""
    assert from_name != ""


# ---- recording the origin while the share is healthy ------------------------


def test_origin_recorded_on_proof_of_life(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(
        backend_mod.netmount, "mount_origin", lambda p: (calls.append(p), ("smbfs", "//u@nas/Media"))[1]
    )
    secrets = []
    monkeypatch.setattr(backend_mod.diagnostics, "register_secret", lambda v, tag="": secrets.append(v))
    b = _bridge()
    b._remember_share_origin("/Volumes/Media/Music/Artist")
    assert calls == ["/Volumes/Media"]
    assert b.settings.data.network_mount_origins == {"/Volumes/Media": "smb://u@nas/Media"}
    assert b._saved == 1
    assert "smb://u@nas/Media" in secrets and "//u@nas/Media" in secrets


def test_origin_statfs_runs_once_per_volume_per_session(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []
    monkeypatch.setattr(
        backend_mod.netmount, "mount_origin", lambda p: (calls.append(p), ("smbfs", "//u@nas/Media"))[1]
    )
    monkeypatch.setattr(backend_mod.diagnostics, "register_secret", lambda v, tag="": None)
    b = _bridge()
    b._remember_share_origin("/Volumes/Media/Music")
    b._remember_share_origin("/Volumes/Media/Other")
    assert len(calls) == 1


def test_local_paths_record_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        backend_mod.netmount, "mount_origin", lambda p: (_ for _ in ()).throw(AssertionError("statfs on a local path"))
    )
    b = _bridge()
    b._remember_share_origin("/Users/someone/Music")
    assert b.settings.data.network_mount_origins == {}


def test_local_disk_origin_is_not_stored(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.netmount, "mount_origin", lambda p: ("apfs", "/dev/disk3s1"))
    b = _bridge()
    b._remember_share_origin("/Volumes/T7/Music")
    assert b.settings.data.network_mount_origins == {}
    assert b._saved == 0


class _InlineThread:
    """Runs the target on start(), so the keep-warm touch happens in-test."""

    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def test_keepwarm_touch_records_the_origin_without_a_download(monkeypatch):
    """Livetest scar (2026-08-04): the origin was only recorded when a
    download succeeded, so a share ejected before the session's first
    download could never be mounted back. Having the app open while the
    share is mounted must be enough: the keep-warm touch records it."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: [])
    monkeypatch.setattr(backend_mod.netmount, "mount_origin", lambda p: ("smbfs", "//u@nas/Media"))
    monkeypatch.setattr(backend_mod.diagnostics, "register_secret", lambda v, tag="": None)
    monkeypatch.setattr(backend_mod, "Thread", _InlineThread)
    b = _bridge(base_path="/Volumes/Media/Music")
    b._keepwarm_inflight = False
    b._keepwarm_tick()
    assert b.settings.data.network_mount_origins == {"/Volumes/Media": "smb://u@nas/Media"}
    assert b._keepwarm_inflight is False


def test_keepwarm_touch_on_a_dead_share_records_nothing(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def dead(p):
        raise OSError("share is gone")

    monkeypatch.setattr(backend_mod.os, "listdir", dead)
    monkeypatch.setattr(
        backend_mod.netmount, "mount_origin", lambda p: (_ for _ in ()).throw(AssertionError("statfs on a dead share"))
    )
    monkeypatch.setattr(backend_mod, "Thread", _InlineThread)
    b = _bridge(base_path="/Volumes/Media/Music")
    b._keepwarm_inflight = False
    b._keepwarm_tick()
    assert b.settings.data.network_mount_origins == {}
    assert b._keepwarm_inflight is False


# ---- the remount decision ----------------------------------------------------


def test_gone_volume_with_recorded_origin_remounts(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: ["Macintosh HD", "T7"])
    mounted = []
    monkeypatch.setattr(backend_mod.netmount, "remount", lambda url, timeout_s=20.0: (mounted.append(url), True)[1])
    b = _bridge(origins={"/Volumes/Media": "smb://nas/Media"})
    assert b._remount_download_share("/Volumes/Media/Music") is True
    assert mounted == ["smb://nas/Media"]


def test_present_volume_is_left_alone(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: ["Media"])
    monkeypatch.setattr(
        backend_mod.netmount,
        "remount",
        lambda url, timeout_s=20.0: (_ for _ in ()).throw(AssertionError("remounted over a live mount")),
    )
    b = _bridge(origins={"/Volumes/Media": "smb://nas/Media"})
    assert b._remount_download_share("/Volumes/Media/Music") is False


def test_remount_respects_the_cooldown(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: [])
    mounted = []
    monkeypatch.setattr(backend_mod.netmount, "remount", lambda url, timeout_s=20.0: (mounted.append(url), False)[1])
    b = _bridge(origins={"/Volumes/Media": "smb://nas/Media"})
    assert b._remount_download_share("/Volumes/Media/Music") is False
    assert b._remount_download_share("/Volumes/Media/Music") is False
    assert len(mounted) == 1  # the second call sat out the cooldown


def test_suffixed_twin_reuses_the_recorded_origin(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: [])
    mounted = []
    monkeypatch.setattr(backend_mod.netmount, "remount", lambda url, timeout_s=20.0: (mounted.append(url), True)[1])
    # The base last healed onto "Media 1"; the origin was recorded under the
    # plain name. Same stem, same share: the URL still applies.
    b = _bridge(origins={"/Volumes/Media": "smb://nas/Media"})
    assert b._remount_download_share("/Volumes/Media 1/Music") is True
    assert mounted == ["smb://nas/Media"]


def test_no_recorded_origin_means_no_attempt(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: [])
    monkeypatch.setattr(
        backend_mod.netmount,
        "remount",
        lambda url, timeout_s=20.0: (_ for _ in ()).throw(AssertionError("mounted without an origin")),
    )
    b = _bridge(origins={})
    assert b._remount_download_share("/Volumes/Media/Music") is False


def test_wedged_volume_is_forced_off_then_mounted_back(monkeypatch):
    """A mount point that exists but answers nothing (zombie SMB mount) is
    normally left alone; with wedged=True it is force-unmounted first and
    the share mounted back, the by-hand remedy for a hung network mount."""
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: ["Media"])
    forced = []

    def fake_run(argv, capture_output=True, timeout=15):
        forced.append(argv)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(backend_mod.subprocess, "run", fake_run)
    mounted = []
    monkeypatch.setattr(backend_mod.netmount, "remount", lambda url, timeout_s=20.0: (mounted.append(url), True)[1])
    b = _bridge(origins={"/Volumes/Media": "smb://nas/Media"})
    assert b._remount_download_share("/Volumes/Media/Music", wedged=True) is True
    assert forced and forced[0][:3] == ["/usr/sbin/diskutil", "unmount", "force"]
    assert mounted == ["smb://nas/Media"]


def test_wedged_unmount_declined_means_no_mount_on_top(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(backend_mod.os, "listdir", lambda p: ["Media"])
    monkeypatch.setattr(backend_mod.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=1))
    monkeypatch.setattr(
        backend_mod.netmount,
        "remount",
        lambda url, timeout_s=20.0: (_ for _ in ()).throw(AssertionError("mounted on top of a stuck zombie")),
    )
    b = _bridge(origins={"/Volumes/Media": "smb://nas/Media"})
    assert b._remount_download_share("/Volumes/Media/Music", wedged=True) is False


# ---- a download that fails because the folder died mid-flight ----------------


def _midflight_bridge(monkeypatch, verdict, remounted=False):
    b = _bridge(base_path="/Volumes/Media/Music")
    b._pending_downloads = []
    b._pending_lock = Lock()
    b._queue = [{"qid": 7, "status": "running"}]
    b._queue_lock = Lock()
    b._job_tracks = {7: {}}
    b._emitted_queue = 0
    b._emit_queue = lambda: setattr(b, "_emitted_queue", b._emitted_queue + 1)
    b.statuses = []
    b._set_status = b.statuses.append
    b._states = []
    b.downloadState = types.SimpleNamespace(emit=lambda mid, st: b._states.append((mid, st)))
    b._recovered = 0
    b.downloadFolderRecovered = types.SimpleNamespace(emit=lambda: setattr(b, "_recovered", b._recovered + 1))
    b._watch_requests = 0
    b._recoveryWatchWanted = types.SimpleNamespace(emit=lambda: setattr(b, "_watch_requests", b._watch_requests + 1))
    b._recovery_dialog_shown = True
    b._recovery_dialog_deadline = 0.0

    def probe(timeout_s=8.0):
        b._last_probe_remounted = remounted
        return (verdict, b.settings.data.download_base_path)

    b._probe_download_base = probe
    return b


def test_track_failure_with_a_healthy_folder_stays_failed(monkeypatch):
    b = _midflight_bridge(monkeypatch, "ok")
    assert b._download_failed_with_folder(lambda: None, "m1", 7, "Song") is False
    assert b._queue and b._pending_downloads == []


def test_folder_death_midflight_is_held_and_watched(monkeypatch):
    b = _midflight_bridge(monkeypatch, "dead")
    assert b._download_failed_with_folder(lambda: None, "m1", 7, "Song") is True
    assert [m for m, _fn in b._pending_downloads] == ["m1"]
    assert b._queue == [] and b._job_tracks == {}  # row withdrawn
    assert ("m1", "") in b._states  # button reset, not red
    assert b._watch_requests == 1
    assert b._recovery_dialog_shown is False  # dialog deferred, not dead


def test_folder_remounted_midflight_replays_immediately(monkeypatch):
    b = _midflight_bridge(monkeypatch, "ok", remounted=True)
    assert b._download_failed_with_folder(lambda: None, "m1", 7, "Song") is True
    assert [m for m, _fn in b._pending_downloads] == ["m1"]
    assert b._recovered == 1  # replay fires now
    assert b._watch_requests == 0


# ---- the probe's second chance ----------------------------------------------


def test_dead_probe_remounts_and_probes_again(monkeypatch):
    b = _bridge(base_path="/Volumes/Media/Music")
    state = {"mounted": False}
    monkeypatch.setattr(
        WavesBridge,
        "_probe_folder_verdict",
        staticmethod(lambda path, volumes_root="/Volumes": ("ok", path) if state["mounted"] else ("dead", path)),
    )
    b._remount_download_share = lambda path: state.update(mounted=True) or True
    assert b._probe_download_base(timeout_s=2.0) == ("ok", "/Volumes/Media/Music")


def test_failed_remount_keeps_the_dead_verdict(monkeypatch):
    b = _bridge(base_path="/Volumes/Media/Music")
    probes = []
    monkeypatch.setattr(
        WavesBridge,
        "_probe_folder_verdict",
        staticmethod(lambda path, volumes_root="/Volumes": (probes.append(1), ("dead", path))[1]),
    )
    b._remount_download_share = lambda path: False
    assert b._probe_download_base(timeout_s=2.0) == ("dead", "/Volumes/Media/Music")
    assert len(probes) == 1  # no remount, no second probe


# ---- Browse fallback ---------------------------------------------------------


def test_existing_folder_returns_the_path_itself(tmp_path):
    b = _bridge()
    assert b.existingFolder(str(tmp_path)) == str(tmp_path)


def test_existing_folder_walks_up_to_a_live_ancestor(tmp_path):
    b = _bridge()
    assert b.existingFolder(str(tmp_path / "gone" / "deeper")) == str(tmp_path)


def test_existing_folder_empty_input_stays_empty():
    b = _bridge()
    assert b.existingFolder("") == ""
