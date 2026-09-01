"""A library built before 0.1.17 keeps its album folders on the SHIPPED settings.

The layout guard is asked one level up as well: a collection's own folder is
baked into the item template before any item is queued, so the folder has to be
chosen while the collection is still an album. The trouble is what an album can
answer. A template's tokens are filled in by the thing being formatted, and the
shipped album template opens with {artist_name}, which only a track can answer,
so the album-level spelling still carried a literal "{artist_name}" segment. The
folder tested for existence therefore contained that literal text, no such
folder is ever on disk, every library looked new, and a pre-0.1.17 album got a
second, tidy-spelled folder beside the one it was already filed in. On defaults,
which is where nearly everyone is.

A template topped with {album_artist} (a question an album CAN answer) never had
the problem, which is why this went unnoticed: the guard worked in every test
that spelled the artist out.

The folders compared are now the ones an item of the collection actually lands
in, so the guard fires on the shipped template too.
"""

from __future__ import annotations

import pathlib
import threading
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tidalapi import Album, Track

import waves.download as download_module
from waves.download import Download
from waves.model.cfg import Settings

_ARTIST = "Bright Eyes"
# A slash in the title. Removing it left a doubled space until 0.1.17 tidied it,
# so a library from before then holds the doubled-space spelling (issue #15).
_ALBUM_TITLE = "The Better Life / Dead Love"
_LEGACY_DIR = "[2011] The Better Life  Dead Love"
_TIDY_DIR = "[2011] The Better Life Dead Love"
_DEFAULT_ALBUM_TEMPLATE = Settings.format_album
_ALBUM_ARTIST_TEMPLATE = _DEFAULT_ALBUM_TEMPLATE.replace("{artist_name}/", "{album_artist}/", 1)


def _album() -> Album:
    album = Album.__new__(Album)
    album.id = 101
    album.name = _ALBUM_TITLE
    album.artists = [SimpleNamespace(name=_ARTIST, roles=None)]
    album.artist = SimpleNamespace(name=_ARTIST)
    album.num_tracks = 2
    album.num_volumes = 1
    album.explicit = False
    album.release_date = datetime(2011, 5, 3)
    album.type = "ALBUM"
    return album


def _tracks(album: Album) -> list[Track]:
    tracks = []
    for num, title in ((1, "One"), (2, "Two")):
        track = Track.__new__(Track)
        track.id = num
        track.name = title
        track.version = None
        track.full_name = title
        track.explicit = False
        track.track_num = num
        track.volume_num = 1
        track.artists = [SimpleNamespace(name=_ARTIST)]
        track.artist = SimpleNamespace(name=_ARTIST)
        track.album = album
        track.media_metadata_tags = []
        tracks.append(track)
    return tracks


def _make_download(base: pathlib.Path, template: str) -> Download:
    dl = Download(
        tidal_obj=MagicMock(),
        skip_existing=True,
        path_base=str(base),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.filename_illegal_replacement = ""
    dl.settings.data.filename_illegal_map = None
    dl.settings.data.filename_delimiter_artist = ", "
    dl.settings.data.filename_delimiter_album_artist = ", "
    dl.settings.data.use_primary_album_artist = False
    dl.settings.data.album_track_num_pad_min = 2
    dl.settings.data.format_album = template
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()
    dl.progress_gui = None
    dl.extension_guess = lambda *_args, **_kwargs: ".flac"
    return dl


def _first_item_folder(base: pathlib.Path, template: str, monkeypatch, items=None) -> pathlib.Path:
    """Where the first track of the album lands, through the real engine.

    The whole chain runs: the collection context bakes the folder, then the
    per-item destination formats the rest, which is the pair that has to agree
    with what is already on disk.
    """
    album = _album()
    items = _tracks(album) if items is None else items
    monkeypatch.setattr(download_module, "items_results_all", lambda *_a, **_k: items)
    dl = _make_download(base, template)

    relative, _name, _short, resolved_items, _stdout = dl._setup_collection_download_context(album, template, False)
    destination, _extension = dl._destination_path(resolved_items[0], relative, None, 1, len(resolved_items))

    return destination.parent.relative_to(base)


class TestTheShippedTemplateFindsTheOldFolder:
    def test_the_default_template_still_carries_the_artist_token_at_album_level(self):
        # The premise, pinned so the tests below cannot quietly stop testing
        # anything: an album cannot answer {artist_name}, so the album-level
        # spelling is not a usable path on its own.
        assert _DEFAULT_ALBUM_TEMPLATE.startswith("{artist_name}/")

    def test_a_legacy_album_folder_keeps_receiving_downloads(self, tmp_path, monkeypatch):
        (tmp_path / _ARTIST / _LEGACY_DIR).mkdir(parents=True)

        assert _first_item_folder(tmp_path, _DEFAULT_ALBUM_TEMPLATE, monkeypatch) == pathlib.Path(_ARTIST, _LEGACY_DIR)

    def test_no_tidy_sibling_folder_is_created_beside_it(self, tmp_path, monkeypatch):
        # The user-visible half of the same fact: two folders for one album,
        # and the app never deletes either.
        (tmp_path / _ARTIST / _LEGACY_DIR).mkdir(parents=True)

        landed = _first_item_folder(tmp_path, _DEFAULT_ALBUM_TEMPLATE, monkeypatch)

        assert landed != pathlib.Path(_ARTIST, _TIDY_DIR)
        assert not (tmp_path / _ARTIST / _TIDY_DIR).exists()

    def test_a_fresh_library_gets_the_tidy_folder(self, tmp_path, monkeypatch):
        (tmp_path / _ARTIST).mkdir()

        assert _first_item_folder(tmp_path, _DEFAULT_ALBUM_TEMPLATE, monkeypatch) == pathlib.Path(_ARTIST, _TIDY_DIR)

    def test_an_artist_folder_alone_is_not_read_as_an_old_layout(self, tmp_path, monkeypatch):
        # Issue #16: an ancestor exists as soon as anything by the artist was
        # ever saved, so it is no evidence of an older album spelling.
        (tmp_path / _ARTIST).mkdir()

        landed = _first_item_folder(tmp_path, _DEFAULT_ALBUM_TEMPLATE, monkeypatch)

        assert landed.parts[-1] == _TIDY_DIR

    def test_the_album_artist_template_is_unchanged(self, tmp_path, monkeypatch):
        # The template that always worked still works, spelled the same way.
        (tmp_path / _ARTIST / _LEGACY_DIR).mkdir(parents=True)

        assert _first_item_folder(tmp_path, _ALBUM_ARTIST_TEMPLATE, monkeypatch) == pathlib.Path(_ARTIST, _LEGACY_DIR)

    def test_an_empty_collection_still_bakes_a_usable_template(self, tmp_path, monkeypatch):
        # Nothing to resolve the spelling against: the guard falls back to
        # judging the spellings as they are, which is where it started.
        album = _album()
        monkeypatch.setattr(download_module, "items_results_all", lambda *_a, **_k: [])
        dl = _make_download(tmp_path, _DEFAULT_ALBUM_TEMPLATE)

        relative, _name, _short, items, _stdout = dl._setup_collection_download_context(
            album, _DEFAULT_ALBUM_TEMPLATE, False
        )

        assert items == []
        assert relative.startswith("{artist_name}/")
        assert _TIDY_DIR in relative


class TestTheArtistFolderIsChosenTheSameWay:
    """The artist folder is a token too, and it is chosen per item.

    The album folder is baked once for the whole collection; the artist folder
    above it is still a token when that happens, so the per-item guard is what
    keeps it. Both halves have to land on the old spelling for a pre-0.1.17
    library to be left alone.
    """

    _LEGACY_ARTIST = "Bright  Eyes"  # the doubled space a stripped slash left
    _TIDY_ARTIST = "Bright Eyes"

    def _album_with_slashed_artist(self) -> Album:
        album = _album()
        album.artists = [SimpleNamespace(name="Bright / Eyes", roles=None)]
        album.artist = SimpleNamespace(name="Bright / Eyes")
        return album

    def test_a_legacy_artist_folder_keeps_receiving_downloads(self, tmp_path, monkeypatch):
        album = self._album_with_slashed_artist()
        items = _tracks(album)
        for track in items:
            track.artists = [SimpleNamespace(name="Bright / Eyes")]
            track.artist = SimpleNamespace(name="Bright / Eyes")
        (tmp_path / self._LEGACY_ARTIST / _LEGACY_DIR).mkdir(parents=True)
        monkeypatch.setattr(download_module, "items_results_all", lambda *_a, **_k: items)
        dl = _make_download(tmp_path, _DEFAULT_ALBUM_TEMPLATE)

        relative, _name, _short, resolved, _stdout = dl._setup_collection_download_context(
            album, _DEFAULT_ALBUM_TEMPLATE, False
        )
        destination, _extension = dl._destination_path(resolved[0], relative, None, 1, len(resolved))

        assert destination.parent.relative_to(tmp_path) == pathlib.Path(self._LEGACY_ARTIST, _LEGACY_DIR)


class TestTheFolderTestReadsTheProbes:
    def test_the_spelling_whose_resolved_folder_exists_is_the_one_chosen(self, tmp_path):
        # The folder tested is the probe's, not the template's: the templates
        # here name no folder that could exist, and the answer still follows
        # the library on disk.
        dl = _make_download(tmp_path, _DEFAULT_ALBUM_TEMPLATE)
        tidy = "{artist_name}/[2011] Album Name/{track_title}"
        legacy = "{artist_name}/[2011] Album  Name/{track_title}"
        (tmp_path / _ARTIST / "[2011] Album  Name").mkdir(parents=True)

        chosen = dl._keep_existing_collection_layout(
            tidy,
            legacy,
            probes=[
                f"{_ARTIST}/[2011] Album Name/1. One",
                f"{_ARTIST}/[2011] Album  Name/1. One",
            ],
        )

        assert chosen == legacy
        # And without the probes the very same call cannot see it, which is
        # the whole defect: the templates point at "{artist_name}/...".
        assert dl._keep_existing_collection_layout(tidy, legacy) == tidy

    def test_a_probe_folder_one_level_up_is_still_no_evidence(self, tmp_path):
        # Issue #16 through the probes: a spelling that empties the album
        # segment resolves to the artist folder, which exists as soon as
        # anything by that artist was saved.
        dl = _make_download(tmp_path, _DEFAULT_ALBUM_TEMPLATE)
        standin = "{artist_name}/-/{track_title}"
        dropped = "{artist_name}/{track_title}"
        (tmp_path / _ARTIST).mkdir()

        chosen = dl._keep_existing_collection_layout(
            standin,
            dropped,
            probes=[f"{_ARTIST}/-/1. One", f"{_ARTIST}/1. One"],
        )

        assert chosen == standin

    def test_a_probe_list_of_the_wrong_length_is_ignored(self, tmp_path):
        # A caller that cannot supply one probe per spelling gets the older
        # behaviour rather than a mismatched comparison.
        dl = _make_download(tmp_path, _DEFAULT_ALBUM_TEMPLATE)
        tidy = f"{_ARTIST}/{_TIDY_DIR}/{{track_title}}"
        legacy = f"{_ARTIST}/{_LEGACY_DIR}/{{track_title}}"
        (tmp_path / _ARTIST / _LEGACY_DIR).mkdir(parents=True)

        assert dl._keep_existing_collection_layout(tidy, legacy, probes=["only one"]) == legacy


@pytest.mark.parametrize("template_name", ["format_album", "format_track"])
def test_the_shipped_templates_open_with_a_track_only_token(template_name):
    # The reason this defect reached the default settings at all. If a future
    # default opens with an album-answerable token instead, this test is the
    # place that says the folder choice no longer depends on the item probe.
    assert getattr(Settings, template_name).startswith("{artist_name}/")
