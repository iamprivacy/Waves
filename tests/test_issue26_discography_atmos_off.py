"""With the Atmos setting off, a discography sweep prefers the stereo edition.

WHAT THIS FENCES OFF (issue #26)
--------------------------------
Since the two-rows rule (test_atmos_is_its_own_row.py) the Atmos edition keys
apart from its stereo twin in every same-release grouping, so it survives the
discography's dedup and edition handling as its own album. Nothing after that
asked the Atmos setting: the sweep queued the Atmos edition beside the stereo
one, every track of it is Atmos-only, and the engine's "nothing else to fetch"
clause (test_atmos_only_downloads_anyway.py) then delivered spatial files to a
user who had turned Atmos off.

THE RULE
--------
The setting means "prefer stereo where there is a choice", and a bulk sweep is
where the choice is made: with the setting off, _drop_spatial_editions leaves
out every Atmos-only release whose stereo twin is also in the sweep. A spatial
release with NO stereo twin anywhere in the sweep stays, downloaded as Atmos by
the engine's own clause: dropping it would put a hole in the discography, the
exact harm that retired the old exclusion apparatus. An explicitly clicked
ATMOS row never passes through the sweep and downloads as it always did.

Pairing uses the edition base key without its Atmos kind, so a
"(Dolby Atmos)"-suffixed twin still meets its plainly-titled stereo edition,
and it looks across both buckets, so a guest Atmos release pairs with a stereo
twin wherever the sweep holds it.
"""

from __future__ import annotations

from types import SimpleNamespace

from test_discography_video_source import _Artist as _VideoArtist
from test_discography_video_source import _DiscoStub
from tidalapi.album import Album
from tidalapi.media import AudioMode, Quality

from tidaler.waves_ui.backend import _drop_spatial_editions

ATMOS = AudioMode.dolby_atmos.value


def _album(aid, title, modes, *, artist="Artist", quality=Quality.high_lossless):
    a = Album.__new__(Album)
    a.id = aid
    a.name = title
    a.artist = SimpleNamespace(name=artist, id=hash(artist) % 1000)
    a.artists = [a.artist]
    a.audio_modes = modes
    a.audio_quality = quality
    a.media_metadata_tags = None
    a.num_tracks = 10
    a.num_videos = 0
    a.explicit = False
    return a


def _ids(albums):
    return [a.id for a in albums]


def test_the_atmos_edition_is_left_out_beside_its_stereo_twin():
    stereo = _album("s", "Random Access Memories", ["STEREO"])
    atmos = _album("a", "Random Access Memories", [ATMOS])
    own, guest, dropped = _drop_spatial_editions([stereo, atmos], [])
    assert _ids(own) == ["s"]
    assert guest == []
    assert dropped == 1


def test_a_dolby_atmos_suffixed_twin_still_pairs_with_its_stereo_edition():
    """TIDAL sometimes titles the spatial edition "(Dolby Atmos)". The pairing
    key strips edition qualifiers, so the suffixed twin is still recognised."""
    stereo = _album("s", "Album", ["STEREO"])
    atmos = _album("a", "Album (Dolby Atmos)", [ATMOS])
    own, _, dropped = _drop_spatial_editions([stereo, atmos], [])
    assert _ids(own) == ["s"]
    assert dropped == 1


def test_a_spatial_release_with_no_stereo_twin_stays():
    """No twin anywhere in the sweep means dropping it would leave a hole in
    the discography, so it stays and the engine fetches it as Atmos."""
    lone = _album("a", "Spatial Only Single", [ATMOS])
    other = _album("s", "Some Other Album", ["STEREO"])
    own, _, dropped = _drop_spatial_editions([lone, other], [])
    assert _ids(own) == ["a", "s"]
    assert dropped == 0


def test_guest_releases_are_filtered_and_pair_across_buckets():
    """A guest Atmos release is left out when its stereo twin sits in EITHER
    bucket; a guest spatial release with no twin stays."""
    own_stereo = _album("os", "Shared Album", ["STEREO"])
    guest_atmos = _album("ga", "Shared Album", [ATMOS])
    guest_stereo = _album("gs", "Guest Album", ["STEREO"])
    guest_atmos_twin = _album("gat", "Guest Album (Dolby Atmos)", [ATMOS])
    guest_lone = _album("gl", "Guest Spatial Only", [ATMOS])
    own, guest, dropped = _drop_spatial_editions(
        [own_stereo], [guest_atmos, guest_stereo, guest_atmos_twin, guest_lone]
    )
    assert _ids(own) == ["os"]
    assert _ids(guest) == ["gs", "gl"]
    assert dropped == 2


def test_same_title_by_a_different_artist_is_not_a_twin():
    stereo = _album("s", "Album", ["STEREO"], artist="Someone Else")
    atmos = _album("a", "Album", [ATMOS], artist="Artist")
    own, _, dropped = _drop_spatial_editions([stereo, atmos], [])
    assert _ids(own) == ["s", "a"]
    assert dropped == 0


def test_stereo_and_dual_mode_releases_pass_through_untouched():
    """A dual-mode single id is a stereo release that also has Atmos: it is
    never "Atmos and nothing else", so the sweep keeps it (and the track-level
    setting decides the fetch, as it always did)."""
    stereo = _album("s", "Album", ["STEREO"])
    both = _album("b", "Album", [ATMOS, "STEREO"])
    own, _, dropped = _drop_spatial_editions([stereo, both], [])
    assert _ids(own) == ["s", "b"]
    assert dropped == 0


# --------------------------------------------------------------------------- #
# The wiring: downloadArtist itself makes the choice
# --------------------------------------------------------------------------- #
class _AtmosDiscoStub(_DiscoStub):
    """The video-source file's discography stub, handed releases that carry
    real audio modes, so the sweep's Atmos choice runs against the real
    downloadArtist body."""

    def __init__(self, releases, *, atmos_on: bool):
        super().__init__(_VideoArtist([]), video_download=False)
        self.settings.data.download_dolby_atmos = atmos_on
        self._releases = releases

    def _artist_releases(self, artist):
        return list(self._releases), [], True


def test_download_artist_queues_only_the_stereo_edition_with_the_setting_off():
    stereo = _album("s", "Album", ["STEREO"])
    atmos = _album("a", "Album", [ATMOS])
    stub = _AtmosDiscoStub([stereo, atmos], atmos_on=False)
    stub.downloadArtist("art1")
    assert stub._albumsQueued.emits == [["s"]]


def test_download_artist_queues_both_editions_with_the_setting_on():
    stereo = _album("s", "Album", ["STEREO"])
    atmos = _album("a", "Album", [ATMOS])
    stub = _AtmosDiscoStub([stereo, atmos], atmos_on=True)
    stub.downloadArtist("art1")
    assert stub._albumsQueued.emits == [["s", "a"]]


def test_download_artist_keeps_a_spatial_release_with_no_twin_with_the_setting_off():
    lone = _album("a", "Spatial Only", [ATMOS])
    stub = _AtmosDiscoStub([lone], atmos_on=False)
    stub.downloadArtist("art1")
    assert stub._albumsQueued.emits == [["a"]]
