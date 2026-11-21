"""Issue #66: the saved cover's file name comes from Settings.

"cover" stays the shipped name; "folder" is what Windows Explorer and many
players look for. Waves adds the extension and keeps the name legal on every
platform, and a folder that already holds a cover under the old name keeps it.
"""

import dataclasses
from types import SimpleNamespace

import pytest

from tests.test_settings_save_guard import _apply_bridge
from tests.test_settings_sections import _schema
from tests.test_sidecar_ordering import _make_download, _run, _with_sidecars
from waves.helper.path import cover_file_name, cover_file_stem
from waves.model.cfg import Settings as CfgSettings


@pytest.mark.parametrize(
    "typed,name",
    [
        ("cover", "cover.jpg"),
        ("folder", "folder.jpg"),
        ("Folder", "Folder.jpg"),
        ("folder.jpg", "folder.jpg"),
        ("folder.JPEG", "folder.jpg"),
        ("  folder  ", "folder.jpg"),
        ("album art", "album art.jpg"),
        ("", "cover.jpg"),
        ("   ", "cover.jpg"),
        (".jpg", "cover.jpg"),
        (None, "cover.jpg"),
        ("..", "cover.jpg"),
        (".hidden", "hidden.jpg"),
        ("a/b", "ab.jpg"),
        ("a\\b", "ab.jpg"),
        ("x?", "x.jpg"),
        ("CON", "CON_.jpg"),
    ],
)
def test_the_typed_name_becomes_a_legal_file_name(typed, name):
    assert cover_file_name(typed) == name


def test_a_long_name_is_cut_short():
    assert len(cover_file_stem("x" * 300)) <= 64


def test_something_other_than_text_reads_as_the_shipped_name():
    assert cover_file_name(object()) == "cover.jpg"


def test_the_shipped_name_is_cover():
    default = next(f.default for f in dataclasses.fields(CfgSettings) if f.name == "cover_file_name")
    assert default == "cover"


@pytest.mark.parametrize("typed,stored", [("folder.jpg", "folder"), ("  ", "cover"), ("a/b", "ab")])
def test_save_stores_the_name_the_engine_will_use(typed, stored):
    stub = _apply_bridge()
    stub.applySettings({"cover_file_name": typed})
    assert stub.settings.data.cover_file_name == stored


def test_the_row_sits_with_the_cover_rows():
    section = next(s for s in _schema() if s["id"] == "metadata")
    keys = [f["key"] for f in section["fields"]]
    assert keys.index("cover_file_name") == keys.index("metadata_cover_baseline") + 1
    field = next(f for f in section["fields"] if f["key"] == "cover_file_name")
    assert (field["type"], field["label"], field["value"]) == ("str", "Cover file name", "cover")
    assert field["default_value"] == "cover"
    # A compact box beside the help, wide enough for a word, on its own row.
    assert (field["inline"], field["third"], field["box_width"]) == (True, False, 160)
    assert chr(0x2014) not in field["help"]


def _landed(tmp_path, cover_name):
    library = tmp_path / "library"
    library.mkdir()
    dl = _make_download(tmp_path)
    dl.settings.data.cover_file_name = cover_name
    _with_sidecars(dl, tmp_path)
    ok, _ = _run(dl, library / "Song.flac")
    assert ok is True
    return library


def test_the_cover_lands_under_the_chosen_name(tmp_path):
    library = _landed(tmp_path, "folder")
    assert (library / "folder.jpg").read_bytes() == b"cover bytes"
    assert not (library / "cover.jpg").exists()


def test_the_shipped_name_still_lands_as_cover_jpg(tmp_path):
    library = _landed(tmp_path, "cover")
    assert (library / "cover.jpg").read_bytes() == b"cover bytes"


def test_an_old_cover_is_kept_beside_the_new_name(tmp_path):
    library = tmp_path / "library"
    library.mkdir()
    (library / "cover.jpg").write_bytes(b"old cover")
    dl = _make_download(tmp_path)
    dl.settings.data.cover_file_name = "folder"
    _with_sidecars(dl, tmp_path)
    ok, _ = _run(dl, library / "Song.flac")
    assert ok is True
    assert (library / "cover.jpg").read_bytes() == b"old cover"
    assert (library / "folder.jpg").read_bytes() == b"cover bytes"


def test_the_excluded_page_finds_a_cover_under_the_chosen_name(tmp_path):
    from waves.waves_ui.bridge_releases import ReleasesMixin

    (tmp_path / "Album Art.jpg").write_bytes(b"x")
    (tmp_path / "front.jpg").write_bytes(b"y")
    bridge = SimpleNamespace(settings=SimpleNamespace(data=SimpleNamespace(cover_file_name="album art")))
    found = ReleasesMixin._releases_folder_cover(bridge, str(tmp_path), 1.0)
    assert found.endswith("Album%20Art.jpg")


def test_a_stored_name_survives_a_config_load():
    import json

    from waves.config import _drop_unusable_fields

    stored = json.dumps({"cover_file_name": "folder", "cover_album_file": True})
    kept = json.loads(_drop_unusable_fields(stored, CfgSettings))
    assert kept["cover_file_name"] == "folder"
    assert CfgSettings.from_json(json.dumps(kept)).cover_file_name == "folder"
