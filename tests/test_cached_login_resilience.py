"""Regression tests for WavesTidal.login_token (cached-sign-in launch login).

The upstream ``Tidal.login_token`` deletes the saved sign-in on *any* exception,
so a black-holed network at launch permanently logs the user out (the OAuth
refresh credential is unrecoverable). ``WavesTidal`` inverts that default: it
deletes only when TIDAL positively refused the sign-in with a 401 or a 403, and
keeps it for every other failure.

The cases that matter most are the ones that look like auth failures but are
not: a 429 rate limit, a 5xx server fault, and a captive portal serving HTML
where JSON was expected. Each of those reached the delete path before.

The instance is built with ``__new__`` so no tidalapi ``Session`` or on-disk
singleton is created: only the handful of attributes ``login_token`` reads are
set, and ``session.load_oauth_session`` is a fake that raises (or returns) what
each case needs.
"""

from __future__ import annotations

import types

import pytest
import requests
from tidalapi.exceptions import TooManyRequests

from tidaler.waves_ui.session import WavesTidal


def _bare(tmp_path, *, raises=None, returns=False, has_token=True):
    wt = WavesTidal.__new__(WavesTidal)
    wt.token_from_storage = has_token
    # Dummy fixture values, not real credentials (bandit S106 false positive).
    wt.data = types.SimpleNamespace(
        token_type="Bearer", access_token="a", refresh_token="r", expiry_time=0  # noqa: S106
    )
    token_file = tmp_path / "token.json"
    token_file.write_text("{}")
    wt.file_path = str(token_file)

    def _load_oauth_session(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return returns

    wt.session = types.SimpleNamespace(load_oauth_session=_load_oauth_session)
    return wt, token_file


def _http_error(status: int) -> requests.exceptions.HTTPError:
    """An HTTPError shaped like the one ``raise_for_status`` produces."""
    response = requests.Response()
    response.status_code = status
    return requests.exceptions.HTTPError(f"{status} from TIDAL", response=response)


def _raised_while_handling(inner: BaseException, outer: BaseException) -> BaseException:
    """``outer`` with ``inner`` on its ``__context__``, exactly as raising would set it.

    This is the real tidalapi shape: ``http_error_to_tidal_error`` calls
    ``response.json()`` on the error body while handling the HTTPError, and an
    HTML error page raises out of that handler, so the status ends up one link
    down the chain rather than on the exception that arrives.
    """
    outer.__context__ = inner
    return outer


def _html_where_json_was_expected() -> requests.exceptions.JSONDecodeError:
    return requests.exceptions.JSONDecodeError("Expecting value", "<html>Sign in</html>", 0)


# --- TIDAL never answered: the sign-in must survive -------------------------


@pytest.mark.parametrize(
    "error",
    [
        requests.exceptions.ConnectionError("offline"),
        requests.exceptions.Timeout("slow"),
        requests.exceptions.ChunkedEncodingError("cut off"),
    ],
    ids=["connection", "timeout", "chunked"],
)
def test_a_network_failure_keeps_the_saved_sign_in(tmp_path, error):
    wt, token_file = _bare(tmp_path, raises=error)
    assert wt.login_token() is False
    assert token_file.exists(), "a network failure must NOT delete the saved sign-in"


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 511])
def test_a_rate_limit_or_server_fault_keeps_the_saved_sign_in(tmp_path, status):
    """TIDAL said "not now", not "not you". Deleting here was the logout bug."""
    wt, token_file = _bare(tmp_path, raises=_http_error(status))
    assert wt.login_token() is False
    assert token_file.exists(), f"a {status} must NOT delete the saved sign-in"


def test_the_translated_rate_limit_keeps_the_saved_sign_in(tmp_path):
    """tidalapi turns a 429 into a TooManyRequests that carries no response."""
    wt, token_file = _bare(tmp_path, raises=TooManyRequests("Too many requests", retry_after=30))
    assert wt.login_token() is False
    assert token_file.exists(), "a rate limit must NOT delete the saved sign-in"


def test_a_captive_portal_keeps_the_saved_sign_in(tmp_path):
    """Hotel wifi answers 200 with HTML, so ``.json()`` raises before any status."""
    wt, token_file = _bare(tmp_path, raises=_html_where_json_was_expected())
    assert wt.login_token() is False
    assert token_file.exists(), "a captive portal must NOT delete the saved sign-in"


def test_a_server_fault_with_an_html_body_keeps_the_saved_sign_in(tmp_path):
    """A 502 from a proxy: tidalapi's error handler chokes parsing the HTML body."""
    chained = _raised_while_handling(_http_error(502), _html_where_json_was_expected())
    wt, token_file = _bare(tmp_path, raises=chained)
    assert wt.login_token() is False
    assert token_file.exists(), "a 502 behind a parse failure must NOT delete the sign-in"


def test_a_malformed_answer_keeps_the_saved_sign_in(tmp_path):
    """A 200 whose body is missing sessionId is a server problem, not a dead sign-in."""
    wt, token_file = _bare(tmp_path, raises=KeyError("sessionId"))
    assert wt.login_token() is False
    assert token_file.exists(), "a malformed 200 must NOT delete the saved sign-in"


# --- TIDAL refused: the sign-in is dead and must go -------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_sign_in_is_removed(tmp_path, status):
    wt, token_file = _bare(tmp_path, raises=_http_error(status))
    assert wt.login_token() is False
    assert not token_file.exists(), f"a {status} means TIDAL refused it, so it must go"


def test_a_refusal_behind_a_parse_failure_is_still_removed(tmp_path):
    """A 401 with an HTML body: the status is on the chained cause, not on top."""
    chained = _raised_while_handling(_http_error(401), _html_where_json_was_expected())
    wt, token_file = _bare(tmp_path, raises=chained)
    assert wt.login_token() is False
    assert not token_file.exists(), "the chain must be walked to find the 401"


# --- The ordinary paths -----------------------------------------------------


def test_successful_login_keeps_the_saved_sign_in(tmp_path):
    wt, token_file = _bare(tmp_path, returns=True)
    assert wt.login_token() is True
    assert token_file.exists()


def test_no_stored_sign_in_is_a_noop(tmp_path):
    wt, token_file = _bare(tmp_path, returns=True, has_token=False)
    assert wt.login_token() is False
    assert token_file.exists(), "with nothing stored the file is never touched"


# --- A client probe is not the user's sign-in (issue #30) -------------------
#
# Fetching Dolby Atmos swaps the client id and re-authenticates mid-download to
# prove the swap took, and that goes through login_token. A refusal there is the
# Atmos client being turned away; it says nothing about the saved sign-in, yet
# it used to delete it from under a running queue, on a worker thread, while the
# window still said signed in. The loss only showed at the next launch.


def _probe_capable(wt):
    """The attributes ``_reauthenticate_current_client`` and the Atmos swap read."""
    wt.is_pkce = True
    wt.is_atmos_session = False
    wt.original_client_id = "orig-id"
    wt.original_client_secret = "orig-secret"  # noqa: S105
    wt.original_client_id_pkce = "orig-pkce-id"
    wt.original_client_secret_pkce = "orig-pkce-secret"  # noqa: S105
    wt.settings = types.SimpleNamespace(data=types.SimpleNamespace(quality_audio="LOSSLESS"))
    wt.session.config = types.SimpleNamespace(
        client_id="orig-id",
        client_secret="orig-secret",  # noqa: S106
        client_id_pkce="orig-pkce-id",
        client_secret_pkce="orig-pkce-secret",  # noqa: S106
    )
    wt.session.audio_quality = "LOSSLESS"
    wt.session.refresh_token = "saved-refresh-credential"  # noqa: S105
    wt.session.token_refresh = lambda _credential: True
    return wt


@pytest.mark.parametrize("status", [401, 403])
def test_a_refused_client_probe_keeps_the_saved_sign_in(tmp_path, status):
    wt, saved = _bare(tmp_path, raises=_http_error(status))
    _probe_capable(wt)

    assert wt._reauthenticate_current_client() is False
    assert saved.exists(), f"a {status} while proving a client says nothing about the sign-in"


def test_a_refused_atmos_swap_keeps_the_saved_sign_in(tmp_path):
    """The whole reported chain, end to end: the swap is what calls the probe."""
    wt, saved = _bare(tmp_path, raises=_http_error(401))
    _probe_capable(wt)

    assert wt.switch_to_atmos_session() is False
    assert wt.is_atmos_session is False
    assert saved.exists(), "an Atmos track must never cost the user their sign-in"


def test_the_guard_is_released_after_the_probe(tmp_path):
    """One probe must not disarm the launch-time deletion for the rest of the run."""
    wt, saved = _bare(tmp_path, raises=_http_error(401))
    _probe_capable(wt)
    wt._reauthenticate_current_client()

    assert wt.login_token() is False
    assert not saved.exists(), "a refusal outside a probe still means TIDAL refused the sign-in"


def test_a_probe_refused_without_an_answer_still_keeps_it(tmp_path):
    """The ordinary keep-it path is unchanged inside a probe."""
    wt, saved = _bare(tmp_path, raises=requests.exceptions.ConnectionError("offline"))
    _probe_capable(wt)

    assert wt._reauthenticate_current_client() is False
    assert saved.exists()
