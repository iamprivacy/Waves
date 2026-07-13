"""HLS playlists must never be fetched by m3u8's DefaultHTTPClient.

m3u8.load()'s built-in client fetches with urllib and a bare
ssl.create_default_context(), which trusts only the interpreter's compiled-in
OpenSSL default paths. Those paths exist on the dev machine (Homebrew's
cert.pem) but not inside a packaged build on an end-user system, so every
playlist fetch failed TLS verification there. In the player that failure was
swallowed into the master-playlist fallback: the quality label stuck on AUTO,
the resolution menu did nothing, Qt picked a low variant (awful picture), and
seeking the multi-variant master restarted playback from zero.

The rule these tests pin down: every playlist fetch goes through a
requests+certifi session (RequestsClient / the pooled probe session), which
carries its own CA bundle on every platform.
"""

import pathlib
import re
from unittest.mock import MagicMock, patch

from tidaler.download import Download, RequestsClient

REPO = pathlib.Path(__file__).resolve().parent.parent

MASTER = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=6372000,RESOLUTION=1920x1080,CODECS="avc1.640028"
hls/1080.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=1540000,RESOLUTION=854x480,CODECS="avc1.64001f"
hls/480.m3u8
"""


def _iter_source_files():
    for path in (REPO / "tidaler").rglob("*.py"):
        yield path, path.read_text(encoding="utf-8")


def test_no_bare_m3u8_load_anywhere():
    """Every m3u8.load call must pass an explicit http_client."""
    offenders = []
    for path, src in _iter_source_files():
        # Real calls only: prose like "m3u8.load()" in docstrings has an
        # immediately closed argument list.
        for m in re.finditer(r"m3u8\.load\(\s*([^)\s][^\n]*)", src):
            if "http_client=" not in m.group(1):
                offenders.append(f"{path.relative_to(REPO)}: {m.group(0)}")
    assert not offenders, f"m3u8.load without http_client= (urllib fetch, breaks in packaged builds): {offenders}"


def test_requests_client_uses_shared_session():
    """RequestsClient rides the pooled certifi session, not bare requests.get."""
    fake = MagicMock()
    fake.get.return_value.text = MASTER
    fake.get.return_value.url = "https://cdn.example/master.m3u8"
    with patch.object(Download, "_shared_http", return_value=fake):
        text, _url = RequestsClient().download("https://cdn.example/master.m3u8")
    fake.get.assert_called_once()
    assert text == MASTER


def test_backend_load_playlist_parses_via_pooled_session():
    """_load_playlist fetches over the probe pool and resolves absolute URIs."""
    from tidaler.waves_ui import backend as backend_mod

    cls = next(
        obj
        for name in dir(backend_mod)
        if isinstance(obj := getattr(backend_mod, name), type) and hasattr(obj, "_load_playlist")
    )
    fake_resp = MagicMock()
    fake_resp.text = MASTER
    fake_resp.__enter__ = lambda s: fake_resp
    fake_resp.__exit__ = MagicMock(return_value=False)
    fake_session = MagicMock()
    fake_session.get.return_value = fake_resp
    with patch.object(backend_mod, "_probe_http", return_value=fake_session):
        pl = cls._load_playlist("https://cdn.example/video/master.m3u8")
    assert pl.is_variant
    heights = sorted(p.stream_info.resolution[1] for p in pl.playlists)
    assert heights == [480, 1080]
    # uri= passed through, so variant URIs resolve absolutely for the probe.
    assert all(str(p.absolute_uri).startswith("https://cdn.example/video/hls/") for p in pl.playlists)
