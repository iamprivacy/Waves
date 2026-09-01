"""A locked settings file on Windows must never crash the app at launch.

os.replace fails with a sharing violation (WinError 32 -> PermissionError)
while any other process holds settings.json open: an antivirus scan, a backup
tool, a second app instance. The atomic save now retries briefly, and the
startup write-back in read() degrades to in-memory settings instead of dying
with an uncaught exception.
"""

import json

import pytest

from waves import config as config_mod
from waves.config import BaseConfig, _replace_with_retry
from waves.model.cfg import Settings as ModelSettings


class _Cfg(BaseConfig):
    """BaseConfig without the singleton wrapper, on a temp settings file."""

    def __init__(self, tmp_path):
        self.cls_model = ModelSettings
        self.file_path = str(tmp_path / "settings.json")
        self.path_base = str(tmp_path)
        self.data = ModelSettings()


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(config_mod.time, "sleep", lambda _s: None)


def _flaky_replace(monkeypatch, failures):
    """Make os.replace raise PermissionError the first ``failures`` times."""
    real = config_mod.os.replace
    calls = {"n": 0}

    def fake(src, dst):
        calls["n"] += 1
        if calls["n"] <= failures:
            raise PermissionError(32, "The process cannot access the file")
        real(src, dst)

    monkeypatch.setattr(config_mod.os, "replace", fake)
    return calls


def test_save_retries_through_transient_lock(tmp_path, monkeypatch):
    cfg = _Cfg(tmp_path)
    calls = _flaky_replace(monkeypatch, failures=2)
    cfg.save()
    assert calls["n"] == 3
    assert json.loads((tmp_path / "settings.json").read_text())
    assert not (tmp_path / "settings.json.tmp").exists()


def test_save_raises_after_retries_and_cleans_tmp(tmp_path, monkeypatch):
    cfg = _Cfg(tmp_path)
    _flaky_replace(monkeypatch, failures=10**6)
    with pytest.raises(PermissionError):
        cfg.save()
    assert not (tmp_path / "settings.json.tmp").exists()


def test_startup_writeback_degrades_instead_of_crashing(tmp_path, monkeypatch):
    """read() keeps the loaded settings and returns normally when the
    write-back cannot land: the exact crash a user hit on Windows 0.1.13."""
    cfg = _Cfg(tmp_path)
    cfg.save()  # a valid settings.json on disk
    _flaky_replace(monkeypatch, failures=10**6)
    fresh = _Cfg(tmp_path)
    assert fresh.read(fresh.file_path) is True
    assert isinstance(fresh.data, ModelSettings)


def test_replace_retry_bound_is_finite(tmp_path, monkeypatch):
    calls = _flaky_replace(monkeypatch, failures=10**6)
    src = tmp_path / "a.tmp"
    src.write_text("x")
    with pytest.raises(PermissionError):
        _replace_with_retry(str(src), str(tmp_path / "a"))
    assert calls["n"] == config_mod._REPLACE_ATTEMPTS
