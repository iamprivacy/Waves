"""Three things at the edges of starting up, and of landing a file.

* ``settings.json`` holding valid JSON that is not an OBJECT ("[]", "null", a
  bare string, a number) raised AttributeError out of the parse, past the arm
  that exists to move a broken config aside and carry on. It was an uncaught
  traceback at every launch, and only deleting the file by hand recovered the
  app. Same file, same code path, for ``token.json``.
* A track whose swap into the library had already succeeded was reported FAILED
  when the throwaway source refused to unlink for a moment (a Windows scanner
  holding it, the very lock the retry helpers exist for): the retry found the
  destination occupied and gave up, so the row went red over a file that was
  in place, and its lyrics and cover never followed.
* And the legacy-config migration's breadcrumb was written before diagnostics
  were installed, so the one line recording a FAILED migration reached neither
  the ring, the disk log, nor an exported bundle.
"""

from __future__ import annotations

import inspect
import json
import logging
import pathlib
import threading
from unittest.mock import MagicMock

import pytest

from waves.config import BaseConfig
from waves.download import Download
from waves.model.cfg import Settings as ModelSettings
from waves.waves_ui import app as waves_app


# --------------------------------------------------------------------------- #
# F-18: a config that parses but is not an object.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("body", ["[]", "null", '"a string"', "3", "[1, 2, 3]", "true"])
def test_a_config_that_is_not_an_object_heals_instead_of_crashing(tmp_path, body, capsys):
    path = tmp_path / "settings.json"
    path.write_text(body, encoding="utf-8")
    cfg = BaseConfig()
    cfg.cls_model = ModelSettings
    cfg.file_path = str(path)
    cfg.path_base = str(tmp_path)

    assert cfg.read(str(path)) is False

    assert (tmp_path / "settings.json.bak").read_text(encoding="utf-8") == body
    assert cfg.data == ModelSettings(), "a broken config must leave the app on defaults"
    # And the replacement is a real config again, not the corrupt shape.
    assert isinstance(json.loads(path.read_text(encoding="utf-8")), dict)


def test_an_object_that_is_simply_unknown_still_reads(tmp_path):
    """Only the SHAPE is the failure; unknown keys have always been survivable."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"not_a_setting": 1}), encoding="utf-8")
    cfg = BaseConfig()
    cfg.cls_model = ModelSettings
    cfg.file_path = str(path)
    cfg.path_base = str(tmp_path)

    assert cfg.read(str(path)) is True
    assert not (tmp_path / "settings.json.bak").exists()


def test_a_missing_config_is_not_a_broken_one(tmp_path):
    cfg = BaseConfig()
    cfg.cls_model = ModelSettings
    cfg.file_path = str(tmp_path / "settings.json")
    cfg.path_base = str(tmp_path)

    assert cfg.read(str(tmp_path / "settings.json")) is False
    assert not (tmp_path / "settings.json.bak").exists(), "nothing was there to back up"


# --------------------------------------------------------------------------- #
# F-19: past the swap, the track has landed.
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


def test_a_source_that_will_not_unlink_does_not_fail_a_landed_track(tmp_path, monkeypatch):
    dl = _make_download(tmp_path)
    source = tmp_path / "staged.flac"
    source.write_bytes(b"audio")
    destination = tmp_path / "library" / "Song.flac"
    destination.parent.mkdir()

    def refuse(self, missing_ok=False):
        raise PermissionError("the scanner has it open")

    monkeypatch.setattr(pathlib.Path, "unlink", refuse)

    assert dl._stage_and_swap(source, destination, skip_if_exists=False) is True
    assert destination.read_bytes() == b"audio", "the track is in the library"


def test_a_failure_before_the_swap_is_still_a_failure(tmp_path, monkeypatch):
    """The guard covers the cleanup after the swap, never the swap itself."""
    dl = _make_download(tmp_path)
    source = tmp_path / "staged.flac"
    source.write_bytes(b"audio")
    destination = tmp_path / "library" / "Song.flac"
    destination.parent.mkdir()

    def refuse(self, target):
        raise PermissionError("the destination is locked")

    monkeypatch.setattr(pathlib.Path, "replace", refuse)

    with pytest.raises(PermissionError):
        dl._stage_and_swap(source, destination, skip_if_exists=False)

    assert not destination.exists()
    leftovers = [p.name for p in destination.parent.iterdir()]
    assert leftovers == [], f"the staging temp survived: {leftovers}"


def test_the_source_is_still_taken_away_when_it_can_be(tmp_path):
    dl = _make_download(tmp_path)
    source = tmp_path / "staged.flac"
    source.write_bytes(b"audio")
    destination = tmp_path / "library" / "Song.flac"
    destination.parent.mkdir()

    assert dl._stage_and_swap(source, destination, skip_if_exists=False) is True
    assert not source.exists()


# --------------------------------------------------------------------------- #
# F-22: the migration breadcrumb has to be able to reach the ring.
# --------------------------------------------------------------------------- #
def test_the_migration_breadcrumb_is_logged_after_diagnostics_are_installed():
    """Structural: the bridge is what installs the handlers, so the line has to
    come after it. Logged any earlier it was dropped at the root default (INFO)
    or reached stderr only (WARNING), which a packaged build cannot show, and it
    never reached the ring, the disk log or an exported bundle."""
    source = inspect.getsource(waves_app.waves_activate)
    at_bridge = source.index("WavesBridge(tidal=tidal)")
    at_breadcrumb = source.index("_log_config_migration()")

    assert at_breadcrumb > at_bridge, "the migration outcome is logged before any handler exists"


class _Catcher(logging.Handler):
    """Attached to waves.config itself, not to the root: diagnostics.install
    stops the waves tree propagating, and whether it has run depends on what
    else the session touched, so caplog sees nothing when it has."""

    def __init__(self):
        super().__init__(level=logging.NOTSET)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _breadcrumbs(fn):
    logger = logging.getLogger("waves.config")
    catcher = _Catcher()
    level, logger.level = logger.level, logging.INFO
    logger.addHandler(catcher)
    try:
        fn()
    finally:
        logger.removeHandler(catcher)
        logger.level = level
    return catcher.records


def test_the_breadcrumb_says_what_happened_and_never_where(monkeypatch):
    from waves.helper import path as path_helper

    # By level NUMBER: the diagnostics install renames WARNING to "WARN".
    for outcome, level, word in (
        ("moved", logging.INFO, "migrated"),
        ("failed", logging.WARNING, "migration failed"),
    ):
        monkeypatch.setattr(path_helper, "CONFIG_MIGRATION", outcome)

        records = _breadcrumbs(waves_app._log_config_migration)

        assert len(records) == 1
        assert records[0].levelno == level
        assert word in records[0].getMessage()
        # Never the path itself: a home folder carries the user's name.
        assert str(pathlib.Path.home()) not in records[0].getMessage()


def test_no_breadcrumb_when_nothing_was_migrated(monkeypatch):
    from waves.helper import path as path_helper

    monkeypatch.setattr(path_helper, "CONFIG_MIGRATION", "")

    assert _breadcrumbs(waves_app._log_config_migration) == []
