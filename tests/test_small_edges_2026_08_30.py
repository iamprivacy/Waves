"""The small edges: two loggers, one trail; a name that fits; a real errno.

* The breadcrumb dump limiter is per handler, and install put one handler on
  each of the two logger trees, so an error from a waves.* logger and one from
  a third-party root-tree logger seconds apart each wrote the whole trail
  inside the window the limiter exists to hold.
* A factory reset kept the Windows updater's own update.log, which quotes
  install paths carrying the user's name, and a crashed helper's batch file.
* Deep on Windows, the staging name's unique part had a floor of ten characters
  whatever the arithmetic said, so parents in the last ten characters before
  the cap failed every staging attempt while the destination itself fitted.
* The merge handler read the loop variable, so a target file that would not
  open at all replaced the real error with a NameError.
* And the name trim subtracted a UTF-16 unit count from a UTF-8 byte count,
  which agrees for ASCII and nothing else: a CJK title gave back a third of
  what it owed and the halving backstop then took half the stem.
"""

from __future__ import annotations

import logging
import os
import pathlib
import sys
import threading
from unittest.mock import MagicMock

import pytest

from waves import download as download_mod
from waves.download import Download, _staging_path
from waves.helper import path as path_helper
from waves.helper.path import PATH_LENGTH_MAX, _longest_stem_that_fits
from waves.waves_ui import diagnostics
from waves.waves_ui.backend import _FACTORY_WIPE_SUBDIRS


# --------------------------------------------------------------------------- #
# F-39: one trail per window, not one per logger tree.
# --------------------------------------------------------------------------- #
class _Sink(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.NOTSET)
        self.records: list[str] = []

    def handle(self, record):
        self.records.append(record.getMessage())

    def emit(self, record):  # pragma: no cover - handle() is what the dumper calls
        self.records.append(record.getMessage())


def test_two_logger_trees_share_one_dump_limiter():
    crumbs = diagnostics._BreadcrumbHandler(capacity=5)
    crumbs.setFormatter(logging.Formatter("%(message)s"))
    for i in range(3):
        crumbs.emit(logging.LogRecord("waves.x", logging.INFO, "", 0, f"crumb {i}", None, None))
    sink = _Sink()
    dumper = diagnostics._CrumbDumpHandler(crumbs, sink)

    # The waves tree's error, then the root tree's a moment later. One handler
    # instance on both trees is what makes the second one hit the limiter.
    dumper.emit(logging.LogRecord("waves.download", logging.ERROR, "", 0, "boom", None, None))
    first = len(sink.records)
    dumper.emit(logging.LogRecord("urllib3", logging.ERROR, "", 0, "also boom", None, None))

    assert first > 0, "the first error must write the trail"
    assert len(sink.records) == first, "the trail was written twice inside one window"


def test_install_builds_a_single_dump_handler():
    """Structural: the handler is built once and added to both trees."""
    import inspect

    source = inspect.getsource(diagnostics.install)

    assert source.count("_CrumbDumpHandler(") == 1, "a dump handler per tree gives a limiter per tree"


# --------------------------------------------------------------------------- #
# F-41: the reset takes Waves' own files with it.
# --------------------------------------------------------------------------- #
def test_the_updater_log_and_helper_are_named_in_the_factory_wipe():
    named = {name for _sub, names, _pats in _FACTORY_WIPE_SUBDIRS for name in names}

    assert "update.log" in named, "update.log quotes install paths that carry the user's name"
    assert "apply_update.bat" in named, "a crashed helper's script kept the updates folder alive"


def test_the_wipe_still_names_what_it_always_did():
    subdirs = {sub for sub, _names, _pats in _FACTORY_WIPE_SUBDIRS}

    assert os.path.join("updates", "staged") in subdirs
    assert "bin" in subdirs
    assert "applied.json" in {name for _sub, names, _pats in _FACTORY_WIPE_SUBDIRS for name in names}


def _wipes(subdir: str, name: str) -> bool:
    """Would the reset take this file out of that subdirectory?"""
    for rel, names, patterns in _FACTORY_WIPE_SUBDIRS:
        if rel == subdir:
            return name in names or any(pat.match(name) for pat in patterns)
    raise AssertionError(f"{subdir} is not in the wipe list at all")


def test_the_staged_swap_marker_falls_with_the_updates_folder():
    """The install path the marker records is the leak the wipe exists to take.

    armed.json holds the whole install result, "applied_to" included, which on
    Windows is C:\\Users\\<name>\\... . It arrived after update.log was listed
    and inherited none of its treatment, so the folder stopped falling at all
    and the path stayed on disk across a reset that promises otherwise.
    """
    assert _wipes("updates", "armed.json"), "the staged-swap marker records the install path"
    assert _wipes("updates", "install.lock"), "an orphaned lock kept the updates folder alive"


def test_the_per_pid_swap_helper_falls_too():
    """The helper is named per pid (apply_update_<pid>.bat), so the exact name
    in the list has not matched anything since it was renamed."""
    assert _wipes("updates", "apply_update_66648.bat")
    assert _wipes("updates", "apply_update_1.bat")
    # Anchored at both ends: nothing that merely looks similar is deletable.
    assert not _wipes("updates", "apply_update_.bat")
    assert not _wipes("updates", "apply_update_12.bat.bak")
    assert not _wipes("updates", "my_apply_update_12.bat")


def test_the_ffmpeg_installer_strays_fall_with_the_bin_folder():
    """Both mkstemp shapes ffmpeg_manager stages through, so a crashed install
    cannot keep bin/ alive forever."""
    assert _wipes("bin", "ffmpeg.QmX7d2.new")
    assert _wipes("bin", "ffmpeg.exe.QmX7d2.new")
    assert _wipes("bin", "ffmpeg.json.a1b2c3.tmp")
    # The download temp has no Waves-written prefix to anchor on: it stays, on
    # purpose, and keeps its directory alive rather than widening the match.
    assert not _wipes("bin", "tmpq8s7d1.zip")
    assert not _wipes("bin", "notffmpeg.x.new")


# --------------------------------------------------------------------------- #
# F-46: a staging name that fits beside a destination that fits.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("parent_len", [200, 230, 243, 248, 250, 251])
def test_a_deep_parent_still_gets_a_staging_name_that_fits(parent_len, monkeypatch):
    """The band the ten-character floor could not reach. The destination itself
    fits at these depths (a one-character stem plus '.flac'), so the staging
    name has to as well or the track can never land."""
    monkeypatch.setattr(download_mod, "_PATH_LENGTH_MAX", 259)
    parent = pathlib.PurePosixPath("/" + "d" * (parent_len - 1))
    destination = pathlib.Path(str(parent)) / "X.flac"

    staged = _staging_path(destination)

    assert len(str(staged)) <= 259, f"{len(str(staged))} > 259 at parent {parent_len}"
    assert staged.name.endswith(".tmp")


def test_the_staging_name_keeps_something_unique_however_deep(monkeypatch):
    """A name with nothing unique in it is shared by every track in the folder."""
    monkeypatch.setattr(download_mod, "_PATH_LENGTH_MAX", 259)
    destination = pathlib.Path("/" + "d" * 251) / "X.flac"

    staged = _staging_path(destination)

    assert len(staged.name) > len("...tmp"), "no unique part left at all"


def test_an_ordinary_path_still_gets_a_full_uuid(tmp_path):
    staged = _staging_path(tmp_path / "Song.flac")

    assert staged.name.startswith(".Song.flac.")
    assert staged.name.endswith(".tmp")
    assert len(staged.name) == len(".Song.flac.") + 36 + len(".tmp")


# --------------------------------------------------------------------------- #
# F-47: the real error, not a NameError over it.
# --------------------------------------------------------------------------- #
def _make_download(tmp_path: pathlib.Path) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    return dl


def test_a_target_that_will_not_open_is_a_clean_failure(tmp_path):
    """The per-item temp directory vanished between download and merge: the
    handler used to read a loop variable that had never been bound."""
    dl = _make_download(tmp_path)
    gone = tmp_path / "not-there" / "merged.flac"
    segments = [
        download_mod.DownloadSegmentResult(True, "", tmp_path / f"seg{i}", i)
        for i in range(2)  # more than one, so the spurious-tail arm is reached
    ]

    assert dl._segments_merge(gone, segments) is False


def test_a_single_segment_target_that_will_not_open_fails_too(tmp_path):
    dl = _make_download(tmp_path)
    gone = tmp_path / "not-there" / "merged.flac"
    segments = [download_mod.DownloadSegmentResult(True, "", tmp_path / "seg0", 0)]

    assert dl._segments_merge(gone, segments) is False


def test_a_real_merge_still_works(tmp_path):
    dl = _make_download(tmp_path)
    segments = []
    for i in range(3):
        part = tmp_path / f"seg{i}"
        part.write_bytes(bytes([i]) * 4)
        segments.append(download_mod.DownloadSegmentResult(True, "", part, i))
    target = tmp_path / "merged.flac"

    assert dl._segments_merge(target, segments) is True
    assert target.read_bytes() == b"\x00" * 4 + b"\x01" * 4 + b"\x02" * 4


# --------------------------------------------------------------------------- #
# F-48: the trim gives back what it owes, in the units it owes them in.
# --------------------------------------------------------------------------- #
CJK = "楽曲"  # two characters: one UTF-16 unit each, three bytes each


def test_a_cjk_title_gives_back_characters_not_thirds_of_them(monkeypatch):
    """On Windows the cap counts UTF-16 units; the stem's bytes are three times
    that for CJK, so a byte-count trim removed a third of what it owed and the
    halving backstop then took half the stem."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(path_helper, "PATH_LENGTH_MAX", 259, raising=False)
    directory = pathlib.PurePosixPath("/" + "d" * 200)
    stem = CJK * 40  # 40 characters over what the parent leaves room for

    kept = _longest_stem_that_fits(pathlib.Path(str(directory)), stem, ".flac")

    assert path_helper._path_length(pathlib.Path(str(directory)) / (kept + ".flac")) <= 259
    # And it kept as much as it possibly could: one more character overflows.
    assert path_helper._path_length(pathlib.Path(str(directory)) / (stem[: len(kept) + 1] + ".flac")) > 259


def test_an_ascii_title_is_trimmed_exactly_as_before(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(path_helper, "PATH_LENGTH_MAX", 259, raising=False)
    directory = pathlib.Path("/" + "d" * 200)
    stem = "a" * 100

    kept = _longest_stem_that_fits(directory, stem, ".flac")

    # 201 for the directory, 1 for the separator, 5 for the extension.
    assert len(kept) == 259 - 201 - 1 - len(".flac")


def test_the_extension_always_survives(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(path_helper, "PATH_LENGTH_MAX", 259, raising=False)
    directory = pathlib.Path("/" + "d" * 250)

    kept = _longest_stem_that_fits(directory, "a" * 50, ".flac")

    assert kept and len(kept) >= 1
    assert PATH_LENGTH_MAX  # imported for the reader, not the assertion


# --------------------------------------------------------------------------- #
# F-40: the redactor learns a credential when it is minted, not once at login.
# --------------------------------------------------------------------------- #
def test_a_token_persist_tells_the_listener():
    from waves.config import Tidal

    tidal = Tidal.__new__(Tidal)
    seen: list[object] = []
    tidal.on_session_credentials = seen.append
    tidal.session = object()

    tidal._note_session_credentials()

    assert seen == [tidal.session]


def test_no_listener_is_not_an_error():
    from waves.config import Tidal

    tidal = Tidal.__new__(Tidal)
    tidal.on_session_credentials = None
    tidal.session = object()

    tidal._note_session_credentials()  # a headless run installs nothing


def test_a_listener_that_raises_never_takes_the_login_down():
    from waves.config import Tidal

    tidal = Tidal.__new__(Tidal)

    def boom(session):
        raise RuntimeError("the redactor is unhappy")

    tidal.on_session_credentials = boom
    tidal.session = object()

    tidal._note_session_credentials()


def test_the_refresh_and_the_persist_both_report():
    """Structural: an Atmos switch forces a refresh that is deliberately NOT
    persisted, so the persist call alone would miss every one of them."""
    import inspect

    from waves.config import Tidal

    assert "_note_session_credentials()" in inspect.getsource(Tidal.token_persist)
    assert "_note_session_credentials()" in inspect.getsource(Tidal._reauthenticate_current_client)


def test_the_bridge_installs_itself_as_the_listener():
    import inspect

    from waves.waves_ui.backend import WavesBridge

    source = inspect.getsource(WavesBridge.__init__)

    assert "on_session_credentials = self._register_session_secrets" in source


def test_the_registrar_takes_a_session_it_is_handed():
    """The hook passes the session; the login path passes nothing and the
    bridge reads its own. Both must work."""
    import inspect

    from waves.waves_ui.backend import WavesBridge

    assert "session=None" in str(inspect.signature(WavesBridge._register_session_secrets))
