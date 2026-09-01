"""The ambient wave loop plays from a local cached copy, never the install volume.

THE COST THIS FENCES OFF
------------------------
The boot video streamed straight off wherever the app is installed, which can
be a network share or a slow disk. During launch the app is already reading
its config, art cache and databases from that same volume, and the starved
decoder showed up as the water stuttering under the wordmark (probe:
presentation gaps up to 300 ms with the GUI thread quiet). motionVideoUrl
serves a local copy under the config folder when one exists, hands out the
bundled asset otherwise, and stages the copy for the next launch AFTER boot
settles so the staging itself never adds contention.

Tested with the method-bound stub pattern (no display, no live bridge).
"""

from __future__ import annotations

from conftest import _InlinePool

from waves.waves_ui import backend as backend_mod
from waves.waves_ui.backend import WavesBridge


def _stub(tmp_path, src_path):
    class _S:
        pass

    s = _S()
    s._motion_video_src = str(src_path)
    s.settings = type("_Cfg", (), {"file_path": str(tmp_path / "config" / "settings.json")})()
    s.threadpool = _InlinePool()
    return s


def _url(stub):
    return WavesBridge.motionVideoUrl(stub)


def _immediate_single_shot(monkeypatch):
    # The copy is deliberately deferred past boot (QTimer.singleShot); run it
    # inline here so the test can watch the staging land.
    monkeypatch.setattr(backend_mod.QtCore.QTimer, "singleShot", staticmethod(lambda _ms, fn: fn()))


def test_first_ask_serves_the_bundle_and_stages_a_local_copy(tmp_path, monkeypatch):
    _immediate_single_shot(monkeypatch)
    src = tmp_path / "bundle" / "wave_loop.mp4"
    src.parent.mkdir()
    src.write_bytes(b"waves" * 100)
    s = _stub(tmp_path, src)

    first = _url(s)
    assert first.endswith("bundle/wave_loop.mp4"), "the first launch must play the bundled asset"

    local = tmp_path / "config" / "motion_bg" / f"wave_loop_{src.stat().st_size}.mp4"
    assert local.is_file(), "the local copy was not staged"
    assert local.read_bytes() == src.read_bytes()
    assert _url(s).endswith(f"motion_bg/wave_loop_{src.stat().st_size}.mp4"), "later launches must play the local copy"


def test_a_changed_asset_replaces_the_copy_and_sweeps_the_stale_one(tmp_path, monkeypatch):
    _immediate_single_shot(monkeypatch)
    src = tmp_path / "bundle" / "wave_loop.mp4"
    src.parent.mkdir()
    src.write_bytes(b"new-loop-bytes")
    s = _stub(tmp_path, src)
    cache_dir = tmp_path / "config" / "motion_bg"
    cache_dir.mkdir(parents=True)
    stale = cache_dir / "wave_loop_12345.mp4"
    stale.write_bytes(b"old")

    _url(s)
    assert (cache_dir / f"wave_loop_{src.stat().st_size}.mp4").is_file()
    assert not stale.exists(), "the previous asset's copy must not accumulate"


def test_no_source_configured_returns_empty(tmp_path):
    s = _stub(tmp_path, "")
    s._motion_video_src = ""
    assert _url(s) == ""


def test_a_missing_asset_is_handed_to_the_player_untouched(tmp_path):
    # The player's own error path hides the video; the slot must not raise or
    # stage anything.
    s = _stub(tmp_path, tmp_path / "bundle" / "gone.mp4")
    assert _url(s).endswith("bundle/gone.mp4")
    assert not (tmp_path / "config" / "motion_bg").exists()


def test_the_staging_never_runs_on_the_calling_thread_by_default(tmp_path):
    # Without the timer monkeypatch the slot returns before any copy work: the
    # deferral is the contract (boot must not pay for the staging read).
    src = tmp_path / "bundle" / "wave_loop.mp4"
    src.parent.mkdir()
    src.write_bytes(b"waves")
    s = _stub(tmp_path, src)
    assert _url(s).endswith("bundle/wave_loop.mp4")
    assert not (tmp_path / "config" / "motion_bg").exists(), "the copy must be deferred, not inline"
