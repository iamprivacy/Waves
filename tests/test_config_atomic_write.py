"""Regression tests for BaseConfig.save atomic write (crash-safe config files).

BaseConfig.save wrote settings.json / token.json in place, so a crash mid-write
truncated the file: a corrupt config, or a lost login on the next launch. It now
serializes to a temp sibling, fsyncs it, and os.replaces the real file, so a
reader only ever sees a whole file and a failed write leaves the original intact.
"""

from __future__ import annotations

import json
import os

import pytest

from tidaler.config import BaseConfig
from tidaler.model.cfg import Settings as ModelSettings


def _cfg(tmp_path):
    cfg = BaseConfig.__new__(BaseConfig)
    cfg.data = ModelSettings()
    cfg.path_base = str(tmp_path)
    cfg.file_path = str(tmp_path / "settings.json")
    return cfg


def test_save_writes_valid_json_and_leaves_no_temp(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.save()
    target = tmp_path / "settings.json"
    assert target.exists()
    assert isinstance(json.loads(target.read_text()), dict)
    assert not (tmp_path / "settings.json.tmp").exists(), "the temp sibling must be renamed away"


def test_save_is_atomic_original_survives_a_failed_replace(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    cfg.save()
    target = tmp_path / "settings.json"
    original = target.read_text()

    # Change the model so a non-atomic write would leave different bytes behind.
    cfg.data.download_base_path = "/waves/atomic/marker"

    # Simulate a crash at the final swap: the real file must not be touched.
    def _boom(_src, _dst):
        raise RuntimeError("crash during replace")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(RuntimeError):
        cfg.save()

    assert target.read_text() == original, "a failed write must leave the original file intact"
