"""What the headless two-tree proof run of the audit-fix branch turned up.

Every fix on the branch was proven to hold and nothing regressed, but driving
the fixed app for real surfaced fourteen NEW problems the fixes had introduced
or left open. These are the guards for the behavioural half of them; the
guard-shaped ones (a test that could not fail) live beside the tests they
fence, and the updater/path/redactor ones with their own subjects.

  N-02  signing out during a search latched the busy spinner for good
  N-03  a rollup ended red over a run in which every member landed
  N-14  the F-10 release was half a release, the F-13 stale refusal left its
        twin stash behind, and three launch-fatal shapes stayed in __init__
"""

from __future__ import annotations

import contextlib
import inspect
import os
import pathlib
import sqlite3
from threading import Lock
from types import SimpleNamespace

import pytest

from waves import download as download_mod
from waves.download import _os_error_text
from waves.ownership import OwnershipStore
from waves.waves_ui import diagnostics
from waves.waves_ui.backend import WavesBridge


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _InlinePool:
    @staticmethod
    def start(worker, priority: int = 0):
        worker.fn()


# --------------------------------------------------------------------------- #
# N-02: sign-out during a search must not leave the spinner turning.
# --------------------------------------------------------------------------- #
class _SearchStub:
    """The real search slot on the attributes it reads, nothing more."""

    search = WavesBridge.search
    _search_total = WavesBridge._search_total
    _SEARCH_TTL = 60.0

    def __init__(self):
        self.threadpool = _InlinePool()
        self.statuses: list[str] = []
        self.busy: list[bool] = []
        self._logged_in = True
        self._search_gen = 0
        self._search_cache: dict = {}
        self._objs: dict = {"album": {}, "track": {}, "artist": {}}
        self._objs_lock = Lock()
        self.tidal = SimpleNamespace(session=object())
        self.searchResults = _Signal()
        self.artistMetaLoaded = _Signal()

    def _set_status(self, text):
        self.statuses.append(text)

    def _set_busy(self, on):
        self.busy.append(bool(on))


def test_a_superseded_search_worker_cannot_clear_busy_itself(monkeypatch):
    """The shape the sign-out fix has to answer for.

    Every generation-gated return in the search worker is a BARE return, and
    each of them sits above the _set_busy(False) at the end of work(). So a
    worker whose generation was bumped out from under it leaves busy exactly
    as it found it: whoever bumps the generation owns clearing the flag.
    """
    from waves.waves_ui import backend as backend_mod

    stub = _SearchStub()

    def _superseded(session, needle, **kw):
        stub._search_gen += 1  # what logout does to an in-flight search
        return {}

    monkeypatch.setattr(backend_mod, "search_results_all", _superseded)

    stub.search("anything")

    assert stub.busy == [True], "the worker returned without ever clearing what it set"


def _logout_stub(tmp_path):
    """A stub carrying every attribute logout touches, and nothing else."""
    stub = SimpleNamespace()
    stub.logout = WavesBridge.logout.__get__(stub, type(stub))
    stub.busy: list[bool] = []
    stub.statuses: list[str] = []
    stub.stopped = []
    stub.stopAll = lambda: stub.stopped.append(True)
    stub.tidal = SimpleNamespace(logout=lambda: None)
    stub._reset_tidal_session = lambda: None
    stub._set_logged_in = lambda value: None
    stub._set_busy = lambda on: stub.busy.append(bool(on))
    stub._set_status = lambda text: stub.statuses.append(text)
    for name in (
        "_lib_cache",
        "_lib_loading",
        "_lib_sort",
        "_fav_ids",
        "_browse_pages",
        "_browse_loading",
        "_category_pl",
        "_prefetch_unrecorded",
        "_album_tracks_inflight",
        "_album_tracks_unrecorded",
        "_item_fetch_ts",
        "_artist_cache",
        "_artist_loading",
        "_album_tracks_cache",
        "_lib_reval_ts",
        "_search_cache",
        "_artist_pop_cache",
    ):
        setattr(stub, name, {})
    stub._pending_lock = Lock()
    stub._prefetch_lock = Lock()
    stub._objs_lock = Lock()
    stub._pending_downloads = []
    stub._lib_gen = 0
    stub._browse_root_cache = None
    stub._browse_gen = 0
    stub._browse_reval_ts = 0.0
    stub._prefetch_key = None
    stub._prefetch_claimed = False
    stub._home_cache = None
    stub._home_loading = False
    stub._home_reval_ts = 0.0
    stub._media_lists_cache = None
    stub._folder_tree = None
    stub._tree_warm_waiting = []
    stub._search_gen = 0
    stub._objs = {"album": {}, "track": {}, "artist": {}, "playlist": {}, "video": {}, "mix": {}}
    stub._page_cache_path = str(tmp_path / "page_cache.json")
    return stub


def test_signing_out_clears_the_spinner_it_orphans(tmp_path):
    """logout bumps _search_gen (and _browse_gen), which is what strands the
    in-flight worker above its own _set_busy(False). Nothing else clears the
    flag, so the sign-out has to, or the spinner turns for the rest of the
    session and every later status reads as if something were still loading."""
    stub = _logout_stub(tmp_path)

    stub.logout()

    assert stub.busy and stub.busy[-1] is False, "sign-out left busy latched"
    assert stub.statuses[-1] == "Signed out"
    assert stub._search_gen == 1, "this is the bump that orphans the worker"


# --------------------------------------------------------------------------- #
# N-03: a member that failed and then landed is not a failure.
# --------------------------------------------------------------------------- #
class _FolderBumpStub:
    _bump_folder_group = WavesBridge._bump_folder_group

    def __init__(self, group: dict):
        self._folder_groups = {"fold1": group}
        self._folder_lock = Lock()
        self._scan_gen = 0
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self.folderRemaining = _Signal()


class _ArtistBumpStub:
    _bump_artist_group = WavesBridge._bump_artist_group

    def __init__(self, group: dict):
        self._artist_groups = {"art1": group}
        self._artist_lock = Lock()
        self._scan_gen = 0
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()


def _group(keys, weighted: bool):
    grp = {"keys": set(keys), "done": set(), "failed": set(), "prog": {}}
    if weighted:
        grp["weights"] = dict.fromkeys(keys, 1)
        grp["total"] = len(keys)
    return grp


def test_a_folder_rollup_that_recovers_ends_green():
    """A held-and-recovered playlist fails once (the folder went away) and then
    succeeds on the replay. The group's verdict is bool(grp["failed"]) and the
    credit was add-only, so the whole folder button went red over a run in
    which every playlist is on disk."""
    stub = _FolderBumpStub(_group(("p1", "p2"), weighted=True))

    stub._bump_folder_group("p1", None, "failed")  # the share dropped out
    stub._bump_folder_group("p1", 100.0, "done")  # the replay landed it
    stub._bump_folder_group("p2", 100.0, "done")

    assert stub.downloadState.emits[-1] == ("fold1", "done"), stub.downloadState.emits
    assert ("fold1", 100.0) in stub.downloadProgress.emits


def test_a_discography_rollup_that_recovers_ends_green():
    stub = _ArtistBumpStub(_group(("al1", "al2"), weighted=False))

    stub._bump_artist_group("al1", None, "failed")
    stub._bump_artist_group("al1", 100.0, "done")
    stub._bump_artist_group("al2", 100.0, "done")

    assert stub.downloadState.emits[-1] == ("art1", "done"), stub.downloadState.emits


def test_a_member_that_really_failed_still_ends_red():
    """The other direction, or the fix would just hide every failure: an album
    that never came back keeps the discography red."""
    stub = _ArtistBumpStub(_group(("al1", "al2"), weighted=False))

    stub._bump_artist_group("al1", None, "failed")
    stub._bump_artist_group("al2", 100.0, "done")

    assert stub.downloadState.emits[-1] == ("art1", "failed")


# --------------------------------------------------------------------------- #
# N-14: the F-10 release was half a release.
# --------------------------------------------------------------------------- #
class _RemoveStub:
    _remove_rows_where = WavesBridge._remove_rows_where
    _reindex_queue = WavesBridge._reindex_queue

    def __init__(self, rows):
        self._queue = list(rows)
        self._queue_lock = Lock()
        self._qdirty_removed: list[int] = []
        self._redownload_overrides: set[str] = set()
        self._library_claim_overrides: set[str] = set()
        # The third mark a withdrawn row gives up, alongside the two above.
        self._merge_plans: dict = {}
        # The held-download stash the withdrawal reads to tell a hold from a
        # give-up: all three marks above survive a withdrawal that is really a
        # hold. Nothing is held in these tests, so every mark is released.
        self._pending_downloads: list = []
        self._pending_lock = Lock()


def _row(qid: int, media_id: str, status: str = "queued"):
    return {"qid": qid, "media_id": media_id, "status": status, "idx": 0}


def test_withdrawing_a_row_releases_both_marks_it_registered():
    """registerRedownload marks the force AND the library-claim override, and
    downloadAlbumAnyway marks the second on its own. Releasing only the first
    left the album exempt from the library scan's bulk tag-claim gate for the
    rest of the session: the next click from anywhere downloaded tracks the
    gate should have skipped, with nothing on screen to say why."""
    stub = _RemoveStub([_row(1, "al1")])
    stub._redownload_overrides.add("al1")
    stub._library_claim_overrides.add("al1")

    assert stub._remove_rows_where(lambda it: it["qid"] == 1) == [1]

    assert stub._redownload_overrides == set()
    assert stub._library_claim_overrides == set(), "the claim override outlived its row"


def test_a_retry_keeps_both_marks():
    """A RETRY re-queues the item before its old row is dropped, so a live row
    still holds both marks. The half-release had this half right; the point is
    that closing the other half must not break it."""
    stub = _RemoveStub([_row(1, "al1", "failed"), _row(2, "al1", "queued")])
    stub._redownload_overrides.add("al1")
    stub._library_claim_overrides.add("al1")

    stub._remove_rows_where(lambda it: it["qid"] == 1)

    assert stub._redownload_overrides == {"al1"}
    assert stub._library_claim_overrides == {"al1"}


# --------------------------------------------------------------------------- #
# N-14: the F-13 stale refusal left its twin stash behind.
# --------------------------------------------------------------------------- #
class _EnqueueStub:
    _enqueue_albums = WavesBridge._enqueue_albums

    def __init__(self):
        self._scan_gen = 1
        self._merge_scanned: set[str] = set()
        self._merge_plans: dict[str, list] = {}
        self.downloadState = _Signal()
        self.queued: list[str] = []

    def downloadAlbum(self, key):
        self.queued.append(key)

    @contextlib.contextmanager
    def _queue_batch(self):
        yield


def test_a_stale_album_batch_drops_the_merge_plan_too():
    """STOP between the edition scan stashing its plan and the batch being
    delivered. The refusal already releases the scan exemption for exactly this
    reason; the plan needs the same treatment, or the next PLAIN click on that
    album silently downloads a cross-edition assembly with no "Best of both:"
    line anywhere to say so."""
    stub = _EnqueueStub()
    stub._merge_scanned.add("al1")
    stub._merge_plans["al1"] = ["a plan"]

    stub._enqueue_albums(0, ["al1"])  # gen 0 != _scan_gen 1: STOP landed

    assert stub.queued == [], "a stale batch queues nothing"
    assert stub._merge_scanned == set()
    assert stub._merge_plans == {}, "the plan outlived the batch that would have consumed it"


def test_a_live_album_batch_keeps_its_merge_plan():
    stub = _EnqueueStub()
    stub._merge_plans["al1"] = ["a plan"]

    stub._enqueue_albums(1, ["al1"])

    assert stub.queued == ["al1"]
    assert stub._merge_plans == {"al1": ["a plan"]}, "the download about to run needs it"


# --------------------------------------------------------------------------- #
# N-14: three shapes in __init__ that still killed the launch.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "method",
    ["_apply_first_run_defaults", "_migrate_video_template"],
)
def test_the_first_run_write_backs_survive_an_unwritable_config(method):
    """config.py wraps its own two write-backs in try/except OSError because an
    unwritable config folder inside the constructor is a launch with no window.
    These two run from the same constructor and did not."""
    source = inspect.getsource(
        WavesBridge.__init__ if method == "_migrate_video_template" else getattr(WavesBridge, method)
    )
    # The save the method performs must be inside a try that names OSError.
    assert "self.settings.save()" in source
    assert "except OSError" in source, f"{method}'s save can still take the launch down"


def test_a_broken_ownership_file_does_not_take_the_launch_with_it(tmp_path, monkeypatch):
    """The store is constructed unguarded in __init__. A config folder that has
    gone read-only, a corrupt file, or a migration losing a race it cannot
    resolve was a traceback and no window; an in-memory store forgets what has
    been downloaded until the next launch, and that is all."""
    source = inspect.getsource(WavesBridge.__init__)
    marker = "self._ownership = OwnershipStore(_own_file)"
    assert marker in source, "the guarded construction is gone"
    head = source.split(marker)[0]
    assert head.rstrip().endswith("try:"), "the store is constructed outside a try again"
    assert 'OwnershipStore(":memory:")' in source.split(marker)[1], "no stand-in when it raises"


# --------------------------------------------------------------------------- #
# N-14: the duplicate-column race must not be judged by its message text.
# --------------------------------------------------------------------------- #
class _RacingConn:
    """The losing copy's view of the first launch after an upgrade.

    Its FIRST column-list read happens before the other copy's ALTER lands, so
    every added column reads as missing; by the time it runs its own ALTER the
    winner holds the write lock, and sqlite answers "database is locked"
    instead of "duplicate column name". A later read then sees the columns.

    sqlite3.Connection.execute is read-only, so the connection itself is what
    gets swapped, never one of its methods.
    """

    def __init__(self, conn, *, winner_lands: bool):
        self._conn = conn
        self._winner_lands = winner_lands
        self.info_reads = 0
        self.alters = 0

    def execute(self, sql, *a, **k):
        head = sql.strip().upper()
        if head.startswith("PRAGMA TABLE_INFO"):
            self.info_reads += 1
            if self.info_reads == 1 or not self._winner_lands:
                return []  # the stale read that starts the race
            return self._conn.execute(sql, *a, **k)
        if head.startswith("ALTER TABLE"):
            self.alters += 1
            raise sqlite3.OperationalError("database is locked")
        return self._conn.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_locked_database_does_not_re_raise_when_the_column_arrived(tmp_path):
    """Two copies of Waves opening the store on the first launch after an
    upgrade both read the column list before either alters. sqlite answers the
    loser with "duplicate column name" USUALLY, and with "database is locked"
    once its busy timeout is exceeded, which carries no column name at all.
    Matching the text re-raised precisely the loser this handles, under load,
    at startup. The file is the only witness that settles it."""
    store = OwnershipStore(str(tmp_path / "own.sqlite3"))
    real = store._conn
    guard = _RacingConn(real, winner_lands=True)
    store._conn = guard

    store._ensure_columns()  # the loser must live: the columns are all there

    assert guard.alters, "the race never reached the ALTER, so nothing was tested"
    store._conn = real
    store.close()


def test_a_real_alter_failure_still_raises(tmp_path):
    """The other direction: when the column is genuinely NOT there, an
    OperationalError is a real failure and must not be swallowed."""
    store = OwnershipStore(str(tmp_path / "own.sqlite3"))
    real = store._conn
    store._conn = _RacingConn(real, winner_lands=False)

    with pytest.raises(sqlite3.OperationalError):
        store._ensure_columns()

    store._conn = real
    store.close()


def test_the_store_still_opens_a_real_file(tmp_path):
    """The guards above must not have made a working store optional."""
    path = tmp_path / "own.sqlite3"
    store = OwnershipStore(str(path))
    store.record("t1", str(tmp_path / "a.flac"), "LOSSLESS")
    store.close()

    assert os.path.isfile(path)


# --------------------------------------------------------------------------- #
# N-07: an OSError carries a filename, and inside a download that filename is
# the artist, the album and the title.
# --------------------------------------------------------------------------- #
def test_a_failed_move_does_not_print_the_track_title_in_the_clear():
    """G-03 wrapped the retry helper's DESCRIPTION and stopped there. The
    error interpolated one field later carries
    "<library>/<Artist>/<Album>/.<track title>.<uuid>.tmp" as its filename, so
    the same line that had just anonymised its paths put the title straight
    back into the bundle, past the "also hide titles and searches" switch."""
    name = "/Music/Some Artist/Some Album/.Song.m4a.abc.tmp"
    error = PermissionError(13, "Permission denied", name)

    text = _os_error_text(error)

    assert diagnostics.content(name) in text, "the filename is not marked as content"
    assert "[Errno 13] Permission denied" in text, "the diagnosis itself must survive"
    # And the marking is what the export's content pass acts on, which is the
    # whole point: with "also hide titles and searches" on, nothing readable
    # about the artist, the album or the song is left.
    hidden = diagnostics._Redactor.scrub_content(text)
    assert "Some Artist" not in hidden and "Song.m4a" not in hidden, hidden
    assert "[Errno 13] Permission denied" in hidden


def test_a_rename_error_wraps_both_of_its_filenames():
    """os.replace failures carry filename AND filename2, and a move inside a
    download names two user paths."""
    error = OSError(18, "Invalid cross-device link", "/Music/A/B/.x.tmp", None, "/Music/A/B/x.flac")

    text = _os_error_text(error)

    assert text.count(diagnostics._C_OPEN) == 2, text
    assert "/Music/A/B" not in diagnostics._Redactor.scrub_content(text)


@pytest.mark.parametrize(
    "error",
    [OSError("something went wrong"), ValueError("not an OSError at all")],
)
def test_an_error_with_nothing_to_take_apart_reads_as_it_always_did(error):
    assert _os_error_text(error) == str(error)


def test_the_retry_helper_actually_uses_it():
    """The helper is only worth anything at the call sites the finding named."""
    source = inspect.getsource(download_mod.Download._retry_file_operation)

    assert "{error}" not in source and "{error_last}" not in source
    assert source.count("_os_error_text(") == 2


# --------------------------------------------------------------------------- #
# N-14: one sum, two rulers.
# --------------------------------------------------------------------------- #
def test_the_unique_suffix_budget_measures_the_path_the_way_the_platform_does(monkeypatch):
    """_path_with_unique_suffix subtracted a UTF-8 BYTE count for the suffix
    from a whole-path budget the platform measures in UTF-16 units. On POSIX
    the two are the same number, which is why it never showed; on Windows a
    CJK or emoji suffix was charged three or four units against a cap that
    charges one or two, so a name near the path cap gave up stem it did not
    have to."""
    from waves.helper import path as path_mod

    # A suffix whose two measures differ: three bytes in UTF-8, one UTF-16
    # unit. The path budget must charge the platform's number.
    assert path_mod._text_length("字") == len("字".encode())  # POSIX: bytes
    monkeypatch.setattr(path_mod.sys, "platform", "win32")
    assert path_mod._text_length("字") == 1, "Windows counts UTF-16 units"
    assert path_mod._text_length("\U0001f600") == 2, "an astral character costs two"


def test_a_windows_stem_keeps_every_character_the_path_cap_allows(monkeypatch):
    """The cost of the mixed units, in characters of the user's song title.

    On Windows the whole-path budget is UTF-16 units, and the stem was trimmed
    against it in UTF-8 BYTES: a CJK title was charged three units per
    character where MAX_PATH charges one, so it lost two thirds of the name it
    was entitled to keep. The result always fitted, which is why nothing ever
    failed; it was just needlessly short.
    """
    from waves.helper import path as path_mod

    monkeypatch.setattr(path_mod.sys, "platform", "win32")
    monkeypatch.setattr(path_mod, "PATH_LENGTH_MAX", 120)
    parent = pathlib.Path("/" + "d" * 90)
    destination = parent / ("字" * 40 + ".flac")

    got = path_mod._path_with_unique_suffix(destination, "_01")
    stem = got.name[: -len("_01.flac")]

    # 120 - 91 (the parent, leading slash included) - 1 (separator)
    # - 8 ("_01.flac") = 20 units of stem. Measured in bytes that budget is 6.
    assert len(stem) == 20, f"kept {len(stem)} of the 20 characters that fit"
    assert path_mod._path_length(got) == 120, "and it uses the budget exactly, never overruns it"


def test_a_stem_is_trimmed_to_fit_both_caps(monkeypatch):
    """Whatever the units, the result must satisfy the filename cap in bytes
    AND the path cap in the platform's own measure: that is what the old
    min() was reaching for and what the two-budget form has to keep."""
    from waves.helper import path as path_mod

    monkeypatch.setattr(path_mod, "PATH_LENGTH_MAX", 120)
    parent = pathlib.Path("/" + "d" * 90)
    destination = parent / ("字" * 40 + ".flac")

    got = path_mod._path_with_unique_suffix(destination, "_01")

    assert len(os.fsencode(got.name)) <= path_mod.FILENAME_LENGTH_MAX
    assert path_mod._path_length(got) <= 120, path_mod._path_length(got)
    assert got.name.endswith("_01.flac"), "the part that makes it unique is never trimmed"


def test_an_ordinary_name_is_untouched():
    from waves.helper import path as path_mod

    got = path_mod._path_with_unique_suffix(pathlib.Path("/music/Artist/Album/Song.flac"), "_01")

    assert got == pathlib.Path("/music/Artist/Album/Song_01.flac")


# --------------------------------------------------------------------------- #
# N-14: a kept backup is a whole copy of the app; say so where it stays said.
# --------------------------------------------------------------------------- #
def test_the_restart_message_names_a_kept_backup():
    """_apply_macos keeps the entire old bundle whenever it held anything
    foreign, and the only notice was a transient status line this very message
    then overwrote."""
    source = inspect.getsource(WavesBridge.installAppUpdate)

    assert 'result.get("kept_backup"' in source, "the install result's notice is dropped on the floor"
    assert "Restart to finish." in source
