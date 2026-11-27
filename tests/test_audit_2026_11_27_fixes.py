"""Regression tests for the 2026-11-27 audit fixes: a merged album's refused
slots, the factory reset's reach, copied dates across sections, the
MusicBrainz switch, a repeat click on a running album, the library
scanner's cancel and share recovery, a late prefs flush, and the share
mount and path checks."""

from __future__ import annotations

from threading import Event
from types import SimpleNamespace

import pytest

from tests.test_final_audit_backend_2026_09_24 import _Engine, _merge_bridge, _PlanEntry, _Signal, _track


class _FlakyEngine(_Engine):
    """Refuses every 'std' stream (a TIDAL refusal) and drops 'net' tracks on
    a dropped connection (a real failure, not a refusal)."""

    def item(self, **kw):
        media = kw["media"]
        if str(media.id).startswith("net"):
            with self._lock:
                self.fetched.append((str(media.id), kw["list_position"]))
            return False, ""
        return super().item(**kw)


# ---- a slot refused twice must not hide a real failure ----------------------
def test_refused_twice_plus_a_hard_failure_is_not_done():
    bridge = _merge_bridge({101: _track("std-x")})  # the fallback cut is refused too
    engine = _FlakyEngine()
    plan = [
        _PlanEntry(_track("std-1"), 1, 1, "101"),
        _PlanEntry(_track("net-2"), 2, 1, "102"),
        _PlanEntry(_track("dlx-3"), 3, 1, "103"),
    ]
    with pytest.raises(Exception, match="failed"):
        bridge._download_merge_plan(engine, SimpleNamespace(list_item=_Signal()), Event(), object(), "t", plan)


# ---- rescued slots are not "no longer on TIDAL" ----------------------------
def test_rescued_slots_leave_no_unavailable_count():
    own = {101: _track("dlx-1"), 102: _track("dlx-2"), 103: _track("dlx-3")}
    bridge = _merge_bridge(own)
    engine = _Engine()
    plan = [_PlanEntry(_track(f"std-{i}"), i, 1, str(100 + i)) for i in (1, 2, 3)]
    bridge._download_merge_plan(engine, SimpleNamespace(list_item=_Signal()), Event(), object(), "t", plan)
    assert len([f for f in engine.fetched if f[0].startswith("dlx")]) == 3, "all three were rescued"
    assert engine.unavailable_count == 0, f"finish line would say {engine.unavailable_count} no longer on TIDAL"


# ---- factory reset ---------------------------------------------------
from tests.test_factory_reset import _bind as _fr_bind  # noqa: E402
from tests.test_factory_reset import _run_factory_reset  # noqa: E402
from waves.waves_ui import backend as _backend  # noqa: E402


def test_a_settings_save_after_the_reset_does_not_bring_settings_json_back(tmp_path, monkeypatch):
    base = tmp_path / "cfg"
    base.mkdir()
    (base / "settings.json").write_text('{"download_base_path": "/Users/someone/Music"}')
    stub, _ = _run_factory_reset(base, monkeypatch)
    assert not (base / "settings.json").exists(), "the wipe took it"
    # A download worker saves settings after the wipe (share origin, reachability).
    target = base / "settings.json"
    stub.settings = SimpleNamespace(
        data=SimpleNamespace(to_json=lambda: '{"download_base_path": "/Users/someone/Music"}'),
        write_serialized=lambda js: target.write_text(js),
    )
    stub._config_writer = _backend._SingleFlightWriter()
    _fr_bind(stub, "_submit_settings_write")()
    stub._config_writer.flush()  # shutdown() does exactly this
    assert not target.exists(), "settings.json came back after 'Erase everything'"


@pytest.mark.parametrize(
    "name",
    ["token.json.1764200000.bak", "settings.json.1764200000.bak", "waves.json.bak", "waves.json.bak-20261127-101500"],
)
def test_rescue_copies_fall_to_the_reset(tmp_path, monkeypatch, name):
    base = tmp_path / "cfg"
    base.mkdir()
    (base / name).write_text("x")
    _run_factory_reset(base, monkeypatch)
    assert not (base / name).exists(), f"{name} survived the reset"


# ---- a new album wearing an EP's date gets its own day on the FIRST sweep --
def test_album_wearing_an_eps_date_is_lifted_on_the_first_sweep(tmp_path):
    import datetime as dt

    from tests.test_releases_backend import _Album, _fake_artist, _releases_stub, _stub
    from waves.waves_ui import backend

    def album(rid, title, released, listed):
        return _Album(
            id=rid,
            title=title,
            name=title,
            version=None,
            copyright="2026 Veil of Maya",
            artist=SimpleNamespace(id="A"),
            release_date=dt.datetime.fromisoformat(released),
            tidal_release_date=dt.datetime.fromisoformat(listed).replace(tzinfo=dt.UTC),
        )

    s = _stub(tmp_path)
    _releases_stub(
        s,
        _fake_artist(
            albums=[album("xx", "All Things Set Aside XX", "2007-01-01", "2026-11-25")],
            eps=[album("orig", "All Things Set Aside", "2007-01-01", "2026-02-17")],
        ),
    )
    plain = s._album_dict
    s._album_dict = lambda a: {**plain(a), "listed": backend._listed_date_str(a)}
    rows, _f, _l = s._artist_recent_releases("A")
    first = {r["id"]: r["listed"] for r in rows}["xx"]
    rows2, _f, _l = s._artist_recent_releases("A")
    second = {r["id"]: r["listed"] for r in rows2}["xx"]
    assert (first, second) == ("2026-11-25", "2026-11-25"), f"first sweep {first!r}, second sweep {second!r}"


# ---- MusicBrainz off hides the first-release days at once -------------------
def test_switching_musicbrainz_off_hides_stored_first_days(tmp_path):
    from tests.test_follow_store import _rel, _seed, _store
    from tests.test_listed_date import _listing
    from waves.waves_ui import backend
    from waves.waves_ui.backend import WavesBridge

    store = _store(tmp_path)
    _seed(store, releases=[])
    album = _listing([("Old Record", "2010-01-01", "2010-01-01")], artist="1")[0]
    backend._set_first_days({str(album.id): "2005-03-01"})  # the check is on
    listed = backend._listed_date_str(album)
    assert listed == "2005-03-01", "with the check on the sweep row carries the first day"
    store.record_check("1", [_rel(str(album.id), date="2010-01-01", listed=listed)])

    stub = SimpleNamespace(
        _releases=store,
        _waves_pref_bool=lambda key: False,  # the user just switched it off
        releasesChanged=_Signal(),
        _releases_rearm=lambda *_: None,
    )
    stub._first_days_reload = WavesBridge._first_days_reload.__get__(stub)
    WavesBridge._releases_musicbrainz_switched(stub, False)
    row = next(r for r in store.feed() if r["id"] == str(album.id))
    shown = row.get("listed") or row.get("date")
    assert shown == "2010-01-01", f"with the check off the feed still shows {shown!r}"


# ---- a repeat click on a RUNNING album reports running, not queued ---------
def test_a_repeat_click_on_a_running_album_reports_its_real_state():
    from threading import Lock

    from waves.waves_ui.backend import WavesBridge

    stub = SimpleNamespace(
        _logged_in=True,
        _download_gate=lambda: "go",
        _ffmpeg_gate_holds=lambda *a: False,
        _ask_quality_for=lambda *a: ("HI_RES_LOSSLESS", "HI-RES"),
        _ask_atmos_for=lambda *a: False,
        _queue_lock=Lock(),
        _queue=[
            {
                "media_id": "A",
                "type": "album",
                "status": "running",
                "template": "t",
                "askQuality": "HI_RES_LOSSLESS",
                "askAtmos": False,
            }
        ],
        downloadState=_Signal(),
    )
    WavesBridge._download(stub, SimpleNamespace(id="A"), "album", "Album", "t", True, "A")
    assert stub.downloadState.calls == [("A", "running")], f"emitted {stub.downloadState.calls}"


# ---- a canceled child's late "exit" must not crash the next job -------------
def test_a_late_exit_from_the_canceled_child_does_not_fail_the_next_job():
    import io
    import threading
    import time

    from waves.waves_ui.library_proc import LibraryWorker, WorkerFailed

    release_a = threading.Event()

    class _Out:
        """stdout of a child: yields its lines, the first only once `gate` opens."""

        def __init__(self, lines, gate=None, delay=0.0):
            self.lines, self.gate, self.delay = lines, gate, delay

        def __iter__(self):
            if self.gate is not None:
                self.gate.wait(5)  # the killed child's pipe drains late
            time.sleep(self.delay)
            yield from self.lines

        def close(self):
            pass

    class _Proc:
        def __init__(self, out):
            self.stdin, self.stdout, self.stderr = io.BytesIO(), out, io.BytesIO()
            self.dead = False

        def poll(self):
            return 0 if self.dead else None

        def kill(self):
            self.dead = True

        def wait(self, timeout=None):
            return 0

    procs = [
        _Proc(_Out([], gate=release_a)),  # child A: its EOF arrives late
        _Proc(_Out([b'{"ev":"probe_done","id":1}\n'], delay=0.6)),  # child B: answers in 0.6 s
    ]
    w = LibraryWorker(["unused"], spawn=lambda *a, **k: procs.pop(0))
    w._ensure()  # child A is running
    w.cancel()  # the user changed the library folder
    threading.Timer(0.2, release_a.set).start()  # A's reader reaches its finally after B spawned
    try:
        reply = w.run_probe({"root": "/x"}, alive=lambda: True)
    except WorkerFailed as exc:
        pytest.fail(f"job B was failed by child A's exit: {exc}; crashes={w._crashes}")
    assert reply and reply["ev"] == "probe_done"


# ---- recovery never runs after a scan that did not run ----------------------
def test_recovery_skips_a_scan_whose_root_did_not_answer(tmp_path, monkeypatch):
    from waves import library_recover
    from waves.library_index import LibraryIndex

    lib = LibraryIndex(str(tmp_path / "library.sqlite3"))
    # The cache a flagged share left: opening it set the flag (unreliable_dirs).
    lib.last_scan_partial = True
    monkeypatch.setattr(lib, "unreliable_dirs", lambda: [str(tmp_path / "share" / "Music")])
    lib.refresh(str(tmp_path / "share-that-is-gone"))  # the NAS is asleep
    assert lib.last_scan_status != "ok"
    called = []
    monkeypatch.setattr(library_recover.smb_relist, "sweep_stale", lambda *a, **k: called.append("sweep"))
    monkeypatch.setattr(library_recover.smb_relist, "relist_folders", lambda *a, **k: called.append("relist") or {})
    library_recover.recover_untrusted(lib, str(tmp_path / "share-that-is-gone"), str(tmp_path), lambda: True)
    assert (
        "relist" not in called
    ), f"partial flag still {lib.last_scan_partial}; recovery mounted the dead share: {called}"


# ---- a mount a killed relist left behind is swept even when nothing is flagged
def test_a_leftover_private_mount_is_swept_on_an_unflagged_library(tmp_path, monkeypatch):
    from waves import library_recover
    from waves.waves_ui import smb_relist

    leftover = tmp_path / smb_relist.MOUNTS_DIR_NAME / "pid-99999999"  # the killed scanner child
    leftover.mkdir(parents=True)
    unmounted = []
    monkeypatch.setattr(smb_relist, "_on_macos", lambda: True)
    monkeypatch.setattr(smb_relist, "unmount_share", lambda p: unmounted.append(p) or True)
    lib = SimpleNamespace(last_scan_partial=False)  # the user switched to a healthy library
    library_recover.recover_untrusted(lib, "/x", str(tmp_path), lambda: True)
    assert unmounted, "the hidden mount stays until some later flagged scan (or reboot)"


# ---- a late flush never lets an older snapshot land last -------------------
def test_flush_past_its_deadline_keeps_the_newest_pref(tmp_path):
    import threading
    import time

    from waves.waves_ui.backend import _SingleFlightWriter

    target = tmp_path / "waves.json"
    started = threading.Event()

    def slow_old():
        started.set()
        time.sleep(1.0)  # a network home folder
        target.write_text("old")

    w = _SingleFlightWriter()
    w.submit("prefs", slow_old)
    started.wait(2)
    w.submit("prefs", lambda: target.write_text("new"))  # the last pref change before quit
    w.flush(timeout=0.2)  # shutdown's deadline passes while "old" is mid-write
    time.sleep(1.2)
    assert target.read_text() == "new", "the older snapshot landed last"


# ---- recovery for a symlinked library, and case-only twins -----------------
def test_a_symlinked_library_still_maps_to_its_mount(tmp_path, monkeypatch):
    from waves.waves_ui import netmount, smb_relist

    point = tmp_path / "Volumes" / "NAS"
    (point / "Music").mkdir(parents=True)
    link = tmp_path / "home" / "NAS"
    link.parent.mkdir()
    link.symlink_to(point)
    monkeypatch.setattr(smb_relist, "_on_macos", lambda: True)
    # statfs follows the link: it names the real mount point.
    monkeypatch.setattr(netmount, "mount_info", lambda p: ("smbfs", "//user@host/share", str(point)))
    monkeypatch.setattr(smb_relist.diagnostics, "register_secret", lambda *a, **k: None, raising=False)
    url, sub = smb_relist.plan_for(str(link / "Music"))
    assert url, f"no relist for a library reached through a symlink (sub={sub!r})"


def test_case_only_twins_do_not_make_a_listing_untrusted():
    from waves.waves_ui import smb_relist

    assert smb_relist.trusted_names(["Blur", "blur", "Oasis"]) is not None, "two real folders read as a repeat"


# ---- a mounted share spelled with other case still reads as mounted --------
def test_is_mounted_ignores_case_and_unicode_form(monkeypatch):
    import unicodedata

    from waves.waves_ui import netmount

    monkeypatch.setattr(
        netmount, "mount_points", lambda: {"/Volumes/Media", unicodedata.normalize("NFC", "/Volumes/Música")}
    )
    assert netmount.is_mounted("/Volumes/media") is True, "case"
    assert netmount.is_mounted(unicodedata.normalize("NFD", "/Volumes/Música")) is True, "unicode form"
