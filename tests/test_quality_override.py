"""A per-item quality choice (issue #36) reaches the job it was made for, and
only that job.

WHAT THIS FENCES OFF
--------------------
A choice made on a row's quality badge is recorded on the bridge by media id
and read by ``_download`` at the moment the row is queued, so every entry
point (a button, the owned gate's REDOWNLOAD, the library claim's DOWNLOAD
ANYWAY, a re-fetched share link, a download held by the folder or ffmpeg
gate) asks at that tier without carrying it. The row's two quality fields
(``askQuality``, the value the job pins; ``quality``, the word the drawer
states) come from the choice instead of the setting. A download does NOT
spend the choice: it stands on its item until that item is given another
tier, so the badge keeps stating the tier the copy on disk was asked at
(livetest report: a song downloaded at a chosen LOSSLESS had its badge fall
straight back to the catalog's HI-RES). A track without a choice of its own
inherits its album's.

HOW THIS STAYS FIXED
--------------------
Method-bound stubs, no display and no session: the store accepts exactly the
four tiers and DEFAULT; ``_ask_quality_for`` answers own / inherited / none;
``_enqueue`` writes what it is handed; ``_download`` (with every gate
stubbed open) queues a row at the choice and leaves it standing, on the
duplicate-row short cut too; the ownership currency check targets the
choice, so a lower owned copy reads as an upgrade; and a bare stub without
the store falls through to the setting.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

import pytest
from tidalapi.media import Quality

from waves.ownership import quality_rank
from waves.waves_ui import backend


class _Emit:
    def __init__(self) -> None:
        self.calls: list = []

    def emit(self, *args) -> None:
        self.calls.append(args)


def _bind(stub, *names) -> None:
    for name in names:
        setattr(stub, name, getattr(backend.WavesBridge, name).__get__(stub))


def _bridge(setting=Quality.high_lossless):
    """A bare bridge with the real override + queue methods bound on."""
    b = SimpleNamespace()
    b._quality_overrides = {}
    b._objs = {"track": {}, "album": {}}
    b.settings = SimpleNamespace(data=SimpleNamespace(quality_audio=setting))
    b.qualityOverridesChanged = _Emit()
    b.qualityChoiceChanged = _Emit()
    b.ownershipChanged = _Emit()
    b.downloadState = _Emit()
    b._queue_seq = 0
    b._queue = []
    b._queue_index = {}
    b._qdirty_added = []
    b._queue_lock = Lock()
    b._emit_queue = lambda: None
    b._library_bulk_skip_on = lambda: False
    b._logged_in = True
    b._download_gate = lambda: "ok"
    b._ffmpeg_gate_holds = lambda *a, **k: False
    b._job_tracks = {}
    b._job_objs = {}
    b._job_specs = {}
    b._pending_qids = []
    b._pump_queue = lambda: None
    b._queue_item = lambda qid: b._queue_index.get(qid)
    _bind(
        b,
        "setQualityOverride",
        "qualityOverrideOf",
        "_quality_choice_scope",
        "_quality_override_key",
        "_ask_quality_for",
        "_override_target_rank",
        "_queued_quality_value",
        "_target_tier",
        "_target_quality_rank",
        "_enqueue",
        "_download",
    )
    return b


def _track(tid="t1", album_id="a1"):
    return SimpleNamespace(
        id=tid,
        name="Song",
        version=None,
        album=SimpleNamespace(id=album_id, name="Album"),
        artist=SimpleNamespace(name="Artist"),
        artists=[SimpleNamespace(name="Artist")],
    )


@pytest.fixture(autouse=True)
def _plain_helpers(monkeypatch):
    """The row helpers _download calls on the media object, kept off the
    stand-in's shape: what they say is not what is under test here."""
    monkeypatch.setattr(backend, "_image", lambda obj, size: "")
    monkeypatch.setattr(backend, "_quality_label", lambda obj: "HI-RES")
    monkeypatch.setattr(backend, "_primary_artist_name", lambda obj: "Artist")
    monkeypatch.setattr(backend, "_track_count", lambda obj: 1)


# ---- the store ---------------------------------------------------------------


def test_the_store_takes_the_four_tiers_and_default_and_nothing_else():
    b = _bridge()
    b.setQualityOverride("t1", "high")
    b.setQualityOverride("t2", "HI-RES")
    b.setQualityOverride("a1", "default")
    assert b._quality_overrides == {"t1": "HIGH", "t2": "HI-RES", "a1": "DEFAULT"}
    assert len(b.qualityOverridesChanged.calls) == 3
    b.setQualityOverride("t3", "ULTRA")
    b.setQualityOverride("", "HIGH")
    assert "t3" not in b._quality_overrides and "" not in b._quality_overrides
    assert len(b.qualityOverridesChanged.calls) == 3, "a refused word announced a change"
    b.setQualityOverride("t1", "HIGH")
    assert len(b.qualityOverridesChanged.calls) == 3, "re-setting the same word announced a change"
    b.setQualityOverride("t1", "")
    assert "t1" not in b._quality_overrides
    assert b.qualityOverrideOf("t1") == "" and b.qualityOverrideOf("t2") == "HI-RES"
    assert len(b.qualityOverridesChanged.calls) == 4
    b.setQualityOverride("t1", "")
    assert len(b.qualityOverridesChanged.calls) == 4, "clearing what was not set announced a change"


# ---- what a download asks for -------------------------------------------------


def test_an_items_own_choice_is_what_a_download_asks_for():
    b = _bridge()
    b.setQualityOverride("t1", "HIGH")
    assert b._ask_quality_for(_track(), "track", "t1") == (Quality.low_320k.value, "HIGH")


def test_a_track_inherits_its_albums_choice():
    b = _bridge()
    b.setQualityOverride("a1", "LOW")
    assert b._ask_quality_for(_track(), "track", "t1") == (Quality.low_96k.value, "LOW")
    # Its own choice wins over the album's.
    b.setQualityOverride("t1", "HI-RES")
    assert b._ask_quality_for(_track(), "track", "t1") == (Quality.hi_res_lossless.value, "HI-RES")
    # A track from another album is untouched.
    assert b._ask_quality_for(_track("t9", "a9"), "track", "t9") == (Quality.high_lossless.value, "LOSSLESS")


def test_default_pins_the_setting_on_a_track_under_an_album_that_chose():
    b = _bridge(setting=Quality.hi_res_lossless)
    b.setQualityOverride("a1", "HIGH")
    b.setQualityOverride("t1", "DEFAULT")
    assert b._ask_quality_for(_track(), "track", "t1") == (Quality.hi_res_lossless.value, "HI-RES")


def test_without_a_choice_the_setting_answers_exactly_as_before():
    b = _bridge(setting=Quality.low_320k)
    assert b._ask_quality_for(_track(), "track", "t1") == (Quality.low_320k.value, "HIGH")
    assert b._ask_quality_for(SimpleNamespace(id="a1"), "album", "a1") == (Quality.low_320k.value, "HIGH")


def test_a_bare_stub_without_the_store_falls_through_to_the_setting():
    b = _bridge()
    del b._quality_overrides
    assert b._ask_quality_for(_track(), "track", "t1") == (Quality.high_lossless.value, "LOSSLESS")
    assert b.qualityOverrideOf("t1") == ""
    assert b.qualityOverridesChanged.calls == []


# ---- the row ------------------------------------------------------------------


def test_enqueue_writes_the_ask_it_is_handed_and_the_setting_otherwise():
    b = _bridge(setting=Quality.high_lossless)
    qid = b._enqueue("Song", "track", "t1", ask_quality=Quality.low_96k.value, ask_tier="LOW")
    row = b._queue_index[qid]
    assert (row["askQuality"], row["quality"]) == ("LOW", "LOW")
    qid2 = b._enqueue("Song", "track", "t2")
    row2 = b._queue_index[qid2]
    assert (row2["askQuality"], row2["quality"]) == ("LOSSLESS", "LOSSLESS")


def test_a_download_queues_at_the_choice_and_leaves_it_standing():
    """Livetest report: choose LOSSLESS on a HI-RES song, download it, and
    the badge fell back to HI-RES the instant the row was queued, so the
    badge and the file on disk disagreed. The choice now outlives its
    download and the badge keeps stating it."""
    b = _bridge()
    b.setQualityOverride("t1", "HIGH")
    n = len(b.qualityOverridesChanged.calls)
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    row = b._queue[-1]
    assert (row["askQuality"], row["quality"]) == ("HIGH", "HIGH")
    assert b._quality_overrides == {"t1": "HIGH"}, "the download spent the choice"
    assert len(b.qualityOverridesChanged.calls) == n, "the download repainted the badges for nothing"
    # And the next click asks at it again, not at the setting (a different
    # template, so the duplicate-row short cut does not answer instead).
    b._download(_track(), "track", "Song", "{other}", False, "t1")
    assert (b._queue[-1]["askQuality"], b._queue[-1]["quality"]) == ("HIGH", "HIGH")


def test_a_track_download_under_an_album_choice_keeps_the_albums_choice():
    b = _bridge()
    b.setQualityOverride("a1", "LOW")
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    assert (b._queue[-1]["askQuality"], b._queue[-1]["quality"]) == ("LOW", "LOW")
    assert b._quality_overrides == {"a1": "LOW"}, "one track's download spent the album's choice"
    # The album's own download leaves it standing too, so every track badge
    # under it keeps stating the tier they were fetched at.
    b._download(SimpleNamespace(id="a1", name="Album"), "album", "Album", "{tmpl}", True, "a1")
    assert b._queue[-1]["askQuality"] == "LOW"
    assert b._quality_overrides == {"a1": "LOW"}


def test_a_duplicate_row_short_cut_acknowledges_without_a_second_row():
    b = _bridge()
    b.setQualityOverride("t1", "HIGH")
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    assert len(b._queue) == 1
    # Same tier asked again while that row waits: acknowledged, no second row.
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    assert len(b._queue) == 1, "a duplicate row was queued"
    assert b._quality_overrides == {"t1": "HIGH"}
    assert b.downloadState.calls[-1] == ("t1", "queued")
    # A DIFFERENT tier is a new ask and gets its own row.
    b.setQualityOverride("t1", "LOW")
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    assert len(b._queue) == 2 and b._queue[-1]["askQuality"] == "LOW"


def test_a_held_download_asks_at_the_choice_standing_when_it_is_released():
    b = _bridge()
    b.setQualityOverride("t1", "HIGH")
    b._download_gate = lambda: "block"
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    assert b._queue == [] and b._quality_overrides == {"t1": "HIGH"}
    held: list = []
    b._download_gate = lambda: "nudge"
    b._stash_pending_download = lambda mid, fn: held.append(fn)
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    assert b._queue == [] and b._quality_overrides == {"t1": "HIGH"} and len(held) == 1
    # Released later: it asks at the choice that stands then, which is the
    # one the badge is stating at that moment.
    b._download_gate = lambda: "ok"
    b.setQualityOverride("t1", "LOW")
    held[0]()
    assert b._queue[-1]["askQuality"] == "LOW" and b._quality_overrides == {"t1": "LOW"}


# ---- the ownership currency check -------------------------------------------


def test_the_currency_check_targets_the_choice_so_a_lower_copy_offers_an_upgrade():
    b = _bridge(setting=Quality.high_lossless)
    assert b._override_target_rank("t1") == quality_rank(Quality.high_lossless.value)
    b.setQualityOverride("t1", "HI-RES")
    assert b._override_target_rank("t1") == quality_rank(Quality.hi_res_lossless.value)
    # Inherited from the album, through the remembered track object.
    b._objs["track"]["t2"] = _track("t2", "a1")
    b.setQualityOverride("a1", "LOW")
    assert b._override_target_rank("t2") == quality_rank(Quality.low_96k.value)
    # DEFAULT and an unknown track both mean the setting.
    b.setQualityOverride("t2", "DEFAULT")
    assert b._override_target_rank("t2") == quality_rank(Quality.high_lossless.value)
    assert b._override_target_rank("t404") == quality_rank(Quality.high_lossless.value)


def test_ownership_of_uses_the_choice_aware_rank():
    """The wiring: ownershipOf's up_to_date is computed against
    _override_target_rank, not the bare setting."""
    import inspect

    src = inspect.getsource(backend.WavesBridge.ownershipOf)
    assert "_override_target_rank(tid)" in src
    assert "_copy_is_current(rec, self._target_quality_rank()" not in src


def test_the_download_reads_the_choice_after_every_gate_and_writes_it_on_the_row():
    import inspect

    src = inspect.getsource(backend.WavesBridge._download)
    gates = src.index("_ffmpeg_gate_holds")
    ask = src.index("_ask_quality_for(obj, type_media, media_id)")
    assert gates < ask, "the choice is read before a gate that may hold the download"
    assert "ask_quality=ask" in src and "ask_tier=ask_tier" in src
    assert "_consume_quality_override" not in src, "a download spends the choice again"
    assert not hasattr(backend.WavesBridge, "_consume_quality_override"), "the spending helper came back"


def test_a_retry_keeps_the_tier_its_row_asked_at():
    """RETRY re-enters _download; the retried row's own ask rides along, so
    a choice or setting that moved since does not retarget it."""
    b = _bridge(setting=Quality.high_lossless)
    b.setQualityOverride("t1", "LOW")
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1", keep_ask=("HIGH", "HIGH"))
    assert (b._queue[-1]["askQuality"], b._queue[-1]["quality"]) == ("HIGH", "HIGH")
    assert b._quality_overrides == {"t1": "LOW"}, "a retry moved the choice standing on the item"
    # The retry path itself hands the row's ask through.
    b._merge_plans = {}
    _bind(b, "_start_retry")
    b._start_retry(
        {
            "media_id": "t1",
            "type": "track",
            "name": "Song",
            "template": "{tmpl}",
            "collection": False,
            "askQuality": "HI_RES_LOSSLESS",
            "quality": "HI-RES",
        },
        _track(),
    )
    assert (b._queue[-1]["askQuality"], b._queue[-1]["quality"]) == ("HI_RES_LOSSLESS", "HI-RES")
    assert b._quality_overrides == {"t1": "LOW"}
    # A row without an ask of its own (older rows) retries at the choice.
    b._start_retry(
        {"media_id": "t1", "type": "track", "name": "Song", "template": "{tmpl}", "collection": False}, _track()
    )
    assert b._queue[-1]["askQuality"] == "LOW" and b._quality_overrides == {"t1": "LOW"}


# ---- what a choice hands back --------------------------------------------------


def test_a_choice_names_what_it_moves_and_re_asks_their_ownership():
    """A track downloaded this session at one tier read DOWNLOADED after a
    higher tier was chosen on it, with nothing to click (livetest report).
    The choice now names the ids it moves (qualityChoiceChanged, so the page
    hands their session-done buttons back) and re-asks their ownership
    (ownershipChanged, so the currency check runs against the choice)."""
    b = _bridge()
    b.setQualityOverride("t1", "HIGH")
    assert b.qualityChoiceChanged.calls == [(["t1"],)]
    assert b.ownershipChanged.calls == [("t1",)]
    # An album's choice reaches every track of it Waves knows: the members
    # the ownership store learned and the results page's track objects, once
    # each, and never a track of another album.
    b._objs["track"]["t2"] = _track("t2", "a1")
    b._objs["track"]["t9"] = _track("t9", "a9")
    b._ownership = SimpleNamespace(members_of=lambda cid: ["t2", "t3"] if cid == "a1" else None)
    b.setQualityOverride("a1", "LOW")
    assert b.qualityChoiceChanged.calls[-1] == (["a1", "t2", "t3"],)
    assert b.ownershipChanged.calls[-3:] == [("a1",), ("t2",), ("t3",)]
    # A track's choice never reaches its album.
    b.setQualityOverride("t2", "HI-RES")
    assert b.qualityChoiceChanged.calls[-1] == (["t2"],)
    # A repeated or refused word announces nothing; clearing announces the
    # same scope, so the buttons settle back on the setting's verdict.
    n = len(b.qualityChoiceChanged.calls)
    b.setQualityOverride("a1", "LOW")
    b.setQualityOverride("a1", "ULTRA")
    assert len(b.qualityChoiceChanged.calls) == n
    b.setQualityOverride("a1", "")
    assert b.qualityChoiceChanged.calls[-1] == (["a1", "t2", "t3"],)


def test_a_download_hands_nothing_back_and_re_asks_nothing():
    """Only a choice moves what a button stands on. The download that
    follows one has just queued the item: its button is the queue's, and its
    badge is already stating the choice, so queueing announces neither."""
    b = _bridge()
    b.setQualityOverride("t1", "HIGH")
    n, m = len(b.qualityChoiceChanged.calls), len(b.ownershipChanged.calls)
    b._download(_track(), "track", "Song", "{tmpl}", False, "t1")
    assert len(b.qualityChoiceChanged.calls) == n and len(b.ownershipChanged.calls) == m


def test_a_store_that_cannot_list_members_still_names_the_item():
    b = _bridge()

    def boom(cid):
        raise RuntimeError("locked")

    b._ownership = SimpleNamespace(members_of=boom)
    b.setQualityOverride("a1", "LOW")
    assert b.qualityChoiceChanged.calls == [(["a1"],)]
    # And a bare stub without the buckets or the store answers the item alone.
    del b._objs
    del b._ownership
    assert b._quality_choice_scope("a1") == ["a1"]
