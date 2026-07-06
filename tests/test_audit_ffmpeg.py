"""Audit-remediation regression tests for the Waves FFmpeg manager.

These cover the specific failures fixed in the audit and are independent of
``tests/test_ffmpeg_manager.py`` (no shared state, no network, no real
subprocess execution; the smoke test is monkeypatched).
"""

from __future__ import annotations

import hashlib
import io
import zipfile

import pytest

from tidaler.waves_ui import ffmpeg_manager as fm


# --------------------------------------------------------------------------- #
# helpers (self-contained; do not import from the sibling test module)
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
    def __init__(self, routes):
        self.routes = routes
        self.headers = {}

    def get(self, url, **kw):
        for key, resp in self.routes.items():
            if key in url:
                return resp
        raise AssertionError(f"unexpected URL {url}")


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _session_for(payload: bytes, url: str = "https://example.test/ffmpeg.zip") -> tuple[fm.Release, _FakeSession]:
    digest = hashlib.sha256(payload).hexdigest()
    session = _FakeSession(
        {
            "ffmpeg.zip.sha256": _Resp(text=f"{digest}  ffmpeg.zip\n"),
            "ffmpeg.zip": _Resp(content=payload, headers={"Content-Length": str(len(payload))}),
        }
    )
    rel = fm.Release(source="martin-riedl", version="123_8.1.1", label="8.1.1", url=url, sha256_url=url + ".sha256")
    return rel, session


# --------------------------------------------------------------------------- #
# Finding 1: smoke-test the STAGED binary before swapping; a non-runnable but
# checksum-valid download must NOT clobber the existing working copy.
# --------------------------------------------------------------------------- #
def test_failed_smoketest_leaves_existing_binary_untouched(tmp_path, monkeypatch):
    # First install a working managed copy.
    rel, session = _session_for(_zip_bytes({"ffmpeg": b"GOODBINARY"}))
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n8.1.1")
    mgr = fm.FfmpegManager(tmp_path)
    monkeypatch.setattr(mgr, "os_key", "linux", raising=False)  # avoid macOS codesign path
    mgr.install(release=rel, session=session)
    assert mgr.binary_path.read_bytes() == b"GOODBINARY"
    good_manifest = mgr._read_manifest()

    # Now a *new* release downloads fine and passes checksum, but the staged
    # binary won't run (probe returns "").
    rel2, session2 = _session_for(_zip_bytes({"ffmpeg": b"BADARCHBINARY"}))
    rel2 = fm.Release(
        source="martin-riedl", version="999_9.9", label="9.9", url=rel2.url, sha256_url=rel2.url + ".sha256"
    )
    monkeypatch.setattr(fm, "_probe_version", lambda p: "")  # staged binary is non-runnable

    with pytest.raises(RuntimeError, match="version"):
        mgr.install(release=rel2, session=session2)

    # The previously-working binary and its manifest are intact.
    assert mgr.binary_path.read_bytes() == b"GOODBINARY"
    assert mgr._read_manifest() == good_manifest
    # No stale staged file left behind.
    assert not (mgr.install_dir / "ffmpeg.new").exists()


def test_failed_smoketest_writes_no_manifest_when_nothing_installed(tmp_path, monkeypatch):
    # Fresh install (no prior binary) whose download won't run: nothing lands,
    # and status() must not report managed/green off a stale manifest.
    rel, session = _session_for(_zip_bytes({"ffmpeg": b"BADBINARY"}))
    monkeypatch.setattr(fm, "_probe_version", lambda p: "")
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")
    mgr = fm.FfmpegManager(tmp_path)
    monkeypatch.setattr(mgr, "os_key", "linux", raising=False)

    with pytest.raises(RuntimeError):
        mgr.install(release=rel, session=session)

    assert not mgr.is_installed()
    assert not mgr.manifest_path.exists()
    st = mgr.status()
    assert st["state"] == "missing" and st["managed"] is False


def test_successful_install_still_promotes_and_writes_manifest(tmp_path, monkeypatch):
    # The reordering must not break the happy path.
    rel, session = _session_for(_zip_bytes({"ffmpeg": b"OKBINARY"}))
    monkeypatch.setattr(fm, "_probe_version", lambda p: "n8.1.1")
    mgr = fm.FfmpegManager(tmp_path)
    monkeypatch.setattr(mgr, "os_key", "linux", raising=False)
    st = mgr.install(release=rel, session=session)
    assert mgr.is_installed()
    assert mgr.binary_path.read_bytes() == b"OKBINARY"
    assert st["state"] == "managed" and st["version"] == "n8.1.1"
    assert mgr._read_manifest()["version"] == "123_8.1.1"
    assert not (mgr.install_dir / "ffmpeg.new").exists()


# --------------------------------------------------------------------------- #
# Finding 2: on macOS we VERIFY the signature and never ad-hoc re-sign or strip
# quarantine over an unverified binary.
# --------------------------------------------------------------------------- #
def test_macos_verify_never_resigns_or_strips_quarantine(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, *a, **kw):
        calls.append(list(argv))

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr(fm.subprocess, "run", fake_run)
    mgr = fm.FfmpegManager(tmp_path)
    monkeypatch.setattr(mgr, "os_key", "macos", raising=False)
    staged = tmp_path / "ffmpeg.new"
    staged.write_bytes(b"MACHOBINARY")

    mgr._macos_verify(staged)

    programs = [c[0] for c in calls]
    # We verify the advertised signature...
    assert "codesign" in programs
    codesign_argv = next(c for c in calls if c[0] == "codesign")
    assert "--verify" in codesign_argv
    # ...and never force/ad-hoc re-sign it.
    assert "--force" not in codesign_argv and "--sign" not in codesign_argv
    # We never strip the quarantine xattr to bypass Gatekeeper.
    assert not any(c[0] == "xattr" for c in calls)


def test_macos_verify_failure_is_not_fatal_and_does_not_resign(tmp_path, monkeypatch):
    calls: list[list[str]] = []

    def fake_run(argv, *a, **kw):
        calls.append(list(argv))

        class _R:
            # codesign --verify fails; anything else "succeeds".
            returncode = 1 if argv[0] == "codesign" else 0
            stdout = ""
            stderr = "not signed"

        return _R()

    monkeypatch.setattr(fm.subprocess, "run", fake_run)
    mgr = fm.FfmpegManager(tmp_path)
    monkeypatch.setattr(mgr, "os_key", "macos", raising=False)
    staged = tmp_path / "ffmpeg.new"
    staged.write_bytes(b"UNSIGNED")

    # A verification failure must not raise here (the smoke test gates promotion)
    # and must not trigger a re-sign.
    mgr._macos_verify(staged)
    resigns = [c for c in calls if c[0] == "codesign" and "--sign" in c]
    assert resigns == []
    assert not any(c[0] == "xattr" for c in calls)


# --------------------------------------------------------------------------- #
# Finding 3: _probe_version is memoized and uses a bounded timeout so status()
# on the GUI thread cannot re-fork a subprocess per call.
# --------------------------------------------------------------------------- #
def test_probe_version_is_cached_per_binary(tmp_path, monkeypatch):
    fm._probe_cache.clear()
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"BIN")

    runs: list[int] = []

    class _R:
        returncode = 0
        stdout = "ffmpeg version n7.1 built with clang"
        stderr = ""

    def fake_run(argv, *a, **kw):
        runs.append(1)
        assert kw.get("timeout") == fm._PROBE_TIMEOUT  # bounded, not 15s
        return _R()

    monkeypatch.setattr(fm.subprocess, "run", fake_run)

    assert fm._probe_version(str(binary)) == "n7.1"
    assert fm._probe_version(str(binary)) == "n7.1"
    assert fm._probe_version(str(binary)) == "n7.1"
    assert sum(runs) == 1  # only the first call actually forked


def test_probe_version_reprobes_when_binary_changes(tmp_path, monkeypatch):
    fm._probe_cache.clear()
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"BIN")

    versions = iter(["n7.1", "n9.9"])
    runs: list[int] = []

    def fake_run(argv, *a, **kw):
        runs.append(1)

        class _R:
            returncode = 0
            stdout = f"ffmpeg version {next(versions)}"
            stderr = ""

        return _R()

    monkeypatch.setattr(fm.subprocess, "run", fake_run)
    assert fm._probe_version(str(binary)) == "n7.1"
    # Replace the file with different content + bump mtime → cache evicts.
    import os

    binary.write_bytes(b"DIFFERENTBIN")
    st = binary.stat()
    os.utime(binary, (st.st_atime + 5, st.st_mtime + 5))
    assert fm._probe_version(str(binary)) == "n9.9"
    assert sum(runs) == 2


def test_probe_version_missing_file_returns_empty_and_evicts(tmp_path, monkeypatch):
    fm._probe_cache.clear()
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"BIN")

    class _R:
        returncode = 0
        stdout = "ffmpeg version n7.1"
        stderr = ""

    monkeypatch.setattr(fm.subprocess, "run", lambda *a, **k: _R())
    assert fm._probe_version(str(binary)) == "n7.1"
    assert str(binary) in fm._probe_cache
    binary.unlink()
    # A now-missing path returns "" (no subprocess) and drops the stale entry.
    called: list[int] = []
    monkeypatch.setattr(fm.subprocess, "run", lambda *a, **k: called.append(1))
    assert fm._probe_version(str(binary)) == ""
    assert called == []
    assert str(binary) not in fm._probe_cache
