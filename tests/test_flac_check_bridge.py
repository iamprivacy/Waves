"""Glue tests for the FLAC signature check on the bridge (bridge_flac.py).

Qt-free, the way test_library_bridge.py does it: the mixin's methods are
bound onto a bare stub with an inline pool, and the worker's events are
handed straight to the GUI-thread handler. What is pinned: the scan reads
only, walks exactly the selected folders (the Waves library's two, or the one
folder chosen with Browse), and writes its report under the config folder;
the fix writes only the files the scan found without a signature, and only
while the selection still names the folders that scan walked; every guard
(no folder, no ffmpeg, a run in progress, a stale run's events, a selection
changed since the scan) fails closed.
"""

from __future__ import annotations

import csv
import hashlib
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import _InlinePool, _Signal

from waves import flac_verify as fv
from waves.waves_ui import backend, bridge_flac
from waves.waves_ui.bridge_flac import FlacCheckMixin

FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")

_METHODS = (
    "_flac_check_init",
    "flacCheckState",
    "_flac_check_prefs",
    "_flac_check_resume_target",
    "_flac_check_waves_roots",
    "_flac_check_scope",
    "flacCheckSources",
    "_flac_check_set_pref",
    "flacCheckSetSource",
    "flacCheckSetFolder",
    "_flac_check_ffmpeg",
    "_flac_check_report_dir",
    "_flac_check_begin",
    "_flac_check_fail",
    "flacCheckScan",
    "flacCheckFix",
    "flacCheckResume",
    "flacCheckCancel",
    "flacCheckRevealReports",
    "_flac_check_alive",
    "_flac_check_progress",
    "_flac_check_finish",
    "_flac_check_open_cache",
    "_flac_check_cache_event",
    "_flac_check_cache_path",
    "_flac_check_close_cache",
    "_flac_check_open_cache_file",
    "_flac_check_scan_job",
    "_flac_check_fix_job",
    "_on_flac_check_event",
    "_flac_check_settled",
    "_flac_check_missed",
)


class _Stub(SimpleNamespace):
    pass


def _bridge(tmp_path: Path, *, library: str = "", ffmpeg: str | None = None, download: str | None = None) -> _Stub:
    b = _Stub()
    for name in _METHODS:
        setattr(b, name, getattr(FlacCheckMixin, name).__get__(b))
    config = tmp_path / "config"
    config.mkdir(exist_ok=True)
    b.settings = SimpleNamespace(
        file_path=str(config / "settings.json"),
        data=SimpleNamespace(
            download_base_path=download if download is not None else str(tmp_path / "Downloads"),
            path_binary_ffmpeg=ffmpeg or "",
        ),
    )
    b.threadpool = _InlinePool()
    b.flacCheckChanged = _Signal()
    b._library_root = lambda: library
    b._waves_prefs = {"flac_check_source": "waves", "flac_check_folder": ""}
    b.saves: list[dict] = []
    b._save_waves_prefs = lambda: b.saves.append(dict(b._waves_prefs))
    b.revealed: list[str] = []
    b._reveal_in_file_manager = b.revealed.append
    # The worker's hop to the GUI thread, inline.
    b._emit_from_worker = lambda name, gen, fields: b._on_flac_check_event(gen, fields)
    b._flac_check_post = lambda gen, fields: b._on_flac_check_event(gen, fields)
    b._flac_check_gen = 0
    b._flac_check_cancel = __import__("threading").Event()
    b._flac_check_rows = []
    b._flac_check_unreadable = []
    b._flac_check_result = (0, [], [])
    b._flac_check_state = bridge_flac._idle_state()
    b._flac_check_resuming = False
    b._flac_check_keep_resume = False
    b._flac_check_resume_unread = None
    return b


def _packed(rate: int, channels: int, bps: int, total: int) -> bytes:
    return ((rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total).to_bytes(8, "big")


def _fake_flac(path: Path, md5: bytes = bytes(16)) -> bytes:
    info = b"\x10\x00\x10\x00" + b"\x00\x00\x64" + b"\x00\x23\x28" + _packed(44100, 2, 16, 0) + md5
    data = b"fLaC" + bytes([0x80]) + (34).to_bytes(3, "big") + info + b"\xff\xf8" + b"\x11" * 100
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _run(*args: str) -> bytes:
    # argv is ffmpeg from PATH plus literal test arguments.
    return subprocess.run([FFMPEG, "-v", "error", *args], capture_output=True, check=True).stdout  # noqa: S603


def _real_flac(path: Path, *, unset: bool) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    sine = "sine=frequency=440:duration=0.3"
    _run("-y", "-f", "lavfi", "-i", sine, "-ac", "2", "-ar", "44100", "-sample_fmt", "s16", "-c:a", "flac", str(path))
    if unset:
        si = fv.read_streaminfo(path)
        fv.write_streaminfo(path, si, fv.sealed_bytes(si, bytes(16), 0))
    pcm = _run("-i", str(path), "-f", "s16le", "-")
    return hashlib.md5(pcm).digest()  # noqa: S324


def _report_rows(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


# ---- the scan -------------------------------------------------------------------


def test_scan_reads_only_and_writes_a_report_under_the_config_folder(tmp_path):
    b = _bridge(tmp_path)
    dl = tmp_path / "Downloads"
    files = {
        dl / "A" / "unset.flac": _fake_flac(dl / "A" / "unset.flac"),
        dl / "A" / "set.flac": _fake_flac(dl / "A" / "set.flac", b"\x01" * 16),
        dl / "B" / "junk.flac": None,
    }
    (dl / "B").mkdir(parents=True)
    (dl / "B" / "junk.flac").write_bytes(b"not flac")
    files[dl / "B" / "junk.flac"] = b"not flac"
    stamps = {p: os.stat(p).st_mtime_ns for p in files}

    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert st["phase"] == "done"
    assert st["running"] is False
    assert (st["last"], st["mode"], st["roots"]) == ("scan", "header", 1)
    assert st["files"] == 3
    assert st["counts"] == {fv.UNSET: 1, fv.SET: 1, fv.UNREADABLE: 1}
    assert st["fixable"] == 1
    assert b.flacCheckChanged.emits, "the card was told"
    for p, data in files.items():
        assert p.read_bytes() == data
        assert os.stat(p).st_mtime_ns == stamps[p], "a scan never writes"
    report = Path(st["report"])
    assert report.parent == tmp_path / "config" / bridge_flac.REPORTS_DIR
    assert report.name.startswith("flac-check-scan-")
    rows = _report_rows(str(report))
    assert {r["file"]: r["status"] for r in rows} == {
        str(dl / "A" / "set.flac"): fv.SET,
        str(dl / "A" / "unset.flac"): fv.UNSET,
        str(dl / "B" / "junk.flac"): fv.UNREADABLE,
    }
    # The rows are parked for a fix; nothing but the UNSET one is a candidate.
    assert [r.status for r in b._flac_check_rows].count(fv.UNSET) == 1


def test_scan_walks_the_download_folder_and_the_library_folder_only(tmp_path):
    dl = tmp_path / "Downloads"
    lib = tmp_path / "Library"
    elsewhere = tmp_path / "Elsewhere"
    for folder in (dl, lib, elsewhere):
        _fake_flac(folder / "x.flac")

    b = _bridge(tmp_path)  # library scan off: _library_root() is ""
    b.flacCheckScan(False)
    assert {r["file"] for r in _report_rows(b.flacCheckState()["report"])} == {str(dl / "x.flac")}

    b = _bridge(tmp_path, library=str(lib))
    b.flacCheckScan(False)
    st = b.flacCheckState()
    assert st["roots"] == 2
    assert {r["file"] for r in _report_rows(st["report"])} == {str(dl / "x.flac"), str(lib / "x.flac")}


def test_scan_with_the_download_folder_inside_the_library_walks_once(tmp_path):
    lib = tmp_path / "Library"
    dl = lib / "Downloads"
    _fake_flac(dl / "x.flac")
    _fake_flac(lib / "y.flac")
    b = _bridge(tmp_path, library=str(lib), download=str(dl))

    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert st["roots"] == 1
    assert st["files"] == 2


def test_scan_without_a_folder_fails_closed(tmp_path):
    b = _bridge(tmp_path, download="")
    b.flacCheckScan(False)
    st = b.flacCheckState()
    assert st["phase"] == "failed"
    assert st["running"] is False
    assert "download folder" in st["error"]
    assert st["report"] == ""


def test_a_waves_library_that_cannot_be_opened_fails_instead_of_reporting_a_clean_scan(tmp_path):
    """The download folder on a share that is not mounted: not an empty
    library. The run fails and says so, writes no report, and an unfinished
    fix's marker stays (that fix's folders were never looked at)."""
    b = _bridge(tmp_path, download=str(tmp_path / "Share not mounted"), ffmpeg="/usr/bin/true")
    key = b.flacCheckSources()["key"]
    b._waves_prefs["flac_check_resume"] = key
    assert b.flacCheckState()["resume_source"] == "waves"

    b.flacCheckResume()

    st = b.flacCheckState()
    assert (st["phase"], st["running"], st["error"]) == (
        "failed",
        False,
        "The Waves library folder could not be opened.",
    )
    assert st["report"] == ""
    assert not (tmp_path / "config" / bridge_flac.REPORTS_DIR).exists()
    assert b._waves_prefs["flac_check_resume"] == key
    assert st["resume_source"] == "waves", "RESUME is still offered"

    lib = tmp_path / "Library not mounted"
    b = _bridge(tmp_path, download=str(tmp_path / "Share not mounted"), library=str(lib))
    b.flacCheckScan(False)
    assert b.flacCheckState()["error"] == "The Waves library folders could not be opened."


def test_a_waves_library_folder_that_cannot_be_opened_is_counted_and_keeps_the_marker(tmp_path):
    """One of the two folders gone: the scan finishes over the other, says
    how many folders it could not read, lists them in the report, and does
    not take "nothing left to fix" as true of the folder it never saw."""
    lib = tmp_path / "Library"
    _fake_flac(lib / "set.flac", b"\x05" * 16)
    gone = tmp_path / "Share not mounted"
    b = _bridge(tmp_path, download=str(gone), library=str(lib))
    key = b.flacCheckSources()["key"]
    b._waves_prefs["flac_check_resume"] = key

    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert (st["phase"], st["files"], st["fixable"], st["folders_unreadable"]) == ("done", 1, 0, 1)
    assert b._waves_prefs["flac_check_resume"] == key, "the unread folder may still hold the unfinished fix's files"
    rows = _report_rows(st["report"])
    assert [(r["status"], r["file"]) for r in rows] == [
        (fv.SET, str(lib / "set.flac")),
        (fv.FOLDER_UNREADABLE, str(gone)),
    ]

    # The share is back: a full scan that finds nothing left drops the marker.
    gone.mkdir()
    b.flacCheckScan(False)
    st = b.flacCheckState()
    assert (st["phase"], st["folders_unreadable"]) == ("done", 0)
    assert b._waves_prefs["flac_check_resume"] == ""


def _locked(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    os.chmod(folder, 0)
    if os.access(folder, os.R_OK):
        os.chmod(folder, 0o700)
        pytest.skip("permissions are not enforced for this user")


def test_a_chosen_folder_that_exists_but_cannot_be_listed_fails(tmp_path):
    """The folder is there (a network volume the system has not let Waves
    read, or one with no read permission): isdir says yes, listing it fails.
    Never an empty, clean scan, and the marker stays."""
    chosen = tmp_path / "Locked"
    _fake_flac(chosen / "a.flac")
    b = _bridge(tmp_path, ffmpeg="/usr/bin/true")
    _choose(b, chosen)
    b._waves_prefs["flac_check_resume"] = b.flacCheckSources()["key"]
    _locked(chosen)
    try:
        b.flacCheckResume()
    finally:
        os.chmod(chosen, 0o700)
    st = b.flacCheckState()
    assert (st["phase"], st["error"]) == ("failed", "The chosen folder could not be opened.")
    assert b._waves_prefs["flac_check_resume"] == f"folder:{chosen}"


def test_a_subfolder_that_cannot_be_listed_is_counted(tmp_path):
    """Counted and reported. A marker from before this launch is not held by
    a subfolder alone: the scan behind that fix is not known, and a folder no
    scan can ever list (a drive root's lost+found) would hold RESUME on the
    card for good."""
    dl = tmp_path / "Downloads"
    _fake_flac(dl / "Open" / "set.flac", b"\x05" * 16)
    _fake_flac(dl / "Locked" / "a.flac")
    b = _bridge(tmp_path)
    key = b.flacCheckSources()["key"]
    b._waves_prefs["flac_check_resume"] = key
    _locked(dl / "Locked")
    try:
        b.flacCheckScan(False)
    finally:
        os.chmod(dl / "Locked", 0o700)
    st = b.flacCheckState()
    assert (st["phase"], st["files"], st["fixable"], st["folders_unreadable"]) == ("done", 1, 0, 1)
    assert b._waves_prefs["flac_check_resume"] == ""
    assert _report_rows(st["report"])[-1]["status"] == fv.FOLDER_UNREADABLE


@needs_ffmpeg
def test_a_subfolder_no_scan_can_list_never_holds_resume_for_good(tmp_path, monkeypatch):
    """A fix cut short over a library with a folder that is never readable:
    RESUME after a restart finishes the fix, and the marker is gone."""
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    dl = tmp_path / "Downloads"
    _locked(dl / "lost+found")
    try:
        b = _bridge(tmp_path, ffmpeg=FFMPEG)
        _cut_short_fix(tmp_path, monkeypatch, b, dl, 3)
        key = b.flacCheckState()["scope"]
        assert b.flacCheckState()["folders_unreadable"] == 1
        assert b._waves_prefs["flac_check_resume"] == key

        b2 = _bridge(tmp_path, ffmpeg=FFMPEG)
        b2._waves_prefs = dict(b._waves_prefs)
        b2.flacCheckResume()
        st = b2.flacCheckState()
        assert (st["phase"], st["last"], st["counts"].get(fv.SEALED), st["folders_unreadable"]) == (
            "done",
            "fix",
            2,
            1,
        )
        assert b2._waves_prefs["flac_check_resume"] == ""
        assert b2.flacCheckState()["resume_source"] == ""

        # The same within one launch: the cut-short fix's own scan missed it.
        for f in dl.glob("*.flac"):
            f.unlink()
        _cut_short_fix(tmp_path, monkeypatch, b2, dl, 2)
        assert b2._waves_prefs["flac_check_resume"] == key
        b2.flacCheckResume()
        assert b2.flacCheckState()["last"] == "fix"
        assert b2._waves_prefs["flac_check_resume"] == ""
    finally:
        os.chmod(dl / "lost+found", 0o700)


@needs_ffmpeg
def test_a_subfolder_the_cut_short_fix_could_read_keeps_the_marker(tmp_path, monkeypatch):
    """Within one launch the scan behind the cut-short fix is known: a folder
    it read that is now unreadable may still hold that fix's files, so
    neither a scan nor a finished RESUME drops the marker until it opens."""
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    dl = tmp_path / "Downloads"
    b = _bridge(tmp_path, ffmpeg=FFMPEG)
    _real_flac(dl / "Later" / "x.flac", unset=False)
    _cut_short_fix(tmp_path, monkeypatch, b, dl, 3)
    key = b.flacCheckState()["scope"]
    _locked(dl / "Later")
    try:
        b.flacCheckScan(False)
        assert (b.flacCheckState()["fixable"], b.flacCheckState()["folders_unreadable"]) == (2, 1)
        assert b._waves_prefs["flac_check_resume"] == key
        b.flacCheckResume()
        st = b.flacCheckState()
        assert (st["phase"], st["last"], st["fixable"]) == ("done", "fix", 0)
        assert b._waves_prefs["flac_check_resume"] == key, "the unread folder was never looked at"
    finally:
        os.chmod(dl / "Later", 0o700)
    b.flacCheckScan(False)
    assert b.flacCheckState()["folders_unreadable"] == 0
    assert b._waves_prefs["flac_check_resume"] == ""


def test_a_fix_over_a_scan_that_missed_folders(tmp_path):
    """A fix carries its scan's unread-folder count. Finishing it drops a
    marker it set itself (it did all it set out to do), but not the marker
    of an earlier unfinished fix: what that one left in the unread folder
    was never looked at."""
    lib = tmp_path / "Library"
    gone = tmp_path / "Share not mounted"
    for prior in (False, True):
        _fake_flac(lib / "unset.flac")
        b = _bridge(tmp_path, download=str(gone), library=str(lib))
        b._flac_check_ffmpeg = lambda: "/usr/bin/true"
        key = b.flacCheckSources()["key"]
        if prior:
            b._waves_prefs["flac_check_resume"] = key
        b.flacCheckScan(False)
        assert (b.flacCheckState()["fixable"], b.flacCheckState()["folders_unreadable"]) == (1, 1)

        b.flacCheckFix()

        st = b.flacCheckState()
        assert (st["phase"], st["last"], st["folders_unreadable"]) == ("done", "fix", 1)
        assert b._waves_prefs["flac_check_resume"] == (key if prior else ""), prior
        assert _report_rows(st["report"])[-1] == {
            **dict.fromkeys(fv.REPORT_COLUMNS, ""),
            "status": fv.FOLDER_UNREADABLE,
            "meaning": fv.STATUS_LABELS[fv.FOLDER_UNREADABLE],
            "file": str(gone),
        }


def test_two_reports_in_the_same_second_are_both_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(fv.time, "time", lambda: 1_700_000_000.0)
    b = _bridge(tmp_path)
    _fake_flac(tmp_path / "Downloads" / "x.flac")
    b.flacCheckScan(False)
    first = b.flacCheckState()["report"]
    b.flacCheckScan(False)
    second = b.flacCheckState()["report"]
    assert first != second
    assert Path(second).name == Path(first).name[: -len(".csv")] + "-2.csv"
    assert Path(first).exists()


def test_deep_scan_needs_a_decoder(tmp_path):
    b = _bridge(tmp_path, ffmpeg="")
    b._flac_check_ffmpeg = lambda: ""
    _fake_flac(tmp_path / "Downloads" / "x.flac")

    b.flacCheckScan(True)

    st = b.flacCheckState()
    assert st["phase"] == "failed"
    assert "FFmpeg" in st["error"]
    # The header scan still works without one.
    b.flacCheckScan(False)
    assert b.flacCheckState()["phase"] == "done"


def test_a_run_in_progress_blocks_a_second_and_a_stale_runs_events_are_dropped(tmp_path):
    b = _bridge(tmp_path)
    _fake_flac(tmp_path / "Downloads" / "x.flac")
    starts: list = []
    b.threadpool = SimpleNamespace(start=starts.append)  # never runs the worker

    b.flacCheckScan(False)
    assert b.flacCheckState()["running"] is True
    assert len(starts) == 1
    b.flacCheckScan(False)
    b.flacCheckFix()
    assert len(starts) == 1, "a second run is refused while one is on"

    # An event from an older generation changes nothing.
    b._on_flac_check_event(b._flac_check_gen - 1, {"phase": "done", "running": False})
    assert b.flacCheckState()["running"] is True
    b._on_flac_check_event(b._flac_check_gen, {"phase": "done", "running": False})
    assert b.flacCheckState()["running"] is False


def test_cancel_stops_the_walk_and_says_so(tmp_path):
    b = _bridge(tmp_path)
    for i in range(3):
        _fake_flac(tmp_path / "Downloads" / f"d{i}" / "x.flac")
    # Cancel the moment the run starts: the pool runs inline, so the flag is
    # set before the worker's first alive() check.
    real_start = b.threadpool.start

    def start(worker):
        b.flacCheckCancel()
        real_start(worker)

    b.threadpool = SimpleNamespace(start=start)

    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert st["phase"] == "cancelled"
    assert st["running"] is False
    assert st["files"] < 3
    assert st["report"] != "", "what was seen is still reported"


def test_cancel_outside_a_run_is_a_noop(tmp_path):
    b = _bridge(tmp_path)
    b.flacCheckCancel()
    assert not b._flac_check_cancel.is_set()


def test_view_reports_shows_the_newest_report_only_when_there_is_one(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    b = _bridge(tmp_path)
    assert b.flacCheckState()["reports"] is False
    b.flacCheckRevealReports()
    assert b.revealed == []
    _fake_flac(tmp_path / "Downloads" / "x.flac")
    b.flacCheckScan(False)
    assert b.flacCheckState()["reports"] is True
    b.flacCheckRevealReports()
    assert b.revealed == [b.flacCheckState()["report"]]


def test_newest_report_goes_by_the_time_in_the_name_then_the_counter(tmp_path):
    """Written in the same instant, the name decides: its time, then its counter."""
    assert fv.newest_report(tmp_path / "missing") == ""
    assert fv.newest_report(tmp_path) == ""
    assert fv.has_report(tmp_path / "missing") is False
    for name in (
        "flac-check-scan-20261012-020000.csv",
        "flac-check-fix-20261012-020000-2.csv",
        "flac-check-scan-20261012-020000-10.csv",
        "flac-check-scan-20261001-235959.csv",
        "flac-check-scan-20991231-000000.csv.bak",
    ):
        (tmp_path / name).write_text("x")
        os.utime(tmp_path / name, ns=(1_800_000_000 * 10**9,) * 2)
    assert fv.has_report(tmp_path) is True
    assert fv.newest_report(tmp_path) == str(tmp_path / "flac-check-scan-20261012-020000-10.csv")


def test_newest_report_is_the_one_written_last_when_the_clock_fell_back(tmp_path):
    """After the clock falls back an hour (or a move west) a new report's
    name carries an earlier time than the last one's; it is still newest."""
    before = tmp_path / "flac-check-scan-20261101-013000.csv"
    after = tmp_path / "flac-check-scan-20261101-011500.csv"
    for i, path in enumerate((before, after)):
        path.write_text("x")
        os.utime(path, ns=((1_800_000_000 + i * 60) * 10**9,) * 2)
    assert fv.newest_report(tmp_path) == str(after)


def test_view_reports_opens_the_folder_outside_macos(tmp_path, monkeypatch):
    """Handing a .csv to the desktop launcher opens it in a spreadsheet:
    off macOS (no select-in-folder reveal) the folder itself is shown."""
    monkeypatch.setattr(sys, "platform", "linux")
    b = _bridge(tmp_path)
    folder = b._flac_check_report_dir()
    os.makedirs(folder)
    (Path(folder) / "flac-check-scan-20261012-020000.csv").write_text("x")
    b.flacCheckRevealReports()
    assert b.revealed == [folder]


def test_view_reports_works_after_a_relaunch_and_during_a_run(tmp_path, monkeypatch):
    """The reports on disk are found without a run in this launch, and the
    button works while a scan is running."""
    monkeypatch.setattr(sys, "platform", "darwin")
    b = _bridge(tmp_path)
    folder = b._flac_check_report_dir()
    os.makedirs(folder)
    for name in ("flac-check-fix-20261011-120000.csv", "flac-check-scan-20261012-020000.csv", "notes.csv"):
        (Path(folder) / name).write_text("x")
    fresh = _bridge(tmp_path)
    assert fresh.flacCheckState()["reports"] is True
    fresh._flac_check_state["running"] = True
    fresh.flacCheckRevealReports()
    assert fresh.revealed == [os.path.join(folder, "flac-check-scan-20261012-020000.csv")]


# ---- the fix --------------------------------------------------------------------


@needs_ffmpeg
def test_fix_writes_only_the_files_the_scan_found_without_a_signature(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    b = _bridge(tmp_path, ffmpeg=FFMPEG)
    dl = tmp_path / "Downloads"
    md5_unset = _real_flac(dl / "unset.flac", unset=True)
    _real_flac(dl / "sealed.flac", unset=False)
    fake = _fake_flac(dl / "fake.flac")  # unset, but not audio ffmpeg can decode
    before = {p: p.read_bytes() for p in (dl / "sealed.flac", dl / "fake.flac")}

    b.flacCheckScan(False)
    assert b.flacCheckState()["fixable"] == 2
    b.flacCheckFix()

    st = b.flacCheckState()
    assert (st["phase"], st["last"], st["running"]) == ("done", "fix", False)
    assert st["counts"][fv.SEALED] == 1
    assert st["counts"][fv.SET] == 1
    assert st["counts"][fv.DECODE_FAILED] == 1
    assert st["fixable"] == 0
    assert st["files"] == 2, "the fix counts the files it worked on"
    assert "files" not in st["counts"], "counts hold statuses only"
    assert fv.read_streaminfo(dl / "unset.flac").md5 == md5_unset
    assert fv.check_file(str(dl / "unset.flac"), FFMPEG, deep=True).status == fv.OK
    for p, data in before.items():
        assert p.read_bytes() == data, p.name
    assert (dl / "fake.flac").read_bytes() == fake
    report = Path(st["report"])
    assert report.name.startswith("flac-check-fix-")
    assert {r["file"]: r["status"] for r in _report_rows(str(report))} == {
        str(dl / "unset.flac"): fv.SEALED,
        str(dl / "sealed.flac"): fv.SET,
        str(dl / "fake.flac"): fv.DECODE_FAILED,
    }


def test_fix_without_candidates_or_without_a_decoder_does_nothing(tmp_path):
    b = _bridge(tmp_path)
    starts: list = []
    _fake_flac(tmp_path / "Downloads" / "set.flac", b"\x05" * 16)
    b.flacCheckScan(False)
    assert b.flacCheckState()["fixable"] == 0
    b.threadpool = SimpleNamespace(start=starts.append)
    b.flacCheckFix()
    assert starts == []

    b = _bridge(tmp_path, ffmpeg="")
    b._flac_check_ffmpeg = lambda: ""
    _fake_flac(tmp_path / "Downloads" / "unset.flac")
    b.flacCheckScan(False)
    assert b.flacCheckState()["fixable"] == 1
    b.threadpool = SimpleNamespace(start=starts.append)
    b.flacCheckFix()
    assert starts == []
    st = b.flacCheckState()
    assert st["phase"] == "failed"
    assert "FFmpeg" in st["error"]


def test_report_names_are_what_the_factory_wipe_knows(tmp_path):
    """The wipe deletes by an anchored pattern; the names the check writes
    must match it, and nothing looser may."""
    from waves.waves_ui.backend import _FACTORY_WIPE_SUBDIRS

    entry = next(e for e in _FACTORY_WIPE_SUBDIRS if e[0] == bridge_flac.REPORTS_DIR)
    _, exact, patterns = entry
    assert exact == ()
    # The queue's missing-tracks reports share the folder (their own test is
    # tests/test_missing_tracks_export.py); the FLAC check's is the first.
    assert len(patterns) == 2
    pat = patterns[0]
    assert pat.match(fv.report_name("scan", 0))
    assert pat.match(fv.report_name("fix", 1_700_000_000))
    for n in (2, 9, 10, 99, 100, fv._REPORT_SUFFIX_MAX):
        assert pat.match(fv.report_name("scan", 0, n)), n
    for foreign in (
        "my-report.csv",
        "flac-check-scan-2026.csv",
        "flac-check-scan-20260101-010101.csv.bak",
        "flac-check-scan-20260101-010101-1.csv",
        "flac-check-scan-20260101-010101-01.csv",
        "flac-check-scan-20260101-010101-1000.csv",
        "flac-check-scan-20260101-010101-2-2.csv",
        "notes.txt",
    ):
        assert not any(p.match(foreign) for p in patterns), foreign


# ---- what earlier scans found -------------------------------------------------------


def test_a_second_scan_of_an_unchanged_library_says_so_and_reads_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    b = _bridge(tmp_path)
    dl = tmp_path / "Downloads"
    _fake_flac(dl / "A" / "unset.flac")
    _fake_flac(dl / "A" / "set.flac", b"\x01" * 16)
    b.flacCheckScan(False)
    first = b.flacCheckState()
    assert first["cached"] == 0
    assert (tmp_path / "config" / fv.CACHE_NAME).is_file()

    def refuse(path):
        raise AssertionError(path)

    monkeypatch.setattr(fv, "read_streaminfo", refuse)
    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert (st["phase"], st["files"], st["cached"]) == ("done", 2, 2)
    assert st["counts"] == first["counts"]
    assert st["fixable"] == 1
    assert {r["file"]: r["from_earlier_check"] for r in _report_rows(st["report"])} == {
        str(dl / "A" / "set.flac"): "yes",
        str(dl / "A" / "unset.flac"): "yes",
    }


def test_recheck_reads_every_file_again(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    b = _bridge(tmp_path)
    _fake_flac(tmp_path / "Downloads" / "a.flac")
    _fake_flac(tmp_path / "Downloads" / "b.flac", b"\x01" * 16)
    b.flacCheckScan(False)

    b.flacCheckScan(False, True)
    st = b.flacCheckState()
    assert (st["phase"], st["files"], st["cached"]) == ("done", 2, 0)
    assert {r["from_earlier_check"] for r in _report_rows(st["report"])} == {""}

    b.flacCheckScan(False)
    assert b.flacCheckState()["cached"] == 2


def test_a_damaged_cache_file_is_started_over(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    b = _bridge(tmp_path)
    _fake_flac(tmp_path / "Downloads" / "a.flac")
    (tmp_path / "config" / fv.CACHE_NAME).write_bytes(b"this is not a database" * 100)

    b.flacCheckScan(False)
    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert (st["phase"], st["files"], st["cached"]) == ("done", 1, 1)


def test_no_cache_is_opened_during_a_factory_reset(tmp_path):
    b = _bridge(tmp_path)
    b._factory_reset = True
    assert b._flac_check_open_cache() is None
    assert not (tmp_path / "config" / fv.CACHE_NAME).exists()
    assert b._flac_check_cache_event().is_set(), "nothing open, so a reset need not wait"


def test_a_reset_waits_for_an_open_cache_and_the_scan_deletes_it_on_close(tmp_path):
    """Windows deletes no open file: the cache is marked busy while a scan
    holds it, and a reset that came meanwhile has the scan delete it."""
    b = _bridge(tmp_path)
    cache = b._flac_check_open_cache()
    assert cache is not None
    free = b._flac_check_cache_event()
    assert not free.is_set(), "a reset waits while the scan holds the file"
    b._factory_reset = True
    b._flac_check_close_cache(cache)
    assert free.is_set()
    assert not any(n.startswith(fv.CACHE_NAME) for n in os.listdir(tmp_path / "config"))


def test_the_reset_waits_on_the_scan_before_it_wipes():
    src = Path(backend.__file__).read_text(encoding="utf-8")
    body = src[src.index("    def factoryReset(self)") :]
    body = body[: body.index("\n    @Slot", 1)]
    assert body.index("_flac_check_cache_event().wait(_FLAC_CACHE_RELEASE_WAIT_S)") < body.index(
        "for name in _FACTORY_WIPE_FILES"
    )
    assert body.index("_flac_check_cancel.set()") < body.index("_flac_check_cache_event().wait(")
    assert 0 < backend._FLAC_CACHE_RELEASE_WAIT_S <= 10


def _damage_inner_pages(path: Path) -> None:
    size = path.stat().st_size
    with open(path, "r+b") as f:
        for off in range(4096 * 3, size, 4096 * 5):
            f.seek(off + 100)
            f.write(b"\xff" * 200)


def test_a_cache_damaged_past_its_first_pages_is_started_over_after_the_scan(tmp_path):
    """It still opens, so the open-time rebuild never sees it: a read that
    finds it malformed marks it, and closing deletes it for the next scan."""
    b = _bridge(tmp_path)
    path = Path(b._flac_check_cache_path())
    cache = fv.VerdictCache(str(path), now=lambda: 1e12)
    info = fv._parse_streaminfo(8, bytes(range(34)))
    names = [f"/m/{i:05d}{'x' * 200}.flac" for i in range(3000)]
    for name in names:
        cache.store(fv.Row(name, fv.SET, info=info, size=10, mtime_ns=1), (10, 1), False)
    cache.close()
    _damage_inner_pages(path)

    cache = b._flac_check_open_cache()
    assert cache is not None
    for name in names:
        cache.remembered(name, False, (10, 1))
    assert cache.damaged
    b._flac_check_close_cache(cache)

    assert not path.exists()
    fresh = b._flac_check_open_cache()
    assert fresh is not None and not fresh.damaged
    b._flac_check_close_cache(fresh)


def test_a_locked_cache_is_not_taken_for_a_damaged_one(tmp_path):
    b = _bridge(tmp_path)
    cache = b._flac_check_open_cache()
    cache._drop(sqlite3.OperationalError("database is locked"))
    assert not cache.damaged
    b._flac_check_close_cache(cache)
    assert Path(b._flac_check_cache_path()).exists()


def test_the_factory_wipe_takes_the_cache_and_its_sidecars():
    from waves.waves_ui.backend import _FACTORY_WIPE_FILES

    for suffix in ("", "-journal", "-wal", "-shm"):
        assert fv.CACHE_NAME + suffix in _FACTORY_WIPE_FILES


# ---- the folder choice -----------------------------------------------------------


def _choose(b: _Stub, folder: Path | str) -> None:
    b.flacCheckSetFolder(str(folder))


def test_the_defaults_name_the_waves_library():
    from waves.waves_ui.backend import WavesBridge

    defaults = WavesBridge._default_waves_prefs(SimpleNamespace())
    assert defaults["flac_check_source"] == "waves"
    assert defaults["flac_check_folder"] == ""


def test_sources_report_the_selection_and_the_waves_folders(tmp_path):
    lib = tmp_path / "Library"
    b = _bridge(tmp_path, library=str(lib))
    src = b.flacCheckSources()
    assert src["source"] == "waves"
    assert src["folder"] == ""
    assert src["waves"] == [str(tmp_path / "Downloads"), str(lib)]
    assert src["key"] == "waves:" + "\n".join(src["waves"])

    other = tmp_path / "Elsewhere"
    _choose(b, other)
    src = b.flacCheckSources()
    assert (src["source"], src["folder"], src["key"]) == ("folder", str(other), f"folder:{other}")
    assert src["waves"] == [str(tmp_path / "Downloads"), str(lib)], "the Waves folders are still listed"


def test_choosing_a_folder_takes_only_an_absolute_path_saves_it_and_selects_it(tmp_path):
    b = _bridge(tmp_path)
    for bad in ("", "   ", "relative/music", "./music"):
        b.flacCheckSetFolder(bad)
    assert b._waves_prefs == {"flac_check_source": "waves", "flac_check_folder": ""}
    assert b.saves == []
    assert b.flacCheckChanged.emits == []

    _choose(b, tmp_path / "Elsewhere")
    assert b._waves_prefs == {"flac_check_source": "folder", "flac_check_folder": str(tmp_path / "Elsewhere")}
    assert b.saves[-1] == b._waves_prefs, "the choice is written to the prefs file"
    assert len(b.flacCheckChanged.emits) >= 1

    b.flacCheckSetSource("waves")
    assert b._waves_prefs["flac_check_source"] == "waves"
    assert b._waves_prefs["flac_check_folder"] == str(tmp_path / "Elsewhere"), "the folder is kept for next time"
    for bad in ("", "library", "FOLDER", "/etc"):
        b.flacCheckSetSource(bad)
    assert b._waves_prefs["flac_check_source"] == "waves"


def test_the_choice_cannot_change_while_a_run_is_on(tmp_path):
    b = _bridge(tmp_path)
    b._flac_check_state["running"] = True
    _choose(b, tmp_path / "Elsewhere")
    b.flacCheckSetSource("folder")
    assert b._waves_prefs == {"flac_check_source": "waves", "flac_check_folder": ""}
    assert b.saves == []


def test_a_chosen_folder_scan_walks_that_folder_only(tmp_path):
    lib = tmp_path / "Library"
    b = _bridge(tmp_path, library=str(lib))
    _fake_flac(tmp_path / "Downloads" / "dl.flac")
    _fake_flac(lib / "lib.flac")
    other = tmp_path / "Elsewhere"
    _fake_flac(other / "Album" / "pick.flac")
    _choose(b, other)

    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert (st["phase"], st["roots"], st["files"], st["scope"]) == ("done", 1, 1, f"folder:{other}")
    assert [r["file"] for r in _report_rows(st["report"])] == [str(other / "Album" / "pick.flac")]


def test_the_waves_library_scan_still_walks_both_folders_after_a_folder_was_chosen(tmp_path):
    lib = tmp_path / "Library"
    b = _bridge(tmp_path, library=str(lib))
    _fake_flac(tmp_path / "Downloads" / "dl.flac")
    _fake_flac(lib / "lib.flac")
    _fake_flac(tmp_path / "Elsewhere" / "pick.flac")
    _choose(b, tmp_path / "Elsewhere")
    b.flacCheckSetSource("waves")

    b.flacCheckScan(False)

    st = b.flacCheckState()
    assert sorted(r["file"] for r in _report_rows(st["report"])) == sorted(
        [str(tmp_path / "Downloads" / "dl.flac"), str(lib / "lib.flac")]
    )


def test_a_chosen_source_without_a_usable_folder_fails_closed(tmp_path):
    for folder in ("", "relative/music"):
        b = _bridge(tmp_path)
        starts: list = []
        b.threadpool = SimpleNamespace(start=starts.append)
        # As a hand-edited prefs file could leave it: the setters never would.
        b._waves_prefs.update(flac_check_source="folder", flac_check_folder=folder)
        b.flacCheckScan(False)
        st = b.flacCheckState()
        assert (st["phase"], st["error"]) == ("failed", "Choose a folder first."), folder
        assert starts == []


def test_a_chosen_folder_that_is_gone_fails_instead_of_reporting_a_clean_scan(tmp_path):
    b = _bridge(tmp_path)
    _choose(b, tmp_path / "Unplugged")
    b.flacCheckScan(False)
    st = b.flacCheckState()
    assert (st["phase"], st["running"]) == ("failed", False)
    assert st["error"] == "The chosen folder could not be opened."
    assert st["report"] == ""
    assert not (tmp_path / "config" / bridge_flac.REPORTS_DIR).exists()


def test_a_fix_is_refused_once_the_selection_names_other_folders(tmp_path):
    b = _bridge(tmp_path)
    b._flac_check_ffmpeg = lambda: "/usr/bin/true"
    _fake_flac(tmp_path / "Downloads" / "unset.flac")
    _fake_flac(tmp_path / "Elsewhere" / "other.flac")
    b.flacCheckScan(False)
    assert b.flacCheckState()["fixable"] == 1
    starts: list = []
    b.threadpool = SimpleNamespace(start=starts.append)

    # Another folder chosen after the scan: the rows are not what the card shows.
    _choose(b, tmp_path / "Elsewhere")
    emits = len(b.flacCheckChanged.emits)
    b.flacCheckFix()
    assert starts == []
    # Said on the card, never a silent no-op the user could keep clicking.
    st = b.flacCheckState()
    assert (st["phase"], st["running"], st["error"]) == (
        "failed",
        False,
        "The folders changed since the scan. Scan again first.",
    )
    assert len(b.flacCheckChanged.emits) == emits + 1

    # Back on the folders that were scanned: the fix may go ahead.
    b.flacCheckSetSource("waves")
    b.flacCheckFix()
    assert len(starts) == 1
    assert b.flacCheckState()["scope"] == "waves:" + str(tmp_path / "Downloads")


def test_a_fix_is_refused_once_the_waves_library_folders_moved(tmp_path):
    b = _bridge(tmp_path)
    b._flac_check_ffmpeg = lambda: "/usr/bin/true"
    _fake_flac(tmp_path / "Downloads" / "unset.flac")
    b.flacCheckScan(False)
    assert b.flacCheckState()["fixable"] == 1
    starts: list = []
    b.threadpool = SimpleNamespace(start=starts.append)

    b.settings.data.download_base_path = str(tmp_path / "NewDownloads")
    b.flacCheckFix()
    assert starts == []
    assert b.flacCheckState()["phase"] == "failed"


# ---- a factory reset while a run is on ---------------------------------------


def test_a_run_cut_short_by_a_factory_reset_writes_no_report(tmp_path):
    """The reset wipes the config folder; a report (every file the run saw,
    by full path) must not land there after it. The run's outcome is
    dropped instead of written."""
    b = _bridge(tmp_path)
    for i in range(3):
        _fake_flac(tmp_path / "Downloads" / f"{i}.flac")
    reports = tmp_path / "config" / bridge_flac.REPORTS_DIR
    real_start = b.threadpool.start

    def start(worker):
        # What backend.factoryReset does to the check, before the wipe.
        b._factory_reset = True
        b._flac_check_gen += 1
        b._flac_check_cancel.set()
        real_start(worker)

    b.threadpool = SimpleNamespace(start=start)

    b.flacCheckScan(False)

    assert not reports.exists(), "nothing written back into the wiped folder"
    assert b.flacCheckState()["running"] is True, "the superseded run's outcome is dropped at the handler"


def test_a_superseded_run_writes_no_report_either(tmp_path):
    b = _bridge(tmp_path)
    _fake_flac(tmp_path / "Downloads" / "a.flac")
    rows = fv.scan([str(tmp_path / "Downloads")])
    b._flac_check_gen = 5
    reports = tmp_path / "config" / bridge_flac.REPORTS_DIR

    b._flac_check_finish(4, "scan", rows, cancelled=False)

    assert not reports.exists()
    assert b.flacCheckState()["phase"] == "idle"


# ---- progress and resume --------------------------------------------------------


def test_time_left_waits_for_a_pace_then_scales_the_elapsed_time():
    tl = bridge_flac._time_left
    assert tl(2.0, 10, 100) == -1, "too early to go by"
    assert tl(10.0, 0, 100) == -1, "nothing done yet"
    assert tl(10.0, 100, 100) == -1, "nothing left"
    assert tl(10.0, 5, 0) == -1, "no total"
    assert tl(10.0, 25, 100) == 30


def test_progress_posts_the_walk_then_the_total_and_weighs_a_fix_by_size(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(bridge_flac.time, "monotonic", lambda: clock[0])
    posts: list[dict] = []
    scan = bridge_flac._Progress(posts.append)
    scan.on_walk(1, False)
    scan.on_walk(2, False)  # rate-limited: same instant
    clock[0] += 1
    scan.on_walk(3, True)  # the total always goes out
    assert posts == [{"found": 1}, {"found": 3, "total": 3}]

    posts.clear()
    clock[0] = 200.0
    fix = bridge_flac._Progress(posts.append, 2, {"/a.flac": 100, "/b.flac": 900})
    clock[0] = 210.0
    fix.on_row(fv.Row("/a.flac", fv.SEALED))
    # 100 of 1000 bytes in 10 s: 90 s left, though half the files are done.
    assert posts == [{"files": 1, "counts": {fv.SEALED: 1}, "eta": 90}]


def test_a_scan_and_a_fix_carry_their_totals(tmp_path):
    b = _bridge(tmp_path, ffmpeg="/usr/bin/true")
    b._flac_check_ffmpeg = lambda: "/usr/bin/true"
    for i in range(3):
        _fake_flac(tmp_path / "Downloads" / f"{i}.flac")
    b.flacCheckScan(False)
    st = b.flacCheckState()
    assert (st["total"], st["found"], st["files"], st["eta"]) == (3, 3, 3, -1)
    starts: list = []
    b.threadpool = SimpleNamespace(start=starts.append)  # the fix stays running
    b.flacCheckFix()
    st = b.flacCheckState()
    assert (st["phase"], st["total"], st["files"]) == ("fixing", 3, 0)


def _cut_short_fix(tmp_path, monkeypatch, b, folder: Path, n: int) -> None:
    """Scan ``folder`` (n unset files) and fix, the run stopping after the
    first file is written: the cancel flag is what a quit sets."""
    for i in range(n):
        _real_flac(folder / f"{i}.flac", unset=True)
    b.flacCheckScan(False)
    assert b.flacCheckState()["fixable"] == n
    real_seal = fv.seal_file

    def seal_then_quit(*args, **kwargs):
        row = real_seal(*args, **kwargs)
        b._flac_check_cancel.set()
        return row

    monkeypatch.setattr(fv, "seal_file", seal_then_quit)
    b.flacCheckFix()
    monkeypatch.setattr(fv, "seal_file", real_seal)
    st = b.flacCheckState()
    assert (st["phase"], st["counts"][fv.SEALED], st["fixable"]) == ("cancelled", 1, n - 1)


@needs_ffmpeg
def test_a_fix_cut_short_is_remembered_and_resume_finishes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    b = _bridge(tmp_path, ffmpeg=FFMPEG)
    dl = tmp_path / "Downloads"
    _cut_short_fix(tmp_path, monkeypatch, b, dl, 3)
    key = b.flacCheckState()["scope"]
    assert b._waves_prefs["flac_check_resume"] == key, "kept until the fix finishes"
    signed = (dl / "0.flac").read_bytes()

    # The next launch: a fresh bridge over the same prefs.
    b2 = _bridge(tmp_path, ffmpeg=FFMPEG)
    b2._waves_prefs = dict(b._waves_prefs)
    st = b2.flacCheckState()
    assert (st["phase"], st["resume_source"], st["resume_folders"]) == ("idle", "waves", [str(dl)])

    b2.flacCheckResume()

    st = b2.flacCheckState()
    assert (st["phase"], st["last"], st["files"]) == ("done", "fix", 2)
    assert st["counts"] == {fv.SEALED: 2, fv.SET: 1}
    assert (dl / "0.flac").read_bytes() == signed, "a file the first run signed is not written again"
    for i in range(3):
        assert fv.check_file(str(dl / f"{i}.flac"), FFMPEG, deep=True).status == fv.OK
    assert b2._waves_prefs["flac_check_resume"] == ""
    assert b2.flacCheckState()["resume_source"] == ""


@needs_ffmpeg
def test_resume_selects_the_chosen_folder_it_was_writing_in_again(tmp_path, monkeypatch):
    monkeypatch.setattr(fv, "SETTLE_SECONDS", 0.0)
    b = _bridge(tmp_path, ffmpeg=FFMPEG)
    chosen = tmp_path / "Elsewhere"
    _choose(b, chosen)
    _cut_short_fix(tmp_path, monkeypatch, b, chosen, 2)
    b.flacCheckSetSource("waves")  # the card moved on to the Waves library
    st = b.flacCheckState()
    assert (st["resume_source"], st["resume_folders"]) == ("folder", [str(chosen)])

    b.flacCheckResume()

    assert b._waves_prefs["flac_check_source"] == "folder"
    st = b.flacCheckState()
    assert (st["last"], st["phase"], st["counts"].get(fv.SEALED)) == ("fix", "done", 1)
    assert b._waves_prefs["flac_check_resume"] == ""


def test_a_resume_that_finds_nothing_left_clears_the_marker_and_writes_nothing(tmp_path):
    b = _bridge(tmp_path, ffmpeg="/usr/bin/true")
    _fake_flac(tmp_path / "Downloads" / "set.flac", b"\x05" * 16)
    b._waves_prefs["flac_check_resume"] = b.flacCheckSources()["key"]
    starts: list = []
    real_start = b.threadpool.start

    def start(worker):
        starts.append(worker.fn.__name__)
        real_start(worker)

    b.threadpool = SimpleNamespace(start=start)

    b.flacCheckResume()

    assert starts == ["_flac_check_scan_job"], "no fix follows a scan that found nothing"
    assert b.flacCheckState()["last"] == "scan"
    assert b._waves_prefs["flac_check_resume"] == ""


def test_an_ordinary_scan_never_starts_a_fix(tmp_path):
    b = _bridge(tmp_path, ffmpeg="/usr/bin/true")
    _fake_flac(tmp_path / "Downloads" / "unset.flac")
    b._waves_prefs["flac_check_resume"] = b.flacCheckSources()["key"]
    starts: list = []
    real_start = b.threadpool.start
    b.threadpool = SimpleNamespace(start=lambda w: (starts.append(w.fn.__name__), real_start(w)))

    b.flacCheckScan(False)

    assert starts == ["_flac_check_scan_job"]
    assert b.flacCheckState()["fixable"] == 1
    assert b._waves_prefs["flac_check_resume"] != "", "files are still left: the marker stays"


def test_no_resume_is_offered_once_the_waves_library_folders_moved(tmp_path):
    b = _bridge(tmp_path)
    b._waves_prefs["flac_check_resume"] = "waves:" + str(tmp_path / "OldDownloads")
    starts: list = []
    b.threadpool = SimpleNamespace(start=starts.append)
    assert b.flacCheckState()["resume_source"] == ""
    b.flacCheckResume()
    assert starts == []
    assert b.flacCheckState()["phase"] == "idle"


def test_the_defaults_hold_no_unfinished_fix():
    from waves.waves_ui.backend import WavesBridge

    assert WavesBridge._default_waves_prefs(SimpleNamespace())["flac_check_resume"] == ""
