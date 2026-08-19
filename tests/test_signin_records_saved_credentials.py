"""A completed sign-in is recorded as saved, so the app can re-authenticate.

WHAT THIS FENCES OFF
--------------------
``login_finalize`` writes the credentials file, but the flag that says a saved
sign-in exists was set only in ``Tidal.__init__``: a command-line assumption,
where the process signs in once and exits and the next run re-reads the file on
the way up. A window that stays open does not get a next run, so after a first
launch on a new install (or a sign-out and back in) the session worked while the
flag stayed False.

``login_token`` opens on that flag. Every later re-authentication in the same
run therefore answered False without attempting anything, and the one that
re-authenticates is the Dolby Atmos switch: it swaps in the Atmos credentials
and signs in again. So every Atmos track in every download failed for the rest
of the run, printing "Atmos session authentication failed", and only quitting
the app fixed it. A restart-to-refresh dependency is exactly what this app does
not do.

HOW THIS STAYS FIXED
--------------------
The real ``WavesTidal`` finalizes a sign-in against a real credentials file in
a temp folder, and the symptom is then reproduced end to end through the REAL
``tidaler.config.Tidal.switch_to_atmos_session`` and the REAL
``WavesTidal.login_token``. Only tidalapi's own session object is a stand-in,
and it counts how many times it was actually asked to sign in, so "returned
False without trying" is distinguishable from "tried and failed".
"""

from __future__ import annotations

from types import SimpleNamespace

from tidaler.model.cfg import Token as ModelToken
from tidaler.waves_ui.session import WavesTidal


class _Session:
    """Just the tidalapi Session surface a sign-in and a re-sign-in touch."""

    def __init__(self, *, signed_in=True, reauth_ok=True):
        self._signed_in = signed_in
        self._reauth_ok = reauth_ok
        self.attempts = 0
        # Dummy fixture values, not real credentials (bandit S105/S106).
        self.token_type = "Bearer"  # noqa: S105
        self.access_token = "a"  # noqa: S105
        self.refresh_token = "r"  # noqa: S105
        self.expiry_time = 1.0
        self.audio_quality = "LOSSLESS"
        self.config = SimpleNamespace(
            client_id="normal-id",
            client_secret="normal-secret",  # noqa: S106
            # The PKCE pair is what the refresh path authenticates with, so the
            # Atmos swap moves it too and the stand-in has to carry it.
            client_id_pkce="normal-id-pkce",
            client_secret_pkce="normal-secret-pkce",  # noqa: S106
            quality="LOSSLESS",
        )

    def check_login(self) -> bool:
        return self._signed_in

    def load_oauth_session(self, *a, **k) -> bool:
        self.attempts += 1
        return self._reauth_ok

    def token_refresh(self, refresh_token) -> bool:
        """The swap forces a real refresh so the new client is exercised; a
        load alone never contacts TIDAL while the saved pass is still valid."""
        return self._reauth_ok


def _tidal(tmp_path, *, session=None):
    """A real WavesTidal with its config paths sandboxed into tmp_path, built
    without __init__ so no real session or real config folder is touched."""
    t = WavesTidal.__new__(WavesTidal)
    t.cls_model = ModelToken
    t.data = ModelToken()
    t.path_base = str(tmp_path)
    t.file_path = str(tmp_path / "creds.json")
    t.is_pkce = True
    t.is_atmos_session = False
    t.token_from_storage = False  # a fresh install: nothing saved yet
    t.session = session or _Session()
    t.original_client_id = t.session.config.client_id
    t.original_client_secret = t.session.config.client_secret
    t.original_client_id_pkce = t.session.config.client_id_pkce
    t.original_client_secret_pkce = t.session.config.client_secret_pkce
    t.settings = SimpleNamespace(data=SimpleNamespace(quality_audio="LOSSLESS"))
    return t


def test_a_fresh_install_starts_with_nothing_saved(tmp_path):
    """The premise. Without it the rest of this file proves nothing."""
    t = _tidal(tmp_path)
    assert t.token_from_storage is False
    assert not (tmp_path / "creds.json").exists()


def test_finishing_a_sign_in_records_it_as_saved(tmp_path):
    t = _tidal(tmp_path)
    assert t.login_finalize() is True
    assert (tmp_path / "creds.json").exists(), "the sign-in was not written"
    assert t.token_from_storage is True, "the file is on disk but the app does not believe it exists"


def test_a_sign_in_that_did_not_complete_records_nothing(tmp_path):
    t = _tidal(tmp_path, session=_Session(signed_in=False))
    assert t.login_finalize() is False
    assert t.token_from_storage is False
    assert not (tmp_path / "creds.json").exists()


def test_the_app_can_re_authenticate_after_signing_in(tmp_path):
    """The whole point of the flag: login_token opens on it. Before the fix it
    returned False having never asked TIDAL anything."""
    t = _tidal(tmp_path)
    t.login_finalize()
    assert t.login_token() is True
    assert t.session.attempts == 1, "the app did not even attempt to sign in again"


def test_dolby_atmos_works_without_restarting_the_app(tmp_path):
    """The user-visible symptom, through the REAL switch_to_atmos_session."""
    t = _tidal(tmp_path)
    t.login_finalize()
    assert t.switch_to_atmos_session() is True
    assert t.is_atmos_session is True


def test_every_atmos_track_used_to_fail_until_a_restart(tmp_path):
    """The same call with the sign-in NOT recorded, which is what the app did
    for the whole run: the switch fails, and it fails without asking TIDAL."""
    t = _tidal(tmp_path)
    t.login_finalize()
    t.token_from_storage = False  # the state the defect left behind
    assert t.switch_to_atmos_session() is False
    assert t.session.attempts == 0
    assert t.is_atmos_session is False


def test_the_normal_session_comes_back_after_atmos(tmp_path):
    """A download alternates Atmos and stereo tracks, so the switch has to work
    in both directions within one run, not just once."""
    t = _tidal(tmp_path)
    t.login_finalize()
    assert t.switch_to_atmos_session() is True
    assert t.restore_normal_session() is True
    assert t.is_atmos_session is False
    assert t.session.config.client_id == "normal-id"
    # Both pairs come back, not just the plain one.
    assert t.session.config.client_id_pkce == "normal-id-pkce"
