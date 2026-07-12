"""Unit tests for the in-app self-updater (no network, no Qt).

Covers the pure logic (version compare, platform asset selection, status
states, the configured/frozen gates) and a mocked download→verify→stage flow
that asserts a checksum mismatch never corrupts anything. The real platform
swap (``_apply``) targets the live executable, so it is stubbed here; it is only
exercisable against real CI artifacts.
"""

import errno
import hashlib
import io
import os
import zipfile

import pytest

from tidaler.waves_ui import signing
from tidaler.waves_ui import updater as u
from tidaler.waves_ui.updater import AppUpdater, Release, UpdaterError


# ---- cross-platform apply (EXDEV / rollback) --------------------------------
def test_apply_unix_tree_is_cross_device_safe(tmp_path, monkeypatch):
    """The staged tree usually lives on a different filesystem than the install
    (e.g. ~/.config vs /opt or an AppImage mount). rename(2) can't cross
    devices, so a bare os.replace(new_tree, install_root) raised EXDEV and left
    the app uninstalled. The fix lands the tree on the install volume first, so
    the final swap is a same-device rename. Simulate EXDEV for any os.replace
    whose source is the staged tree; the new flow must never make that call."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    install_root = tmp_path / "app"
    install_root.mkdir()
    (install_root / "Waves").write_text("OLD")
    (install_root / "lib.so").write_text("oldlib")
    target = install_root / "Waves"
    staged = tmp_path / "staging" / "Waves.dist"
    staged.mkdir(parents=True)
    (staged / "Waves").write_text("NEW")
    (staged / "lib.so").write_text("newlib")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src) == str(staged):  # the cross-device move the old code did
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    up._apply_unix_tree(staged, target, lambda *a, **k: None)

    assert (install_root / "Waves").read_text() == "NEW"
    assert (install_root / "lib.so").read_text() == "newlib"
    assert not (tmp_path / "app.old").exists()
    assert not (tmp_path / "app.new").exists()
    assert not staged.exists()


def test_apply_unix_tree_rolls_back_on_swap_failure(tmp_path, monkeypatch):
    """If the swap fails partway, the live install must be restored from backup
    rather than left deleted."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    install_root = tmp_path / "app"
    install_root.mkdir()
    (install_root / "Waves").write_text("LIVE")
    target = install_root / "Waves"
    staged = tmp_path / "staging" / "Waves.dist"
    staged.mkdir(parents=True)
    (staged / "Waves").write_text("NEW")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src).endswith(".new"):  # the staged→install swap blows up
            raise OSError(errno.EIO, "boom")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    with pytest.raises(OSError):
        up._apply_unix_tree(staged, target, lambda *a, **k: None)
    assert install_root.exists() and (install_root / "Waves").read_text() == "LIVE"


def test_windows_helper_spawn_contract(tmp_path, monkeypatch):
    """The swap helper must get a hidden console (CREATE_NO_WINDOW), never
    DETACHED_PROCESS: detached cmd has no console at all and the batch never
    ran (tasklist/find/start are console programs), so updates downloaded but
    were never applied. The cwd is pinned to the staging dir so the helper
    cannot hold a lock inside the install folder it renames."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    calls = {}

    def fake_popen(args, **kw):
        calls["args"], calls["kw"] = args, kw

    monkeypatch.setattr(u.subprocess, "Popen", fake_popen)
    target = tmp_path / "install" / "Waves.exe"
    target.parent.mkdir()
    target.write_text("OLD")
    up.staging_dir.mkdir(parents=True, exist_ok=True)
    staged = up.staging_dir / "Waves.exe"
    staged.write_text("NEW")
    monkeypatch.setattr(u.os, "getpid", lambda: 4242)

    up._apply_windows(staged, target, lambda *a, **k: None)

    assert calls["kw"]["creationflags"] == 0x08000000  # CREATE_NO_WINDOW
    assert calls["kw"]["cwd"] == str(up.staging_dir)
    bat = (up.staging_dir / "apply_update.bat").read_text()
    assert "PID eq 4242" in bat
    assert "update.log" in bat  # every step is diagnosable in the field
    assert ":swap" in bat and "mtries" in bat  # bounded retry while the exe unlocks
    assert bat.count('start "" ') >= 2  # every failure path still relaunches


def test_apply_macos_is_cross_device_safe(tmp_path, monkeypatch):
    """The staged `.app` usually lives under ~/.config while the install sits in
    /Applications (a different volume), so a bare os.replace(staged, bundle) would
    raise EXDEV. The new flow lands the bundle on the install volume first, so the
    final swap is a same-device rename and never calls os.replace on the staged
    path directly."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)  # no real xattr
    apps = tmp_path / "Applications"
    bundle = apps / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    target = bundle / "Contents" / "MacOS" / "Waves"
    target.write_text("OLD")
    staged = tmp_path / "staging" / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text("NEW")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src) == str(staged):  # the cross-device move the old code did
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    up._apply_macos(staged, target, lambda *a, **k: None)

    assert (bundle / "Contents" / "MacOS" / "Waves").read_text() == "NEW"
    assert not (apps / "Waves.app.old").exists()
    assert not (apps / "Waves.app.new").exists()
    assert not staged.exists()


def test_apply_macos_rolls_back_on_swap_failure(tmp_path, monkeypatch):
    """If the bundle swap fails partway, the live `.app` must be restored from its
    backup rather than left deleted."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.subprocess, "run", lambda *a, **k: None)
    apps = tmp_path / "Applications"
    bundle = apps / "Waves.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    target = bundle / "Contents" / "MacOS" / "Waves"
    target.write_text("LIVE")
    staged = tmp_path / "staging" / "Waves.app"
    (staged / "Contents" / "MacOS").mkdir(parents=True)
    (staged / "Contents" / "MacOS" / "Waves").write_text("NEW")

    real_replace = os.replace

    def fake_replace(src, dst, *a, **k):
        if str(src).endswith(".app.new"):  # the staged→bundle swap blows up
            raise OSError(errno.EIO, "boom")
        return real_replace(src, dst, *a, **k)

    monkeypatch.setattr(u.os, "replace", fake_replace)
    with pytest.raises(OSError):
        up._apply_macos(staged, target, lambda *a, **k: None)
    assert (bundle / "Contents" / "MacOS" / "Waves").read_text() == "LIVE"


def test_apply_windows_helper_backs_up_and_restores(tmp_path, monkeypatch):
    """The detached .bat that swaps a single .exe must back the old exe up and, on
    a failed move, restore it and relaunch; never leave `target` missing."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    install = tmp_path / "app"
    install.mkdir()
    target = install / "Waves.exe"
    staged = tmp_path / "staging" / "Waves.exe"
    staged.parent.mkdir(parents=True)
    staged.write_text("NEW")

    up._apply_windows(staged, target, lambda *a, **k: None)

    script = (up.staging_dir / "apply_update.bat").read_text()
    backup = target.with_suffix(target.suffix + ".old")
    assert f'move /Y "{target}" "{backup}"' in script  # back the live exe up
    assert f'move /Y "{backup}" "{target}"' in script  # restore it on failure
    assert "exit /b 1" in script


def test_apply_windows_tree_helper_backs_up_and_restores(tmp_path, monkeypatch):
    """The detached .bat that mirrors a whole .dist tree must rename the live
    install to .old first and restore it if robocopy reports a real failure."""
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.staging_dir.mkdir(parents=True)
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: None)
    install_root = tmp_path / "Waves"
    install_root.mkdir()
    target = install_root / "Waves.exe"
    new_tree = tmp_path / "staging" / "Waves.dist"
    new_tree.mkdir(parents=True)

    up._apply_windows_tree(new_tree, target, lambda *a, **k: None)

    script = (up.staging_dir / "apply_update.bat").read_text()
    backup = install_root.with_name(install_root.name + ".old")
    assert f'move "{install_root}" "{backup}"' in script  # back the install up
    assert f'move "{backup}" "{install_root}"' in script  # restore on failure
    assert "GEQ 8" in script  # only restore/abort on a real robocopy failure


# ---- version parse / compare ------------------------------------------------
@pytest.mark.parametrize(
    "tag,expected",
    [
        ("v1.2.3", (1, 2, 3)),
        ("1.2", (1, 2)),
        ("v2", (2,)),
        ("v1.2.0-beta.1", (1, 2, 0)),  # pre-release suffix ignored
        ("waves-3.4.5", (3, 4, 5)),
        ("nope", ()),
        ("", ()),
    ],
)
def test_parse_version(tag, expected):
    assert u._parse_version(tag) == expected


@pytest.mark.parametrize(
    "latest,current,expected",
    [
        ("v1.3.0", "1.2.9", True),
        ("v1.2.1", "1.2.0", True),
        ("v2.0.0", "1.9.9", True),
        ("v1.2.0", "1.2.0", False),  # equal
        ("1.2", "1.2.0", False),  # 1.2 == 1.2.0
        ("v0.9.0", "1.0.0", False),  # older
        ("", "1.0.0", False),  # unparseable latest → never "newer"
    ],
)
def test_is_newer(latest, current, expected):
    assert u._is_newer(latest, current) is expected


# ---- asset selection --------------------------------------------------------
_ASSETS = [
    {"name": "Waves-macos-arm64.dmg", "browser_download_url": "mac-arm"},
    {"name": "Waves-macos-arm64.dmg.sha256", "browser_download_url": "mac-arm-sha"},
    {"name": "Waves-macos-x64.dmg", "browser_download_url": "mac-x64"},
    {"name": "Waves-linux-x86_64.zip", "browser_download_url": "lin-x64"},
    {"name": "Waves-windows-x64.zip", "browser_download_url": "win-x64"},
    {"name": "checksums.txt", "browser_download_url": "sums"},
]


def test_select_macos_arm_with_sha():
    assert u._select_asset(_ASSETS, "macos", "arm64") == ("Waves-macos-arm64.dmg", "mac-arm", "mac-arm-sha")


def test_select_prefers_correct_arch():
    name, url, _ = u._select_asset(_ASSETS, "macos", "amd64")
    assert (name, url) == ("Waves-macos-x64.dmg", "mac-x64")


def test_select_windows_no_sidecar():
    assert u._select_asset(_ASSETS, "windows", "amd64") == ("Waves-windows-x64.zip", "win-x64", None)


def test_select_never_picks_wrong_arch():
    # Only an x86_64 linux build exists; an arm64 machine must get nothing
    # rather than an incompatible binary.
    assert u._select_asset(_ASSETS, "linux", "arm64") == ("", "", None)


def test_select_linux_amd64():
    name, url, _ = u._select_asset(_ASSETS, "linux", "amd64")
    assert (name, url) == ("Waves-linux-x86_64.zip", "lin-x64")


def test_select_arch_agnostic_fallback():
    assets = [{"name": "Waves-linux.tar.gz", "browser_download_url": "uni"}]
    assert u._select_asset(assets, "linux", "arm64")[0] == "Waves-linux.tar.gz"


def test_select_arch_agnostic_appimage_still_needs_the_flag():
    # Format follows install: even a release carrying ONLY an AppImage must not
    # be installed over a zip/dist-tree copy (and vice versa).
    assets = [{"name": "Waves-linux.AppImage", "browser_download_url": "uni"}]
    assert u._select_asset(assets, "linux", "arm64")[0] == ""
    assert u._select_asset(assets, "linux", "arm64", prefer_appimage=True)[0] == "Waves-linux.AppImage"


def test_select_shipped_macos_names():
    # Pin the REAL CI asset names: "intel" is an amd64 token, so the Intel zip
    # matches amd64 and is skipped on arm64; the apple-silicon zip carries no
    # arch token and is picked on arm64 via the arch-agnostic fallback.
    assets = [
        {"name": "waves_macos-intel.zip", "browser_download_url": "mac-intel"},
        {"name": "waves_macos-intel.zip.sha256", "browser_download_url": "mac-intel-sha"},
        {"name": "waves_macos-apple-silicon.zip", "browser_download_url": "mac-as"},
        {"name": "waves_macos-apple-silicon.zip.sha256", "browser_download_url": "mac-as-sha"},
        {"name": "waves_linux-x64.zip", "browser_download_url": "lin-x64"},
        {"name": "waves_windows-arm64.zip", "browser_download_url": "win-arm"},
    ]
    assert u._select_asset(assets, "macos", "arm64") == ("waves_macos-apple-silicon.zip", "mac-as", "mac-as-sha")
    assert u._select_asset(assets, "macos", "amd64") == ("waves_macos-intel.zip", "mac-intel", "mac-intel-sha")


def test_select_no_os_match():
    assert u._select_asset([{"name": "readme.txt", "browser_download_url": "x"}], "macos", "arm64") == ("", "", None)


def test_select_ignores_lone_sidecar():
    assets = [{"name": "Waves-macos-arm64.dmg.sha256", "browser_download_url": "s"}]
    assert u._select_asset(assets, "macos", "arm64") == ("", "", None)


# ---- status / configuration gates ------------------------------------------
def test_status_not_configured_when_repo_blank():
    up = AppUpdater("/tmp/x", "1.0.0", repo="")
    st = up.status()
    assert st["state"] == "not_configured" and not st["configured"] and not st["can_self_install"]


def test_status_source_when_configured_but_not_frozen(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: False)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "source" and st["configured"] and not st["can_self_install"]
    assert st["releases_url"] == "https://github.com/owner/Waves/releases"


def test_status_ready_when_frozen_and_configured(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "ready" and st["can_self_install"]


def test_update_available_no_network_when_unconfigured():
    up = AppUpdater("/tmp/x", "1.0.0", repo="")
    # Must NOT hit the network when there's no repo.
    up.latest = lambda *a, **k: (_ for _ in ()).throw(AssertionError("network!"))
    assert up.update_available() == (False, "1.0.0", "")


def test_update_available_compares_versions():
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(version="v1.1.0", asset="a", url="u")
    assert up.update_available() == (True, "1.0.0", "1.1.0")  # bare version, no tag "v"
    up.latest = lambda *a, **k: Release(version="v1.0.0", asset="a", url="u")
    assert up.update_available() == (False, "1.0.0", "1.0.0")


# ---- install gates ----------------------------------------------------------
def test_install_blocked_when_unconfigured():
    up = AppUpdater("/tmp/x", "1.0.0", repo="")
    with pytest.raises(UpdaterError, match="aren't configured"):
        up.install()


def test_install_blocked_from_source(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: False)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    with pytest.raises(UpdaterError, match="packaged builds"):
        up.install()


# ---- mocked download → verify → stage (signed manifest; real swap stubbed) --
_ASSET = "Waves.bin"


def _manifest(payload: bytes, asset: str = _ASSET, version: str = "v2.0.0") -> bytes:
    """A signed-manifest body: the CI ``# waves-version`` line (anti-rollback) plus a
    coreutils-style SHA256SUMS line pinning ``payload``'s digest to ``asset``."""
    line = f"{hashlib.sha256(payload).hexdigest()}  {asset}\n"
    return (f"# waves-version: {version}\n{line}").encode()


def _prep(monkeypatch, tmp_path, *, payload, manifest, signature, pubkey, asset=_ASSET):
    """Wire an AppUpdater whose download writes ``payload`` and whose signed
    SHA256SUMS manifest + signature + embedded public key are as given. The
    platform swap is stubbed so a passing case records the call without touching
    the live executable."""
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "UPDATE_PUBLIC_KEY", pubkey)
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(
        version="v2.0.0",
        asset=asset,
        url="http://x/" + asset,
        sha256sums_url="http://x/SHA256SUMS",
        sig_url="http://x/SHA256SUMS.sig",
    )

    def fake_download(self, sess, url, dest, progress_cb, abort):
        with open(dest, "wb") as fh:
            fh.write(payload)
        if progress_cb:
            progress_cb(100.0)

    monkeypatch.setattr(AppUpdater, "_download", fake_download, raising=True)
    monkeypatch.setattr(AppUpdater, "_fetch_manifest", lambda self, sess, url: manifest, raising=True)
    monkeypatch.setattr(AppUpdater, "_fetch_signature", lambda self, sess, url: signature, raising=True)
    applied = {}
    monkeypatch.setattr(
        AppUpdater, "_apply", lambda self, p, rel, log: applied.setdefault("path", p) or p, raising=True
    )
    return up, applied


def _staged(tmp_path):
    """Leftover download temp files (must be empty after any install attempt)."""
    return list((tmp_path / "updates").glob("*-" + _ASSET))


def test_install_happy_path(monkeypatch, tmp_path):
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    result = up.install(session=object())
    assert result["ok"] and result["version"] == "v2.0.0" and result["relaunch"] is True
    assert "path" in applied  # the swap was invoked with the verified payload
    assert not _staged(tmp_path)  # temp payload cleaned up


def test_install_checksum_mismatch_aborts(monkeypatch, tmp_path):
    # Signature is VALID over the manifest, but the downloaded bytes don't match the
    # hash the (authentic) manifest pins → integrity failure, abort.
    pub, priv = signing.keygen()
    manifest = _manifest(b"genuine")
    up, applied = _prep(
        monkeypatch,
        tmp_path,
        payload=b"tampered",
        manifest=manifest,
        signature=signing.sign(manifest, priv),
        pubkey=pub,
    )
    with pytest.raises(UpdaterError, match="Checksum mismatch"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_tampered_manifest_aborts(monkeypatch, tmp_path):
    # Sign the genuine manifest, then serve a manifest whose hash line was swapped:
    # the signature no longer covers these bytes → authenticity failure, abort.
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    genuine = _manifest(payload)
    sig = signing.sign(genuine, priv)
    forged = _manifest(b"attacker-payload")  # different hash, same asset name
    up, applied = _prep(monkeypatch, tmp_path, payload=payload, manifest=forged, signature=sig, pubkey=pub)
    with pytest.raises(UpdaterError, match="signature is invalid"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_bad_signature_aborts(monkeypatch, tmp_path):
    pub, _ = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature="not-a-valid-signature", pubkey=pub
    )
    with pytest.raises(UpdaterError, match="signature is invalid"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_wrong_key_aborts(monkeypatch, tmp_path):
    # Signed with key A but the binary embeds key B's public key → reject.
    _, priv_a = signing.keygen()
    pub_b, _ = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch,
        tmp_path,
        payload=payload,
        manifest=manifest,
        signature=signing.sign(manifest, priv_a),
        pubkey=pub_b,
    )
    with pytest.raises(UpdaterError, match="signature is invalid"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_missing_signature_aborts(monkeypatch, tmp_path):
    # Manifest present and hash correct, but the signature couldn't be fetched
    # (missing/404). Fail-closed: no signature → never install.
    pub, _priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=None, pubkey=pub)
    with pytest.raises(UpdaterError, match="could not fetch"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_unconfigured_key_aborts(monkeypatch, tmp_path):
    # An otherwise-valid signed update must still refuse when this build ships no
    # public key (the dormant UPDATE_PUBLIC_KEY="" default).
    _pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload)
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=""
    )
    with pytest.raises(UpdaterError, match="no update-signing key"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_downgrade_refused(monkeypatch, tmp_path):
    # Anti-rollback: a perfectly-signed manifest for an OLDER version than the one
    # installed (current is 1.0.0) must be refused, so a replayed old release can't
    # roll the user back to a build with known holes.
    pub, priv = signing.keygen()
    payload = b"old-waves-binary"
    manifest = _manifest(payload, version="v0.5.0")
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    with pytest.raises(UpdaterError, match="downgrade protection"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_missing_version_line_refused(monkeypatch, tmp_path):
    # A signed manifest lacking the version line is refused (fail-closed): the
    # downgrade protection must rest on an authenticated version being present.
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = f"{hashlib.sha256(payload).hexdigest()}  {_ASSET}\n".encode()  # no "# waves-version"
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    with pytest.raises(UpdaterError, match="no version line"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


def test_install_asset_not_in_manifest_aborts(monkeypatch, tmp_path):
    # Valid signature, but our asset isn't listed in the signed manifest → abort.
    pub, priv = signing.keygen()
    payload = b"new-waves-binary"
    manifest = _manifest(payload, asset="SomethingElse.zip")
    up, applied = _prep(
        monkeypatch, tmp_path, payload=payload, manifest=manifest, signature=signing.sign(manifest, priv), pubkey=pub
    )
    with pytest.raises(UpdaterError, match="not in the signed manifest"):
        up.install(session=object())
    assert "path" not in applied
    assert not _staged(tmp_path)


# ---- extraction: symlink-preserving + escape-rejecting ----------------------
def _zip_with(members):
    """members: list of (name, data, mode); mode's file-type bits choose file vs symlink."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data, mode in members:
            zi = zipfile.ZipInfo(name)
            zi.external_attr = mode << 16
            zf.writestr(zi, data)
    return buf.getvalue()


def test_safe_extractall_preserves_symlinks_and_exec_bit(tmp_path):
    # A macOS .app relies on framework symlinks (Versions/Current -> A); plain
    # zipfile.extractall would flatten them into broken files. The exec bit on the
    # main binary must also survive, or the swapped bundle won't launch.
    zp = tmp_path / "a.zip"
    zp.write_bytes(
        _zip_with(
            [
                ("waves.app/Contents/MacOS/Waves", b"BINARY", 0o100755),  # regular, rwxr-xr-x
                ("waves.app/Contents/Frameworks/Foo.framework/Versions/Current", b"A", 0o120755),  # symlink
            ]
        )
    )
    out = tmp_path / "out"
    with zipfile.ZipFile(zp) as zf:
        AppUpdater._safe_extractall(zf, out)
    exe = out / "waves.app/Contents/MacOS/Waves"
    link = out / "waves.app/Contents/Frameworks/Foo.framework/Versions/Current"
    assert exe.read_bytes() == b"BINARY" and os.access(exe, os.X_OK)
    assert link.is_symlink() and os.readlink(link) == "A"


def test_safe_extractall_rejects_path_traversal(tmp_path):
    zp = tmp_path / "e.zip"
    zp.write_bytes(_zip_with([("../escape.txt", b"x", 0o100644)]))
    out = tmp_path / "out"
    with zipfile.ZipFile(zp) as zf, pytest.raises(UpdaterError, match="unsafe archive member"):
        AppUpdater._safe_extractall(zf, out)


def test_safe_extractall_rejects_escaping_symlink(tmp_path):
    zp = tmp_path / "s.zip"
    zp.write_bytes(_zip_with([("evil", b"/etc/passwd", 0o120755)]))  # absolute symlink target
    out = tmp_path / "out"
    with zipfile.ZipFile(zp) as zf, pytest.raises(UpdaterError, match="unsafe symlink"):
        AppUpdater._safe_extractall(zf, out)


# ---- apply: Linux/Windows swap the whole standalone .dist tree --------------
def test_apply_unix_tree_swaps_whole_directory(tmp_path):
    # Nuitka --standalone ships a multi-file tree; the new binary must run against
    # its OWN bundled libs, so the entire install dir is replaced, not just the exe.
    install_root = tmp_path / "waves.dist"
    install_root.mkdir()
    (install_root / "Waves").write_bytes(b"OLD-EXE")
    (install_root / "libQt6Core.so.6").write_bytes(b"OLD-LIB")
    target = install_root / "Waves"

    new_tree = tmp_path / "staged" / "waves.dist"
    new_tree.mkdir(parents=True)
    (new_tree / "Waves").write_bytes(b"NEW-EXE")
    (new_tree / "libQt6Core.so.6").write_bytes(b"NEW-LIB")

    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key = "linux"
    assert up._apply_unix_tree(new_tree, target, lambda *a: None) == target
    assert (install_root / "Waves").read_bytes() == b"NEW-EXE"
    assert (install_root / "libQt6Core.so.6").read_bytes() == b"NEW-LIB"  # the lib was swapped too
    assert os.access(install_root / "Waves", os.X_OK)


# ---- managed install channels (package-manager-owned copies) -----------------
@pytest.fixture
def _no_channel_env(monkeypatch):
    """Neutralize every channel signal so each test enables exactly one."""
    for var in ("SNAP", "FLATPAK_ID", "APPIMAGE"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(u, "_current_exe", lambda: __import__("pathlib").Path("/Applications/waves.app/x"))
    monkeypatch.setattr(u.os.path, "exists", lambda p: False if p == "/.flatpak-info" else os.path.exists(p))


def _point_config_at(monkeypatch, tmp_path):
    import tidaler.helper.path as path_helper

    monkeypatch.setattr(path_helper, "path_config_base", lambda: str(tmp_path))


def test_managed_channel_empty_by_default(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    assert u.managed_channel() == ""


def test_managed_channel_from_container_env(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    monkeypatch.setenv("SNAP", "/snap/waves/1")
    assert u.managed_channel() == "snap"
    monkeypatch.delenv("SNAP")
    monkeypatch.setenv("FLATPAK_ID", "app.waves.Waves")
    assert u.managed_channel() == "flatpak"


def test_appimage_is_not_a_managed_channel(_no_channel_env, monkeypatch, tmp_path):
    # An AppImage self-updates (the updater swaps the $APPIMAGE file), so it
    # must NOT read as managed, which would down-rank it to notify-only.
    _point_config_at(monkeypatch, tmp_path)
    monkeypatch.setenv("APPIMAGE", "/home/u/Waves.AppImage")
    assert u.managed_channel() == ""


def test_managed_channel_from_scoop_path(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    monkeypatch.setattr(
        u, "_current_exe", lambda: __import__("pathlib").Path("C:/Users/u/scoop/apps/waves/current/Waves.exe")
    )
    assert u.managed_channel() == "scoop"


def test_managed_channel_from_sentinel_file(_no_channel_env, monkeypatch, tmp_path):
    _point_config_at(monkeypatch, tmp_path)
    (tmp_path / "install_channel").write_text("homebrew-cask\n")
    assert u.managed_channel() == "homebrew-cask"


def test_sentinel_content_is_sanitized(_no_channel_env, monkeypatch, tmp_path):
    # The sentinel is a plain file anyone could edit; it must never inject
    # markup/whitespace into UI strings. One lowercase token, bounded length.
    _point_config_at(monkeypatch, tmp_path)
    (tmp_path / "install_channel").write_text("  HomeBrew-Cask extra words <b>x</b>\n")
    assert u.managed_channel() == "homebrew-cask"
    (tmp_path / "install_channel").write_text("   \n")
    assert u.managed_channel() == ""


def test_status_managed_when_channel_owns_install(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "managed"
    assert st["can_self_install"] is False
    assert st["channel"] == "homebrew-cask"
    assert st["channel_label"] == "Homebrew"
    assert st["update_hint"] == "brew upgrade --cask waves"


def test_status_ready_reports_no_channel(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "ready" and st["can_self_install"]
    assert st["channel"] == "" and st["channel_label"] == "" and st["update_hint"] == ""


def test_source_state_wins_over_managed(monkeypatch):
    # A dev running from source on a machine that also has a brew install:
    # "source" is the truer state (self-install impossible either way).
    monkeypatch.setattr(u, "is_frozen", lambda: False)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    assert up.status()["state"] == "source"


def test_install_blocked_on_managed_channel_without_manager_binary(monkeypatch):
    # Homebrew-managed but brew itself is gone: nothing runnable, so the old
    # refusal with the manual hint is what the user gets.
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    with pytest.raises(UpdaterError, match=r"managed by Homebrew.*brew upgrade --cask waves"):
        up.install()


def test_install_blocked_on_unknown_channel_without_hint(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "nixpkgs")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    with pytest.raises(UpdaterError, match=r"managed by nixpkgs"):
        up.install()


# ---- managed upgrade runner (the manager's own command does the update) ------
class _FakeProc:
    """subprocess.Popen stand-in that streams canned output lines."""

    def __init__(self, lines, code=0, hang_after=None, on_terminate=None):
        import io

        self._lines = lines
        self._code = code
        self.stdout = io.StringIO("".join(line + "\n" for line in lines))
        self.terminated = False
        self._on_terminate = on_terminate

    def wait(self):
        return self._code

    def poll(self):
        return self._code

    def terminate(self):
        self.terminated = True
        if self._on_terminate:
            self._on_terminate()


def _managed_up(monkeypatch, proc, latest=None):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")
    monkeypatch.setattr(u.subprocess, "Popen", lambda *a, **k: proc)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = (lambda *a, **k: latest) if latest is not None else (lambda *a, **k: None)
    return up


def test_managed_upgrade_runs_brew_and_reports_done(monkeypatch):
    argv_seen = {}
    proc = _FakeProc(["==> Downloading waves", "==> Upgrading waves 1.0.0 -> 1.1.0", "🍺  waves was upgraded"])
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")

    def fake_popen(argv, **kw):
        argv_seen["argv"] = argv
        return proc

    monkeypatch.setattr(u.subprocess, "Popen", fake_popen)
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    up.latest = lambda *a, **k: Release(version="v1.1.0", asset="a", url="u")

    pcts, logs = [], []
    result = up.install(progress_cb=pcts.append, log_cb=logs.append)

    assert argv_seen["argv"] == ["/opt/homebrew/bin/brew", "upgrade", "--cask", "iamprivacy/waves/waves"]
    assert result["ok"] is True and result["version"] == "v1.1.0" and result["relaunch"] is True
    assert pcts[-1] == 100.0
    assert any("Upgrading" in m for m in logs), "the manager's output reaches the UI"


def test_managed_upgrade_failure_surfaces_output_tail(monkeypatch):
    up = _managed_up(monkeypatch, _FakeProc(["Error: some cask problem"], code=1))
    with pytest.raises(UpdaterError, match=r"Homebrew reported an error[\s\S]*some cask problem"):
        up.install()


def test_managed_upgrade_stale_tap_is_a_clear_error(monkeypatch):
    # brew exits 0 but did nothing: its tap clone hasn't seen the release yet.
    up = _managed_up(monkeypatch, _FakeProc(["Warning: waves already installed, it's already up-to-date"], code=0))
    with pytest.raises(UpdaterError, match=r"does not see the new version"):
        up.install()


def test_managed_upgrade_abort_cancels(monkeypatch):
    from threading import Event as _Event

    abort = _Event()
    abort.set()  # cancelled before/while output streams
    proc = _FakeProc(["==> Downloading waves"], code=1)
    up = _managed_up(monkeypatch, proc)
    with pytest.raises(u.UpdateCancelled):
        up.install(abort=abort)


def test_status_can_managed_install_tracks_brew_presence(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "homebrew-cask")
    monkeypatch.setattr(u, "_find_brew", lambda: "/opt/homebrew/bin/brew")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    st = up.status()
    assert st["state"] == "managed" and st["can_managed_install"] is True and st["can_self_install"] is False
    monkeypatch.setattr(u, "_find_brew", lambda: "")
    assert up.status()["can_managed_install"] is False


def test_status_snap_has_no_managed_install(monkeypatch):
    monkeypatch.setattr(u, "is_frozen", lambda: True)
    monkeypatch.setattr(u, "managed_channel", lambda: "snap")
    up = AppUpdater("/tmp/x", "1.0.0", repo="owner/Waves")
    assert up.status()["can_managed_install"] is False


# ---- AppImage: format follows install -----------------------------------------
def _assets(*names):
    return [{"name": n, "browser_download_url": "http://x/" + n} for n in names]


def test_select_asset_zip_install_never_gets_an_appimage():
    # ".AppImage" sorts before ".zip" alphabetically; without hard partitioning
    # every zip user would silently be switched to an AppImage payload.
    name, url, _ = u._select_asset(_assets("waves_linux-x64.AppImage", "waves_linux-x64.zip"), "linux", "amd64")
    assert name == "waves_linux-x64.zip"


def test_select_asset_appimage_install_gets_only_appimage():
    name, url, sha = u._select_asset(
        _assets("waves_linux-x64.AppImage", "waves_linux-x64.AppImage.sha256", "waves_linux-x64.zip"),
        "linux",
        "amd64",
        prefer_appimage=True,
    )
    assert name == "waves_linux-x64.AppImage"
    assert sha == "http://x/waves_linux-x64.AppImage.sha256"


def test_select_asset_appimage_missing_yields_nothing():
    # An old release without AppImage assets: better no update than a zip tree
    # smeared over a single file.
    name, url, _ = u._select_asset(_assets("waves_linux-x64.zip"), "linux", "amd64", prefer_appimage=True)
    assert name == "" and url == ""


def test_apply_targets_the_appimage_file(monkeypatch, tmp_path):
    appimage_path = tmp_path / "Waves.AppImage"
    appimage_path.write_bytes(b"OLD")
    monkeypatch.setenv("APPIMAGE", str(appimage_path))
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key = "linux"
    payload = tmp_path / "dl.bin"
    payload.write_bytes(b"NEW-APPIMAGE")
    rel = Release(version="v2.0.0", asset="waves_linux-x64.AppImage", url="http://x")
    applied = up._apply(payload, rel, lambda *a: None)
    assert applied == appimage_path
    assert appimage_path.read_bytes() == b"NEW-APPIMAGE"
    assert os.access(appimage_path, os.X_OK), "the swapped AppImage must stay executable"


def test_apply_without_appimage_env_untouched(monkeypatch, tmp_path):
    # Sanity: no $APPIMAGE → the normal exe-relative path is used (covered in
    # depth by the tree/single-file apply tests above).
    monkeypatch.delenv("APPIMAGE", raising=False)
    assert u._running_appimage() == ""


def test_managed_channel_from_app_dir_sentinel(_no_channel_env, monkeypatch, tmp_path):
    # System packages (AUR) plant the sentinel next to the binary, where a
    # root-owned install CAN write and per-user config CANNOT.
    _point_config_at(monkeypatch, tmp_path / "config-without-sentinel")
    exe_dir = tmp_path / "opt" / "waves"
    exe_dir.mkdir(parents=True)
    (exe_dir / "install_channel").write_text("aur\n")
    monkeypatch.setattr(u, "_current_exe", lambda: exe_dir / "Waves")
    assert u.managed_channel() == "aur"
