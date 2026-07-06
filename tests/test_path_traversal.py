"""Regression tests: remote-controlled media names must not escape the download
directory via ``..`` path traversal (pathvalidate leaves ``..`` untouched)."""

from __future__ import annotations

from tidaler.helper import path as p


def test_no_traversal_neutralizes_dot_components():
    assert p._no_traversal("..") == "_"
    assert p._no_traversal(".") == "_"
    # Legitimate names are left exactly as-is.
    assert p._no_traversal("Normal Album") == "Normal Album"
    assert p._no_traversal("foo..bar") == "foo..bar"  # only a *bare* '..' is a segment


def test_path_file_sanitize_blocks_parent_traversal(tmp_path):
    base = tmp_path / "Music"
    # An artist/album named '..' would otherwise climb out of the download root.
    out = p.path_file_sanitize(base / ".." / ".." / "evil.flac")
    # No live traversal segment survives.
    assert ".." not in out.parts
    # And the destination resolves to somewhere *inside* the download root.
    assert str(out.resolve()).startswith(str(base.resolve()))


def test_path_file_sanitize_keeps_legit_path(tmp_path):
    base = tmp_path / "Music"
    out = p.path_file_sanitize(base / "Daft Punk" / "Discovery" / "01. One More Time.flac")
    assert out.parts[-3:] == ("Daft Punk", "Discovery", "01. One More Time.flac")
    assert str(out.resolve()).startswith(str(base.resolve()))


def test_config_dir_is_waves_specific():
    # Waves must never share per-user state (token.json, settings.json) with an
    # installed Tidaler / tidal-dl-ng: a fresh install has to start signed out.
    import os

    from tidaler import __config_dirname__
    from tidaler.helper.path import path_config_base, path_file_settings, path_file_token

    assert __config_dirname__ in ("Waves", "Waves-dev")
    assert os.path.basename(path_config_base()).startswith("Waves")
    for f in (path_file_token(), path_file_settings()):
        assert os.path.basename(os.path.dirname(f)).startswith("Waves")
