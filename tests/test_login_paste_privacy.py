"""A mis-pasted sign-in field must never reach the logs.

The sign-in flow asks the user to copy a URL from the browser, so a stale
clipboard entry (a password, a chat message, a personal note) is a realistic
slip. tidalapi refuses a paste without "https://" by raising with the pasted
text INSIDE the exception message, and completeLogin's logger.exception would
persist that verbatim: breadcrumb ring, stderr, the always-on disk log, the
ERROR-triggered crumb dump, and any exported bundle. The scrubber's nets catch
structured PII, not arbitrary prose, and the "also hide titles and searches"
switch only hashes content() spans, which third-party exception text never
gets. completeLogin now refuses such a paste before tidalapi can see it.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

from waves.waves_ui.backend import WavesBridge


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(self.format(record) + (record.exc_text or ""))


class _Stub:
    completeLogin = WavesBridge.completeLogin

    def __init__(self):
        self.statuses: list[str] = []
        self.busy: list[bool] = []
        # Booby-trapped on purpose: a paste that fails the guard must never
        # reach tidalapi or the pool at all.
        self.tidal = None
        self.threadpool = None

    def _set_status(self, text):
        self.statuses.append(text)

    def _set_busy(self, on):
        self.busy.append(bool(on))


def _watching_waves_logger():
    handler = _Capture()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger = logging.getLogger("waves")
    logger.addHandler(handler)
    return logger, handler


def test_a_stray_clipboard_paste_is_refused_and_never_logged():
    logger, handler = _watching_waves_logger()
    try:
        stub = _Stub()
        stub.completeLogin("hunter2 remind mom about tuesday")

        assert stub.statuses and "Copy the full URL" in stub.statuses[-1]
        assert stub.busy == [], "a refused paste must not cycle the spinner"
        joined = "\n".join(handler.messages)
        assert "hunter2" not in joined and "tuesday" not in joined
    finally:
        logger.removeHandler(handler)


def test_a_real_looking_url_still_goes_through_to_tidalapi():
    # The guard uses tidalapi's own predicate ("https://" anywhere in the
    # paste); anything it would accept must still be handed over. Here the
    # hand-off fails downstream, which is the normal sign-in failure path.
    class _Pool:
        @staticmethod
        def start(worker, priority: int = 0):
            worker.fn()

    stub = _Stub()
    stub.threadpool = _Pool()

    def _raise_keyerror(url):
        raise KeyError("code")

    stub.tidal = SimpleNamespace(session=SimpleNamespace(pkce_get_auth_token=_raise_keyerror))

    stub.completeLogin("https://tidal.com/android/login/auth?weird=1")

    assert stub.statuses[-1] == "Sign-in failed. Try again."
    assert stub.busy == [True, False]
