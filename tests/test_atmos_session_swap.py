"""The Atmos session swap must actually engage the Atmos client.

The bug this pins: ``switch_to_atmos_session`` used to move only the plain
client pair and then call ``login_token``, which loads the saved grant without
contacting TIDAL while the access pass is still valid. So the swap was a silent
no-op: the app kept requesting under the original PKCE client and TIDAL handed
back the stereo fallback for Atmos-only tracks. The fix moves the PKCE pair too
and forces a real refresh so the new client is exercised.

Hermetic and network-free: the real, unbound ``Tidal`` methods are bound onto a
stand-in whose session/config are plain namespaces, so the credential motion and
the forced refresh are observed directly.
"""

from __future__ import annotations

import types

import tidalapi

from waves.config import Tidal
from waves.constants import ATMOS_CLIENT_ID, ATMOS_CLIENT_SECRET, ATMOS_REQUEST_QUALITY

# Dummy fixture values, not real credentials (bandit S105).
_ORIG_ID = "orig-plain-id"
_ORIG_SECRET = "orig-plain-secret"  # noqa: S105
_ORIG_ID_PKCE = "orig-pkce-id"
_ORIG_SECRET_PKCE = "orig-pkce-secret"  # noqa: S105


class _FakeConfig:
    def __init__(self):
        self.client_id = _ORIG_ID
        self.client_secret = _ORIG_SECRET
        self.client_id_pkce = _ORIG_ID_PKCE
        self.client_secret_pkce = _ORIG_SECRET_PKCE


class _FakeSession:
    def __init__(self, *, refresh_ok: bool = True):
        self.config = _FakeConfig()
        self.audio_quality = tidalapi.Quality(tidalapi.Quality.high_lossless.value)
        self.refresh_token = "saved-refresh-credential"  # noqa: S105
        self._refresh_ok = refresh_ok
        self.refresh_calls: list[str] = []

    def token_refresh(self, refresh_token: str) -> bool:
        # Record which client was in place at the moment of the forced refresh.
        self.refresh_calls.append(self.config.client_id_pkce)
        return self._refresh_ok


def _make(*, login_ok: bool = True, refresh_ok: bool = True) -> Tidal:
    stub = Tidal.__new__(Tidal)
    stub.session = _FakeSession(refresh_ok=refresh_ok)
    stub.is_pkce = True
    stub.is_atmos_session = False
    stub.original_client_id = _ORIG_ID
    stub.original_client_secret = _ORIG_SECRET
    stub.original_client_id_pkce = _ORIG_ID_PKCE
    stub.original_client_secret_pkce = _ORIG_SECRET_PKCE
    stub.settings = types.SimpleNamespace(
        data=types.SimpleNamespace(quality_audio=tidalapi.Quality.high_lossless.value)
    )
    stub._login_calls: list[bool] = []

    def _login_token(do_pkce: bool = True) -> bool:
        stub._login_calls.append(do_pkce)
        return login_ok

    stub.login_token = _login_token
    return stub


def test_switch_moves_the_pkce_pair_and_forces_a_refresh():
    t = _make()
    assert t.switch_to_atmos_session() is True

    cfg = t.session.config
    # BOTH pairs move to the Atmos client (the PKCE pair is the regression).
    assert cfg.client_id == ATMOS_CLIENT_ID
    assert cfg.client_secret == ATMOS_CLIENT_SECRET
    assert cfg.client_id_pkce == ATMOS_CLIENT_ID
    assert cfg.client_secret_pkce == ATMOS_CLIENT_SECRET
    assert t.session.audio_quality == ATMOS_REQUEST_QUALITY
    assert t.is_atmos_session is True
    # A real refresh happened, under the Atmos client (not the old no-op).
    assert t.session.refresh_calls == [ATMOS_CLIENT_ID]


def test_a_settings_save_mid_switch_cannot_take_the_atmos_tier_back():
    """The Atmos switch writes the tier the Atmos client asks for and then
    re-authenticates, which is two network round trips (tens of seconds while
    TIDAL throttles). Saving an audio-quality change in Settings is a GUI-
    thread call on the SAME shared session, gated only on the "we are in Atmos"
    flag, and that flag used to go up only after the re-authentication: a save
    landing in that window wrote the user's stereo tier over the Atmos request,
    and the get_stream that followed asked at a tier the Atmos client never
    requests."""
    t = _make()
    seen: list[bool] = []

    def _refresh(refresh_token: str) -> bool:
        # The Settings SAVE lands here, mid-switch.
        seen.append(t.is_atmos_session)
        t.settings.data.quality_audio = tidalapi.Quality.hi_res_lossless.value
        t.settings_apply()
        return True

    t.session.token_refresh = _refresh

    assert t.switch_to_atmos_session() is True
    assert seen == [True], "the save saw a session that did not yet call itself Atmos"
    assert t.session.audio_quality == ATMOS_REQUEST_QUALITY


def test_a_settings_save_mid_restore_still_reaches_the_session():
    """The mirror image: while the flag is up a save is held off the session,
    so leaving it up across the restore's re-authentication meant a quality
    change saved during the restore reached the session nowhere at all."""
    t = _make()
    t.switch_to_atmos_session()

    def _refresh(refresh_token: str) -> bool:
        t.settings.data.quality_audio = tidalapi.Quality.hi_res_lossless.value
        t.settings_apply()
        return True

    t.session.token_refresh = _refresh

    assert t.restore_normal_session() is True
    assert t.session.audio_quality == tidalapi.Quality(tidalapi.Quality.hi_res_lossless.value)


def test_atmos_client_ships_no_secret():
    # The public id authenticates on its own; nothing private is shipped.
    assert ATMOS_CLIENT_SECRET == ""
    assert ATMOS_CLIENT_ID


def test_restore_puts_both_pairs_back_and_refreshes():
    t = _make()
    t.switch_to_atmos_session()
    t.session.refresh_calls.clear()

    assert t.restore_normal_session() is True

    cfg = t.session.config
    assert cfg.client_id == _ORIG_ID
    assert cfg.client_secret == _ORIG_SECRET
    assert cfg.client_id_pkce == _ORIG_ID_PKCE
    assert cfg.client_secret_pkce == _ORIG_SECRET_PKCE
    assert t.is_atmos_session is False
    # Refreshed back under the original client.
    assert t.session.refresh_calls == [_ORIG_ID_PKCE]


def test_a_failed_refresh_falls_back_to_normal():
    t = _make(refresh_ok=False)
    assert t.switch_to_atmos_session() is False
    # Left in normal mode with the original client restored.
    assert t.is_atmos_session is False
    assert t.session.config.client_id_pkce == _ORIG_ID_PKCE
    assert t.session.config.client_id == _ORIG_ID


def test_a_real_atmos_copy_settles_instead_of_re_fetching_forever():
    """Once Atmos really arrives, the delivered tier is LOW (that is what TIDAL
    reports for an Atmos manifest), which on the stereo scale is below every
    target the user can pick. The gate has to answer on the audio_mode, not the
    tier, or every save re-fetches the identical Atmos file. Pinned because the
    whole point of restoring delivery is undone if the copy never settles."""
    from waves.ownership import QUALITY_RANK
    from waves.waves_ui.backend import _copy_is_current, _delivers_atmos

    atmos_only = types.SimpleNamespace(audio_modes=["DOLBY_ATMOS"])
    rec_atmos = {"audio_mode": "DOLBY_ATMOS", "quality_tier": "LOW", "quality_rank": QUALITY_RANK["LOW"]}

    for atmos_on in (False, True):
        wants = _delivers_atmos(atmos_only, atmos_on)
        assert wants is True, "an Atmos-only track is fetched as Atmos either way"
        for target in ("HIGH", "LOSSLESS", "HI_RES_LOSSLESS"):
            assert _copy_is_current(rec_atmos, QUALITY_RANK[target], wants) is True, (
                f"an owned Atmos copy re-fetches at {target} with the setting " f"{'on' if atmos_on else 'off'}"
            )


def test_rebuilding_the_session_recaptures_every_original_the_swap_restores():
    """``_reset_tidal_session`` (after a sign-out) says it mirrors
    ``Tidal.__init__``. If it captures fewer originals than the swap restores,
    a sign-out leaves the Atmos restore reaching for a stale value. Pin the two
    sets against each other rather than a hand-written list, so adding a field
    to the constructor and forgetting the reset fails here."""
    import inspect
    import re

    from waves.waves_ui.backend import WavesBridge

    def _captured(src: str) -> set[str]:
        return set(re.findall(r"original_client_\w+", src))

    in_init = _captured(inspect.getsource(Tidal.__init__))
    in_reset = _captured(inspect.getsource(WavesBridge._reset_tidal_session))
    assert in_init, "guard is looking at the wrong constructor"
    assert in_init == in_reset, (
        "the session rebuild does not capture the same originals as the "
        f"constructor; missing {sorted(in_init - in_reset)}"
    )


def test_the_switch_leaves_a_breadcrumb_that_never_names_the_client(caplog):
    """User-visible actions log at INFO so they reach the crash-report
    breadcrumb ring, and the message is a category only: the Atmos client id
    must never appear in a log line."""
    import logging

    with caplog.at_level(logging.INFO, logger="waves.config"):
        _make().switch_to_atmos_session()
    text = "\n".join(r.message for r in caplog.records)
    assert "Dolby Atmos session engaged" in text
    assert ATMOS_CLIENT_ID not in text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="waves.config"):
        _make(refresh_ok=False).switch_to_atmos_session()
    records = [r for r in caplog.records if r.name == "waves.config"]
    assert any(r.levelno == logging.WARNING for r in records), "a failed switch is logged"
    assert ATMOS_CLIENT_ID not in "\n".join(r.message for r in records)


def test_a_second_switch_while_already_atmos_is_a_no_op():
    t = _make()
    t.switch_to_atmos_session()
    t.session.refresh_calls.clear()
    # Already in Atmos mode: no re-auth, no extra refresh.
    assert t.switch_to_atmos_session() is True
    assert t.session.refresh_calls == []
