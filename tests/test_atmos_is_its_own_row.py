"""A Dolby Atmos release is its own row, wearing ATMOS, beside its stereo edition.

WHAT THIS FENCES OFF
--------------------
TIDAL ships Atmos as a SEPARATE release with its own id, every track of it
Atmos-only, listed under the same title as the stereo edition (sometimes with a
"(Dolby Atmos)" suffix, sometimes not). Both stages of Waves' duplicate handling
group by title, so the Atmos edition met its stereo twin in one group and,
ranking as the tier TIDAL reports for its container, either lost the collapse
(the Atmos row silently vanished) or won it (the stereo edition was replaced by
a spatial one for someone who never asked). Either way one of two real releases
never reached the screen.

And when an Atmos-only row DID reach the screen it wore the tier its container
would carry if there were a stereo stream, LOSSLESS usually, which promised a
file it could never deliver.

THE RULE, decided 2026-08-18
----------------------------
* An Atmos-only release or track keys apart from its stereo edition in EVERY
  same-release grouping (the always-on dedup and the opt-in edition collapse),
  so both rows survive. Two rows, never two badges: the Atmos setting decides
  which one you download, not which one you may see.
* Its quality label is the word ATMOS. The pill draws that as an outline with a
  filled dot and SPATIAL for a spec, in plain ink, off the ranked ladder.
* A stereo edition, and a rare single id that carries BOTH modes, key and label
  exactly as before: the kind is "Atmos and nothing else", never "has Atmos".
"""

from __future__ import annotations

from types import SimpleNamespace

from tidalapi.album import Album
from tidalapi.media import AudioMode, Quality, Track

from tidaler.waves_ui import backend

ATMOS = AudioMode.dolby_atmos.value


def _album(aid, title, modes, *, tracks=10, quality=Quality.high_lossless):
    a = Album.__new__(Album)
    a.id = aid
    a.name = title
    a.artist = SimpleNamespace(name="Artist", id=1)
    a.artists = [a.artist]
    a.audio_modes = modes
    a.audio_quality = quality
    a.media_metadata_tags = None
    a.num_tracks = tracks
    a.num_videos = 0
    a.explicit = False
    return a


def _track(tid, title, modes, *, quality=Quality.high_lossless):
    t = Track.__new__(Track)
    t.id = tid
    t.name = title
    t.full_name = title
    t.artist = SimpleNamespace(name="Artist", id=1)
    t.artists = [t.artist]
    t.audio_modes = modes
    t.audio_quality = quality
    t.media_metadata_tags = None
    t.explicit = False
    return t


def _bridge():
    b = backend.WavesBridge.__new__(backend.WavesBridge)
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=Quality.hi_res_lossless))
    b._waves_prefs = {"explicit_mode": "explicit"}
    for name in ("_album_key", "_track_key", "_max_quality_rank", "_dedup_albums", "_dedup_tracks"):
        setattr(b, name, getattr(backend.WavesBridge, name).__get__(b, backend.WavesBridge))
    return b


# --------------------------------------------------------------------------- #
# The kind, and the label
# --------------------------------------------------------------------------- #
def test_atmos_only_means_atmos_and_nothing_else():
    assert backend._atmos_only(_album("1", "A", [ATMOS]))
    assert backend._atmos_only(_track("1", "A", [ATMOS]))
    assert not backend._atmos_only(_album("1", "A", ["STEREO"]))
    assert not backend._atmos_only(_album("1", "A", [ATMOS, "STEREO"]))
    assert not backend._atmos_only(_album("1", "A", []))
    assert not backend._atmos_only(_album("1", "A", None))
    assert not backend._atmos_only(SimpleNamespace())


def test_an_atmos_only_release_is_labelled_atmos_not_its_container_tier():
    """TIDAL reports LOSSLESS for the Atmos edition's container. There is no
    stereo stream at that tier, so the pill says what the row IS."""
    assert backend._quality_label(_album("1", "A", [ATMOS], quality=Quality.high_lossless)) == backend.ATMOS_WORD
    assert backend._quality_label(_track("1", "A", [ATMOS], quality=Quality.hi_res_lossless)) == backend.ATMOS_WORD
    # A stereo edition, and a dual-mode id, keep their tier.
    assert backend._quality_label(_album("1", "A", ["STEREO"], quality=Quality.high_lossless)) == "LOSSLESS"
    assert backend._quality_label(_album("1", "A", [ATMOS, "STEREO"], quality=Quality.high_lossless)) == "LOSSLESS"


# --------------------------------------------------------------------------- #
# The always-on dedup: two rows survive
# --------------------------------------------------------------------------- #
def test_the_atmos_edition_survives_the_same_title_dedup_beside_the_stereo_one():
    b = _bridge()
    stereo = _album("s", "Random Access Memories", ["STEREO"], quality=Quality.hi_res_lossless)
    atmos = _album("a", "Random Access Memories", [ATMOS], quality=Quality.high_lossless)
    kept = b._dedup_albums([stereo, atmos])
    assert [x.id for x in kept] == ["s", "a"], [x.id for x in kept]


def test_the_atmos_edition_never_replaces_the_stereo_one_when_it_would_outrank_it():
    """The container tier TIDAL reports for an Atmos edition can be HIGHER
    than the stereo edition's real tier. Ranked as a version of the same
    release it would WIN the collapse and a spatial file would replace the
    stereo one for someone who never asked. Kind, not rank, keeps them apart."""
    b = _bridge()
    stereo = _album("s", "Album", ["STEREO"], quality=Quality.low_320k)
    atmos = _album("a", "Album", [ATMOS], quality=Quality.hi_res_lossless)
    kept = b._dedup_albums([stereo, atmos])
    assert [x.id for x in kept] == ["s", "a"]


def test_two_stereo_editions_still_collapse_to_one_row():
    """The kind is added to the key, it does not replace it: ordinary
    quality/region duplicates of one release still collapse as before."""
    b = _bridge()
    low = _album("l", "Album", ["STEREO"], quality=Quality.low_320k)
    high = _album("h", "Album", ["STEREO"], quality=Quality.hi_res_lossless)
    kept = b._dedup_albums([low, high])
    assert [x.id for x in kept] == ["h"]


def test_two_atmos_listings_of_one_release_still_collapse_to_one_row():
    b = _bridge()
    one = _album("x", "Album", [ATMOS], quality=Quality.high_lossless)
    two = _album("y", "Album", [ATMOS], quality=Quality.high_lossless)
    kept = b._dedup_albums([one, two])
    assert len(kept) == 1


def test_a_dual_mode_single_id_keys_with_the_stereo_editions_not_apart():
    """A rare id carrying both modes is a stereo release that also has Atmos:
    it belongs in the stereo group, so it collapses with its stereo twins the
    way it always did."""
    b = _bridge()
    both = _album("b", "Album", [ATMOS, "STEREO"], quality=Quality.hi_res_lossless)
    stereo = _album("s", "Album", ["STEREO"], quality=Quality.low_320k)
    kept = b._dedup_albums([both, stereo])
    assert [x.id for x in kept] == ["b"]


def test_tracks_follow_the_same_rule():
    b = _bridge()
    stereo = _track("s", "Song", ["STEREO"], quality=Quality.hi_res_lossless)
    atmos = _track("a", "Song", [ATMOS], quality=Quality.high_lossless)
    kept = b._dedup_tracks([stereo, atmos])
    assert [x.id for x in kept] == ["s", "a"]


# --------------------------------------------------------------------------- #
# The opt-in edition collapse: the twin is not a subset of its stereo edition
# --------------------------------------------------------------------------- #
def test_the_edition_collapse_never_folds_the_atmos_edition_into_the_stereo_one():
    """_strip_edition_quals peels every trailing parenthetical, "(Dolby Atmos)"
    included, so without the kind in the key the twin would land in the stereo
    group with an identical track list and be dropped as a strict subset (or,
    on a keep_both conflict, kept only by luck of the tier)."""
    stereo = _album("s", "Album", ["STEREO"], tracks=10, quality=Quality.hi_res_lossless)
    atmos = _album("a", "Album (Dolby Atmos)", [ATMOS], tracks=10, quality=Quality.high_lossless)
    tracks = [(f"Song {i}", 200 + i) for i in range(10)]
    kept = backend._collapse_album_editions(
        [stereo, atmos], tracks_of=lambda a: tracks, quality_of=backend._quality_rank, conflict="quality"
    )
    assert [x.id for x in kept] == ["s", "a"]
    assert backend._edition_base_key(stereo) != backend._edition_base_key(atmos)
    assert backend._edition_base_key(stereo)[:2] == backend._edition_base_key(atmos)[:2]
