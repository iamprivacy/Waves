"""The library scan's bulk claim gate only fires where it is meant to.

WHAT THIS FENCES OFF
--------------------
"Bulk downloads skip what you have" (library_bulk_skip) is the second gate in
the download path that can skip a track nothing else would, and the first one
fed by TAG GUESSES rather than exact identifiers, so its carve-offs matter
even more than the duplicate-recording gate's:

* Only injected at all for collection jobs (the bridge passes library_claim
  None for a single-track click, which is an explicit ask): covered by the
  None-callable case here.
* Never ahead of ownership. An owned record's verdict stands: 'skip' stays
  'skip', and 'force' (a genuine quality upgrade) must never be talked down
  to a skip by a tag match.
* Never inside a "best of both" merge. A merge assembles one complete folder,
  and the claim points at the library, not this job's destination. A merge
  member is recognised by ``waves_identity_id``.
* A claim lookup failure never gates: downloading twice beats not
  downloading at all.

The verdict method is exercised directly on a bare instance (no Qt, no
network, no files): everything it consults is injected, mirroring
test_duplicate_recording_gate.py.
"""

from __future__ import annotations

import types

from tidaler.waves_ui.backend import _TrackedDownload


def _media(identity: str | None = None):
    m = types.SimpleNamespace(id=99, name="Song", artist=types.SimpleNamespace(name="Artist"))
    if identity is not None:
        m.waves_identity_id = identity
    return m


def _gate(*, claim, ownership_of=None, target_rank=3, identity=None):
    dl = _TrackedDownload.__new__(_TrackedDownload)
    dl._ownership_of = ownership_of
    dl._target_rank = target_rank
    dl._library_claim = claim
    dl._force_redownload = False
    return dl, _media(identity=identity)


def test_a_claimed_track_skips():
    dl, m = _gate(claim=lambda media: True)
    assert dl._claim_verdict(m) == "skip"


def test_an_unclaimed_track_downloads():
    dl, m = _gate(claim=lambda media: False)
    assert dl._claim_verdict(m) is None


def test_no_callable_no_gate():
    # The bridge injects None for single-item jobs and whenever the pref or
    # the master switch is off; the engine then never asks.
    calls = []
    dl, m = _gate(claim=None)
    assert dl._claim_verdict(m) is None
    assert calls == []


def test_a_merge_member_is_never_claim_skipped():
    dl, m = _gate(claim=lambda media: True, identity="123")
    # Ownership is None here, so the identity carve-off is the only thing
    # standing between this claimed track and a hole in the merged folder.
    assert dl._claim_verdict(m) is None


def test_an_ownership_upgrade_beats_the_claim():
    # Owned at a lower quality than this run targets: ownership says 'force'
    # (overwrite in place), and the tag guess must not turn that into a skip.
    rec = {"quality_rank": 1, "path": ""}
    dl, m = _gate(claim=lambda media: True, ownership_of=lambda mid: rec, target_rank=3)
    assert dl._claim_verdict(m) == "force"


def test_an_ownership_skip_never_reaches_the_claim():
    seen = []
    rec = {"quality_rank": 3, "path": ""}

    def claim(media):
        seen.append(media)
        return False

    dl, m = _gate(claim=claim, ownership_of=lambda mid: rec, target_rank=3)
    assert dl._claim_verdict(m) == "skip"
    assert seen == []  # ownership answered; the guess was never consulted


def test_a_claim_lookup_failure_never_gates():
    def boom(media):
        raise RuntimeError("index went away")

    dl, m = _gate(claim=boom)
    assert dl._claim_verdict(m) is None


# --- What the gate is asked about (issue #24) ---------------------------------
# The claim is only ever "you already have this track filed under the release I
# am fetching". Which release that is has to reach the matcher, or the question
# degrades into "you own this song somewhere", which is true of every best-of
# and was skipping tracks out of albums the user had asked for.


def _adapter(media, album=None):
    """_library_claim_media on a stub that records the question it asks."""
    from tidaler.waves_ui.backend import WavesBridge

    asked: list[tuple] = []
    stub = types.SimpleNamespace(_library_claims_track=lambda *a: asked.append(a) or False)
    WavesBridge._library_claim_media(stub, media, album=album)
    return asked[0] if asked else None


def _track(album_name, album_year, duration=200):
    return types.SimpleNamespace(
        id=99,
        name="Song",
        artist=types.SimpleNamespace(name="Artist"),
        artists=[types.SimpleNamespace(name="Artist")],
        duration=duration,
        album=types.SimpleNamespace(name=album_name, year=album_year),
    )


def test_an_album_job_names_its_own_release():
    # The job's album is the only place the release YEAR is reliably spelled
    # out: a track's embedded album usually carries a title and no date.
    job = types.SimpleNamespace(name="True", year=2013)
    assert _adapter(_track("True", None), album=job) == ("Artist", "Song", "True", "2013", 200)


def test_a_playlist_job_lets_each_track_name_its_own():
    assert _adapter(_track("True", 2013)) == ("Artist", "Song", "True", "2013", 200)


def test_a_track_with_no_release_to_name_asks_an_unprovable_question():
    # Which the matcher answers unproven, so the track is fetched. A wrong
    # skip costs a track nobody finds out was missing.
    assert _adapter(_track("", None)) == ("Artist", "Song", "", "", 200)
