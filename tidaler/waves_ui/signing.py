"""Release-signing primitives for the Waves self-updater.

The self-updater downloads and *executes* code, so before installing anything it
must prove the bytes are both authentic (published by us) and intact. A checksum
fetched from the same host as the binary only proves the latter, an attacker who
can write to the release serves a matching checksum too. So the trust anchor is an
**Ed25519 signature** over the ``SHA256SUMS`` manifest, made with a private key that
lives only in CI secrets and is verified here against :data:`UPDATE_PUBLIC_KEY`, a
public key compiled into the binary. The signature proves authenticity; the hashes
inside the signed manifest then prove the integrity of each downloaded asset.

This module is pure (no Qt, no network) and uses pycryptodome (``Crypto``), already
a dependency. The app only needs :func:`verify` and :func:`parse_sha256sums`;
:func:`keygen` and :func:`sign` are the producer half, exercised by the
``tools/`` release scripts and the unit tests.
"""

from __future__ import annotations

import base64
import binascii

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

# Base64 of the raw 32-byte Ed25519 *public* key. BLANK until go-live, while it
# is empty :func:`verify` returns ``False`` for everything, so the updater stays
# fail-closed and can never self-install. Fill this in at release time with the
# value printed by ``tools/waves_release_keygen.py`` (and store the matching
# private key as the CI secret ``WAVES_SIGNING_KEY``). Keep the two in lockstep:
# a non-empty key here without a CI signing step makes every install refuse.
UPDATE_PUBLIC_KEY = "cetggrhiqyMN5HsBCi/f2gJL75FVPOYGU/sd4dI5b+0="

_RAW_PUBKEY_LEN = 32  # an Ed25519 public key is exactly 32 bytes


def keygen() -> tuple[str, str]:
    """Generate a fresh Ed25519 keypair for signing releases.

    Returns ``(public_key_b64, private_key_pem)``: the base64 public key to embed
    as :data:`UPDATE_PUBLIC_KEY`, and the PKCS#8 PEM private key to store as the
    ``WAVES_SIGNING_KEY`` CI secret. Run this once, offline; never commit the PEM.
    """
    key = ECC.generate(curve="Ed25519")
    pub_b64 = base64.b64encode(key.public_key().export_key(format="raw")).decode("ascii")
    return pub_b64, key.export_key(format="PEM")


def sign(message: bytes, private_key_pem: str) -> str:
    """Sign ``message`` with a PEM Ed25519 private key; return the base64 signature.

    Producer side (CI). The detached signature is over the *exact* bytes of the
    ``SHA256SUMS`` manifest, callers must not re-encode or normalise newlines.
    """
    key = ECC.import_key(private_key_pem)
    sig = eddsa.new(key, "rfc8032").sign(message)
    return base64.b64encode(sig).decode("ascii")


def verify(message: bytes, signature_b64: str, public_key_b64: str = UPDATE_PUBLIC_KEY) -> bool:
    """Return ``True`` only if ``signature_b64`` is a valid signature of ``message``.

    Fail-closed by construction: an empty/whitespace key, malformed base64, a
    wrong-length key, or any verification error all return ``False`` (never raise),
    so a caller that treats ``False`` as "abort" can never be tricked into
    installing on a bad or absent signature.
    """
    if not public_key_b64 or not public_key_b64.strip():
        return False
    try:
        raw_pub = base64.b64decode(public_key_b64, validate=True)
        if len(raw_pub) != _RAW_PUBKEY_LEN:
            return False
        signature = base64.b64decode(signature_b64, validate=True)
        verifier = eddsa.new(eddsa.import_public_key(raw_pub), "rfc8032")
        verifier.verify(message, signature)
    except (ValueError, TypeError, binascii.Error):
        return False
    return True


def parse_sha256sums(text: str) -> dict[str, str]:
    """Parse a ``SHA256SUMS`` manifest into ``{filename: sha256hex}``.

    Lines are ``"<hex>  <filename>"`` (coreutils ``sha256sum`` style). The binary
    marker ``*`` before a filename and blank/comment lines are tolerated. Only ever
    call this on bytes whose signature has already been verified, the manifest is
    attacker-controlled until then.
    """
    sums: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        digest, name = parts[0].lower(), parts[1].strip().lstrip("*")
        sums[name] = digest
    return sums
