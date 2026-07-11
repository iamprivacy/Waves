"""Unit coverage for the My Tidal / download-folder / cover-size changes.

These pin the pure decision logic that the QML surfaces drive:
- the download-folder gate (blank vs the legacy default vs a real folder),
- the My Tidal sort -> tidalapi order-enum mapping,
- the separate cover.jpg size resolution (and its reuse-vs-refetch shortcut).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tidaler.constants import CoverDimensions
from tidaler.download import Download
from tidaler.waves_ui import backend
from tidaler.waves_ui.backend import WavesBridge

# ----- download-folder gate ------------------------------------------------


def test_folder_gate_blank_blocks():
    assert WavesBridge._folder_gate_action("", False) == "block"
    assert WavesBridge._folder_gate_action("   ", False) == "block"
    assert WavesBridge._folder_gate_action(None, False) == "block"


def test_folder_gate_legacy_default_nudges_once():
    # Existing user still on the old default, not yet warned -> one-time nudge.
    assert WavesBridge._folder_gate_action("~/download", False) == "nudge"
    # Once marked prompted, it must never nag again.
    assert WavesBridge._folder_gate_action("~/download", True) == "ok"


def test_folder_gate_real_path_ok():
    assert WavesBridge._folder_gate_action("/Users/me/Music", False) == "ok"
    assert WavesBridge._folder_gate_action("/Users/me/Music", True) == "ok"


def test_download_gate_never_probes_the_filesystem():
    """The click-time gate runs on the GUI thread: it must stay pure string
    checks. The write probe of the folder (seconds against a stale network
    mount, and it froze the UI before the queue row could even appear) belongs
    to _gate_reachability on the download worker."""
    fake = MagicMock()
    fake._folder_gate_action = lambda *a: "ok"
    assert WavesBridge._download_gate(fake) == "ok"
    fake._probe_download_base.assert_not_called()


def test_gate_reachability_ok_proceeds():
    fake = MagicMock()
    fake._probe_download_base = lambda: ("ok", "/some/folder")
    assert WavesBridge._gate_reachability(fake, lambda: None) is True


def test_gate_reachability_healed_persists_live_path():
    fake = MagicMock()
    fake._probe_download_base = lambda: ("healed", "/Volumes/Music 1/Library")
    assert WavesBridge._gate_reachability(fake, lambda: None) is True
    assert fake.settings.data.download_base_path == "/Volumes/Music 1/Library"
    fake.settings.save.assert_called_once()


def test_gate_reachability_dead_holds_retry_and_warns():
    fake = MagicMock()
    fake._probe_download_base = lambda: ("dead", "/Volumes/Gone/Library")
    retry = lambda: None
    assert WavesBridge._gate_reachability(fake, retry) is False
    assert fake._pending_download is retry
    fake.downloadFolderUnreachable.emit.assert_called_once()


def test_keep_download_folder_marks_prompted_and_replays():
    # "Keep the default location": persist the decision and run the held download.
    ran = []
    fake = MagicMock()
    fake._pending_download = lambda: ran.append("go")
    WavesBridge.keepDownloadFolder(fake)
    assert fake.settings.data.download_folder_prompted is True
    fake.settings.save.assert_called_once()
    assert ran == ["go"]  # the deferred download actually ran
    assert fake._pending_download is None  # and was cleared


def test_dismiss_download_folder_nudge_drops_pending_without_running():
    # "Choose a new location" / dismiss: abandon the held download, persist nothing
    # (so an unresolved default is asked about again next time).
    ran = []
    fake = MagicMock()
    fake._pending_download = lambda: ran.append("go")
    WavesBridge.dismissDownloadFolderNudge(fake)
    assert ran == []  # nothing downloaded
    assert fake._pending_download is None
    fake.settings.save.assert_not_called()


def test_reveal_download_path_opens_nearest_existing(tmp_path, monkeypatch):
    # Clicking the nudge's path opens the OS file manager at the download folder,
    # or the nearest existing ancestor if that folder doesn't exist yet.
    opened = []

    class _FakeDS:
        @staticmethod
        def openUrl(url):
            opened.append(url.toLocalFile())

    monkeypatch.setattr(backend.QtGui, "QDesktopServices", _FakeDS)
    fake = MagicMock()
    fake.settings.data.download_base_path = str(tmp_path)
    WavesBridge.revealDownloadPath(fake)
    assert opened[-1] == str(tmp_path)  # existing folder revealed as-is
    fake.settings.data.download_base_path = str(tmp_path / "does" / "not" / "exist")
    WavesBridge.revealDownloadPath(fake)
    assert opened[-1] == str(tmp_path)  # falls back to nearest existing ancestor


# ----- My Tidal sort -> tidalapi order enums -------------------------------


@pytest.mark.skipif(backend.OrderDirection is None, reason="tidalapi has no ordered favourites")
def test_lib_order_kwargs_maps_per_category():
    # self is unused by the method, so calling it unbound is fine.
    ka = WavesBridge._lib_order_kwargs(None, "albums", ("date", "desc"))
    assert ka["order"] is backend.AlbumOrder.DateAdded
    assert ka["order_direction"] is backend.OrderDirection.Descending

    kt = WavesBridge._lib_order_kwargs(None, "tracks", ("name", "asc"))
    assert kt["order"] is backend.ItemOrder.Name  # tracks use ItemOrder, not *Order.DateAdded
    assert kt["order_direction"] is backend.OrderDirection.Ascending

    assert WavesBridge._lib_order_kwargs(None, "albums", ("release", "asc"))["order"] is backend.AlbumOrder.ReleaseDate


@pytest.mark.skipif(backend.OrderDirection is None, reason="tidalapi has no ordered favourites")
def test_lib_order_kwargs_unsupported_is_empty():
    # An order key a category doesn't offer, or no spec at all -> no kwargs
    # (falls back to the API's default order).
    assert WavesBridge._lib_order_kwargs(None, "artists", ("release", "desc")) == {}
    assert WavesBridge._lib_order_kwargs(None, "albums", None) == {}
    assert WavesBridge._lib_order_kwargs(None, "nonsense", ("date", "desc")) == {}


@pytest.mark.skipif(backend.OrderDirection is None, reason="tidalapi has no ordered favourites")
def test_library_page_default_sort_is_date_desc():
    # A category with no explicit sort must still ASK tidalapi for date-added
    # descending. tidalapi's raw default is not date-added, so leaving it unset
    # made a tab's default "Recently added" show the wrong order and disagree
    # with the Home previews (which force date-desc).
    b = WavesBridge.__new__(WavesBridge)
    b._lib_sort = {}
    favorites = MagicMock()
    favorites.albums.return_value = []
    favorites.get_albums_count.return_value = 0
    b.tidal = MagicMock()
    b.tidal.session.user.favorites = favorites

    rows, more = WavesBridge._library_page(b, "albums", 0, 10)

    assert rows == [] and more is False
    _, kwargs = favorites.albums.call_args
    assert kwargs["order"] is backend.AlbumOrder.DateAdded
    assert kwargs["order_direction"] is backend.OrderDirection.Descending


# ----- separate cover.jpg size ---------------------------------------------


def test_want_cover_file_scope_matrix():
    # Master toggle off -> never write cover.jpg, whatever the scope.
    assert Download._want_cover_file(False, True, True) is False
    # Album/collection download always writes when saving is on (today's behaviour).
    assert Download._want_cover_file(True, True, False) is True
    # A lone single track: off by default, on only when the user opts in.
    assert Download._want_cover_file(True, False, False) is False
    assert Download._want_cover_file(True, False, True) is True


def test_cover_file_dimension_follow_matches_embedded():
    assert Download._cover_file_dimension(CoverDimensions.Px320, "follow") is CoverDimensions.Px320


def test_cover_file_dimension_explicit_and_invalid():
    assert Download._cover_file_dimension(CoverDimensions.Px320, "Px640") is CoverDimensions.Px640
    # An unknown value never crashes; it falls back to the embedded size.
    assert Download._cover_file_dimension(CoverDimensions.Px320, "not-a-size") is CoverDimensions.Px320


def _cover_fixture():
    dl = MagicMock(spec=Download)
    dl.cover_data = MagicMock(return_value=b"fetched")
    track = MagicMock()
    track.album.image = MagicMock(side_effect=lambda d: f"url:{d}")
    return dl, track


def test_cover_file_data_reuses_embedded_when_sizes_match():
    dl, track = _cover_fixture()
    out = Download._album_cover_file_data(dl, track, b"embedded", CoverDimensions.Px320, CoverDimensions.Px320)
    assert out == b"embedded"
    dl.cover_data.assert_not_called()  # no second download when sizes match


def test_cover_file_data_refetches_for_a_different_size():
    dl, track = _cover_fixture()
    out = Download._album_cover_file_data(dl, track, b"embedded", CoverDimensions.Px320, CoverDimensions.Px640)
    assert out == b"fetched"
    track.album.image.assert_called_once_with(int(CoverDimensions.Px640))


def test_cover_file_data_origin_fetches_original():
    dl, track = _cover_fixture()
    out = Download._album_cover_file_data(dl, track, b"embedded", CoverDimensions.Px320, CoverDimensions.PxORIGIN)
    assert out == b"fetched"
    track.album.image.assert_called_once_with(CoverDimensions.PxORIGIN)
