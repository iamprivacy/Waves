"""LRCLIB lyrics lookup (tidaler/lyrics.py).

The fetcher is the front line against TIDAL's machine-transcribed lyrics: it
must return community lyrics when LRCLIB has a confident match and empty
strings in every other case (miss, wrong duration, instrumental, outage), so
the download path can fall back to TIDAL without special-casing.
"""

from __future__ import annotations

import json

from tidaler.lyrics import _DURATION_TOLERANCE_SEC, fetch_lrclib_lyrics, lyrics_file_choice
from tidaler.model.cfg import Settings


class _Response:
    def __init__(self, status_code: int, payload=None):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise json.JSONDecodeError("no body", "", 0)
        return self._payload


class _Session:
    """Records each GET and serves canned responses per endpoint suffix."""

    def __init__(self, get_response: _Response, search_response: _Response | None = None):
        self._by_suffix = {"/get": get_response, "/search": search_response or _Response(404)}
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None, headers=None, timeout=None):
        assert timeout is not None, "a lyrics lookup must always carry a timeout"
        assert headers and headers.get("User-Agent", "").startswith("Waves/")
        self.calls.append((url, params or {}))
        for suffix, response in self._by_suffix.items():
            if url.endswith(suffix):
                return response
        raise AssertionError(f"unexpected URL {url}")


def _record(synced="", plain="", duration=180, instrumental=False):
    return {
        "syncedLyrics": synced,
        "plainLyrics": plain,
        "duration": duration,
        "instrumental": instrumental,
    }


def test_exact_hit_returns_synced_and_plain():
    session = _Session(_Response(200, _record(synced="[00:01.00] line", plain="line")))

    synced, plain = fetch_lrclib_lyrics(session, "Artist", "Title", "Album", 180)

    assert synced == "[00:01.00] line"
    assert plain == "line"
    # The exact endpoint sufficed: no search request was made.
    assert [url for url, _ in session.calls] == ["https://lrclib.net/api/get"]
    assert session.calls[0][1] == {
        "artist_name": "Artist",
        "track_name": "Title",
        "album_name": "Album",
        "duration": 180,
    }


def test_search_fallback_filters_by_duration_and_prefers_synced():
    results = [
        _record(plain="wrong recording", duration=180 + _DURATION_TOLERANCE_SEC + 1),
        _record(plain="plain only", duration=180),
        _record(synced="[00:01.00] synced", plain="synced plain", duration=181),
    ]
    session = _Session(_Response(404), _Response(200, results))

    synced, plain = fetch_lrclib_lyrics(session, "Artist", "Title (2026 Repented)", "Album", 180, title_bare="Title")

    assert synced == "[00:01.00] synced"
    assert plain == "synced plain"
    # The search uses the bare title, not the version-suffixed one.
    assert session.calls[1][1] == {"track_name": "Title", "artist_name": "Artist"}


def test_search_fallback_accepts_plain_when_no_synced_candidate():
    session = _Session(_Response(404), _Response(200, [_record(plain="plain", duration=179)]))

    synced, plain = fetch_lrclib_lyrics(session, "Artist", "Title", "Album", 180)

    assert synced == ""
    assert plain == "plain"


def test_instrumental_and_miss_return_empty():
    # Instrumental exact hit: treated as no lyrics, search then also misses.
    session = _Session(_Response(200, _record(synced="x", instrumental=True)))
    assert fetch_lrclib_lyrics(session, "Artist", "Title", "Album", 180) == ("", "")

    # Plain miss on both endpoints.
    session = _Session(_Response(404))
    assert fetch_lrclib_lyrics(session, "Artist", "Title", "Album", 180) == ("", "")


def test_network_failure_returns_empty_never_raises():
    class _Boom:
        def get(self, *a, **kw):
            raise OSError("connection reset")

    assert fetch_lrclib_lyrics(_Boom(), "Artist", "Title", "Album", 180) == ("", "")


def test_missing_signature_fields_short_circuit():
    class _Never:
        def get(self, *a, **kw):
            raise AssertionError("no request should be made")

    assert fetch_lrclib_lyrics(_Never(), "", "Title", "Album", 180) == ("", "")
    assert fetch_lrclib_lyrics(_Never(), "Artist", "", "Album", 180) == ("", "")
    assert fetch_lrclib_lyrics(_Never(), "Artist", "Title", "Album", 0) == ("", "")


def test_setting_defaults_on():
    # LRCLIB-first is the intended default for new and existing installs.
    assert Settings().lyrics_prefer_lrclib is True


def test_lyrics_file_choice_timed_wins_and_gets_lrc():
    assert lyrics_file_choice("[00:01.00] line", "line", synced_only=False) == ("[00:01.00] line", ".lrc")
    assert lyrics_file_choice("[00:01.00] line", "", synced_only=True) == ("[00:01.00] line", ".lrc")


def test_lyrics_file_choice_untimed_goes_to_txt():
    # A bare text dump must never masquerade as a synced .lrc.
    assert lyrics_file_choice("", "just words", synced_only=False) == ("just words", ".txt")


def test_lyrics_file_choice_synced_only_skips_untimed():
    assert lyrics_file_choice("", "just words", synced_only=True) == ("", "")
    assert lyrics_file_choice("", "", synced_only=False) == ("", "")


def test_primary_lyrics_field_falls_back_to_untimed():
    # Most players read only the primary lyrics field (FLAC LYRICS, MP4 ©lyr);
    # a track with only untimed lyrics must still show them there, and timed
    # lyrics must always win when present.
    from tidaler.metadata import Metadata

    m = Metadata.__new__(Metadata)  # the real __init__ needs a parsable audio file
    m.lyrics = "[00:01.00] timed"
    m.lyrics_unsynced = "plain"
    assert m._primary_lyrics() == "[00:01.00] timed"

    m.lyrics = ""
    assert m._primary_lyrics() == "plain"

    m.lyrics_unsynced = ""
    assert m._primary_lyrics() == ""


def test_synced_only_setting_defaults_off():
    # Default keeps saving untimed lyrics (as .txt), matching historical intent.
    assert Settings().lyrics_file_synced_only is False
