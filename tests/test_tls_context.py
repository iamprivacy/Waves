"""Every plain-urllib read carries the certifi-backed context.

A bare ``urlopen`` trusts the linked OpenSSL's built-in CA path, which in a
packaged macOS build is the build machine's Homebrew folder: on a Mac
without it every MusicBrainz and ListenBrainz read failed verification.
"""

from __future__ import annotations

import ast
import ssl
from pathlib import Path

import certifi

from waves.tls import tls_context

_PKG = Path(__file__).resolve().parent.parent / "waves"


def test_the_shared_context_verifies_against_certifi():
    ctx = tls_context()
    assert ctx is tls_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname
    assert ctx.cert_store_stats()["x509_ca"] > 50
    assert Path(certifi.where()).is_file()


def test_every_urlopen_in_the_app_passes_a_context():
    bare = []
    for path in _PKG.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == "urlopen" and not any(k.arg == "context" for k in node.keywords):
                bare.append(f"{path.relative_to(_PKG.parent)}:{node.lineno}")
    assert not bare, f"urlopen without context=tls_context(): {bare}"
