"""The VIDEOS section's download-all queues the videos, and only the videos.

``downloadArtistVideos`` is the artist page's videos-only bulk download: it
never touches albums or guest tracks, it works regardless of the "Music
videos" discography source toggle (clicking a videos-specific button already
is the explicit intent that toggle captures), and it follows the partial-scan
rule: a failed or ceiling-hit videography scan refuses to queue anything
rather than reporting clean success over a set it never saw. Its rollup state
lives in the shared artist-group dict under a namespaced "vids:" id so it can
never collide with the discography button's bare artist id.
"""

from __future__ import annotations

import re
from pathlib import Path
from threading import Lock
from types import SimpleNamespace

from tidaler.waves_ui.backend import _ARTIST_VIDEO_PAGE, _VIDEOS_GROUP_PREFIX, WavesBridge

QML = (Path(__file__).parent.parent / "tidaler" / "waves_ui" / "qml" / "Main.qml").read_text(encoding="utf-8")


class _Signal:
    def __init__(self):
        self.emits: list = []

    def emit(self, *args):
        self.emits.append(args if len(args) != 1 else args[0])


class _InlinePool:
    @staticmethod
    def start(worker):
        worker.fn()


class _Artist:
    """Serves videos a window at a time, the way the endpoint does."""

    def __init__(self, videos, fail=False):
        self._videos = videos
        self._fail = fail
        self.video_calls = 0

    def get_videos(self, limit=None, offset=0):
        self.video_calls += 1
        if self._fail:
            raise RuntimeError("429")
        window = limit if limit is not None else len(self._videos)
        return list(self._videos[offset : offset + window])


class _Stub:
    downloadArtistVideos = WavesBridge.downloadArtistVideos

    def __init__(self, artist, video_download: bool = False):
        self._dl = object()
        # The discography source toggle: off by default here, to pin that the
        # videos button does not consult it.
        self.settings = SimpleNamespace(data=SimpleNamespace(video_download=video_download))
        self._artist = artist
        self._artist_groups: dict = {}
        self._artist_lock = Lock()
        self._scan_pool = _InlinePool()
        self._scan_gen = 0  # the generation STOP bumps; never bumped here
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self.scanningChanged = _Signal()
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self._videosQueued = _Signal()
        self.statuses: list = []
        self.remembered: list = []

    def _download_gate(self):
        return "ok"

    def _ffmpeg_gate_holds(self, media_id, retry):
        return False

    def _gate_reachability(self, retry, media_id):
        return True

    def _set_status(self, text):
        self.statuses.append(text)

    def _get_artist(self, artist_id):
        return self._artist

    def _dedup_videos(self, videos):
        return list(videos)

    def _remember(self, bucket, key, obj):
        self.remembered.append((bucket, key))


GID = _VIDEOS_GROUP_PREFIX + "art1"


def test_it_queues_every_video_and_nothing_else():
    artist = _Artist([SimpleNamespace(id="v1"), SimpleNamespace(id="v2")])
    stub = _Stub(artist)
    stub.downloadArtistVideos("art1")
    assert stub._videosQueued.emits == [["v1", "v2"]]
    assert stub.remembered == [("video", "v1"), ("video", "v2")]
    assert any("2 videos" in s for s in stub.statuses)


def test_it_works_with_the_discography_video_toggle_off():
    """The toggle governs what rides along with a DISCOGRAPHY download; the
    videos button is its own explicit intent and must ignore it."""
    artist = _Artist([SimpleNamespace(id="v1")])
    stub = _Stub(artist, video_download=False)
    stub.downloadArtistVideos("art1")
    assert stub._videosQueued.emits == [["v1"]]


def test_the_group_is_namespaced_away_from_the_discography_button():
    artist = _Artist([SimpleNamespace(id="v1")])
    stub = _Stub(artist)
    stub.downloadArtistVideos("art1")
    assert GID in stub._artist_groups
    assert "art1" not in stub._artist_groups, "a bare artist-id group would collide with downloadArtist's"
    assert stub._artist_groups[GID]["keys"] == {"v1"}
    # The button's state is published under the same namespaced id.
    assert (GID, "running") in stub.downloadState.emits


def test_a_long_videography_is_paged_through_not_truncated():
    count = _ARTIST_VIDEO_PAGE * 2 + 20
    artist = _Artist([SimpleNamespace(id=f"v{i}") for i in range(count)])
    stub = _Stub(artist)
    stub.downloadArtistVideos("art1")
    queued = stub._videosQueued.emits[0]
    assert len(queued) == count, "the scan stopped at the first window"
    assert queued[-1] == f"v{count - 1}"


def test_a_failed_fetch_refuses_the_whole_set():
    artist = _Artist([SimpleNamespace(id="v1")], fail=True)
    stub = _Stub(artist)
    stub.downloadArtistVideos("art1")
    assert stub._videosQueued.emits == []
    assert GID not in stub._artist_groups
    assert stub.downloadState.emits[-1] == (GID, ""), "the button must settle back to idle"
    assert any("try again" in s for s in stub.statuses)


def test_a_ceiling_hit_scan_refuses_the_whole_set():
    class _EndlessArtist(_Artist):
        def get_videos(self, limit=None, offset=0):
            self.video_calls += 1
            window = limit if limit is not None else 50
            return [SimpleNamespace(id=f"v{offset + i}") for i in range(window)]

    stub = _Stub(_EndlessArtist([]))
    stub.downloadArtistVideos("art1")
    assert stub._videosQueued.emits == []
    assert stub.downloadState.emits[-1] == (GID, "")


def test_an_artist_with_no_videos_settles_back_to_idle():
    stub = _Stub(_Artist([]))
    stub.downloadArtistVideos("art1")
    assert stub._videosQueued.emits == []
    assert stub.downloadState.emits[-1] == (GID, "")
    assert any("No videos" in s for s in stub.statuses)


# ---- QML wiring pins -------------------------------------------------------


def test_the_videos_header_carries_the_download_all_button():
    """The artist VIDEOS SectionHeader wires a trailing DownloadButton whose
    media id matches the backend's namespaced group id."""
    head = re.search(r"id: artistVideosHead.*?\n                \}", QML, re.S)
    assert head, "artist VIDEOS SectionHeader not found"
    block = head.group(0)
    assert '"vids:" + root.artistData.id' in block
    assert "waves.downloadArtistVideos(root.artistData.id)" in block


def test_the_header_trailing_slot_outranks_the_collapse_target():
    """The SectionHeader row must take z 1: without it the whole-header
    collapse MouseArea (secMa, declared after the row) eats the trailing
    button's clicks and every click collapses the section instead."""
    sec = re.search(r"component SectionHeader: Item \{.*?\n    \}", QML, re.S)
    assert sec, "SectionHeader component not found"
    block = sec.group(0)
    assert "property Component trailing: null" in block
    assert re.search(r"RowLayout \{\s*\n\s*z: 1", block), "the header row must stack above secMa"
