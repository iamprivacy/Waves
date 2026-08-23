"""'Download discography' includes music videos when their source toggle is on.

The ``video_download`` flag used to sit under Settings > Downloads and was
wired to nothing: manual video downloads never consult it (``dl.item()``
allows videos by default) and a whole-artist download only ever gathered
albums, EPs and guest tracks. It is now a discography source toggle (Settings
> Discography & editions, beside Albums / EPs & singles / Featured on) and
``downloadArtist`` queues the artist's music videos when it is on. These
tests pin the wiring end to end: the schema placement, the queueing, the
off-by-default restraint, and the partial-scan refusal when the video fetch
fails.
"""

from __future__ import annotations

from threading import Lock
from types import SimpleNamespace

from tidaler.model.cfg import HelpSettings
from tidaler.model.cfg import Settings as CfgSettings
from tidaler.waves_ui.backend import _ARTIST_VIDEO_PAGE, _FLAG_FIELDS, WavesBridge

_KEY = "video_download"


class _Stub:
    """Bare object the real methods get bound onto."""


def _bind(stub, name):
    return getattr(WavesBridge, name).__get__(stub, type(stub))


def _schema_stub():
    stub = _Stub()

    class _Cfg:
        data = CfgSettings()

    stub.settings = _Cfg()
    stub._help = HelpSettings()
    stub._help_for = _bind(stub, "_help_for")
    stub._default_waves_prefs = _bind(stub, "_default_waves_prefs")
    stub._waves_prefs = stub._default_waves_prefs()
    stub._waves_pref_bool = _bind(stub, "_waves_pref_bool")
    stub._ffmpeg_flag_prefs = {}
    stub.ffmpegState = lambda: {"status": "none", "source": "none", "path": ""}
    stub._user_ffmpeg_path = lambda: ""
    stub._ffmpeg_detected_path = lambda: ""
    return stub


def _sections_by_id():
    return {s["id"]: s for s in WavesBridge.settingsSchema(_schema_stub())}


def test_the_toggle_lives_with_the_discography_sources():
    sections = _sections_by_id()
    disco_keys = [f["key"] for f in sections["discography"]["fields"]]
    downloads_keys = [f["key"] for f in sections["downloads"]["fields"]]
    assert _KEY in disco_keys, "the Music videos source belongs in Discography & editions"
    assert _KEY not in downloads_keys, "the toggle must not also sit under Downloads"


def test_the_toggle_reads_as_a_discography_source():
    field = next(f for f in _sections_by_id()["discography"]["fields"] if f["key"] == _KEY)
    assert field["type"] == "bool"
    assert field["label"] == "Music videos"
    # The stock tidaler help ("Allow download of videos.") describes a gate
    # this toggle no longer is; the schema must override it.
    assert field["help"] != HelpSettings().video_download
    assert "music videos" in field["help"].lower()


def test_it_is_still_persisted_as_a_flag():
    assert _KEY in _FLAG_FIELDS


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


class _DiscoStub:
    downloadArtist = WavesBridge.downloadArtist

    def __init__(self, artist, video_download: bool):
        self._dl = object()
        # download_dolby_atmos off on purpose: the sweep's Atmos filter runs
        # (and must pass a spatial-free release list through untouched).
        self.settings = SimpleNamespace(data=SimpleNamespace(video_download=video_download, download_dolby_atmos=False))
        self._artist = artist
        self._artist_groups: dict = {}
        self._artist_lock = Lock()
        self._merge_scanned: set = set()
        self._merge_plans: dict = {}
        self._scan_pool = _InlinePool()
        self._scan_gen = 0  # the generation STOP bumps; never bumped here
        self._scans_in_flight = 0
        self._scan_count_lock = Lock()
        self.scanningChanged = _Signal()
        self.downloadProgress = _Signal()
        self.downloadState = _Signal()
        self._albumsQueued = _Signal()
        self._tracksQueued = _Signal()
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

    def _artist_releases(self, artist):
        return [SimpleNamespace(id="al1")], [], True

    def _dedup_albums(self, albums):
        return list(albums)

    def _dedup_videos(self, videos):
        return list(videos)

    def _waves_pref_bool(self, key):
        return False

    def _merge_pref_on(self):
        # Edition handling off: this file is about the video source toggle.
        return False

    def _library_bulk_skip_on(self):
        # The bulk claim gate is off here, like a library-less install; its
        # own filtering is covered by test_library_bridge.py.
        return False

    def _remember(self, bucket, key, obj):
        self.remembered.append((bucket, key))


def test_a_discography_marks_its_albums_for_one_hop_only():
    # Every queued album is exempted from downloadAlbum's own edition scan,
    # including when the merge is off: the pref can be flipped between the scan
    # above and this queueing, and an album that slipped into the scan here
    # would exit by a path that never bumps the artist rollup. Turning the merge
    # ON later is not trapped by this, because the mark is consumed on the first
    # read (see tests/test_edition_merge_gate.py).
    artist = _Artist([])
    stub = _DiscoStub(artist, video_download=False)

    stub.downloadArtist("art1")

    assert stub._albumsQueued.emits, "the albums did queue"
    assert stub._merge_scanned == {"al1"}


def test_discography_queues_videos_when_the_source_is_on():
    artist = _Artist([SimpleNamespace(id="v1"), SimpleNamespace(id="v2")])
    stub = _DiscoStub(artist, video_download=True)
    stub.downloadArtist("art1")
    assert stub._videosQueued.emits == [["v1", "v2"]]
    assert ("video", "v1") in stub.remembered and ("video", "v2") in stub.remembered
    # The videos roll up into the artist button's aggregate, like albums do.
    assert stub._artist_groups["art1"]["keys"] == {"al1", "v1", "v2"}
    assert any("2 videos" in s for s in stub.statuses)


def test_a_long_videography_is_paged_through_not_truncated():
    """One window is what the artist PAGE shows; a download that stopped there
    would queue 50 of 120 videos and still report clean success, which is the
    partial-scan rule's whole objection."""
    count = _ARTIST_VIDEO_PAGE * 2 + 20
    artist = _Artist([SimpleNamespace(id=f"v{i}") for i in range(count)])
    stub = _DiscoStub(artist, video_download=True)
    stub.downloadArtist("art1")
    queued = stub._videosQueued.emits[0]
    assert len(queued) == count, "the scan stopped at the first window"
    assert queued[-1] == f"v{count - 1}"
    assert artist.video_calls == 3, "one call per window, then a short page ends it"


def test_discography_leaves_videos_alone_when_the_source_is_off():
    artist = _Artist([SimpleNamespace(id="v1")])
    stub = _DiscoStub(artist, video_download=False)
    stub.downloadArtist("art1")
    assert artist.video_calls == 0, "the scan must not even fetch videos while the source is off"
    assert stub._videosQueued.emits == []
    assert stub._artist_groups["art1"]["keys"] == {"al1"}


def test_a_failed_video_fetch_refuses_the_whole_scan():
    """Same partial-scan rule as the album sources: silently dropping the
    videos would download a truncated discography that reports clean
    success."""
    artist = _Artist([], fail=True)
    stub = _DiscoStub(artist, video_download=True)
    stub.downloadArtist("art1")
    assert stub._artist_groups == {}
    assert stub._albumsQueued.emits == [] and stub._videosQueued.emits == []
    assert "Could not load the full discography, try again" in stub.statuses
    assert stub.downloadState.emits[-1] == ("art1", "")


# ---- One-time force-off migration (_migrate_video_flag) --------------------


def _migration_stub(stored: bool, stamped: bool):
    stub = _schema_stub()
    stub.settings.data.video_download = stored
    stub._waves_prefs["video_flag_migrated"] = stamped
    stub._settings_saves = 0
    stub._pref_saves = 0

    def _save_settings():
        stub._settings_saves += 1

    def _save_prefs():
        stub._pref_saves += 1

    stub.settings.save = _save_settings
    stub._save_waves_prefs = _save_prefs
    stub._migrate_video_flag = _bind(stub, "_migrate_video_flag")
    return stub


def test_a_leftover_inert_era_true_is_cleared_once_and_stamped():
    """Before the wiring, the toggle did nothing, so a stored True is not a
    choice; honoring it would queue whole videographies on the first
    discography download after the upgrade."""
    stub = _migration_stub(stored=True, stamped=False)
    stub._migrate_video_flag()
    assert stub.settings.data.video_download is False
    assert stub._settings_saves == 1, "the cleared flag must persist"
    assert stub._waves_prefs["video_flag_migrated"] is True
    assert stub._pref_saves == 1, "the stamp must persist"


def test_an_already_off_value_is_stamped_without_a_settings_write():
    stub = _migration_stub(stored=False, stamped=False)
    stub._migrate_video_flag()
    assert stub.settings.data.video_download is False
    assert stub._settings_saves == 0, "nothing to clear, nothing to write"
    assert stub._waves_prefs["video_flag_migrated"] is True


def test_the_stamp_makes_it_one_time_so_a_conscious_opt_in_survives():
    stub = _migration_stub(stored=True, stamped=True)
    stub._migrate_video_flag()
    assert stub.settings.data.video_download is True, "a post-migration opt-in is a real choice"
    assert stub._settings_saves == 0 and stub._pref_saves == 0


# ---- Scan-ceiling refusal (partial-scan rule) ------------------------------


def test_a_videography_at_the_ceiling_refuses_the_whole_scan():
    """If the endpoint keeps serving full pages to the safety ceiling, the
    scan saw only part of the videography; queueing it would report clean
    success over a set it never saw (the same partial-scan rule as a failed
    fetch)."""

    class _EndlessArtist(_Artist):
        def get_videos(self, limit=None, offset=0):
            self.video_calls += 1
            window = limit if limit is not None else _ARTIST_VIDEO_PAGE
            return [SimpleNamespace(id=f"v{offset + i}") for i in range(window)]

    artist = _EndlessArtist([])
    stub = _DiscoStub(artist, video_download=True)
    stub.downloadArtist("art1")
    assert stub._artist_groups == {}
    assert stub._albumsQueued.emits == [] and stub._videosQueued.emits == []
    assert "Could not load the full discography, try again" in stub.statuses
    assert stub.downloadState.emits[-1] == ("art1", "")


def test_discography_leaves_out_claimed_albums():
    # The bulk claim gate, album-grained: a fully claimed album never queues,
    # the status line counts it, and the artist aggregate only tracks what
    # actually queued (or the group would wait forever on a member that was
    # never dispatched).
    artist = _Artist([])
    stub = _DiscoStub(artist, video_download=False)
    kept = SimpleNamespace(id="al1")
    owned = SimpleNamespace(id="al2")
    stub._artist_releases = lambda a: ([kept, owned], [], True)
    stub._library_bulk_skip_on = lambda: True
    stub._library_claims_album = lambda a: a is owned
    stub.downloadArtist("art1")
    assert stub._albumsQueued.emits == [["al1"]]
    assert stub._artist_groups["art1"]["keys"] == {"al1"}
    assert any("(1 already in your library)" in s for s in stub.statuses)


def test_an_all_claimed_discography_says_so():
    # Nothing queues, but the empty result is a success story ("you have it
    # all"), not the "No albums to download" of a genuinely empty artist.
    artist = _Artist([])
    stub = _DiscoStub(artist, video_download=False)
    stub._library_bulk_skip_on = lambda: True
    stub._library_claims_album = lambda a: True
    stub.downloadArtist("art1")
    assert stub._albumsQueued.emits == []
    assert stub._artist_groups == {}
    assert "Everything here is already in your library" in stub.statuses
    assert stub.downloadState.emits[-1] == ("art1", "")
