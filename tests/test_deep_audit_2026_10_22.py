"""Regressions for the whole-file read of paths, links, settings and SMB.

Each test pins one defect that read fine in isolation: a share link with
TIDAL's trailing "/u", an SMB share name macOS already stores encoded, a
submount relisted from the wrong folder, a live instance's private mount
swept by another, a hand-edited "false", a playlist named after a template
token, a download base typed with "..", and a remux swap a lock refused.
"""

from __future__ import annotations

import json
import os
import pathlib
from threading import Lock
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from tidalapi import Playlist, Track

from waves.config import _drop_unusable_fields
from waves.constants import MediaType
from waves.download import Download
from waves.helper.path import format_path_media
from waves.helper.tidal import get_tidal_media_id, get_tidal_media_type
from waves.model.cfg import Settings as ModelSettings
from waves.waves_ui import netmount, smb_relist


@pytest.mark.parametrize(
    "url",
    [
        "https://tidal.com/browse/track/123/u",
        "https://tidal.com/browse/track/123/",
        "https://tidal.com/track/123?u",
        "https://tidal.com/browse/track/123?utm=x#top",
        "https://tidal.com/browse/track/123/u/",
    ],
)
def test_share_link_tails_still_name_the_track(url):
    assert get_tidal_media_type(url) == MediaType.TRACK
    assert get_tidal_media_id(url) == "123"


def test_a_bare_id_is_still_an_id():
    assert get_tidal_media_id("123") == "123"


def test_an_encoded_share_name_is_not_encoded_twice():
    assert netmount.origin_url("smbfs", "//nas/My%20Music") == "smb://nas/My%20Music"
    assert netmount.origin_url("smbfs", "//nas/My Music") == "smb://nas/My%20Music"


def test_a_submount_relists_below_its_own_folder(monkeypatch):
    monkeypatch.setattr(smb_relist, "_on_macos", lambda: True)
    monkeypatch.setattr(
        smb_relist.netmount,
        "mount_info",
        lambda path: ("smbfs", "//nas/Media/My%20Music", "/Volumes/My Music"),
    )
    url, sub = smb_relist.plan_for("/Volumes/My Music/Jazz")
    assert url == "//nas/Media"
    assert sub == os.path.join("My Music", "Jazz")


def test_a_live_instances_mount_point_is_not_swept(monkeypatch, tmp_path):
    monkeypatch.setattr(smb_relist, "_on_macos", lambda: True)
    base = smb_relist.mounts_dir(str(tmp_path))
    live = os.path.join(base, f"pid-{os.getppid()}")
    os.makedirs(live)
    unmounted: list[str] = []
    assert smb_relist.sweep_stale(str(tmp_path), unmount=lambda p: unmounted.append(p) or True) == 0
    assert unmounted == []
    assert os.path.isdir(live)


def test_a_hand_edited_false_string_loads_as_false():
    stored = json.dumps({"skip_existing": "false", "lyrics_embed": "Yes", "lyrics_file": "maybe"})
    data = ModelSettings.from_json(_drop_unusable_fields(stored, ModelSettings))
    assert data.skip_existing is False
    assert data.lyrics_embed is True
    assert data.lyrics_file is ModelSettings().lyrics_file


def test_a_playlist_named_like_a_token_keeps_one_folder():
    playlist = Playlist.__new__(Playlist)
    playlist.name = "Best of {artist_name}"
    baked = format_path_media("Playlists/{playlist_name}/{artist_name} - {track_title}", playlist)
    assert baked.startswith("Playlists/Best of (artist_name)/")

    track = Track.__new__(Track)
    track.artists = [SimpleNamespace(name="Willie Nelson")]
    track.name = "On the Road Again"
    track.full_name = track.name
    out = format_path_media(baked, track)
    assert out == "Playlists/Best of (artist_name)/Willie Nelson - On the Road Again"


def test_a_track_title_holding_a_token_is_not_expanded_by_a_later_one():
    track = Track.__new__(Track)
    track.artists = [SimpleNamespace(name="Willie Nelson")]
    track.name = "Ode to {artist_name}"
    track.full_name = track.name
    assert format_path_media("{track_title} - {artist_name}", track) == "Ode to (artist_name) - Willie Nelson"


def test_a_download_base_with_dot_dot_is_resolved_once(tmp_path):
    dl = Download.__new__(Download)
    dl.path_base = str(tmp_path / "Music" / ".." / "Downloads")
    assert dl.path_base == str(tmp_path / "Downloads")
    assert ".." not in pathlib.Path(dl.path_base).parts
    dl.path_base = ""
    assert dl.path_base == ""


def _download() -> Download:
    dl = Download.__new__(Download)
    dl.fn_logger = MagicMock()
    dl._FILE_OPERATION_RETRIES = 2
    dl._FILE_OPERATION_RETRY_DELAY_SEC = 0
    dl._names_reserved_lock = Lock()
    dl.settings = SimpleNamespace(data=SimpleNamespace(path_binary_ffmpeg="ffmpeg"))
    return dl


def test_a_refused_remux_swap_keeps_the_raw_file(monkeypatch, tmp_path):
    import waves.download as download_mod

    raw = tmp_path / "merged"
    raw.write_bytes(b"raw")

    class _FakeFFmpeg:
        def __init__(self, **_kw):
            self._out = None

        def option(self, *_a):
            return self

        def input(self, **_kw):
            return self

        def output(self, url, **_kw):
            self._out = url
            return self

        def execute(self):
            pathlib.Path(self._out).write_bytes(b"remuxed")

    monkeypatch.setattr(download_mod, "FFmpeg", _FakeFFmpeg)

    def locked(*_a):
        raise PermissionError("locked by a scanner")

    monkeypatch.setattr(download_mod.os, "replace", locked)
    out = _download()._faststart_remux(raw, ".m4a")
    assert out == raw
    assert raw.read_bytes() == b"raw"
    assert [p.name for p in tmp_path.iterdir()] == ["merged"]


def _lyrics_download(monkeypatch, lrclib: tuple[str, str], tidal):
    import waves.download as download_mod

    dl = _download()
    dl.settings = SimpleNamespace(data=SimpleNamespace(lyrics_prefer_lrclib=True))
    dl._shared_http = lambda: None
    monkeypatch.setattr(download_mod, "fetch_lrclib_lyrics", lambda *_a, **_kw: lrclib)
    return dl, SimpleNamespace(
        artist=None, album=None, duration=0, name="Song", full_name="Song", artists=[], lyrics=tidal
    )


def test_a_plain_only_lrclib_hit_still_takes_tidals_timed_lyrics(monkeypatch):
    dl, track = _lyrics_download(
        monkeypatch, ("", "plain from lrclib"), lambda: SimpleNamespace(text="tidal text", subtitles="[00:01.00] timed")
    )
    assert dl._retrieve_lyrics(track) == ("[00:01.00] timed", "[00:01.00] timed", "plain from lrclib")


def test_a_plain_only_lrclib_hit_stands_when_tidal_has_nothing_timed(monkeypatch):
    dl, track = _lyrics_download(
        monkeypatch, ("", "plain from lrclib"), lambda: SimpleNamespace(text="t", subtitles="")
    )
    assert dl._retrieve_lyrics(track) == ("plain from lrclib", "", "plain from lrclib")


def test_a_plain_only_lrclib_hit_stands_when_tidal_fails(monkeypatch):
    def boom():
        raise RuntimeError("404")

    dl, track = _lyrics_download(monkeypatch, ("", "plain from lrclib"), boom)
    assert dl._retrieve_lyrics(track) == ("plain from lrclib", "", "plain from lrclib")


def test_timed_lrclib_lyrics_never_ask_tidal(monkeypatch):
    def never():
        raise AssertionError("TIDAL was asked")

    dl, track = _lyrics_download(monkeypatch, ("[00:01.00] lrclib", "plain"), never)
    assert dl._retrieve_lyrics(track) == ("[00:01.00] lrclib", "[00:01.00] lrclib", "plain")
