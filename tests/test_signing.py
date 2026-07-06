"""Unit tests for the release-signing primitives (pure crypto, no network/Qt).

Pins the fail-closed contract of :func:`tidaler.waves_ui.signing.verify`: it
returns ``True`` only for a genuine Ed25519 signature from the configured key,
and ``False`` for every tamper / misconfiguration, never raising.
"""

import base64

import pytest

from tidaler.waves_ui import signing


def test_keygen_roundtrip():
    pub, priv = signing.keygen()
    assert len(base64.b64decode(pub)) == 32  # raw Ed25519 public key
    assert priv.startswith("-----BEGIN")  # PKCS#8 PEM
    msg = b"abc123  Waves-macos-arm64.zip\n"
    assert signing.verify(msg, signing.sign(msg, priv), pub) is True


def test_verify_rejects_tampered_message():
    pub, priv = signing.keygen()
    msg = b"genuine manifest bytes\n"
    sig = signing.sign(msg, priv)
    assert signing.verify(msg + b"x", sig, pub) is False
    assert signing.verify(msg, sig, pub) is True  # control


def test_verify_rejects_wrong_key():
    _pub_a, priv_a = signing.keygen()
    pub_b, _ = signing.keygen()
    msg = b"manifest\n"
    assert signing.verify(msg, signing.sign(msg, priv_a), pub_b) is False


@pytest.mark.parametrize("bad_key", ["", "   ", "not-base64!!", base64.b64encode(b"too-short").decode()])
def test_verify_rejects_bad_public_key(bad_key):
    _pub, priv = signing.keygen()
    msg = b"manifest\n"
    sig = signing.sign(msg, priv)
    assert signing.verify(msg, sig, bad_key) is False


@pytest.mark.parametrize("bad_sig", ["", "not-base64!!", base64.b64encode(b"short").decode()])
def test_verify_rejects_bad_signature(bad_sig):
    pub, _ = signing.keygen()
    assert signing.verify(b"manifest\n", bad_sig, pub) is False


def test_shipped_key_is_configured_and_rejects_foreign_signatures():
    # Go-live: the embedded key must be a real 32-byte Ed25519 public key, and a
    # manifest signed by anyone else's key must not verify against it.
    raw = base64.b64decode(signing.UPDATE_PUBLIC_KEY)
    assert len(raw) == 32
    _, priv = signing.keygen()
    msg = b"manifest\n"
    assert signing.verify(msg, signing.sign(msg, priv)) is False


def test_parse_sha256sums():
    text = "aa11  Waves-macos-arm64.zip\n# comment\n\nbb22 *Waves-windows-x64.zip\nmalformed\n"
    assert signing.parse_sha256sums(text) == {
        "Waves-macos-arm64.zip": "aa11",
        "Waves-windows-x64.zip": "bb22",
    }
