"""Regression tests for WavesTidal.login_token (cached-token launch login).

The upstream ``Tidal.login_token`` deletes the token file on *any* exception, so
a black-holed or offline network at launch permanently logs the user out (the
OAuth refresh token is unrecoverable). ``WavesTidal`` keeps the token on a
network failure and only deletes it on a genuine auth/parse failure.

The instance is built with ``__new__`` so no tidalapi ``Session`` or on-disk
singleton is created: only the handful of attributes ``login_token`` reads are
set, and ``session.load_oauth_session`` is a fake that raises (or returns) what
each case needs.
"""

from __future__ import annotations

import types

import requests

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


def test_transient_connection_error_keeps_token(tmp_path):
    wt, token_file = _bare(tmp_path, raises=requests.exceptions.ConnectionError("offline"))
    assert wt.login_token() is False
    assert token_file.exists(), "a network failure must NOT delete the saved token"


def test_timeout_keeps_token(tmp_path):
    wt, token_file = _bare(tmp_path, raises=requests.exceptions.Timeout("slow"))
    assert wt.login_token() is False
    assert token_file.exists()


def test_chunked_encoding_error_keeps_token(tmp_path):
    wt, token_file = _bare(tmp_path, raises=requests.exceptions.ChunkedEncodingError("cut off"))
    assert wt.login_token() is False
    assert token_file.exists()


def test_auth_rejection_removes_token(tmp_path):
    # An HTTPError is a RequestException but NOT a network error: a rejected or
    # expired token is genuinely dead, so it must still be cleared.
    wt, token_file = _bare(tmp_path, raises=requests.exceptions.HTTPError("401 Unauthorized"))
    assert wt.login_token() is False
    assert not token_file.exists(), "a rejected token must be removed"


def test_parse_error_removes_token(tmp_path):
    wt, token_file = _bare(tmp_path, raises=KeyError("missing field"))
    assert wt.login_token() is False
    assert not token_file.exists(), "a corrupt token must be removed"


def test_successful_login_keeps_token(tmp_path):
    wt, token_file = _bare(tmp_path, returns=True)
    assert wt.login_token() is True
    assert token_file.exists()


def test_no_stored_token_is_a_noop(tmp_path):
    wt, token_file = _bare(tmp_path, returns=True, has_token=False)
    assert wt.login_token() is False
    assert token_file.exists(), "with no stored token the file is never touched"
