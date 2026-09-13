"""Waves only ever looks for a downloaded copy in two places: the download
folder and the library folder (waves/ownership.py set_roots).

A copy recorded anywhere else, typically under an earlier download folder
(issue #38), must not read as owned, must never be statted, and must never
stop a download. Pure standard library, like tests/test_ownership_store.py.
"""

from __future__ import annotations

import os

from waves.ownership import OwnershipStore, path_under


def _file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    return str(path)


def _store(tmp_path, roots):
    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    store.set_roots(lambda: roots)
    return store


def test_a_copy_outside_both_folders_is_not_owned(tmp_path):
    old = _file(tmp_path / "old-downloads" / "A" / "01.flac")
    store = _store(tmp_path, [str(tmp_path / "downloads"), str(tmp_path / "library")])
    store.record("1", old, "LOSSLESS")
    assert store.ownership_of("1") is None
    assert store.ownership_of_many(["1"]) == {"1": None}


def test_a_copy_in_either_folder_is_owned(tmp_path):
    dl = _file(tmp_path / "downloads" / "A" / "01.flac")
    lib = _file(tmp_path / "library" / "B" / "01.flac")
    store = _store(tmp_path, [str(tmp_path / "downloads"), str(tmp_path / "library")])
    store.record("1", dl, "LOSSLESS")
    store.record("2", lib, "LOSSLESS")
    assert store.ownership_of("1")["path"] == dl
    assert store.ownership_of_many(["2"])["2"]["path"] == lib


def test_a_better_copy_outside_the_folders_does_not_win(tmp_path):
    """Rows are tried best quality first: an out-of-scope Max copy is passed
    over for the Lossless one that is actually in the download folder."""
    old = _file(tmp_path / "old" / "01.flac")
    dl = _file(tmp_path / "downloads" / "01.flac")
    store = _store(tmp_path, [str(tmp_path / "downloads")])
    store.record("1", old, "HI_RES_LOSSLESS")
    store.record("1", dl, "LOSSLESS")
    assert store.ownership_of("1")["path"] == dl


def test_a_copy_outside_the_folders_is_never_statted(tmp_path, monkeypatch):
    old = _file(tmp_path / "old" / "01.flac")
    store = _store(tmp_path, [str(tmp_path / "downloads")])
    store.record("1", old, "LOSSLESS")
    statted = []
    real = os.path.isfile
    monkeypatch.setattr(os.path, "isfile", lambda p: statted.append(p) or real(p))
    assert store.ownership_of("1") is None
    assert statted == []


def test_the_folders_are_read_on_every_lookup(tmp_path):
    """No restart: a folder changed in Settings answers the next question."""
    f = _file(tmp_path / "music" / "01.flac")
    roots = [str(tmp_path / "downloads")]
    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))
    store.set_roots(lambda: roots)
    store.record("1", f, "LOSSLESS")
    assert store.ownership_of("1") is None
    roots.append(str(tmp_path / "music"))
    assert store.ownership_of("1") is not None


def test_no_folders_configured_owns_nothing(tmp_path):
    f = _file(tmp_path / "downloads" / "01.flac")
    store = _store(tmp_path, ["", "  "])
    store.record("1", f, "LOSSLESS")
    assert store.ownership_of("1") is None


def test_a_failing_folder_read_owns_nothing(tmp_path):
    f = _file(tmp_path / "downloads" / "01.flac")
    store = OwnershipStore(str(tmp_path / "ownership.sqlite3"))

    def _boom():
        raise RuntimeError("prefs unreadable")

    store.set_roots(_boom)
    store.record("1", f, "LOSSLESS")
    assert store.ownership_of("1") is None


def test_path_under_is_a_folder_boundary_not_a_prefix(tmp_path):
    root = str(tmp_path / "Music")
    assert path_under(str(tmp_path / "Music" / "a.flac"), root)
    assert path_under(root, root)
    assert not path_under(str(tmp_path / "Music Old" / "a.flac"), root)
    assert not path_under(str(tmp_path / "Musical" / "a.flac"), root)
    assert not path_under("", root)
    assert not path_under(str(tmp_path / "Music" / "a.flac"), "")


def test_path_under_accepts_a_volume_root():
    """A library at the top of a drive ("N:\\" on Windows, "/" here) already
    ends in its separator; appending another one used to match nothing."""
    top = os.path.abspath(os.sep)
    assert path_under(os.path.join(top, "Music", "a.flac"), top)


def test_path_under_matches_a_relative_download_folder(tmp_path, monkeypatch):
    """Downloads are recorded through abspath, so a relative folder setting
    has to be read the same way to recognize its own files."""
    monkeypatch.chdir(tmp_path)
    assert path_under(str(tmp_path / "downloads" / "a.flac"), "downloads")
