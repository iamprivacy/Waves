"""Pins from the 2026-09-24 rule sweep (one reviewer per invariant, whole repo).

Each test names the rule it fences and the shape that broke it. Source
inspection is used where the mechanism is a lock or a signal order that no
stub can exercise honestly (every bridge test binds unbound methods onto
non-QObject stubs, so Qt visibility and thread affinity are invisible there).
"""

from __future__ import annotations

import inspect
import pathlib
import re
from threading import Event, Lock, Thread
from types import SimpleNamespace

import tidalapi

import waves.config as config
import waves.library_worker as library_worker
from waves.download import Download
from waves.waves_ui import backend, bridge_library

ROOT = pathlib.Path(__file__).resolve().parents[1]
MAIN_QML = (ROOT / "waves" / "waves_ui" / "qml" / "Main.qml").read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    return "\n".join(line.split("//")[0] for line in text.splitlines())


# --- rule 3: the shared session.page parser is never used off the lock ------


def test_a_mix_job_fills_its_items_under_the_browse_lock_before_the_engine_enumerates():
    src = inspect.getsource(backend.WavesBridge._start_job)
    body = src[src.index('devlog.event("download", "start"') :]
    lock = body.index("with self._browse_lock:")
    items = body.index("dl.items(")
    assert lock < items, "the engine enumerated a page-parsed Mix on dl_pool with no lock"
    assert 'type_media == "mix" and not getattr(obj, "_retrieved", True)' in body


# --- rule 2: the ask a row was queued at follows it through every gate --------


def test_every_gate_replay_carries_keep_ask():
    src = inspect.getsource(backend.WavesBridge._download)
    replays = re.findall(r"lambda: self\._download\((.*?)\)\s*,?\s*\)?", src, re.S)
    assert len(replays) >= 2, "expected the nudge and ffmpeg gate replays"
    for args in replays:
        assert "keep_ask=keep_ask" in args, args


# --- rule 6: a verdict that changes without an index swap moves the stamp ---


def test_musicbrainz_answers_bump_the_library_stamp_before_announcing():
    src = inspect.getsource(bridge_library)
    idx = src.index('"MusicBrainz arbitration answered: %s"')
    block = src[idx : idx + 700]
    assert block.index("_bump_library_stamp()") < block.index('self._emit_from_worker("libraryPresenceChanged")')


def test_the_arbiter_toggle_bumps_the_library_stamp_before_announcing():
    src = inspect.getsource(backend.WavesBridge)
    idx = src.index('elif key == "library_mb_arbiter" and value != old:')
    block = src[idx : idx + 1200]
    assert block.index("_bump_library_stamp()") < block.index("self.libraryPresenceChanged.emit()")


# --- rule 2/3: a Settings save cannot slip between a pin and its fetch -------


def _tidal_stub() -> config.Tidal:
    t = config.Tidal.__new__(config.Tidal)
    t.session = SimpleNamespace(audio_quality=None, video_quality=None)
    t.settings = SimpleNamespace(data=SimpleNamespace(quality_audio="LOSSLESS"))
    t.is_atmos_session = False
    t.stream_lock = Lock()
    return t


def test_settings_apply_writes_the_session_quality_under_the_stream_lock():
    t = _tidal_stub()
    seen = []

    class _Session:
        video_quality = None

        @property
        def audio_quality(self):
            return None

        @audio_quality.setter
        def audio_quality(self, value):
            seen.append(t.stream_lock.locked())

    t.session = _Session()
    assert t.settings_apply() is True
    assert seen == [True], "the write happened outside stream_lock"
    assert not t.stream_lock.locked(), "the lock was not released"


def test_settings_apply_still_writes_when_the_lock_is_held_too_long():
    """A pool job holds stream_lock around a hung manifest request; the GUI
    thread's save must wait a bounded time, then write anyway. A real Lock
    held by another thread, so an unbounded acquire shows up as the save
    never returning (a GUI freeze) rather than as a stub answering False."""
    t = _tidal_stub()
    holder_has_it = Event()
    let_go = Event()

    def hold():
        with t.stream_lock:
            holder_has_it.set()
            let_go.wait(20)

    holder = Thread(target=hold, daemon=True)
    holder.start()
    assert holder_has_it.wait(5)
    result: dict = {}
    done = Event()

    def save():
        result["ok"] = t.settings_apply()
        done.set()

    saver = Thread(target=save, daemon=True)
    saver.start()
    finished = done.wait(8.0)  # the bound is 2s; a blocking acquire never returns
    let_go.set()
    holder.join(5)
    assert finished, "settings_apply blocked on a held stream_lock: the wait must be bounded"
    assert result["ok"] is True
    assert t.session.audio_quality == tidalapi.Quality("LOSSLESS"), "a timed-out wait still writes"
    assert not t.stream_lock.locked(), "the holder's lock was released after the save"


def test_the_pin_restores_only_what_it_put_there():
    src = inspect.getsource(backend._TrackedDownload._get_track_stream_info)
    assert "if self.session.audio_quality == pinned:" in src


# --- rule 4: exception text that renders a URL or a path stays off the log ---


def test_the_size_probe_logs_the_exception_class_only():
    src = inspect.getsource(Download._setup_progress)
    idx = src.index("Could not size the download")
    assert "{type(error).__name__}" in src[idx : idx + 200]
    assert "{error}" not in src[idx : idx + 200]


def test_the_scanner_child_reports_a_failed_job_by_class_only():
    src = inspect.getsource(library_worker)
    assert '"message": type(exc).__name__' in src
    assert 'f"{type(exc).__name__}: {exc}"' not in src


def test_the_scanner_stderr_relay_is_marked_content():
    from waves.waves_ui import library_proc

    src = inspect.getsource(library_proc)
    assert 'logger.warning("[scanner] %s", diagnostics.content(line))' in src


def test_the_boot_breadcrumb_carries_no_path():
    src = inspect.getsource(backend.WavesBridge.__init__)
    idx = src.index('"WavesBridge starting"')
    assert "os.path.basename" in src[idx : idx + 160]
    assert "log=str(log_path" not in src[idx : idx + 160]


def test_the_mount_point_is_registered_where_the_share_origin_is():
    src = inspect.getsource(backend.WavesBridge._remember_share_origin)
    assert 'diagnostics.register_secret(root, "‹mount-point›")' in src


# --- rule 5: QML lifecycle ---------------------------------------------------


def test_the_paused_breath_loop_stops_off_screen():
    code = _code_only(MAIN_QML)
    assert "running: artRoot.fxPaused && artRoot.visible && root.onScreen" in code
    assert re.search(r"running:\s*artRoot\.fxPaused\s*\n", code) is None


def test_no_layout_reads_effective_visibility_for_the_video_date():
    code = _code_only(MAIN_QML)
    assert "dateW: vDateTx.visible" not in code
    assert 'dateW: vcell.vcDate !== ""' in code


def test_presence_pills_settle_by_id_not_title():
    code = _code_only(MAIN_QML)
    assert re.search(r"settleKey:\s*appl\.albumTitle\s*\n", code) is None
    assert "appl.album.id" in code
    assert "tppl.track.id" in code


# --- rule 6: a parked My Tidal pane still revalidates --------------------------


def test_my_tidal_has_a_max_age_timer_like_the_browse_landing():
    code = _code_only(MAIN_QML)
    idx = code.index("id: libraryFreshTimer")
    block = code[idx : idx + 900]
    assert "repeat: true" in block
    assert "root.libraryOpen" in block
    assert "waves.loadHome(" in block and "waves.loadLibrary(root.libraryCategory, true)" in block


# --- rule 6: a parked item page or artist page still revalidates ------------


class _SyncPool:
    def start(self, worker, *_a):
        worker.fn() if hasattr(worker, "fn") else worker.run()


class _Emit:
    def __init__(self):
        self.sent = []

    def emit(self, *a):
        self.sent.append(a)


def _item_stub(cached, built, stamp=None):
    from threading import Lock

    from waves.waves_ui.backend import WavesBridge

    s = SimpleNamespace()
    s._logged_in = True
    s._browse_pages = {"item:playlist:p1": cached} if cached is not None else {}
    s._browse_loading = set()
    s._item_fetch_ts = {"item:playlist:p1": stamp} if stamp is not None else {}
    s._ITEM_FRESH_S = WavesBridge._ITEM_FRESH_S
    s._BROWSE_PAGES_MAX = 50
    s._browse_gen = 1
    s._prefetch_lock = Lock()
    s.threadpool = _SyncPool()
    s.browsePageLoaded = _Emit()
    s._build_browse_item = lambda kind, mid, key: built
    s._remember_capped = lambda store, key, payload, cap: store.__setitem__(key, payload)
    s._emit_dressed = lambda sig, payload, gen: sig.emit(payload)
    s._save_page_cache = lambda: None
    s._set_busy = lambda v: None
    s._set_status = lambda v: None
    for name in ("refreshBrowseItem", "_start_browse_item_build"):
        setattr(s, name, getattr(WavesBridge, name).__get__(s))
    return s


def test_a_parked_item_page_revalidates_and_repaints_only_on_change():
    import time

    old = {"key": "item:playlist:p1", "title": "T", "sections": [{"items": [{"id": "a"}]}], "error": False}
    new = {"key": "item:playlist:p1", "title": "T", "sections": [{"items": [{"id": "a"}, {"id": "b"}]}], "error": False}

    s = _item_stub(old, new, stamp=time.monotonic() - 600)
    s.refreshBrowseItem("playlist", "p1")
    assert [p[0] for p in s.browsePageLoaded.sent] == [new], "a changed page is re-emitted for the in-place swap"
    assert s._browse_pages["item:playlist:p1"] == new
    assert not s._browse_loading

    s = _item_stub(old, dict(old), stamp=time.monotonic() - 600)
    s.refreshBrowseItem("playlist", "p1")
    assert s.browsePageLoaded.sent == [], "an unchanged page is not repainted"

    s = _item_stub(old, new, stamp=time.monotonic())
    s.refreshBrowseItem("playlist", "p1")
    assert s.browsePageLoaded.sent == [], "a page fetched within the minute is left alone"

    s = _item_stub(None, new)
    s.refreshBrowseItem("playlist", "p1")
    assert s.browsePageLoaded.sent == [], "a page never opened is not this slot's to load"


def _artist_stub(cached, stamp=None, loading=()):
    from threading import Lock

    from waves.waves_ui.backend import WavesBridge

    s = SimpleNamespace()
    s._logged_in = True
    s._artist_cache = {"ar1": cached} if cached is not None else {}
    s._artist_page_collapses_editions = lambda: False
    s._artist_reval_ts = {"ar1": stamp} if stamp is not None else {}
    s._artist_loading = set(loading)
    s._prefetch_lock = Lock()
    s.builds = []
    s._start_artist_build = lambda aid, cached, collapse, silent: s.builds.append((aid, cached, collapse, silent))
    s.refreshArtist = WavesBridge.refreshArtist.__get__(s)
    return s


def test_a_parked_artist_page_revalidates_silently():
    import time

    page = {"id": "ar1", "editions_collapsed": False}
    s = _artist_stub(page, stamp=time.monotonic() - 600)
    s.refreshArtist("ar1")
    assert s.builds == [("ar1", page, False, True)]
    assert "ar1" in s._artist_loading, "the build owns the loading mark until it lands"

    s = _artist_stub(page, stamp=time.monotonic())
    s.refreshArtist("ar1")
    assert s.builds == [], "fetched within the minute"

    s = _artist_stub(page, stamp=time.monotonic() - 600, loading=("ar1",))
    s.refreshArtist("ar1")
    assert s.builds == [], "already building"

    s = _artist_stub({"id": "ar1", "editions_collapsed": True}, stamp=time.monotonic() - 600)
    s.refreshArtist("ar1")
    assert s.builds == [], "cached under the other edition rule is not this slot's to rebuild"

    s = _artist_stub(None)
    s.refreshArtist("ar1")
    assert s.builds == []


def test_every_artist_fetch_stamps_the_refresh_floor():
    src = inspect.getsource(backend.WavesBridge._start_artist_build)
    assert "reval_ts[artist_id] = time.monotonic()" in src


def test_item_and_artist_pages_have_max_age_timers():
    code = _code_only(MAIN_QML)
    i = code.index("id: browseItemFreshTimer")
    block = code[i : i + 900]
    assert "repeat: true" in block and 'root.browsePageKey !== ""' in block
    assert 'waves.refreshBrowseItem(parts[1], parts.slice(2).join(":"))' in block
    j = code.index("id: artistFreshTimer")
    block = code[j : j + 700]
    assert "repeat: true" in block and "root.artistOpen" in block and "libraryScoped" in block
    assert 'waves.refreshArtist("" + root.artistData.id)' in block
