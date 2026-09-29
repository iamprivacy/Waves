"""The verification path a cautious user is given: the README section, the
release-body footer and the key they both print must be the key the binary
verifies with, and the commands they give must actually verify."""

import base64
import hashlib
import pathlib
import re
import shutil
import subprocess
import sys

import pytest
from Crypto.PublicKey import ECC

from waves.waves_ui import signing
from waves.waves_ui.signing import UPDATE_PUBLIC_KEY, public_key_pem, sign

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _openssl3() -> bool:
    """Only OpenSSL 3 has ``pkeyutl -rawin``; macOS's built-in openssl is LibreSSL."""
    if sys.platform.startswith("win") or not shutil.which("bash") or not shutil.which("openssl"):
        return False
    out = subprocess.run(["openssl", "version"], capture_output=True, text=True, check=False).stdout
    return out.startswith("OpenSSL 3")


def test_the_pem_is_the_embedded_key():
    key = ECC.import_key(public_key_pem())
    assert key.public_key().export_key(format="raw") == base64.b64decode(UPDATE_PUBLIC_KEY)


def test_a_wrong_length_key_is_refused():
    with pytest.raises(ValueError):
        public_key_pem(base64.b64encode(b"short").decode())


def test_the_readme_prints_the_embedded_key_and_the_commands():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Verify a download" in readme
    assert public_key_pem().rstrip("\n") in readme
    for needle in (
        "openssl pkeyutl -verify -pubin -inkey waves-release.pem -rawin -in SHA256SUMS -sigfile SHA256SUMS.sig.bin",
        "base64 --decode < SHA256SUMS.sig > SHA256SUMS.sig.bin",
        "tr -d '\\r' | shasum -a 256 --ignore-missing -c -",
        "Get-FileHash",
    ):
        assert needle in readme, needle


def test_the_release_footer_puts_the_hash_next_to_each_download():
    from tools.release_verify_notes import verify_notes

    out = verify_notes("# waves-version: v9.9.9\naa11  waves_b.zip\nbb22  waves_a.zip\n")
    assert out.startswith("### 🔐 Verify\n")
    assert out.index("`waves_a.zip` | `bb22`") < out.index("`waves_b.zip` | `aa11`")
    assert public_key_pem().rstrip("\n") in out
    assert "openssl pkeyutl -verify" in out
    assert "#verify-a-download" in out
    assert "\u2014" not in out


def test_the_workflow_appends_the_footer_after_signing():
    wf = (ROOT / ".github" / "workflows" / "release-or-test-build.yml").read_text(encoding="utf-8")
    assert "tools/release_verify_notes.py SHA256SUMS" in wf
    assert wf.index("tools/sign_manifest.py") < wf.index("tools/release_verify_notes.py")
    assert wf.index("tools/release_verify_notes.py") < wf.index("name: Set release notes body")


@pytest.mark.skipif(not _openssl3(), reason="needs bash and OpenSSL 3 on PATH")
def test_the_readme_commands_verify_a_signed_manifest_with_openssl(tmp_path):
    """Run the README's exact commands against a manifest signed with a throwaway
    key: they must accept the real manifest and reject a tampered one."""
    key = ECC.generate(curve="Ed25519")
    pub_b64 = base64.b64encode(key.public_key().export_key(format="raw")).decode("ascii")
    priv_pem = key.export_key(format="PEM")
    manifest = tmp_path / "SHA256SUMS"
    asset = tmp_path / "waves_x.zip"
    asset.write_bytes(b"build")
    # The Windows runner's lines end in CRLF (the live v0.1.31 manifest has
    # them); the README's check must still match such an asset.
    digest = hashlib.sha256(b"build").hexdigest()
    manifest.write_bytes(f"# waves-version: v9.9.9\n{digest}  waves_x.zip\r\n{digest}  waves_win.zip\r\n".encode())
    (tmp_path / "SHA256SUMS.sig").write_text(sign(manifest.read_bytes(), priv_pem))
    (tmp_path / "waves-release.pem").write_text(public_key_pem(pub_b64))

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    block = re.search(r"```bash\n(printf[^`]*?)```", readme[readme.index("## Verify a download") :]).group(1)
    commands = [line for line in block.splitlines()[1:] if line.strip()]  # drop the printf that writes the real key
    assert commands, "the README verify block lost its commands"

    def run(cmd: str) -> int:
        return subprocess.run(["bash", "-c", cmd], cwd=tmp_path, capture_output=True, check=False).returncode

    for cmd in commands:
        assert run(cmd) == 0, cmd
    checked = subprocess.run(
        ["bash", "-c", commands[2]], cwd=tmp_path, capture_output=True, text=True, check=False
    ).stdout
    assert "waves_x.zip: OK" in checked, checked
    manifest.write_bytes(manifest.read_bytes() + b"x")
    assert run(commands[1]) != 0, "a tampered manifest must fail the signature check"

    # And the app's own verifier agrees with openssl on the untouched bytes.
    manifest.write_bytes(manifest.read_bytes()[:-1])
    assert signing.verify(manifest.read_bytes(), (tmp_path / "SHA256SUMS.sig").read_text(), pub_b64)
