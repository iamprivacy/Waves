#!/usr/bin/env python
"""Sign a SHA256SUMS manifest with the Waves release key (CI producer side).

Reads the PEM Ed25519 private key from the ``WAVES_SIGNING_KEY`` environment
variable (a GitHub Actions secret) and writes the base64 detached signature over
the manifest's exact bytes. The app verifies this against the embedded public key
before trusting any hash in the manifest.

Fail-closed: if the key is missing it exits non-zero so CI can never publish an
unsigned release that the updater would (correctly) refuse.

Usage::

    WAVES_SIGNING_KEY="$(cat key.pem)" python tools/sign_manifest.py SHA256SUMS SHA256SUMS.sig
"""

from __future__ import annotations

import os
import sys

from tidaler.waves_ui.signing import sign


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: sign_manifest.py <manifest-in> <signature-out>", file=sys.stderr)
        return 2
    key = os.environ.get("WAVES_SIGNING_KEY", "").strip()
    if not key:
        print("WAVES_SIGNING_KEY is not set; refusing to produce an unsigned release.", file=sys.stderr)
        return 1
    with open(argv[1], "rb") as fh:
        data = fh.read()
    with open(argv[2], "w", encoding="ascii") as fh:
        fh.write(sign(data, key))
    print(f"signed {argv[1]} ({len(data)} bytes) -> {argv[2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
