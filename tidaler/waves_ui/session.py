"""Waves-owned TIDAL session config.

Subclasses the upstream-tracking ``tidaler.config.Tidal`` so a correctness fix
lands here instead of in the shared ``config.py`` method body. Keeping the
override out of ``config.py`` means a future tidal-dl-ng bump still merges that
file cleanly (the whole point of the backend rework's patchability constraint).
"""

from __future__ import annotations

import logging
import os

from tidaler.config import Tidal

logger = logging.getLogger("waves.session")

# The only two answers that mean "TIDAL looked at the saved sign-in and refused
# it". Everything else (a dead network, a rate limit, a server fault, a hotel
# captive portal) is TIDAL declining to answer at all, and an unanswered
# question must never cost the user their sign-in.
_SIGN_IN_REFUSED = frozenset({401, 403})


def _answered_status(exc: BaseException) -> int | None:
    """The HTTP status behind ``exc``, following the exception chain.

    The status is often not on the exception that reaches us. tidalapi parses
    the error body as JSON while it is *handling* the original ``HTTPError``, so
    a proxy's HTML error page raises a second exception out of the handler; and
    it translates a 429 into a ``TooManyRequests`` that carries no response at
    all. Walking ``__cause__``/``__context__`` finds the status when TIDAL
    answered with one, and returns None when it never really answered.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        status = getattr(getattr(current, "response", None), "status_code", None)
        if isinstance(status, int):
            return status
        current = current.__cause__ or current.__context__
    return None


class WavesTidal(Tidal):
    """A ``Tidal`` whose cached-credential login survives a bad network.

    Upstream ``login_token`` deletes the saved sign-in on *any* exception, so a
    black-holed network, a 429, a 502 or a captive portal at launch logs the
    user out permanently: the OAuth refresh credential is gone and cannot be
    recovered. This override deletes only when TIDAL positively refused the
    sign-in, and keeps it in every other case.

    The asymmetry is the whole argument. Keeping a sign-in that really is dead
    costs nothing: the user signs in again and ``save()`` atomically replaces the
    file. Deleting a live one cannot be undone.
    """

    def login_token(self, do_pkce: bool = True) -> bool:
        result = False
        self.is_pkce = do_pkce

        if self.token_from_storage:
            try:
                result = self.session.load_oauth_session(
                    self.data.token_type,
                    self.data.access_token,
                    self.data.refresh_token,
                    self.data.expiry_time,
                    is_pkce=do_pkce,
                )
            except Exception as exc:
                result = False
                status = _answered_status(exc)
                if status in _SIGN_IN_REFUSED:
                    logger.info("TIDAL refused the saved sign-in (%s); removing it", status)
                    if os.path.exists(self.file_path):
                        os.remove(self.file_path)
                else:
                    logger.warning(
                        "Cached sign-in got no usable answer from TIDAL (status %s); keeping it",
                        "none" if status is None else status,
                    )

        return result

    def login_finalize(self) -> bool:
        """Record that a completed sign-in is now saved.

        The base method writes the credentials file but leaves the flag saying
        one exists untouched: upstream sets that flag only in ``Tidal.__init__``,
        which is a command-line assumption. There the process signs in once and
        exits, and the next run re-reads the file on the way up. A window that
        stays open does not get a next run.

        ``login_token`` opens on that flag, so without this line every later
        re-authentication in the same session answers False without attempting
        anything. The one that matters is Dolby Atmos: switching to the Atmos
        credentials re-authenticates, so on a first launch after install, or
        after signing out and back in, every Atmos track in every download
        failed for the rest of the run and only quitting the app fixed it.
        Needing a restart to refresh is exactly what this app does not do.
        """
        result = super().login_finalize()
        if result:
            self.token_from_storage = True
        return result
