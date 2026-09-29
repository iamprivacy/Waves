#!/usr/bin/env python
"""Print the "Verify" footer of a GitHub Release body from its SHA256SUMS.

The footer puts the expected hash next to each download, prints the release
public key, and gives the same copyable commands as the README, so a cautious
user can check a build without leaving the release page. CI appends it to the
release notes right after signing the manifest.

Usage::

    python tools/release_verify_notes.py SHA256SUMS >> RELEASE_NOTES.md
"""

from __future__ import annotations

import sys

from waves.waves_ui.signing import parse_sha256sums, public_key_pem

README_VERIFY_URL = "https://github.com/iamprivacy/Waves#verify-a-download"


def verify_notes(manifest_text: str) -> str:
    sums = parse_sha256sums(manifest_text)
    lines = [
        "### 🔐 Verify",
        "",
        "| Asset | SHA-256 |",
        "| --- | --- |",
    ]
    lines.extend(f"| `{name}` | `{digest}` |" for name, digest in sorted(sums.items()))
    lines += [
        "",
        "`SHA256SUMS` lists these hashes and `SHA256SUMS.sig` is an Ed25519 signature over it, made with the Waves release key. The public key, also compiled into every build:",
        "",
        "```",
        public_key_pem().rstrip("\n"),
        "```",
        "",
        "From a folder holding the zip, `SHA256SUMS` and `SHA256SUMS.sig` (Linux, or macOS with OpenSSL 3 from Homebrew in place of the built-in `openssl`):",
        "",
        "```bash",
        "printf '%s\\n' '-----BEGIN PUBLIC KEY-----' '"
        + public_key_pem().splitlines()[1]
        + "' '-----END PUBLIC KEY-----' > waves-release.pem",
        "base64 --decode < SHA256SUMS.sig > SHA256SUMS.sig.bin",
        "openssl pkeyutl -verify -pubin -inkey waves-release.pem -rawin -in SHA256SUMS -sigfile SHA256SUMS.sig.bin",
        "grep -v '^#' SHA256SUMS | tr -d '\\r' | shasum -a 256 --ignore-missing -c -",
        "```",
        "",
        f"Windows steps and details: [Verify a download]({README_VERIFY_URL}).",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: release_verify_notes.py <SHA256SUMS>", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as fh:
        sys.stdout.write(verify_notes(fh.read()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
