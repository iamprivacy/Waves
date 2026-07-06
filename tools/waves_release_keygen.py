#!/usr/bin/env python
"""Generate the Ed25519 release-signing keypair for the Waves self-updater.

Run this once, offline, before the first signed release. It prints two things:

  * ``UPDATE_PUBLIC_KEY = "..."``: paste into ``tidaler/waves_ui/signing.py`` so
    every shipped binary can verify update manifests.
  * a PKCS#8 PEM private key: store verbatim as the GitHub Actions secret
    ``WAVES_SIGNING_KEY`` and never commit it. Anyone with this key can sign
    updates the app will trust, so treat it like a root credential.

Usage::

    poetry run python tools/waves_release_keygen.py
"""

from __future__ import annotations

from tidaler.waves_ui.signing import keygen


def main() -> None:
    public_b64, private_pem = keygen()
    print("# --- 1. Embed this public key in tidaler/waves_ui/signing.py ---")
    print(f'UPDATE_PUBLIC_KEY = "{public_b64}"')
    print()
    print("# --- 2. Store the PEM below as the CI secret WAVES_SIGNING_KEY ---")
    print("#       (do NOT commit it; rotate by shipping a new public key)")
    print(private_pem, end="")


if __name__ == "__main__":
    main()
