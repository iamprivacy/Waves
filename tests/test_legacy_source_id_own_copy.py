"""A merged track re-saved over a copy an older Waves tagged the other way replaces it.

A "best of both" merge fetches a track from one edition (``member.id``, the
SOURCE id) and files it under another edition's slot (``waves_identity_id``).
Every build up to v0.1.21 stamped the SOURCE id into that file; today's build
stamps the identity id. A library assembled by an older Waves is therefore full
of merged tracks tagged with an id the current build no longer files them under.

With skipping off (overwrite mode, or a quality upgrade), the engine asks
``_is_own_copy`` whether the file at the destination is this item's own to
replace. Recognising ONLY the identity id would call the old copy a stranger,
step around it, and write a ``_01`` duplicate beside it: forever, since the app
never deletes a user-visible file. ``_waves_owned_ids`` widens "its own" to
both ids, and this file pins that the widening reaches the disk decision.
"""

import ast
import inspect
import pathlib
import threading
from unittest.mock import MagicMock

import pytest
from tidalapi.media import Track

from tidaler import download as download_module
from tidaler.download import Download, _waves_item_id, _waves_owned_ids

SOURCE_ID = "t-1"
IDENTITY_ID = "identity-9"
STRANGER_ID = "stranger"


@pytest.fixture(autouse=True)
def _identity_from_content(monkeypatch):
    """Let a file answer who it is without building a tagged FLAC per case.

    The engine asks metadata.read_item_id for the item id the download wrote
    into the file's tags. The files here carry that id as their whole content
    behind an ``id-`` marker (the shape test_overwrite_mode_collisions uses,
    minus its digits-only rule: merge ids are the strings the merge planner
    hands out). Anything else reads as untagged, which is what a pre-id
    library file is.
    """

    def _read(path_file) -> str:
        try:
            raw: bytes = pathlib.Path(path_file).read_bytes()
        except OSError:
            return ""

        return raw[3:].decode() if raw.startswith(b"id-") else ""

    monkeypatch.setattr("tidaler.download.read_item_id", _read)


def _occupy(path_file: pathlib.Path, item_id: str) -> None:
    """Put a file at ``path_file`` tagged with ``item_id`` ("" for untagged)."""
    path_file.write_bytes(b"id-" + item_id.encode() if item_id else b"an untagged library file")


def _make_download(tmp_path: pathlib.Path, skip_existing: bool, cls: type[Download] = Download) -> Download:
    dl = cls(
        tidal_obj=MagicMock(),
        skip_existing=skip_existing,
        path_base=str(tmp_path),
        fn_logger=MagicMock(),
        progress=MagicMock(),
    )
    dl.settings = MagicMock()
    dl.settings.data.video_convert_mp4 = False
    dl.settings.data.extract_flac = False
    dl.settings.data.downsample_enabled = False
    dl.settings.data.path_binary_ffmpeg = ""
    dl.event_abort = threading.Event()
    dl.event_run = threading.Event()
    dl.event_run.set()

    def _download(media, stream_manifest, path_file, event_stop=None):
        # Today's build files the download under the identity id.
        path_file.write_bytes(b"id-" + _waves_item_id(media).encode())

        return True, path_file

    dl._download = _download
    dl._handle_metadata_and_extras = lambda *args, **kwargs: None

    return dl


def _merge_member() -> Track:
    """A best-of-both member: streamed from the source edition, filed under the identity slot.

    The shape backend._as_member_of produces (a copy of the source track with
    ``waves_identity_id`` set), built here without importing the Qt bridge.
    """
    t = Track.__new__(Track)
    t.id = SOURCE_ID
    t.waves_identity_id = IDENTITY_ID
    t.audio_modes = []
    t.artists = []
    t.name = "Song"
    t.version = None
    t.album = None
    t.track_num = 1
    t.volume_num = 1

    return t


class TestIsOwnCopyKnowsEveryIdTheFileMayCarry:
    def test_a_copy_tagged_with_the_source_id_is_this_items_own(self, tmp_path):
        # The library an older Waves assembled: the merged track carries the
        # SOURCE edition's id. Still ours to replace.
        dl = _make_download(tmp_path, skip_existing=False)
        path_file = tmp_path / "Song.flac"
        _occupy(path_file, SOURCE_ID)

        assert dl._is_own_copy(path_file, IDENTITY_ID, {IDENTITY_ID, SOURCE_ID}) is True

    def test_a_copy_tagged_with_the_identity_id_is_this_items_own(self, tmp_path):
        # The library today's build writes.
        dl = _make_download(tmp_path, skip_existing=False)
        path_file = tmp_path / "Song.flac"
        _occupy(path_file, IDENTITY_ID)

        assert dl._is_own_copy(path_file, IDENTITY_ID, {IDENTITY_ID, SOURCE_ID}) is True

    def test_a_copy_tagged_with_a_third_id_is_a_different_song(self, tmp_path):
        # Widening to the source id must not widen to everybody: a colliding
        # stranger at this name is still a song that replacing would lose.
        dl = _make_download(tmp_path, skip_existing=False)
        path_file = tmp_path / "Song.flac"
        _occupy(path_file, STRANGER_ID)

        assert dl._is_own_copy(path_file, IDENTITY_ID, {IDENTITY_ID, SOURCE_ID}) is False

    def test_an_untagged_copy_is_still_this_items_own(self, tmp_path):
        # A pre-id library file: identity unknown, and overwrite mode has
        # always replaced it. The owned-ids widening may not narrow that.
        dl = _make_download(tmp_path, skip_existing=False)
        path_file = tmp_path / "Song.flac"
        _occupy(path_file, "")

        assert dl._is_own_copy(path_file, IDENTITY_ID, {IDENTITY_ID, SOURCE_ID}) is True


class TestTheClaimLandsOnTheOldCopy:
    def test_a_member_claims_the_name_its_source_tagged_copy_holds(self, tmp_path):
        # Overwrite mode over an older library: the claim has to come back as
        # the ORIGINAL name, so the download replaces the old copy in place.
        dl = _make_download(tmp_path, skip_existing=False)
        member = _merge_member()
        destination = tmp_path / "Song.flac"
        _occupy(destination, SOURCE_ID)

        path_claimed, name_reserved = dl._claim_destination(
            destination, _waves_item_id(member), _waves_owned_ids(member)
        )

        assert path_claimed == destination, "the old copy is ours to replace, not to sidestep"
        assert name_reserved == str(destination)

    def test_a_member_steps_around_a_stranger(self, tmp_path):
        # The control: the same claim over a file carrying a third id has to
        # move on to the numbered name, or the widening would eat strangers.
        dl = _make_download(tmp_path, skip_existing=False)
        member = _merge_member()
        destination = tmp_path / "Song.flac"
        _occupy(destination, STRANGER_ID)

        path_claimed, name_reserved = dl._claim_destination(
            destination, _waves_item_id(member), _waves_owned_ids(member)
        )

        assert path_claimed == tmp_path / "Song_01.flac"
        assert name_reserved == str(tmp_path / "Song_01.flac")


class _ClaimSpy(Download):
    """A Download that records what the live code path hands the claim."""

    def __init__(self, *args, **kwargs) -> None:
        self.claims: list[tuple] = []
        super().__init__(*args, **kwargs)

    def _claim_destination(self, path_media_dst, media_id, owned_ids=None):
        self.claims.append((path_media_dst, media_id, owned_ids))

        return super()._claim_destination(path_media_dst, media_id, owned_ids)


class TestTheLiveDownloadPathPassesEveryOwnedId:
    def test_the_download_path_claims_with_both_ids(self, tmp_path):
        # The real path a track download takes: _perform_actual_download must
        # hand the claim BOTH ids, or the unit behaviour above never reaches
        # a user's disk.
        dl = _make_download(tmp_path, skip_existing=False, cls=_ClaimSpy)
        member = _merge_member()
        destination = tmp_path / "Song.flac"
        _occupy(destination, SOURCE_ID)

        ok, path = dl._perform_actual_download(
            media=member,
            path_media_dst=destination,
            stream_manifest=MagicMock(),
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )

        assert ok is True
        assert dl.claims == [(destination, IDENTITY_ID, {IDENTITY_ID, SOURCE_ID})]
        assert path == destination, "the forced re-save replaces the old copy in place"
        assert destination.read_bytes() == b"id-" + IDENTITY_ID.encode(), "and re-tags it with the identity id"
        assert [p.name for p in tmp_path.iterdir()] == ["Song.flac"], "no _01 duplicate the app will never delete"

    def test_the_download_path_still_steps_around_a_stranger(self, tmp_path):
        # The same live path over a colliding stranger keeps the stranger.
        dl = _make_download(tmp_path, skip_existing=False, cls=_ClaimSpy)
        member = _merge_member()
        destination = tmp_path / "Song.flac"
        _occupy(destination, STRANGER_ID)

        ok, path = dl._perform_actual_download(
            media=member,
            path_media_dst=destination,
            stream_manifest=MagicMock(),
            do_flac_extract=False,
            is_parent_album=False,
            media_stream=None,
        )

        assert ok is True
        assert path == tmp_path / "Song_01.flac"
        assert destination.read_bytes() == b"id-" + STRANGER_ID.encode(), "the stranger at the base name stays"

    def test_both_claim_sites_in_the_engine_pass_the_owned_ids(self):
        # The playlist symlink move (media_move_and_symlink) claims a name too,
        # and reaching it needs the whole path template machinery. Pinned at
        # the source: every live claim in the engine passes the owned ids,
        # so neither site can quietly fall back to the identity id alone.
        tree = ast.parse(inspect.getsource(download_module))
        claims = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_claim_destination"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ]

        assert len(claims) == 2, "the download path and the playlist symlink move"
        for call in claims:
            assert len(call.args) == 3 and not call.keywords, ast.unparse(call)
            owned = call.args[2]
            assert isinstance(owned, ast.Call) and ast.unparse(owned.func) == "_waves_owned_ids", ast.unparse(call)
