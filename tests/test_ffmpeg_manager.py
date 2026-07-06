"""Unit tests for the Waves in-app FFmpeg manager (no network)."""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from tidaler.waves_ui import ffmpeg_manager as fm


# --------------------------------------------------------------------------- #
# platform detection
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "system,machine,expected",
    [
        ("Darwin", "arm64", ("macos", "arm64")),
        ("Darwin", "x86_64", ("macos", "amd64")),
        ("Linux", "x86_64", ("linux", "amd64")),
        ("Linux", "aarch64", ("linux", "arm64")),
        ("Windows", "AMD64", ("windows", "amd64")),
        ("Windows", "ARM64", ("windows", "arm64")),
    ],
)
def test_target_mapping(monkeypatch, system, machine, expected):
    monkeypatch.setattr(fm.platform, "system", lambda: system)
    monkeypatch.setattr(fm.platform, "machine", lambda: machine)
    assert fm.target() == expected


def test_target_unsupported(monkeypatch):
    monkeypatch.setattr(fm.platform, "system", lambda: "Plan9")
    monkeypatch.setattr(fm.platform, "machine", lambda: "sparc")
    with pytest.raises(fm.FfmpegUnsupportedPlatform):
        fm.target()


# --------------------------------------------------------------------------- #
# source URL resolution
# --------------------------------------------------------------------------- #
_MR_HTML = """
<a href="/download/macos/arm64/1000000000_8.0/ffmpeg.zip">old release</a>
<a href="/download/macos/arm64/1778761665_8.1.1/ffmpeg.zip">new release</a>
<a href="/download/macos/arm64/1781693612_N-125070-gd69e8d0a95/ffmpeg.zip">newer snapshot</a>
<a href="/download/macos/amd64/1778768838_8.1.1/ffmpeg.zip">intel</a>
<a href="/download/linux/arm64/1781692804_N-1/ffmpeg.zip">linux snap</a>
"""


def test_mr_parse_prefers_newest_release_over_snapshot():
    rel = fm._mr_parse(_MR_HTML, "macos", "arm64")
    assert rel is not None
    assert rel.source == "martin-riedl"
    # The release (8.1.1) is chosen even though a snapshot has a higher epoch.
    assert rel.version == "1778761665_8.1.1"
    assert rel.label == "8.1.1"
    assert rel.url == "https://ffmpeg.martin-riedl.de/download/macos/arm64/1778761665_8.1.1/ffmpeg.zip"
    assert rel.sha256_url == rel.url + ".sha256"


def test_mr_parse_snapshot_fallback_when_no_release():
    rel = fm._mr_parse(_MR_HTML, "linux", "arm64")
    assert rel is not None and rel.version == "1781692804_N-1"


def test_mr_parse_none_for_absent_target():
    assert fm._mr_parse(_MR_HTML, "linux", "amd64") is None


def test_btbn_asset_name():
    assert fm._btbn_asset_name("amd64") == "ffmpeg-master-latest-win64-lgpl.zip"
    assert fm._btbn_asset_name("arm64") == "ffmpeg-master-latest-winarm64-lgpl.zip"


def test_btbn_uses_combined_manifest():
    # BtbN ships ONE checksums.sha256 manifest, not per-asset sidecars; the Release
    # must point at it and carry the asset name to select from the multi-line file.
    data = {
        "published_at": "2026-06-30T00:00:00Z",
        "assets": [
            {"name": "ffmpeg-master-latest-win64-lgpl.zip", "browser_download_url": "https://x/win64.zip"},
            {"name": "checksums.sha256", "browser_download_url": "https://x/checksums.sha256"},
        ],
    }
    rel = fm._btbn_latest(_FakeSession({"": _Resp(json_data=data)}), "amd64")
    assert rel is not None
    assert rel.url == "https://x/win64.zip"
    assert rel.sha256_url == "https://x/checksums.sha256"
    assert rel.sha256_name == "ffmpeg-master-latest-win64-lgpl.zip"


def test_source_info_per_platform():
    mac = fm.source_info("macos")
    assert mac["name"] == "ffmpeg.martin-riedl.de" and mac["url"].startswith("https://")
    assert fm.source_info("linux")["name"] == "ffmpeg.martin-riedl.de"
    win = fm.source_info("windows")
    assert win["name"] == "BtbN/FFmpeg-Builds" and win["license"] == "LGPL"


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_extract_flat_zip(tmp_path):
    zp = tmp_path / "f.zip"
    zp.write_bytes(_zip_bytes({"ffmpeg": b"BINARY", "ffprobe": b"x"}))
    dest = tmp_path / "out"
    fm._extract_ffmpeg(zp, dest, "macos")
    assert dest.read_bytes() == b"BINARY"


def test_extract_nested_bin_zip(tmp_path):
    zp = tmp_path / "f.zip"
    zp.write_bytes(
        _zip_bytes(
            {
                "ffmpeg-master-latest-win64-lgpl/README.txt": b"hi",
                "ffmpeg-master-latest-win64-lgpl/bin/ffmpeg.exe": b"WINBIN",
            }
        )
    )
    dest = tmp_path / "out.exe"
    fm._extract_ffmpeg(zp, dest, "windows")
    assert dest.read_bytes() == b"WINBIN"


def test_extract_missing_member(tmp_path):
    zp = tmp_path / "f.zip"
    zp.write_bytes(_zip_bytes({"notffmpeg": b"x"}))
    with pytest.raises(FileNotFoundError):
        fm._extract_ffmpeg(zp, tmp_path / "out", "macos")


# --------------------------------------------------------------------------- #
# install + status + update (requests mocked)
# --------------------------------------------------------------------------- #
class _Resp:
    def __init__(self, *, content=b"", text="", headers=None, json_data=None):
        self.content = content
        self.text = text
        self.headers = headers or {}
        self._json = json_data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self.content), chunk_size):
            yield self.content[i : i + chunk_size]

    def json(self):
        return self._json


class _FakeSession:
    """Serves canned responses keyed by URL substring."""

    def __init__(self, routes):
        self.routes = routes
        self.headers = {}

    def get(self, url, **kw):
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected URL {url}")


def _install_fixture(tmp_path, monkeypatch, exe="ffmpeg"):
    payload = _zip_bytes({exe: b"FAKEFFMPEG"})
    digest = hashlib.sha256(payload).hexdigest()
    url = "https://example.test/ffmpeg.zip"
    session = _FakeSession(
        {
            "ffmpeg.zip.sha256": _Resp(text=f"{digest}  ffmpeg.zip\n"),
            "ffmpeg.zip": _Resp(content=payload, headers={"Content-Length": str(len(payload))}),
        }
    )
    rel = fm.Release(source="martin-riedl", version="123_8.1.1", label="8.1.1", url=url, sha256_url=url + ".sha256")
    # The dummy binary can't actually run, so fake the smoke test.
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n8.1.1")
    return rel, session


def test_install_writes_binary_and_manifest(tmp_path, monkeypatch):
    rel, session = _install_fixture(tmp_path, monkeypatch)
    mgr = fm.FfmpegManager(tmp_path)
    pcts: list[float] = []
    status = mgr.install(release=rel, progress_cb=pcts.append, session=session)

    assert mgr.is_installed()
    assert mgr.binary_path.read_bytes() == b"FAKEFFMPEG"
    import os

    assert os.access(mgr.binary_path, os.X_OK)
    assert pcts and pcts[-1] == 100.0
    assert status["state"] == "managed"
    assert status["version"] == "n8.1.1"
    mani = mgr._read_manifest()
    assert mani["version"] == "123_8.1.1"
    assert mani["ffmpeg_version"] == "n8.1.1"


def test_install_rejects_bad_checksum(tmp_path, monkeypatch):
    payload = _zip_bytes({"ffmpeg": b"X"})
    url = "https://example.test/ffmpeg.zip"
    session = _FakeSession(
        {
            "ffmpeg.zip.sha256": _Resp(text="deadbeef  ffmpeg.zip\n"),
            "ffmpeg.zip": _Resp(content=payload, headers={"Content-Length": str(len(payload))}),
        }
    )
    rel = fm.Release(source="martin-riedl", version="1_1.0", label="1.0", url=url, sha256_url=url + ".sha256")
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n8")
    mgr = fm.FfmpegManager(tmp_path)
    with pytest.raises(ValueError, match="checksum mismatch"):
        mgr.install(release=rel, session=session)
    assert not mgr.is_installed()  # working copy untouched / nothing installed


def test_install_rejects_missing_checksum(tmp_path, monkeypatch):
    # Fail-closed: if no checksum can be fetched (empty/missing/404 sidecar),
    # refuse to install rather than running an unverified binary.
    payload = _zip_bytes({"ffmpeg": b"X"})
    url = "https://example.test/ffmpeg.zip"
    session = _FakeSession(
        {
            "ffmpeg.zip.sha256": _Resp(text=""),  # sidecar present but unusable
            "ffmpeg.zip": _Resp(content=payload, headers={"Content-Length": str(len(payload))}),
        }
    )
    rel = fm.Release(source="martin-riedl", version="1_1.0", label="1.0", url=url, sha256_url=url + ".sha256")
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n8")
    mgr = fm.FfmpegManager(tmp_path)
    with pytest.raises(ValueError, match="no checksum"):
        mgr.install(release=rel, session=session)
    assert not mgr.is_installed()  # nothing installed


def test_update_available_compares_build(tmp_path, monkeypatch):
    rel, session = _install_fixture(tmp_path, monkeypatch)
    mgr = fm.FfmpegManager(tmp_path)
    mgr.install(release=rel, session=session)

    newer = fm.Release(source="martin-riedl", version="999_8.2", label="8.2", url="u")
    monkeypatch.setattr(fm, "latest", lambda os_key, arch, session=None: newer)
    avail, cur, new = mgr.update_available()
    assert avail is True and cur == "123_8.1.1" and new == "999_8.2"

    monkeypatch.setattr(fm, "latest", lambda os_key, arch, session=None: rel)
    avail, _, _ = mgr.update_available()
    assert avail is False


def test_fetch_sha256_selects_from_combined_manifest(tmp_path):
    manifest = (
        "11aa  ffmpeg-master-latest-win64-lgpl.zip\n"
        "22bb *ffmpeg-master-latest-winarm64-lgpl.zip\n"  # binary-mode marker tolerated
    )
    mgr = fm.FfmpegManager(tmp_path)
    sess = _FakeSession({"checksums.sha256": _Resp(text=manifest)})
    # selects OUR asset's line, not the first
    assert mgr._fetch_sha256(sess, "https://x/checksums.sha256", "ffmpeg-master-latest-winarm64-lgpl.zip") == "22bb"
    # an asset absent from the manifest yields None -> install fails closed
    assert mgr._fetch_sha256(sess, "https://x/checksums.sha256", "not-in-manifest.zip") is None
    # the single-file sidecar form (name=None) is unchanged
    sess2 = _FakeSession({"ffmpeg.zip.sha256": _Resp(text="33cc  ffmpeg.zip\n")})
    assert mgr._fetch_sha256(sess2, "https://x/ffmpeg.zip.sha256") == "33cc"


def test_status_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")
    mgr = fm.FfmpegManager(tmp_path)
    st = mgr.status()
    assert st["state"] == "missing" and st["available"] is False


def test_status_on_path(tmp_path, monkeypatch):
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "/usr/bin/ffmpeg")
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n7.1")
    mgr = fm.FfmpegManager(tmp_path)
    st = mgr.status()
    assert st["state"] == "path" and st["path"] == "/usr/bin/ffmpeg" and st["version"] == "n7.1"
    # Per-platform attribution is always present for the UI credit line.
    assert st["source"] and st["source_url"].startswith("https://")


def test_status_custom_path_used_when_unmanaged(tmp_path, monkeypatch):
    # A user-linked binary that isn't on $PATH must still report available, as an
    # *unmanaged* install (state "path", managed False, custom True): yellow.
    custom = tmp_path / "my-ffmpeg"
    custom.write_bytes(b"#!/bin/true\n")
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")  # nothing on PATH
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n9.9" if p == str(custom) else "")
    mgr = fm.FfmpegManager(tmp_path)
    st = mgr.status(str(custom))
    assert st["state"] == "path"
    assert st["available"] is True and st["managed"] is False and st["custom"] is True
    assert st["path"] == str(custom) and st["version"] == "n9.9"


def test_status_custom_path_invalid_is_not_available(tmp_path, monkeypatch):
    # A linked path whose binary won't run (probe fails) is fail-closed: it does
    # NOT count as available; we fall through to PATH, then missing.
    custom = tmp_path / "broken-ffmpeg"
    custom.write_bytes(b"not a real binary")
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")
    monkeypatch.setattr(fm, "_probe_version", lambda p: "")  # won't run
    mgr = fm.FfmpegManager(tmp_path)
    st = mgr.status(str(custom))
    assert st["state"] == "missing" and st["available"] is False


def test_status_custom_path_nonexistent_is_ignored(tmp_path, monkeypatch):
    # A stale/typo'd path (file gone) is ignored without even probing it.
    probed: list[str] = []
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")
    monkeypatch.setattr(fm, "_probe_version", lambda p: probed.append(p) or "")
    mgr = fm.FfmpegManager(tmp_path)
    st = mgr.status("/no/such/ffmpeg")
    assert st["state"] == "missing" and st["available"] is False
    assert probed == []  # never tried to execute a path that isn't a file


def test_status_managed_takes_precedence_over_custom(tmp_path, monkeypatch):
    # Our managed copy wins over any custom override for the glyph state, so the
    # transient managed path the bridge injects can never be misread as a user
    # override (and a managed install always shows green).
    rel, session = _install_fixture(tmp_path, monkeypatch)
    mgr = fm.FfmpegManager(tmp_path)
    mgr.install(release=rel, session=session)
    st = mgr.status("/somewhere/else/ffmpeg")
    assert st["state"] == "managed" and st["managed"] is True and st["custom"] is False


def test_status_default_custom_path_is_backwards_compatible(tmp_path, monkeypatch):
    # Calling status() with no custom path behaves exactly as before.
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "/usr/bin/ffmpeg")
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n7.1")
    mgr = fm.FfmpegManager(tmp_path)
    st = mgr.status()
    assert st["state"] == "path" and st["managed"] is False and st["custom"] is False


def test_remove(tmp_path, monkeypatch):
    rel, session = _install_fixture(tmp_path, monkeypatch)
    mgr = fm.FfmpegManager(tmp_path)
    mgr.install(release=rel, session=session)
    assert mgr.is_installed()
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")
    st = mgr.remove()
    assert not mgr.is_installed() and st["state"] == "missing"
