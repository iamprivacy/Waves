"""Nothing stops a second copy of Waves running, so nothing may assume one.

Two instances share one config folder: the same settings.json, the same
token.json, the same ownership DB, the same staging names. Three places wrote
as if they were alone.

* every config save staged through one fixed ".tmp" sibling, so two saves
  interleaved into it and whichever swap landed last published the mixture:
  the next launch called that corrupt, moved it aside and started on factory
  defaults (the same shape on token.json signs the user out);
* the first launch after an upgrade adds two columns to the ownership DB, and
  two copies could both read the column list before either added anything: the
  loser's ALTER raised "duplicate column name" out of the store's constructor,
  which is built while the bridge is, so that copy died at startup;
* and the ffmpeg install had no in-flight guard at all, so two clicks on two
  surfaces (or one double click) ran two installs over one staging name.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from types import SimpleNamespace

from waves import config as waves_config
from waves.config import BaseConfig
from waves.ownership import OwnershipStore
from waves.waves_ui.backend import WavesBridge


class _Model:
    """The one thing BaseConfig.save asks of its model."""

    def __init__(self, who: str, pad: int):
        self.who = who
        self.pad = pad

    def to_json(self) -> str:
        return json.dumps({"who": self.who, "pad": "x" * self.pad})


def _config(tmp_path, who: str, pad: int) -> BaseConfig:
    cfg = BaseConfig()
    cfg.data = _Model(who, pad)
    cfg.file_path = str(tmp_path / "settings.json")
    cfg.path_base = str(tmp_path)
    return cfg


def test_each_save_stages_through_a_name_of_its_own(tmp_path, monkeypatch):
    staged: list[str] = []
    real = waves_config._replace_with_retry
    monkeypatch.setattr(
        waves_config,
        "_replace_with_retry",
        lambda tmp, dst: (staged.append(tmp), real(tmp, dst))[1],
    )

    _config(tmp_path, "A", 4).save()
    _config(tmp_path, "B", 400).save()

    assert len(set(staged)) == 2, "two saves staged through the same name"
    assert str(tmp_path / "settings.json.tmp") not in staged


def test_two_saves_at_once_never_publish_a_mixture(tmp_path):
    """Short and long payloads: open() truncates and each writer flushes its
    own length, so a shared temp file publishes a complete short document with
    the tail of the longer one still after it."""
    path = tmp_path / "settings.json"
    stop = threading.Event()
    bad: list[str] = []

    def saver(who: str, pad: int) -> None:
        cfg = _config(tmp_path, who, pad)
        for _ in range(150):
            cfg.save()

    def reader() -> None:
        while not stop.is_set():
            try:
                text = path.read_text()
            except OSError:
                continue
            if not text:
                continue
            try:
                json.loads(text)
            except ValueError:
                bad.append(text[:80])
                return

    threads = [threading.Thread(target=saver, args=("A", 4)), threading.Thread(target=saver, args=("B", 4000))]
    watcher = threading.Thread(target=reader)
    watcher.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    stop.set()
    watcher.join(5)

    assert bad == [], f"a reader saw a half-published config: {bad[:1]}"
    assert json.loads(path.read_text())["who"] in {"A", "B"}
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "settings.json"]
    assert leftovers == [], f"temp siblings left behind: {leftovers}"


def _old_schema(path) -> sqlite3.Connection:
    """An ownership DB from before the two rank columns."""
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE downloads (track_id TEXT NOT NULL, path TEXT NOT NULL, PRIMARY KEY (track_id, path))")
    conn.commit()
    return conn


class _RacedConn:
    """A connection where the OTHER copy of Waves adds the column in the moment
    between our reading the column list and our adding it ourselves."""

    def __init__(self, conn: sqlite3.Connection, column: str, decl: str):
        self._conn = conn
        self._column = column
        self._decl = decl
        self._raced = False

    def execute(self, sql, *args):
        if sql.startswith("PRAGMA table_info") and not self._raced:
            self._raced = True
            rows = list(self._conn.execute(sql, *args))
            self._conn.execute(f"ALTER TABLE downloads ADD COLUMN {self._column} {self._decl}")
            return rows
        return self._conn.execute(sql, *args)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def test_a_column_another_copy_added_first_is_not_a_startup_crash(tmp_path):
    conn = _old_schema(tmp_path / "ownership.db")
    store = OwnershipStore.__new__(OwnershipStore)
    store._conn = _RacedConn(conn, "quality_tier", "TEXT")

    store._ensure_columns()  # the loser's ALTER: "duplicate column name"

    have = {row[1] for row in conn.execute("PRAGMA table_info(downloads)")}
    assert "quality_tier" in have
    assert "ceiling_rank" in have, "the columns after the raced one still landed"


def test_a_real_schema_error_is_still_raised(tmp_path):
    """The guard is for one message, not for every failed ALTER."""
    conn = _old_schema(tmp_path / "ownership.db")
    conn.execute("DROP TABLE downloads")
    conn.commit()
    store = OwnershipStore.__new__(OwnershipStore)
    store._conn = conn

    try:
        store._ensure_columns()
    except sqlite3.OperationalError as e:
        assert "no such table" in str(e)
    else:
        raise AssertionError("a missing table must not pass for a raced column")


class _FfmpegStub:
    """Just what installFfmpeg touches, with a pool that runs inline."""

    def __init__(self):
        self._ffmpeg_install_inflight = False
        self._ffmpeg_abort = threading.Event()
        self.started = 0
        self.installs = 0
        self._logged_in = False
        self.ffmpegStateChanged = SimpleNamespace(emit=lambda *a: None)
        self.ffmpegProgress = SimpleNamespace(emit=lambda *a: None)
        self.ffmpegStatusChanged = SimpleNamespace(emit=lambda *a: None)
        self._ffmpeg = SimpleNamespace(install=self._install)
        self.threadpool = SimpleNamespace(start=self._start)

    def _install(self, **kwargs):
        self.installs += 1
        return {"version": "7.1"}

    def _start(self, worker):
        self.started += 1
        worker.run()

    def _restore_ffmpeg_flags(self):
        pass


def test_a_double_click_runs_one_ffmpeg_install(monkeypatch):
    stub = _FfmpegStub()
    slot = WavesBridge.installFfmpeg.__get__(stub, _FfmpegStub)
    # The pool is inline here, so the guard is held for the whole first call:
    # the second click lands while the first is still downloading.
    inflight_during: list[bool] = []
    real_install = stub._install

    def install(**kwargs):
        slot()  # the other surface's button, clicked mid-download
        inflight_during.append(stub._ffmpeg_install_inflight)
        return real_install(**kwargs)

    stub._ffmpeg = SimpleNamespace(install=install)

    slot()

    assert inflight_during == [True]
    assert stub.installs == 1, "the 80MB build was downloaded twice over one staging name"


def test_the_guard_lets_go_when_the_install_is_done(monkeypatch):
    stub = _FfmpegStub()
    slot = WavesBridge.installFfmpeg.__get__(stub, _FfmpegStub)

    slot()
    slot()

    assert stub.installs == 2, "a finished install must not block the next one"


def test_a_failed_install_lets_go_too():
    stub = _FfmpegStub()

    def boom(**kwargs):
        raise RuntimeError("no network")

    stub._ffmpeg = SimpleNamespace(install=boom)
    slot = WavesBridge.installFfmpeg.__get__(stub, _FfmpegStub)

    slot()

    assert stub._ffmpeg_install_inflight is False


# --------------------------------------------------------------------------- #
# gap-round G-09: the backend's own writer had the exact settings.json shape
# --------------------------------------------------------------------------- #
def test_backend_writer_stages_through_a_name_of_its_own(tmp_path, monkeypatch):
    import os

    from waves.waves_ui.backend import _write_text_atomic

    staged: list[str] = []
    real_replace = os.replace

    def replace(src, dst):
        staged.append(os.path.basename(src))
        return real_replace(src, dst)

    monkeypatch.setattr("waves.waves_ui.backend.os.replace", replace)
    target = str(tmp_path / "waves.json")

    _write_text_atomic(target, "{}")
    _write_text_atomic(target, "{}")

    assert len(staged) == 2 and staged[0] != staged[1], "one fixed sibling is what two instances interleave into"
    for name in staged:
        assert name.startswith("waves.json.") and name.endswith(".tmp")
