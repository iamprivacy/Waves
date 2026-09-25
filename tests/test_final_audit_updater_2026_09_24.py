"""Final audit of 2026-09-24, the updater and FFmpeg manager items.

C40: a Restart clicked after the Windows helper gave up re-arms one first.
C41: a swap the helper could not perform is reported, not re-armed forever.
C04: a Homebrew upgrade shows phase words, never the manager's own lines.
C05: install and update failures show fixed wording, never exception text.
L14: a running ffmpeg.exe is renamed aside so an update or a remove lands.
L16: a cross-volume landing copy that fails is cleaned up on every platform.
L17: the release is authenticated before its payload is downloaded.
L18: a pre-release sorts below its final; an unparseable version is refused.

The Windows paths cannot run here, so the helper scripts are asserted as text
and the Python side is driven with a windows ``os_key`` against tmp_path.
"""

from __future__ import annotations

import errno
import os
import pathlib

import pytest
import requests

from waves.waves_ui import ffmpeg_manager as fm
from waves.waves_ui import signing
from waves.waves_ui import updater as u
from waves.waves_ui.updater import AppUpdater, Release, UpdaterError, user_facing_error


def _helper_texts(up):
    return {p.name: p.read_text() for p in sorted(up.staging_dir.glob("apply_update_*.bat"))}


# ---- L18: pre-release ordering ------------------------------------------------
@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("v0.1.31", "0.1.31-rc1", True),  # the final is offered to an rc user
        ("v0.1.31-rc1", "0.1.31", False),  # an rc is never newer than its final
        ("v0.1.31-rc2", "0.1.31-rc1", True),
        ("v0.1.31-rc10", "0.1.31-rc9", True),  # numeric, not lexical
        ("v0.1.31-rc1", "0.1.31-beta.3", True),  # beta < rc
        ("v0.1.31rc1", "0.1.30", True),  # a pre-release of the next version is newer
        ("v0.1.31+build7", "0.1.31", False),  # build metadata is not a pre-release
        ("v1.2", "1.2.0", False),
    ],
)
def test_pre_release_sorts_below_its_final(latest, current, expected):
    assert u._is_newer(latest, current) is expected


def test_is_older_refuses_a_pre_release_of_the_running_final_and_an_unparseable_version():
    assert u._is_older("0.1.31-rc1", "0.1.31") is True  # a replayed rc is a downgrade
    assert u._is_older("0.1.31", "0.1.31-rc1") is False
    assert u._is_older("nope", "0.1.31") is True  # fail closed
    assert u._is_older("", "0.1.31") is True
    assert u._is_older("0.1.32", "0.1.31") is False


def test_parse_prerelease_reads_the_common_spellings():
    assert u._parse_prerelease("v0.1.31") == ()
    assert u._parse_prerelease("v0.1.31+sha.abc") == ()
    assert u._parse_prerelease("v0.1.31-rc1") == ((1, "rc"), (0, 1))
    assert u._parse_prerelease("0.1.31.dev3") == ((1, "dev"), (0, 3))
    assert u._parse_prerelease("0.1.31-beta.2") == ((1, "beta"), (0, 2))
    assert u._parse_prerelease("nope") == ()


def test_signed_manifest_with_an_unparseable_version_is_refused(monkeypatch, tmp_path):
    """The anti-rollback gate used to pass a '# waves-version:' value with no
    digits (fail-open); it now refuses it like a missing line."""
    pub, priv = signing.keygen()
    payload = b"bytes"
    import hashlib

    manifest = f"# waves-version: latest\n{hashlib.sha256(payload).hexdigest()}  Waves-windows-amd64.zip\n".encode()
    up, calls = _prep(monkeypatch, tmp_path, payload, manifest, signing.sign(manifest, priv), pub)
    with pytest.raises(UpdaterError, match="older than the installed"):
        up.install(session=object())
    assert calls["downloads"] == 0


# ---- L17: verify before download ---------------------------------------------
_ASSET = "Waves-windows-amd64.zip"


def _prep(monkeypatch, tmp_path, payload, manifest, signature, pubkey, *, sums_url="http://x/SHA256SUMS"):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "UPDATE_PUBLIC_KEY", pubkey)
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(
        version="v2.0.0", asset=_ASSET, url="http://x/" + _ASSET, sha256sums_url=sums_url, sig_url="http://x/sig"
    )
    calls = {"downloads": 0, "order": []}

    def fake_download(self, sess, url, dest, progress_cb, abort):
        calls["downloads"] += 1
        calls["order"].append("download")
        with open(dest, "wb") as fh:
            fh.write(payload)

    def fake_manifest(self, sess, url):
        calls["order"].append("manifest")
        return manifest

    monkeypatch.setattr(AppUpdater, "_download", fake_download)
    monkeypatch.setattr(AppUpdater, "_fetch_manifest", fake_manifest)
    monkeypatch.setattr(AppUpdater, "_fetch_signature", lambda self, sess, url: signature)
    monkeypatch.setattr(AppUpdater, "_apply", lambda self, p, rel, log, abort=None: p)
    return up, calls


def _signed(payload: bytes, version="v2.0.0"):
    import hashlib

    pub, priv = signing.keygen()
    manifest = f"# waves-version: {version}\n{hashlib.sha256(payload).hexdigest()}  {_ASSET}\n".encode()
    return pub, manifest, signing.sign(manifest, priv)


def test_a_release_without_a_signed_manifest_is_refused_before_any_download(monkeypatch, tmp_path):
    pub, manifest, sig = _signed(b"payload")
    up, calls = _prep(monkeypatch, tmp_path, b"payload", manifest, sig, pub, sums_url=None)
    with pytest.raises(UpdaterError, match="no signed checksum manifest"):
        up.install(session=object())
    assert calls["downloads"] == 0
    assert list((tmp_path / "updates").glob("*-" + _ASSET)) == []  # no temp payload was even created


def test_a_bad_signature_is_refused_before_any_download(monkeypatch, tmp_path):
    pub, manifest, _ = _signed(b"payload")
    up, calls = _prep(monkeypatch, tmp_path, b"payload", manifest, "bm90IGEgc2ln", pub)
    with pytest.raises(UpdaterError, match="signature is invalid"):
        up.install(session=object())
    assert calls["downloads"] == 0


def test_an_asset_missing_from_the_manifest_is_refused_before_any_download(monkeypatch, tmp_path):
    import hashlib

    pub, priv = signing.keygen()
    manifest = f"# waves-version: v2.0.0\n{hashlib.sha256(b'x').hexdigest()}  Other.zip\n".encode()
    up, calls = _prep(monkeypatch, tmp_path, b"x", manifest, signing.sign(manifest, priv), pub)
    with pytest.raises(UpdaterError, match="not in the signed manifest"):
        up.install(session=object())
    assert calls["downloads"] == 0


def test_the_manifest_is_verified_first_and_the_payload_hashed_after(monkeypatch, tmp_path):
    pub, manifest, sig = _signed(b"payload")
    up, calls = _prep(monkeypatch, tmp_path, b"payload", manifest, sig, pub)
    assert up.install(session=object())["ok"] is True
    assert calls["order"] == ["manifest", "download"]
    # ...and a payload that does not match the authenticated hash still fails.
    up, calls = _prep(monkeypatch, tmp_path / "b", b"tampered", manifest, sig, pub)
    with pytest.raises(UpdaterError, match="Checksum mismatch"):
        up.install(session=object())
    assert calls["downloads"] == 1


# ---- L16: partial landing copies are cleaned up --------------------------------
def _half_move(src, dst, *a, **k):
    pathlib.Path(dst).mkdir(parents=True, exist_ok=True)
    (pathlib.Path(dst) / "part.so").write_text("partial")
    raise OSError(errno.ENOSPC, "no space left on device")


def test_apply_unix_tree_cleans_a_partial_landing_copy(tmp_path, monkeypatch):
    up = AppUpdater(tmp_path / "cfg", "1.0.0", repo="owner/Waves")
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    (install_root / "Waves").write_text("OLD")
    new_tree = tmp_path / "staging" / "Waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves").write_text("NEW")
    monkeypatch.setattr(u.shutil, "move", _half_move)
    with pytest.raises(OSError):
        up._apply_unix_tree(new_tree, install_root / "Waves", lambda *a, **k: None)
    assert not install_root.with_name("Waves.new").exists()
    assert (install_root / "Waves").read_text() == "OLD"


def test_apply_macos_cleans_a_partial_landing_copy(tmp_path, monkeypatch):
    up = AppUpdater(tmp_path / "cfg", "1.0.0", repo="owner/Waves")
    bundle = tmp_path / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    (bundle / "Contents" / "MacOS" / "Waves").write_text("OLD")
    staged = tmp_path / "staging" / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text("NEW")
    monkeypatch.setattr(u.shutil, "move", _half_move)
    with pytest.raises(OSError):
        up._apply_macos(staged, bundle / "Contents" / "MacOS" / "Waves", lambda *a, **k: None)
    assert not bundle.with_name("Waves.app.new").exists()
    assert (bundle / "Contents" / "MacOS" / "Waves").read_text() == "OLD"


def test_apply_unix_cleans_a_partial_cross_device_copy(tmp_path, monkeypatch):
    """The AppImage twin: EXDEV falls back to a copy beside the target, and a
    copy that stops partway must not leave a '<name>.new' file behind."""
    up = AppUpdater(tmp_path / "cfg", "1.0.0", repo="owner/Waves")
    target = tmp_path / "apps" / "Waves.AppImage"
    target.parent.mkdir()
    target.write_text("OLD")
    staged = tmp_path / "staging" / "Waves.AppImage"
    staged.parent.mkdir()
    staged.write_text("NEW")
    real_replace = os.replace

    def replace(src, dst, *a, **k):
        if pathlib.Path(src) == staged:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *a, **k)

    def half_copy(src, dst, *a, **k):
        pathlib.Path(dst).write_text("NE")
        raise OSError(errno.ENOSPC, "no space left on device")

    monkeypatch.setattr(u.os, "replace", replace)
    monkeypatch.setattr(u.shutil, "copy2", half_copy)
    with pytest.raises(OSError):
        up._apply_unix(staged, target, lambda *a, **k: None)
    assert not target.with_name("Waves.AppImage.new").exists()
    assert target.read_text() == "OLD"


# ---- C04: Homebrew output never reaches the UI ---------------------------------
class _FakeProc:
    def __init__(self, lines, code=0):
        import io

        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self._code = code

    def wait(self):
        return self._code

    def poll(self):
        return self._code

    def terminate(self):
        pass


def _brew(monkeypatch, lines, code=0):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: _FakeProc(lines, code))
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(version="v1.1.0", asset="a", url="u")
    return up


_BREW_LINES = [
    "==> Downloading https://github.com/iamprivacy/Waves/releases/download/v1.1.0/waves_macos-apple-silicon.zip",
    "Already downloaded: /Users/someone/Library/Caches/Homebrew/downloads/0123abcd--waves_macos-apple-silicon.zip",
    "==> Upgrading waves 1.0.0 -> 1.1.0",
    "==> Moving App 'Waves.app' to '/Applications/Waves.app'",
    "==> Purging files for version 1.0.0 of Cask waves",
    "🍺  waves was upgraded",
]


def test_managed_upgrade_shows_phase_words_only(monkeypatch):
    up = _brew(monkeypatch, _BREW_LINES)
    logs, pcts = [], []
    result = up.install(progress_cb=pcts.append, log_cb=logs.append)
    assert result["ok"] is True
    phases = [m for m in logs if m in ("Downloading", "Installing", "Finishing")]
    assert phases == ["Downloading", "Installing", "Finishing"]
    for msg in logs:
        assert "/Users" not in msg and "https://" not in msg and "==>" not in msg and "Caches" not in msg, msg
    assert pcts == sorted(pcts)  # progress never goes backwards


def test_managed_upgrade_phase_never_moves_backwards():
    assert u._managed_phase("==> Installing Cask waves", "Downloading") == ("Installing", 70.0)
    assert u._managed_phase("==> Downloading a dependency", "Installing") == ("Installing", 0.0)
    assert u._managed_phase("some unrelated line", "Installing") == ("Installing", 0.0)
    assert u._managed_phase("some unrelated line", "") == ("", 0.0)


def test_managed_upgrade_failure_tail_is_scrubbed(monkeypatch):
    home = str(pathlib.Path.home())
    up = _brew(monkeypatch, [f"Error: could not write {home}/Library/Caches/Homebrew/x.zip"], code=1)
    with pytest.raises(UpdaterError) as info:
        up.install()
    assert home not in str(info.value)
    assert "reported an error" in str(info.value)


# ---- C05: fixed wording for failures -------------------------------------------
def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(f"{status} Client Error: x for url: https://example.test/a", response=resp)


@pytest.mark.parametrize(
    "exc,expected",
    [
        (UpdaterError("Refusing to install an update: the release has no signed checksum manifest."), "Refusing"),
        (
            requests.exceptions.ConnectionError("HTTPSConnectionPool(host='x', port=443): Max retries"),
            "Could not reach",
        ),
        (requests.exceptions.ConnectTimeout("timed out"), "Could not reach"),
        (requests.exceptions.ReadTimeout("timed out"), "Could not reach"),
        (_http_error(404), "The download is not available right now"),
        (_http_error(503), "The update server returned an error"),
        (OSError(errno.ENOSPC, "No space left on device"), "Not enough disk space"),
        (
            PermissionError(errno.EACCES, "Permission denied: '/Users/x/Waves.app'"),
            "could not write to its install folder",
        ),
        (ValueError("checksum mismatch: expected abc, got def"), "The download did not verify"),
        (RuntimeError("boom /Users/x"), "Update failed"),
        (KeyError("x"), "Update failed"),
    ],
)
def test_user_facing_error_maps_library_text_to_fixed_wording(exc, expected):
    msg = user_facing_error(exc, "Update failed")
    assert expected in msg
    for leak in ("HTTPSConnectionPool", "for url", "/Users", "Errno", "expected abc"):
        assert leak not in msg, msg


def test_user_facing_error_passes_plain_types_through():
    exc = fm.FfmpegUnsupportedPlatform("No FFmpeg build for plan9/amd64")
    assert user_facing_error(exc, "Install failed") == "Install failed"
    assert user_facing_error(exc, "Install failed", plain=(fm.FfmpegUnsupportedPlatform,)) == str(exc)
    assert user_facing_error(UpdaterError("   "), "Update failed") == "Update failed"


# ---- C40: Restart re-arms a helper that gave up --------------------------------
def _armed_windows(tmp_path, monkeypatch, *, helper_alive):
    """A Windows install() has staged v2 and armed a helper; the helper's
    script is on disk while it waits and gone once it gave up."""
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    target.write_text("OLD")
    new_tree = install_root.with_name("Waves.new")
    new_tree.mkdir()
    (new_tree / "Waves.exe").write_text("NEW")
    monkeypatch.setattr(u, "_current_exe", lambda: target)
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    up.os_key = "windows"
    up.staging_dir.mkdir(parents=True)
    result = {"ok": True, "version": "v2.0.0", "applied_to": str(target), "relaunch": True, "already_staged": False}
    up._armed_result = dict(result)
    up._write_armed_marker(result)
    if helper_alive:
        (up.staging_dir / f"apply_update_{os.getpid()}.bat").write_bytes(b"@echo off\r\n")
    spawned = []
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.append(kw["env"]))
    return up, spawned


def test_restart_after_the_helper_gave_up_arms_a_fresh_one(tmp_path, monkeypatch):
    up, spawned = _armed_windows(tmp_path, monkeypatch, helper_alive=False)
    assert up.rearm_for_restart() is True
    assert len(spawned) == 1, "a fresh helper was spawned against this process"
    assert spawned[0]["WAVES_UPDATE_3"].endswith("Waves.new")
    assert list(_helper_texts(up)) == [f"apply_update_{os.getpid()}.bat"]
    assert up.status()["pending_restart"] is True


def test_restart_with_a_live_helper_never_arms_a_second_one(tmp_path, monkeypatch):
    """Two helpers on one pid would both wake at exit, and the loser's rmdir
    lands on the winner's backup: a live script means leave it alone."""
    up, spawned = _armed_windows(tmp_path, monkeypatch, helper_alive=True)
    assert up.rearm_for_restart() is True
    assert spawned == []
    assert (up.staging_dir / f"apply_update_{os.getpid()}.bat").read_bytes() == b"@echo off\r\n"


def test_rearm_is_a_no_op_off_windows_or_with_nothing_armed(tmp_path, monkeypatch):
    up, spawned = _armed_windows(tmp_path, monkeypatch, helper_alive=False)
    up.os_key = "linux"
    assert up.rearm_for_restart() is False
    up.os_key = "windows"
    up._armed_result = None
    assert up.rearm_for_restart() is False
    assert spawned == []


def test_rearm_reports_false_when_the_staged_tree_is_gone(tmp_path, monkeypatch):
    up, spawned = _armed_windows(tmp_path, monkeypatch, helper_alive=False)
    (tmp_path / "Waves.new" / "Waves.exe").unlink()
    assert up.rearm_for_restart() is False
    assert spawned == [] and up._armed_result is None and up.status()["pending_restart"] is False


# ---- C41: a failed swap is reported, and re-armed at most once ------------------
def test_tree_helper_records_why_the_swap_did_not_happen(tmp_path, monkeypatch):
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    new_tree = tmp_path / "staging" / "Waves"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves.exe").write_bytes(b"NEW")
    up._apply_windows_tree(new_tree, install_root / "Waves.exe", lambda *a, **k: None)
    lines = next(iter(_helper_texts(up).values())).replace("\r\n", "\n").split("\n")
    assert f'set "OUTCOME=%~dp0{AppUpdater._OUTCOME_NAME}"' in lines
    rename_failed = next(i for i, ln in enumerate(lines) if ln.startswith("echo backup rename failed"))
    assert lines[rename_failed + 1] == 'echo swap_failed in_use> "%OUTCOME%"'
    assert lines[rename_failed + 2] == "goto relaunch"
    swap_in = next(ln for ln in lines if ln.startswith('move "%NEWTREE%" "%INSTALL%"'))
    assert 'echo swap_failed swap_in> "%OUTCOME%" & goto restore' in swap_in
    no_exe = next(ln for ln in lines if ln.startswith('if not exist "%TARGET%" (echo swap left no'))
    assert 'echo swap_failed no_exe> "%OUTCOME%" & goto restore' in no_exe
    # the success path writes no outcome
    ok_start = lines.index(next(ln for ln in lines if ln.startswith("echo swap ok")))
    restore = lines.index(":restore")
    assert not any("OUTCOME" in ln for ln in lines[ok_start:restore])


def test_exe_helper_records_why_the_swap_did_not_happen(tmp_path, monkeypatch):
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    install = tmp_path / "app"
    install.mkdir()
    staged = tmp_path / "staging" / "Waves.exe"
    staged.parent.mkdir()
    staged.write_text("NEW")
    up._apply_windows(staged, install / "Waves.exe", lambda *a, **k: None)
    lines = next(iter(_helper_texts(up).values())).replace("\r\n", "\n").split("\n")
    assert f'set "OUTCOME=%~dp0{AppUpdater._OUTCOME_NAME}"' in lines
    move_failed = next(i for i, ln in enumerate(lines) if ln.startswith("echo backup move failed"))
    assert lines[move_failed + 1] == 'echo swap_failed in_use> "%OUTCOME%"'
    new_in = next(ln for ln in lines if ln.startswith('move /Y "%NEWEXE%" "%TARGET%"'))
    assert 'echo swap_failed swap_in> "%OUTCOME%" & move /Y "%BACKUP%" "%TARGET%"' in new_in


def _staged_after_failure(tmp_path, monkeypatch, *, reason="in_use", running="1.0.0", marker=None):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    target.write_text("OLD")
    new_tree = install_root.with_name("Waves.new")
    new_tree.mkdir()
    (new_tree / "Waves.exe").write_text("NEW")
    monkeypatch.setattr(u, "_current_exe", lambda: target)
    up = AppUpdater(tmp_path / "config", running, repo="owner/Waves")
    up.os_key = "windows"
    up.staging_dir.mkdir(parents=True)
    up._write_armed_marker(marker or {"ok": True, "version": "v2.0.0", "applied_to": str(target), "relaunch": True})
    (up.staging_dir / AppUpdater._OUTCOME_NAME).write_text(f"swap_failed {reason}\r\n")
    spawned = []
    monkeypatch.setattr(u.subprocess, "Popen", lambda cmd, **kw: spawned.append(kw["env"]))
    return up, new_tree, spawned


def test_a_failed_swap_is_reported_in_plain_words_and_re_armed_once(tmp_path, monkeypatch):
    up, _new_tree, spawned = _staged_after_failure(tmp_path, monkeypatch)
    pending = up.resume_pending_apply()
    assert pending is not None and pending["swap_failed"] is True and pending["ok"] is False
    assert pending["message"] == "The update could not be applied because the install folder was in use."
    assert pending["rearmed"] is True and pending["version"] == "v2.0.0"
    assert len(spawned) == 1  # one more try
    assert up.status()["swap_failure"] == pending["message"]
    assert up.status()["pending_restart"] is True
    assert not (up.staging_dir / AppUpdater._OUTCOME_NAME).exists()  # consumed
    assert up._read_armed_marker()["rearmed_after_failure"] == 1  # the retry is counted
    for leak in ("\\", "/", "Waves.new", "update.log"):
        assert leak not in pending["message"]


def test_a_second_failed_swap_ends_the_loop(tmp_path, monkeypatch):
    marker = {"ok": True, "version": "v2.0.0", "applied_to": "x", "relaunch": True, "rearmed_after_failure": 1}
    up, new_tree, spawned = _staged_after_failure(tmp_path, monkeypatch, marker=marker)
    pending = up.resume_pending_apply()
    assert pending is not None and pending["swap_failed"] is True and pending["rearmed"] is False
    assert spawned == []  # not armed again
    assert up._read_armed_marker() is None and not new_tree.exists()
    assert up.status()["pending_restart"] is False
    assert up.status()["swap_failure"].startswith("The update could not be applied")
    assert not (up.staging_dir / AppUpdater._OUTCOME_NAME).exists()


def test_other_failure_reasons_get_plain_wording_too(tmp_path, monkeypatch):
    up, _, _ = _staged_after_failure(tmp_path, monkeypatch, reason="swap_in")
    assert up.resume_pending_apply()["message"] == "The update could not be applied, so the previous version was kept."
    (tmp_path / "b").mkdir()
    up2, _, _ = _staged_after_failure(tmp_path / "b", monkeypatch, reason="?? weird\\path")
    assert up2.resume_pending_apply()["message"] == "The update could not be applied."


def test_a_stale_outcome_after_a_landed_swap_is_ignored(tmp_path, monkeypatch):
    """Running the staged version means the retry worked: nothing to report."""
    up, new_tree, spawned = _staged_after_failure(tmp_path, monkeypatch, running="2.0.0")
    assert up.resume_pending_apply() is None
    assert spawned == [] and up.status()["swap_failure"] == ""
    assert not (up.staging_dir / AppUpdater._OUTCOME_NAME).exists()
    assert up._read_armed_marker() is None and not new_tree.exists()


def test_an_outcome_with_nothing_staged_is_cleared(tmp_path, monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    up = AppUpdater(tmp_path / "config", "1.0.0", repo="owner/Waves")
    up.os_key = "windows"
    up.staging_dir.mkdir(parents=True)
    (up.staging_dir / AppUpdater._OUTCOME_NAME).write_text("swap_failed in_use\r\n")
    assert up.resume_pending_apply() is None
    assert not (up.staging_dir / AppUpdater._OUTCOME_NAME).exists()


def test_an_unrelated_file_is_not_a_failure_report(tmp_path, monkeypatch):
    up, _, spawned = _staged_after_failure(tmp_path, monkeypatch)
    (up.staging_dir / AppUpdater._OUTCOME_NAME).write_text("hello\r\n")
    pending = up.resume_pending_apply()
    assert pending is not None and "swap_failed" not in pending  # the ordinary re-arm
    assert len(spawned) == 1 and up.status()["swap_failure"] == ""


# ---- L14: a running ffmpeg.exe is renamed aside ---------------------------------
def _windows_manager(tmp_path, monkeypatch, *, running_name="ffmpeg.exe"):
    """A manager on os_key windows whose live binary behaves like a running
    exe: it can be renamed but never replaced or deleted in place."""
    mgr = fm.FfmpegManager(tmp_path)
    mgr.os_key = "windows"
    mgr.install_dir.mkdir(parents=True)
    live = mgr.install_dir / running_name
    live.write_bytes(b"RUNNING")
    real_replace, real_unlink = os.replace, os.unlink

    def replace(src, dst, *a, **k):
        if pathlib.Path(dst) == live and live.exists():
            raise PermissionError(5, "Access is denied")
        return real_replace(src, dst, *a, **k)

    def unlink(path, *a, **k):
        if pathlib.Path(path) == live and live.exists():
            raise PermissionError(5, "Access is denied")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(fm.os, "replace", replace)
    monkeypatch.setattr(os, "unlink", unlink)
    return mgr, live


def test_ffmpeg_update_over_a_running_exe_lands(tmp_path, monkeypatch):
    mgr, live = _windows_manager(tmp_path, monkeypatch)
    staged = mgr.install_dir / "ffmpeg.exe.abc.new"
    staged.write_bytes(b"NEW")
    mgr._swap_in(staged)
    assert live.read_bytes() == b"NEW"
    asides = list(mgr.install_dir.glob("ffmpeg.exe.old-*"))
    assert len(asides) == 1 and asides[0].read_bytes() == b"RUNNING"


def test_aside_binaries_are_swept_at_the_next_launch(tmp_path, monkeypatch):
    mgr = fm.FfmpegManager(tmp_path)
    mgr.os_key = "windows"
    mgr.install_dir.mkdir(parents=True)
    (mgr.install_dir / "ffmpeg.exe.old-123").write_bytes(b"OLD")
    (mgr.install_dir / "ffmpeg.exe.old-456").write_bytes(b"OLD")
    (mgr.install_dir / "ffmpeg.json").write_text("{}")  # not ours to sweep
    fresh = fm.FfmpegManager(tmp_path)  # the next launch
    fresh.os_key = "windows"
    fresh.status()
    assert list(fresh.install_dir.glob("ffmpeg.exe.old-*")) == []
    assert (fresh.install_dir / "ffmpeg.json").exists()


def test_a_sweep_leaves_a_still_running_aside_for_next_time(tmp_path, monkeypatch):
    mgr = fm.FfmpegManager(tmp_path)
    mgr.os_key = "windows"
    mgr.install_dir.mkdir(parents=True)
    stuck = mgr.install_dir / "ffmpeg.exe.old-1"
    stuck.write_bytes(b"OLD")
    real_unlink = os.unlink

    def unlink(path, *a, **k):
        if pathlib.Path(path) == stuck:
            raise PermissionError(5, "Access is denied")
        return real_unlink(path, *a, **k)

    monkeypatch.setattr(os, "unlink", unlink)
    mgr.sweep_aside()  # no exception
    assert stuck.exists()


def test_removing_a_running_ffmpeg_moves_it_aside_and_reports_missing(tmp_path, monkeypatch):
    mgr, live = _windows_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")
    (mgr.install_dir / "ffmpeg.json").write_text("{}")
    st = mgr.remove()
    assert "remove_error" not in st and st["state"] == "missing"
    assert not live.exists() and not (mgr.install_dir / "ffmpeg.json").exists()


def test_remove_reports_a_plain_message_instead_of_raising(tmp_path, monkeypatch):
    mgr, live = _windows_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(fm.os, "replace", lambda *a, **k: (_ for _ in ()).throw(PermissionError(5, "denied")))
    st = mgr.remove()
    assert live.exists()
    assert st["remove_error"].startswith("FFmpeg is in use right now")
    for leak in ("WinError", "denied", "\\", "/"):
        assert leak not in st["remove_error"]


def test_remove_on_a_unix_host_is_unchanged(tmp_path, monkeypatch):
    mgr = fm.FfmpegManager(tmp_path)
    mgr.os_key = "linux"
    mgr.install_dir.mkdir(parents=True)
    mgr.binary_path.write_bytes(b"X")
    monkeypatch.setattr(fm, "_which_ffmpeg", lambda os_key: "")
    st = mgr.remove()
    assert "remove_error" not in st and not mgr.binary_path.exists()
