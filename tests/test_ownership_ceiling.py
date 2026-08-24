"""The ownership gate converges at each track's achievable ceiling (issue #31).

The defect: _copy_is_current ranked the copy on disk against the tier the run
ASKED for, never against the tier TIDAL can actually deliver for the release.
With the setting at HI_RES_LOSSLESS (rank 3), a release with no hi-res master
delivers LOSSLESS (rank 2), 2 >= 3 is false, and the verdict is "force" on
every run, forever: the forced fetch delivers the same LOSSLESS and re-records
the same rank, so the loop never settles, silently overwriting the identical
file each time. Album-grained (a master tier is a per-release property), which
is exactly the report's "only some albums re-download".

The fix is two ceilings. A LIVE one: callers holding the track pass what its
media_metadata_tags advertise, and a known ceiling caps the target. And a
STORED one: each record now carries the rank its run requested and the ceiling
that run saw, so even a ceiling-blind caller (ownershipOf holds only an id)
settles once a run has asked at the target, reopening only when the advertised
ceiling rises past what that run saw (a genuinely better master appeared).

Same hermetic pattern as test_atmos_ownership_scale.py: real store, real gate,
real Track objects, no Qt app or network session.
"""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from tidalapi.media import Track

from tidaler.ownership import OwnershipStore, quality_rank
from tidaler.waves_ui.backend import _advertised_ceiling, _copy_is_current, _TrackedDownload


# --------------------------------------------------------------------------- #
# The decision table, ceiling arm. wants_atmos is False throughout: the Atmos
# clause has its own file (test_atmos_ownership_scale.py) and runs first.
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rec, target, ceiling, current",
    [
        # The issue #31 row: LOSSLESS copy, HI_RES target, release advertises
        # LOSSLESS as its best. Owning the best that exists is current.
        ({"quality_rank": 2}, 3, 2, True),
        # No ceiling known: unknown never caps, so the raw comparison stands
        # and the copy still reads as an upgrade candidate.
        ({"quality_rank": 2}, 3, None, False),
        # The release genuinely offers hi-res: a LOSSLESS copy is a real
        # upgrade, and the cap must not swallow it.
        ({"quality_rank": 2}, 3, 3, False),
        # A ceiling above the target never raises it: equal-at-target stays
        # current even when a better master exists (the user asked for less).
        ({"quality_rank": 2}, 2, 3, True),
        # A negative ceiling is "unknown", not a cap at nothing.
        ({"quality_rank": 2}, 3, -1, False),
        # The cap can reach the floor: a LOW-only oddity settles at LOW.
        ({"quality_rank": 0}, 3, 0, True),
    ],
)
def test_a_known_ceiling_caps_the_target(rec, target, ceiling, current):
    assert _copy_is_current(rec, target, wants_atmos=False, ceiling_rank=ceiling) is current


@pytest.mark.parametrize(
    "rec, target, ceiling, current",
    [
        # A run already asked at the target and this copy is what was served:
        # current even with no live ceiling (the ownershipOf path).
        ({"quality_rank": 2, "requested_rank": 3, "ceiling_rank": 2}, 3, None, True),
        # The advertised ceiling has risen past what that run saw: a better
        # master now exists, so the upgrade reopens.
        ({"quality_rank": 2, "requested_rank": 3, "ceiling_rank": 2}, 3, 3, False),
        # Asked below the target: no run has tried at this quality yet.
        ({"quality_rank": 2, "requested_rank": 2, "ceiling_rank": 2}, 3, None, False),
        # Legacy row (columns default to -1 after migration): behaves exactly
        # as before the fix, one more re-download and then it converges.
        ({"quality_rank": 2, "requested_rank": -1, "ceiling_rank": -1}, 3, None, False),
        # Rows read through older cache entries may miss the keys entirely.
        ({"quality_rank": 2}, 3, None, False),
        # None values (a NULL-ish read) mean unknown, not rank 0.
        ({"quality_rank": 2, "requested_rank": None, "ceiling_rank": None}, 3, None, False),
    ],
)
def test_an_already_requested_copy_settles_until_the_ceiling_rises(rec, target, ceiling, current):
    assert _copy_is_current(rec, target, wants_atmos=False, ceiling_rank=ceiling) is current


# --------------------------------------------------------------------------- #
# _advertised_ceiling: explicit tags only, never a guess.
# --------------------------------------------------------------------------- #
class _TagEnum:
    """A str-enum stand-in: str() gives the repr-ish name, .value the wire tag."""

    def __init__(self, value):
        self.value = value

    def __str__(self):  # pragma: no cover - the trap the .value unwrap avoids
        return f"Tag.{self.value}"


class _Raising:
    @property
    def media_metadata_tags(self):
        raise RuntimeError("half-parsed object")


@pytest.mark.parametrize(
    "tags, expected",
    [
        (["HIRES_LOSSLESS", "LOSSLESS"], quality_rank("HI_RES_LOSSLESS")),
        (["LOSSLESS"], quality_rank("LOSSLESS")),
        (["DOLBY_ATMOS"], None),  # below the lossless line: never caps
        ([], None),
        (None, None),  # tidalapi leaves None on unavailable tracks
        ([_TagEnum("HIRES_LOSSLESS")], quality_rank("HI_RES_LOSSLESS")),
        ([_TagEnum("LOSSLESS")], quality_rank("LOSSLESS")),
    ],
)
def test_advertised_ceiling_reads_only_explicit_tags(tags, expected):
    assert _advertised_ceiling(SimpleNamespace(media_metadata_tags=tags)) == expected


def test_advertised_ceiling_swallows_a_raising_object():
    assert _advertised_ceiling(_Raising()) is None
    assert _advertised_ceiling(None) is None


# --------------------------------------------------------------------------- #
# End to end: real store, real gate, real Track. The convergence property.
# --------------------------------------------------------------------------- #
def _store(tmp_path) -> OwnershipStore:
    return OwnershipStore(str(tmp_path / "own.db"))


def _file(tmp_path, name):
    p = tmp_path / name
    p.write_text("audio")
    return str(p)


def _track(tid="101", tags=("LOSSLESS",)):
    """A REAL Track built without the network-touching __init__ (the engine's
    exclusion checks isinstance, and a SimpleNamespace would dodge them)."""
    t = Track.__new__(Track)
    t.id = tid
    t.name = "Song"
    t.artist = SimpleNamespace(name="Artist")
    t.audio_modes = ["STEREO"]
    t.media_metadata_tags = list(tags) if tags is not None else None
    return t


def _gate(store, *, target):
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = store.ownership_of
    dl._target_rank = quality_rank(target)
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    return dl


def test_a_lossless_only_release_settles_at_a_hi_res_target(tmp_path):
    """The exact issue #31 loop, walked three rounds. The gate always holds
    the track, so the live ceiling settles even a legacy pre-migration row on
    the very first ask; a run that does fetch stamps the ranks, and the gate
    stays settled off either signal."""
    store = _store(tmp_path)
    path = _file(tmp_path, "song.flac")
    dl = _gate(store, target="HI_RES_LOSSLESS")
    track = _track(tags=["LOSSLESS"])

    # A pre-migration row: delivered LOSSLESS, no requested/ceiling recorded.
    store.record("101", path, "LOSSLESS")
    seen = [dl._ownership_decision(track)[0]]

    # The forced fetch delivers LOSSLESS again; the capture now stamps what
    # was asked and what was advertised, exactly as _get_track_stream_info does.
    store.record(
        "101", path, "LOSSLESS", requested_rank=quality_rank("HI_RES_LOSSLESS"), ceiling_rank=quality_rank("LOSSLESS")
    )
    for _ in range(2):
        seen.append(dl._ownership_decision(track)[0])
    assert seen == ["skip", "skip", "skip"], f"the gate never settles: {seen}"


def test_the_live_ceiling_alone_settles_a_legacy_row(tmp_path):
    """Even the legacy row skips when the caller holds the track and its tags
    say no better master exists: the live ceiling needs no stored ranks."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.flac"), "LOSSLESS")
    dl = _gate(store, target="HI_RES_LOSSLESS")
    assert dl._ownership_decision(_track(tags=["LOSSLESS"]))[0] == "skip"


def test_a_release_that_offers_hi_res_still_forces_the_upgrade(tmp_path):
    """The control: the cap must never freeze a genuine upgrade."""
    store = _store(tmp_path)
    store.record(
        "101",
        _file(tmp_path, "song.flac"),
        "LOSSLESS",
        requested_rank=quality_rank("HI_RES_LOSSLESS"),
        ceiling_rank=quality_rank("LOSSLESS"),
    )
    dl = _gate(store, target="HI_RES_LOSSLESS")
    # The catalog now advertises a hi-res master: the stored "already asked"
    # clause must reopen, not swallow the upgrade.
    assert dl._ownership_decision(_track(tags=["HIRES_LOSSLESS", "LOSSLESS"]))[0] == "force"


def test_unknown_tags_change_nothing(tmp_path):
    """tidalapi leaves media_metadata_tags None on anything unavailable: an
    unknown ceiling must behave exactly as the pre-fix gate did."""
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.flac"), "LOSSLESS")
    dl = _gate(store, target="HI_RES_LOSSLESS")
    assert dl._ownership_decision(_track(tags=None))[0] == "force"


# --------------------------------------------------------------------------- #
# The store: roundtrip and migration.
# --------------------------------------------------------------------------- #
def test_store_roundtrips_the_requested_and_ceiling_ranks(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.flac"), "LOSSLESS", requested_rank=3, ceiling_rank=2)
    rec = store.ownership_of("101")
    assert rec["requested_rank"] == 3
    assert rec["ceiling_rank"] == 2


def test_store_defaults_both_ranks_to_unknown(tmp_path):
    store = _store(tmp_path)
    store.record("101", _file(tmp_path, "song.flac"), "LOSSLESS")
    rec = store.ownership_of("101")
    assert rec["requested_rank"] == -1
    assert rec["ceiling_rank"] == -1


def test_an_old_db_gains_the_columns_on_open(tmp_path):
    """A pre-fix DB (no requested/ceiling columns) migrates through the ALTER
    guard, and its rows read as unknown, never as rank 0."""
    db = tmp_path / "own.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""CREATE TABLE downloads (
               track_id TEXT NOT NULL, path TEXT NOT NULL, quality_tier TEXT,
               quality_rank INTEGER NOT NULL DEFAULT -1, audio_mode TEXT,
               bit_depth INTEGER, sample_rate INTEGER, codecs TEXT,
               user_id TEXT, recorded_at INTEGER NOT NULL DEFAULT 0,
               PRIMARY KEY (track_id, path))""")
    path = _file(tmp_path, "song.flac")
    conn.execute(
        "INSERT INTO downloads (track_id, path, quality_tier, quality_rank) VALUES (?, ?, ?, ?)",
        ("101", path, "LOSSLESS", 2),
    )
    conn.commit()
    conn.close()

    store = OwnershipStore(str(db))
    rec = store.ownership_of("101")
    assert rec["quality_rank"] == 2
    assert rec["requested_rank"] == -1
    assert rec["ceiling_rank"] == -1


# --------------------------------------------------------------------------- #
# The capture side: what a fetch stamps, and what a post-stream skip undoes.
# --------------------------------------------------------------------------- #
def _capture_dl(monkeypatch, *, target_rank, stream_quality="LOSSLESS"):
    """A _TrackedDownload whose super()._get_track_stream_info is a canned
    stream, so the stamping runs without a session or network."""
    from threading import Lock

    from tidaler import download as download_mod

    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._pinned_quality = None
    dl._target_rank = target_rank
    dl._delivered = {}
    dl._delivered_lock = Lock()
    dl.settings = SimpleNamespace(data=SimpleNamespace(download_dolby_atmos=False))
    info = SimpleNamespace(
        media_stream=SimpleNamespace(
            audio_quality=stream_quality, audio_mode="STEREO", bit_depth=16, sample_rate=44100
        ),
        stream_manifest=SimpleNamespace(codecs="flac"),
    )
    monkeypatch.setattr(download_mod.Download, "_get_track_stream_info", lambda self, media: info)
    return dl


def test_a_fetch_stamps_the_requested_and_ceiling_ranks(monkeypatch):
    dl = _capture_dl(monkeypatch, target_rank=3)
    dl._get_track_stream_info(_track(tags=["LOSSLESS"]))
    quality = dl._delivered["101"]
    assert quality["requested_rank"] == 3
    assert quality["ceiling_rank"] == 2


def test_a_fetch_with_unknown_tags_stamps_no_ceiling(monkeypatch):
    dl = _capture_dl(monkeypatch, target_rank=3)
    dl._get_track_stream_info(_track(tags=None))
    quality = dl._delivered["101"]
    assert quality["requested_rank"] == 3
    assert quality["ceiling_rank"] == -1


def test_a_post_stream_skip_drops_the_captured_quality(monkeypatch):
    """The engine's post-stream existing-file check kept the old file: the
    captured snapshot describes a stream that was never written, so it must
    not survive to item()'s done event (it would record the OLD file as
    carrying the NEW stream's tier, and a later genuine upgrade would skip)."""
    dl = _capture_dl(monkeypatch, target_rank=3)
    track = _track(tags=["LOSSLESS"])
    dl._get_track_stream_info(track)
    assert "101" in dl._delivered
    dl._note_skipped_after_stream(track)
    assert "101" not in dl._delivered
    dl._note_skipped_after_stream(track)  # idempotent, and safe on absent ids
    dl._note_skipped_after_stream(None)  # and on no media at all
