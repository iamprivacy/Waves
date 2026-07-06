"""Audit regression tests for the self-updater's asset selection + extraction.

Covers the audited bug where ``_INSTALL_EXTS`` advertised formats
(``.dmg``/``.tar.gz``/``.7z``) that ``_extract_payload`` couldn't unpack: those
formats were ranked equal-priority to ``.zip`` and won on the alphabetical
tie-break, so a ``.dmg``/``.tar.gz`` was preferred over a usable ``.zip`` and
then copied raw and executed (a bricked install).

The fix keeps selection and extraction consistent: only formats the updater can
actually unpack+install are advertised (``.zip``, ``.exe``, ``.appimage``,
``.tar.gz``/``.tgz``); ``.tar.gz`` is now extracted with the same path-traversal
guards as ``.zip``; and unhandled formats (``.dmg``/``.7z``) are never ranked
installable, so a ``.zip`` beside them wins.

All hermetic: no network, no real downloads, archives built in ``tmp_path``.
"""

import io
import os
import tarfile

import pytest

from tidaler.waves_ui import updater as u
from tidaler.waves_ui.updater import AppUpdater, UpdaterError


# ---- helpers ----------------------------------------------------------------
def _tar_gz(path, members):
    """Write a .tar.gz at ``path`` from ``(name, data_or_linkname, kind, mode)``.

    ``kind`` ∈ {"file", "sym", "lnk", "dir"}. For "sym"/"lnk" the second element
    is the link target; for "dir" it is ignored.
    """
    with tarfile.open(path, "w:gz") as tf:
        for name, payload, kind, mode in members:
            if kind == "dir":
                ti = tarfile.TarInfo(name)
                ti.type = tarfile.DIRTYPE
                ti.mode = mode
                tf.addfile(ti)
            elif kind in ("sym", "lnk"):
                ti = tarfile.TarInfo(name)
                ti.type = tarfile.SYMTYPE if kind == "sym" else tarfile.LNKTYPE
                ti.linkname = payload
                ti.mode = mode
                tf.addfile(ti)
            else:
                data = payload
                ti = tarfile.TarInfo(name)
                ti.size = len(data)
                ti.mode = mode
                tf.addfile(ti, io.BytesIO(data))


# ---- selection: unhandled formats never win over a handled one --------------
def test_dmg_no_longer_preferred_over_zip(tmp_path):
    # The audited bug: .dmg was ranked installable and beat .zip alphabetically,
    # so the updater picked an archive it couldn't unpack. A .zip in the same
    # release must now win.
    assets = [
        {"name": "Waves-macos-arm64.dmg", "browser_download_url": "dmg"},
        {"name": "Waves-macos-arm64.zip", "browser_download_url": "zip"},
    ]
    name, url, _ = u._select_asset(assets, "macos", "arm64")
    assert (name, url) == ("Waves-macos-arm64.zip", "zip")


def test_sevenz_no_longer_preferred_over_zip(tmp_path):
    assets = [
        {"name": "Waves-windows-x64.7z", "browser_download_url": "7z"},
        {"name": "Waves-windows-x64.zip", "browser_download_url": "zip"},
    ]
    name, url, _ = u._select_asset(assets, "windows", "amd64")
    assert (name, url) == ("Waves-windows-x64.zip", "zip")


def test_dmg_and_7z_dropped_from_install_exts():
    # Guard the extraction/selection contract: only formats _extract_payload can
    # handle are advertised as installable.
    assert ".dmg" not in u._INSTALL_EXTS
    assert ".7z" not in u._INSTALL_EXTS
    assert set(u._INSTALL_EXTS) == {".zip", ".exe", ".appimage", ".tar.gz", ".tgz"}


def test_tar_gz_is_installable_and_selected(tmp_path):
    assets = [{"name": "Waves-linux-x86_64.tar.gz", "browser_download_url": "tgz"}]
    name, url, _ = u._select_asset(assets, "linux", "amd64")
    assert (name, url) == ("Waves-linux-x86_64.tar.gz", "tgz")


# ---- extraction: tar.gz payload extracts to the expected binary -------------
def test_extract_tar_gz_payload_finds_binary(tmp_path, monkeypatch):
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.sys, "executable", str(tmp_path / "Waves"), raising=False)
    up.os_key, up.arch = "linux", "amd64"
    archive = tmp_path / "Waves-linux-x86_64.tar.gz"
    _tar_gz(
        archive,
        [
            ("Waves.dist", None, "dir", 0o755),
            ("Waves.dist/Waves", b"NEWBINARY", "file", 0o755),
            ("Waves.dist/lib.so", b"newlib", "file", 0o644),
        ],
    )
    staged = up._extract_payload(archive, "Waves-linux-x86_64.tar.gz", lambda *a, **k: None)
    assert staged.name == "Waves"
    assert staged.read_bytes() == b"NEWBINARY"
    assert os.access(staged, os.X_OK)  # exec bit carried over
    assert (staged.parent / "lib.so").read_bytes() == b"newlib"


def test_extract_tar_gz_preserves_symlink(tmp_path, monkeypatch):
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key, up.arch = "macos", "arm64"
    archive = tmp_path / "Waves-macos.tar.gz"
    _tar_gz(
        archive,
        [
            ("Waves.app", None, "dir", 0o755),
            ("Waves.app/Contents", None, "dir", 0o755),
            ("Waves.app/Contents/MacOS", None, "dir", 0o755),
            ("Waves.app/Contents/MacOS/Waves", b"BIN", "file", 0o755),
            ("Waves.app/Contents/Versions/Current", "A", "sym", 0o755),
        ],
    )
    out = up.staging_dir / "staged"
    with tarfile.open(archive, "r:gz") as tf:
        AppUpdater._safe_extractall_tar(tf, out)
    link = out / "Waves.app/Contents/Versions/Current"
    assert link.is_symlink() and os.readlink(link) == "A"


# ---- extraction: malicious tar entries are rejected -------------------------
def test_tar_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.tar.gz"
    _tar_gz(archive, [("../escape.txt", b"x", "file", 0o644)])
    out = tmp_path / "out"
    with tarfile.open(archive, "r:gz") as tf, pytest.raises(UpdaterError, match="unsafe archive member"):
        AppUpdater._safe_extractall_tar(tf, out)


def test_tar_rejects_absolute_member(tmp_path):
    archive = tmp_path / "abs.tar.gz"
    _tar_gz(archive, [("/etc/cron.d/pwn", b"x", "file", 0o644)])
    out = tmp_path / "out"
    with tarfile.open(archive, "r:gz") as tf, pytest.raises(UpdaterError, match="unsafe archive member"):
        AppUpdater._safe_extractall_tar(tf, out)


def test_tar_rejects_escaping_symlink(tmp_path):
    archive = tmp_path / "sym.tar.gz"
    _tar_gz(archive, [("evil", "../../etc/passwd", "sym", 0o777)])
    out = tmp_path / "out"
    with tarfile.open(archive, "r:gz") as tf, pytest.raises(UpdaterError, match="unsafe link"):
        AppUpdater._safe_extractall_tar(tf, out)


def test_tar_rejects_absolute_symlink(tmp_path):
    archive = tmp_path / "asym.tar.gz"
    _tar_gz(archive, [("evil", "/etc/passwd", "sym", 0o777)])
    out = tmp_path / "out"
    with tarfile.open(archive, "r:gz") as tf, pytest.raises(UpdaterError, match="unsafe link"):
        AppUpdater._safe_extractall_tar(tf, out)


# ---- extraction: raw single-file binary still works -------------------------
def test_extract_raw_binary_still_used_as_is(tmp_path, monkeypatch):
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key, up.arch = "linux", "amd64"
    payload = tmp_path / "dl-Waves"
    payload.write_bytes(b"RAWBINARY")
    staged = up._extract_payload(payload, "Waves", lambda *a, **k: None)
    assert staged.name == "Waves"
    assert staged.read_bytes() == b"RAWBINARY"


def test_extract_appimage_raw_used_as_is(tmp_path):
    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    up.os_key, up.arch = "linux", "amd64"
    payload = tmp_path / "dl.AppImage"
    payload.write_bytes(b"APPIMAGE")
    staged = up._extract_payload(payload, "Waves-linux.AppImage", lambda *a, **k: None)
    assert staged.read_bytes() == b"APPIMAGE"


# ---- extraction: zip is still handled ---------------------------------------
def test_extract_zip_still_handled(tmp_path, monkeypatch):
    import zipfile

    up = AppUpdater(tmp_path, "1.0.0", repo="owner/Waves")
    monkeypatch.setattr(u.sys, "executable", str(tmp_path / "Waves"), raising=False)
    up.os_key, up.arch = "linux", "amd64"
    archive = tmp_path / "Waves-linux.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zi = zipfile.ZipInfo("Waves.dist/Waves")
        zi.external_attr = 0o100755 << 16
        zf.writestr(zi, b"ZIPBIN")
    staged = up._extract_payload(archive, "Waves-linux.zip", lambda *a, **k: None)
    assert staged.name == "Waves"
    assert staged.read_bytes() == b"ZIPBIN"
