"""One certificate store for every plain-urllib read the app makes.

``urllib.request.urlopen`` with no context trusts whatever CA file the
linked OpenSSL was configured with. In a packaged macOS build that is the
build machine's Homebrew path (``/opt/homebrew/etc/openssl@3``), which a
user's Mac usually does not have, so every read failed certificate
verification there (the HLS playlist load hit the same trap). certifi ships
inside the app, so a context built on it verifies the same way everywhere.
"""

from __future__ import annotations

import ssl
import threading

import certifi

_lock = threading.Lock()
_context: ssl.SSLContext | None = None


def tls_context() -> ssl.SSLContext:
    """The shared, verifying SSLContext backed by certifi (built once)."""
    global _context
    with _lock:
        if _context is None:
            _context = ssl.create_default_context(cafile=certifi.where())
        return _context
