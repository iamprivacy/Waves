"""End-to-end integration test for the self-updater: no GitHub, no publishing.

Unlike ``test_updater.py`` (which mocks the HTTP session and stubs the platform
swap), this drives the *entire* real pipeline against a throwaway local web
server: a real Ed25519 keypair signs a real ``SHA256SUMS`` manifest over a real
zipped ``.dist`` tree, and the updater actually downloads it over loopback HTTP,
verifies the signature and checksum, unzips it, and swaps it into a temp install
directory. It proves everything a real self-update does except relaunching the
live process (which by nature needs a packaged binary).

This is the "test it without shipping" harness: point the same machinery at a
throwaway *public* GitHub repo instead of ``127.0.0.1`` and it becomes a full
real-world round-trip, including the relaunch.
"""

import functools
import hashlib
import http.server
import threading
import zipfile

import pytest
import requests

from tidaler.waves_ui import signing
from tidaler.waves_ui import updater as u
from tidaler.waves_ui.updater import AppUpdater, Release


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):  # keep test output clean
        pass


def _serve(directory):
    """Start a loopback HTTP server on an ephemeral port; return (baseurl, stop)."""
    handler = functools.partial(_QuietHandler, directory=str(directory))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    host, port = httpd.server_address
    return f"http://{host}:{port}", httpd.shutdown


def test_full_selfupdate_round_trip_over_local_http(tmp_path, monkeypatch):
    # --- 1. A real signing keypair (the same one CI/keygen would produce). -----
    pub_b64, priv_pem = signing.keygen()
    monkeypatch.setattr(u, "UPDATE_PUBLIC_KEY", pub_b64)  # what the binary embeds

    # --- 2. Build a realistic new build: a .dist tree (exe + a bundled lib). ---
    web = tmp_path / "web"
    web.mkdir()
    asset = "waves_linux-x64.zip"
    zip_path = web / asset
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("waves.dist/waves", "NEW-BINARY-v9.9.9")
        zf.writestr("waves.dist/libpython.so", "NEW-BUNDLED-LIB")

    # --- 3. Sign a SHA256SUMS manifest over that asset (producer side). --------
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    manifest = f"# waves-version: v9.9.9\n{digest}  {asset}\n".encode()
    (web / "SHA256SUMS").write_bytes(manifest)
    (web / "SHA256SUMS.sig").write_text(signing.sign(manifest, priv_pem))

    base, stop = _serve(web)
    try:
        # --- 4. A frozen, configured updater whose "install" is a temp dir. ----
        install_root = tmp_path / "app"
        install_root.mkdir()
        target = install_root / "waves"
        target.write_text("OLD-BINARY-v0.1.0")
        (install_root / "libpython.so").write_text("OLD-BUNDLED-LIB")

        monkeypatch.setattr(u, "is_frozen", lambda: True)  # allow real self-install
        # what _apply replaces; argv[0] is what _current_exe prefers (the real
        # binary; sys.executable is a phantom python.exe under Nuitka standalone)
        monkeypatch.setattr(u.sys, "argv", [str(target)])
        monkeypatch.setattr(u.sys, "executable", str(target))

        up = AppUpdater(tmp_path / "data", "0.1.0", repo="owner/local-test")
        up.os_key, up.arch = "linux", "amd64"  # exercise the unix .dist-tree swap

        release = Release(
            version="v9.9.9",
            asset=asset,
            url=f"{base}/{asset}",
            sha256sums_url=f"{base}/SHA256SUMS",
            sig_url=f"{base}/SHA256SUMS.sig",
        )

        # --- 5. Run the REAL pipeline: download -> verify sig -> verify hash
        #        -> unzip -> swap the tree in. Over real (loopback) HTTP. -------
        result = up.install(release=release, session=requests.Session())
    finally:
        stop()

    # --- 6. The new build is now installed and the result reports success. -----
    assert result["ok"] is True
    assert result["version"] == "v9.9.9"
    assert result["relaunch"] is True
    assert (install_root / "waves").read_text() == "NEW-BINARY-v9.9.9"
    assert (install_root / "libpython.so").read_text() == "NEW-BUNDLED-LIB"
    # No backup/staging litter left behind after a clean swap.
    assert not (tmp_path / "app.old").exists()
    assert not (tmp_path / "app.new").exists()


def test_full_selfupdate_rejects_tampered_asset_over_local_http(tmp_path, monkeypatch):
    """Same real pipeline, but the served binary is swapped for a malicious one
    *after* the manifest was signed; the updater must refuse it and leave the
    installed build untouched (this is the security guarantee, exercised for real)."""
    pub_b64, priv_pem = signing.keygen()
    monkeypatch.setattr(u, "UPDATE_PUBLIC_KEY", pub_b64)

    web = tmp_path / "web"
    web.mkdir()
    asset = "waves_linux-x64.zip"
    zip_path = web / asset
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("waves.dist/waves", "LEGIT-BINARY")

    # Sign the manifest over the LEGIT asset...
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    manifest = f"# waves-version: v9.9.9\n{digest}  {asset}\n".encode()
    (web / "SHA256SUMS").write_bytes(manifest)
    (web / "SHA256SUMS.sig").write_text(signing.sign(manifest, priv_pem))
    # ...then a MITM replaces the asset bytes (hash no longer matches the manifest).
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("waves.dist/waves", "MALICIOUS-PAYLOAD")

    base, stop = _serve(web)
    try:
        install_root = tmp_path / "app"
        install_root.mkdir()
        target = install_root / "waves"
        target.write_text("OLD-BINARY-v0.1.0")

        monkeypatch.setattr(u, "is_frozen", lambda: True)
        # what _apply replaces; argv[0] is what _current_exe prefers (the real
        # binary; sys.executable is a phantom python.exe under Nuitka standalone)
        monkeypatch.setattr(u.sys, "argv", [str(target)])
        monkeypatch.setattr(u.sys, "executable", str(target))
        up = AppUpdater(tmp_path / "data", "0.1.0", repo="owner/local-test")
        up.os_key, up.arch = "linux", "amd64"
        release = Release(
            version="v9.9.9",
            asset=asset,
            url=f"{base}/{asset}",
            sha256sums_url=f"{base}/SHA256SUMS",
            sig_url=f"{base}/SHA256SUMS.sig",
        )
        with pytest.raises(u.UpdaterError, match=r"[Cc]hecksum"):
            up.install(release=release, session=requests.Session())
    finally:
        stop()

    # The live install was never touched by the rejected update.
    assert (install_root / "waves").read_text() == "OLD-BINARY-v0.1.0"
