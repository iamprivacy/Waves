"""Waves-owned TIDAL session config.

Subclasses the upstream-tracking ``tidaler.config.Tidal`` so a correctness fix
lands here instead of in the shared ``config.py`` method body. Keeping the
override out of ``config.py`` means a future tidal-dl-ng bump still merges that
file cleanly (the whole point of the backend rework's patchability constraint).
"""

from __future__ import annotations

import logging
import os

import requests

from tidaler.config import Tidal

logger = logging.getLogger("waves.session")

# Network-layer failures that mean "could not reach TIDAL", not "the saved token
# is bad". requests raises these on offline / DNS / timeout / black-holed
# connections. They are all subclasses of requests.RequestException, but we
# deliberately do NOT catch the whole family: a genuine auth rejection surfaces
# as an HTTPError (also a RequestException), and that MUST still fall through to
# the delete path so a truly dead token is cleared.
_NETWORK_ERRORS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ChunkedEncodingError,
)


class WavesTidal(Tidal):
    """A ``Tidal`` whose cached-token login survives a transient network.

    Upstream ``login_token`` deletes the token file on *any* exception. A
    black-holed or offline network at launch therefore logs the user out
    permanently: the OAuth refresh token is gone and cannot be recovered. This
    override keeps the token when the failure is a network problem and only
    deletes it on a real auth or parse failure.
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
            except _NETWORK_ERRORS:
                # Could not reach TIDAL to validate the token. It is almost
                # certainly still valid, so keep it: report not-signed-in and let
                # a retry (or a restart with the network back) recover the
                # session. Deleting here is the permanent-logout bug.
                result = False
                logger.warning("Cached-token login could not reach TIDAL; keeping the saved token")
            except Exception:
                # A real auth or parse failure (rejected or expired token, a
                # corrupt token file): the token is useless, so remove it.
                result = False
                logger.info("Cached-token login failed; removing the invalid token file")
                if os.path.exists(self.file_path):
                    os.remove(self.file_path)

        return result
