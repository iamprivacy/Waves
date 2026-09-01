"""The setting-to-behaviour path for the 'best of both' edition merge.

Nothing pinned this before: the shipped defaults, the gate that decides whether a
plain album click runs an edition scan, and the one-shot exemption that keeps the
scan's own re-queue from scanning again were all executed by no test at all, so
flipping the default off left the suite green.

Bridge methods are borrowed onto a bare stub (no Qt, no network), the way
test_discography_video_source.py does.
"""

from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


class _AlbumStub:
    downloadAlbum = WavesBridge.downloadAlbum

    def __init__(self, prefs=None):
        self._objs = {"album": {"a1": SimpleNamespace(id="a1", name="Album", artist=SimpleNamespace(name="Band"))}}
        self._merge_plans: dict = {}
        self._merge_scanned: set = set()
        self.settings = SimpleNamespace(data=SimpleNamespace(format_album="{album_title}/{track_title}"))
        self._default_waves_prefs = _bind(self, "_default_waves_prefs")
        self._merge_pref_on = _bind(self, "_merge_pref_on")
        self._waves_prefs = dict(self._default_waves_prefs())
        if prefs:
            self._waves_prefs.update(prefs)
        self.scans: list = []
        self.downloads: list = []

    def downloadAlbumBestOfBoth(self, album_id):
        self.scans.append(album_id)

    def _download(self, obj, type_media, name, template, collection, media_id, merge_plan=None):
        self.downloads.append((media_id, merge_plan))

    def _refetch_for_download(self, bucket, media_id):  # pragma: no cover - not reached here
        raise AssertionError("the album is in _objs")


# ---- shipped defaults -------------------------------------------------------
def test_best_of_both_is_on_by_default():
    stub = _AlbumStub()
    assert stub._waves_prefs["edition_conflict"] == "merge"
    assert stub._merge_pref_on() is True


def test_the_merge_no_longer_depends_on_the_collapse_toggle():
    # It used to require collapse_editions as well, which is labelled (and
    # documented) as a discography setting and HID this control when off, so
    # turning it off silently stopped every single-album merge.
    stub = _AlbumStub({"collapse_editions": False})
    assert stub._merge_pref_on() is True

    stub.downloadAlbum("a1")
    assert stub.scans == ["a1"], "the scan still runs with the collapse toggle off"


def test_another_edition_mode_does_not_run_the_merge():
    for mode in ("keep_both", "completeness", "quality"):
        stub = _AlbumStub({"edition_conflict": mode})
        stub.downloadAlbum("a1")
        assert stub.scans == [], f"{mode} must not scan editions"
        assert stub.downloads == [("a1", None)]


# ---- the one-shot exemption -------------------------------------------------
def test_a_plain_click_runs_the_scan_and_queues_nothing_itself():
    stub = _AlbumStub()
    stub.downloadAlbum("a1")

    assert stub.scans == ["a1"]
    assert stub.downloads == [], "the scan re-queues; this call must not also download"
    assert "a1" in stub._merge_scanned, "marked so the scan's own re-queue does not scan again"


def test_the_exemption_is_consumed_by_one_click():
    # The scan found nothing and re-queued the album. That hop must not scan
    # again, but the mark must NOT survive it: a permanent mark meant one scan
    # silently downgraded the album to a plain download for the whole session,
    # with only a restart to clear it.
    stub = _AlbumStub()
    stub._merge_scanned.add("a1")

    stub.downloadAlbum("a1")
    assert stub.scans == [] and stub.downloads == [("a1", None)]
    assert "a1" not in stub._merge_scanned

    stub.downloadAlbum("a1")
    assert stub.scans == ["a1"], "a later click scans again"


def test_a_stashed_plan_downloads_as_a_merge_without_rescanning():
    stub = _AlbumStub()
    stub._merge_plans["a1"] = ["plan"]

    stub.downloadAlbum("a1")

    assert stub.scans == []
    assert stub.downloads == [("a1", ["plan"])]
    assert stub._merge_plans == {"a1": ["plan"]}, "peeked, not popped: a failed merge retries as a merge"
