"""A failed sign-in says why, on the sign-in panel itself (issues #7, #56, #63).

Every completeLogin failure used to land only in the status bar, which sits
under the sign-in panel's overlay, so three people reported that COMPLETE
SIGN-IN "does nothing". Each stage now sets the panel's own error line:

- a paste of the TIDAL login page (no ``code``) is refused before tidalapi,
- a refused one-time code, a network failure, an account TIDAL would not
  confirm and a sign-in that could not be saved each get their own line,
- anything else names the stage it stopped at,

and a second submit while one is in flight (paste auto-submits, then the
button is clicked) is ignored instead of spending the same code twice.
"""

from __future__ import annotations

import logging
import types

import requests
from conftest import _InlinePool

from waves.waves_ui import backend
from waves.waves_ui.backend import WavesBridge

_CODE = "s3cretOneTimeCode"
_GOOD = f"https://tidal.com/android/login/auth?code={_CODE}&state=na"
_AUTHORIZE = (
    "https://login.tidal.com/authorize?response_type=code&redirect_uri="
    "https%3A%2F%2Ftidal.com%2Fandroid%2Flogin%2Fauth&client_id=x&code_challenge=y"
)


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.text: list[str] = []

    def emit(self, record):
        self.text.append(record.getMessage() + (self.format(record) if record.exc_info else ""))


class _Stub:
    completeLogin = WavesBridge.completeLogin
    beginLogin = WavesBridge.beginLogin
    _set_login_error = WavesBridge._set_login_error
    _set_login_busy = WavesBridge._set_login_busy

    def __init__(self, *, exchange=None, process=None, finalize=None):
        self._login_error = ""
        self._login_busy = False
        self.loginErrorChanged = types.SimpleNamespace(emit=lambda: None)
        self.loginBusyChanged = types.SimpleNamespace(emit=lambda: None)
        self.statuses: list[str] = []
        self.busy: list[bool] = []
        self.logged_in: list[bool] = []
        self.exchanges = 0
        self.threadpool = _InlinePool()

        def _exchange(url):
            self.exchanges += 1
            if exchange is not None:
                return exchange(url)
            return {"access_token": "t"}

        def _process(token, is_pkce_token=False):
            if process is not None:
                process()

        self.tidal = types.SimpleNamespace(
            session=types.SimpleNamespace(pkce_get_auth_token=_exchange, process_auth_token=_process),
            login_finalize=finalize or (lambda: True),
        )

    def _set_status(self, text):
        self.statuses.append(text)

    def _set_busy(self, on):
        self.busy.append(bool(on))

    def _set_logged_in(self, value):
        self.logged_in.append(value)

    def _load_page_cache(self):
        pass

    def _init_download(self):
        pass

    def _prefetch_tile_art(self):
        pass


def _http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    return requests.HTTPError(f"{status} Client Error", response=resp)


def _raise(exc):
    def f(*_a, **_k):
        raise exc

    return f


def _watch():
    handler = _Capture()
    logging.getLogger("waves").addHandler(handler)
    return handler


def _unwatch(handler):
    logging.getLogger("waves").removeHandler(handler)


def test_the_login_page_link_is_refused_with_directions_before_tidalapi():
    handler = _watch()
    try:
        stub = _Stub()
        stub.completeLogin(_AUTHORIZE)
        assert stub.exchanges == 0, "a link with no code must never reach tidalapi"
        assert stub._login_error == backend._LOGIN_MSG_NO_CODE
        assert "Page not found" in stub._login_error
        assert stub.busy == []
        assert "login.tidal.com" not in "\n".join(handler.text)
    finally:
        _unwatch(handler)


def test_a_non_url_paste_shows_on_the_panel_too():
    stub = _Stub()
    stub.completeLogin("hunter2")
    assert stub.exchanges == 0
    assert stub._login_error and "hunter2" not in stub._login_error


def test_a_refused_code_says_to_sign_in_again():
    stub = _Stub(exchange=_raise(_http_error(400)))
    stub.completeLogin(_GOOD)
    assert stub._login_error == backend._LOGIN_MSG_REFUSED
    assert stub.statuses[-1] == "Sign-in failed. Try again."
    assert stub.busy == [True, False] and stub._login_busy is False
    assert stub.logged_in == []


def test_a_network_failure_says_so():
    for exc in (requests.ConnectionError("down"), requests.Timeout("slow"), ConnectionError("down")):
        stub = _Stub(exchange=_raise(exc))
        stub.completeLogin(_GOOD)
        assert stub._login_error == backend._LOGIN_MSG_NETWORK, exc


def test_an_account_tidal_will_not_confirm_says_so():
    stub = _Stub(finalize=lambda: False)
    stub.completeLogin(_GOOD)
    assert stub._login_error == backend._LOGIN_MSG_ACCOUNT
    assert stub.logged_in == []


def test_a_sign_in_that_cannot_be_saved_says_so_and_never_logs_the_path():
    handler = _watch()
    try:
        path = "/Users/someone/Library/Application Support/waves/token.json"
        stub = _Stub(finalize=_raise(PermissionError(13, "Permission denied", path)))
        stub.completeLogin(_GOOD)
        assert stub._login_error == backend._LOGIN_MSG_SAVE
        joined = "\n".join(handler.text)
        assert "someone" not in joined and "token.json" not in joined
        assert "PermissionError" in joined
    finally:
        _unwatch(handler)


def test_a_network_failure_at_the_account_step_is_not_a_save_failure():
    stub = _Stub(finalize=_raise(requests.ConnectionError("down")))
    stub.completeLogin(_GOOD)
    assert stub._login_error == backend._LOGIN_MSG_NETWORK


def test_anything_else_names_the_stage_it_stopped_at():
    stub = _Stub(process=_raise(KeyError("sessionId")))
    stub.completeLogin(_GOOD)
    assert "step: session" in stub._login_error
    assert "sessionId" not in stub._login_error


def test_the_code_never_reaches_the_log_or_the_panel():
    handler = _watch()
    try:
        stub = _Stub(exchange=_raise(_http_error(400)))
        stub.completeLogin(_GOOD)
        assert _CODE not in "\n".join(handler.text)
        assert _CODE not in stub._login_error
    finally:
        _unwatch(handler)


def test_a_second_submit_while_signing_in_is_ignored():
    stub = _Stub()
    stub._login_busy = True  # the paste's own submit is still running
    stub.completeLogin(_GOOD)
    assert stub.exchanges == 0, "the same one-time code was spent twice"
    assert stub.busy == []


def test_a_success_clears_the_error_and_the_busy_flag():
    stub = _Stub()
    stub._login_error = "old failure"
    stub.completeLogin(_GOOD)
    assert stub._login_error == ""
    assert stub._login_busy is False
    assert stub.logged_in == [True]


def test_reopening_the_browser_login_clears_the_error():
    stub = _Stub()
    stub._login_error = "old failure"
    stub.threadpool = types.SimpleNamespace(start=lambda worker, priority=0: None)
    stub.beginLogin()
    assert stub._login_error == ""


def test_redirect_code_detection():
    assert backend._redirect_has_code(_GOOD)
    assert not backend._redirect_has_code(_AUTHORIZE)
    assert not backend._redirect_has_code("https://tidal.com/android/login/auth")
    assert not backend._redirect_has_code("https://tidal.com/android/login/auth?code=")


def test_completeLogin_itself_holds_the_guard_while_the_exchange_runs():
    # Re-enter from inside the exchange, the way the paste's own submit and a
    # click land together: the second call must find the guard already up.
    holder: dict = {}

    def _exchange(url):
        assert holder["stub"]._login_busy is True, "the guard was not up during the exchange"
        holder["stub"].completeLogin(_GOOD)
        return {"access_token": "t"}

    stub = _Stub(exchange=_exchange)
    holder["stub"] = stub
    stub.completeLogin(_GOOD)
    assert stub.exchanges == 1, "the same one-time code was sent twice"
    assert stub.logged_in == [True]
    assert stub._login_busy is False


def test_an_empty_box_says_to_paste_first():
    stub = _Stub()
    stub.completeLogin("   ")
    assert stub._login_error == backend._LOGIN_MSG_EMPTY
    assert stub.exchanges == 0 and stub.busy == []


def test_another_link_without_a_code_is_not_called_the_login_page():
    stub = _Stub()
    stub.completeLogin("https://tidal.com/browse/track/12345")
    assert stub.exchanges == 0
    assert stub._login_error == backend._LOGIN_MSG_WRONG_LINK


def test_failures_after_the_exchange_send_the_user_back_to_the_browser():
    # The code is spent by then: pasting the same link again can only fail.
    for msg in (backend._LOGIN_MSG_NETWORK, backend._LOGIN_MSG_ACCOUNT, backend._LOGIN_MSG_SAVE):
        assert "REOPEN BROWSER LOGIN" in msg
    assert "REOPEN BROWSER LOGIN" in backend._login_failure_message("session", KeyError("x"))


def _users_http_error(status: int) -> requests.HTTPError:
    resp = requests.Response()
    resp.status_code = status
    resp.url = "https://api.tidal.com/v1/users/987654321?countryCode=US"
    return requests.HTTPError(f"{status} Client Error: Unauthorized for url: {resp.url}", response=resp)


def test_a_failed_account_read_never_logs_the_account_id():
    handler = _watch()
    try:
        stub = _Stub(process=_raise(_users_http_error(401)))
        stub.completeLogin(_GOOD)
        joined = "\n".join(handler.text)
        assert "987654321" not in joined
        assert "session" in joined and "401" in joined
    finally:
        _unwatch(handler)


def test_a_tidalapi_error_raised_from_the_http_error_is_kept_out_too():
    class ObjectNotFound(Exception):
        pass

    def _process():
        try:
            raise _users_http_error(404)
        except requests.HTTPError as e:
            raise ObjectNotFound("Resource not found") from e

    handler = _watch()
    try:
        stub = _Stub(process=_process)
        stub.completeLogin(_GOOD)
        joined = "\n".join(handler.text)
        assert "987654321" not in joined
        # The status rides on the HTTPError underneath, not on tidalapi's own.
        assert "HTTP 404" in joined
        assert "step: session" in stub._login_error
    finally:
        _unwatch(handler)
