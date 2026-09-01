import contextlib
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Callable
from json import JSONDecodeError
from pathlib import Path
from threading import Event, Lock
from typing import Any

import tidalapi
from requests.adapters import HTTPAdapter, Retry
from urllib3.exceptions import InvalidHeader

from waves.constants import (
    ATMOS_CLIENT_ID,
    ATMOS_CLIENT_SECRET,
    ATMOS_REQUEST_QUALITY,
    REQUESTS_TIMEOUT_SEC,
)
from waves.helper.decorator import SingletonMeta
from waves.helper.path import path_config_base, path_file_settings, path_file_token
from waves.model.cfg import Settings as ModelSettings
from waves.model.cfg import Token as ModelToken

logger = logging.getLogger("waves.config")

# Windows answers os.replace with a sharing violation (WinError 32) while ANY
# other process holds the target open: an antivirus scanning the file, a backup
# tool syncing Roaming, or a second app instance. Those locks are usually gone
# within a moment, so the swap is retried briefly before the error is real.
_REPLACE_ATTEMPTS = 5
_REPLACE_RETRY_DELAY_SEC = 0.2


def _replace_with_retry(tmp_path: str, file_path: str) -> None:
    """os.replace with a short bounded retry for transient Windows file locks.

    On final failure the temp file is removed (it is this process's own
    throwaway sibling) and the error propagates to the caller.
    """
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(tmp_path, file_path)
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                with contextlib.suppress(OSError):
                    os.remove(tmp_path)
                raise
            time.sleep(_REPLACE_RETRY_DELAY_SEC)
        else:
            return


class BaseConfig:
    data: ModelSettings | ModelToken
    file_path: str
    cls_model: ModelSettings | ModelToken
    path_base: str = path_config_base()

    def save(self, config_to_compare: str = None) -> None:
        data_json = self.data.to_json()

        # If old and current config is equal, skip the write operation.
        if config_to_compare == data_json:
            return

        self.write_serialized(data_json)

    def write_serialized(self, data_json: str) -> None:
        """The disk half of :meth:`save`, for a caller that serialized the data
        itself (the GUI snapshots the JSON on its thread, microseconds, and
        hands only this fsync-bearing part to a background writer)."""
        # Try to create the base folder.
        os.makedirs(self.path_base, exist_ok=True)

        # Write atomically. A settings.json or token.json truncated by a crash
        # mid-write corrupts the config or loses the login. Serialize to a temp
        # sibling, flush it to disk, then os.replace (atomic on POSIX and
        # Windows), so a reader (or the next launch) only ever sees a complete
        # file. This mirrors the page_cache write in the Waves bridge.
        obj_json_config = json.loads(data_json)  # pretty format
        # A temp sibling of this write's OWN, not one fixed name. Nothing stops
        # a second copy of Waves running against the same config folder (the
        # launch path contemplates one), and both staged through
        # "settings.json.tmp": open() truncates and each writer flushes its own
        # length, so the two interleaved into one file and whichever os.replace
        # landed last published the mixture. The next launch called that
        # corrupt, moved it to .bak and started on factory defaults, taking the
        # download folder, the templates and the quality with it; the identical
        # shape on token.json signs the user out.
        fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(self.file_path) or ".",
            prefix=f"{os.path.basename(self.file_path)}.",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, mode="w", encoding="utf-8") as f:
                json.dump(obj_json_config, f, indent=4)
                f.flush()
                os.fsync(f.fileno())
        except BaseException:
            # Ours and nobody else's, so a failed write takes it with it rather
            # than leaving a stray sibling in the user's config folder.
            with contextlib.suppress(OSError):
                os.remove(tmp_path)
            raise
        _replace_with_retry(tmp_path, self.file_path)

    def set_option(self, key: str, value: Any) -> None:
        value_old: Any = getattr(self.data, key)

        if type(value_old) == bool:  # noqa: E721
            value = True if value.lower() in ("true", "1", "yes", "y") else False  # noqa: SIM210
        elif type(value_old) == int and type(value) != int:  # noqa: E721
            value = int(value)

        setattr(self.data, key, value)

    def read(self, path: str) -> bool:
        result: bool = False
        settings_json: str = ""

        try:
            with open(path, encoding="utf-8") as f:
                settings_json = f.read()

            self.data = self.cls_model.from_json(settings_json)
            result = True
        except (JSONDecodeError, TypeError, FileNotFoundError, ValueError, AttributeError) as e:
            # AttributeError is what a file of valid JSON whose top level is not
            # an object raises: dataclasses_json asks the parsed value for
            # .items(), and "[]", "null", a bare string or a number has none. It
            # crashed every launch with a traceback, past the very self-heal this
            # arm exists to do, and only deleting the file by hand recovered the
            # app. A file that is there and unusable is a broken config whatever
            # shape it is broken in.
            if isinstance(e, ValueError | AttributeError):
                path_bak = path + ".bak"

                # First check if a backup file already exists. If yes, remove it.
                if os.path.exists(path_bak):
                    os.remove(path_bak)

                # Move the invalid config file to the backup location.
                shutil.move(path, path_bak)
                print(
                    "Something is wrong with your config. Maybe it is not compatible anymore due to a new app version."
                    f" You can find a backup of your old config here: '{path_bak}'. A new default config was created."
                )

            self.data = self.cls_model()

        # Call save in case of we need to update the saved config, due to changes in code.
        # This write-back is an optional upgrade persist: if another process
        # still holds the file after the retries (a second instance, an
        # overzealous antivirus), the app must start on the in-memory settings
        # instead of dying with an uncaught PermissionError at launch. The
        # next successful save persists the same upgrades.
        try:
            self.save(settings_json)
        except OSError as e:
            logger.warning(
                "Config write-back blocked by another process; continuing with in-memory settings (%s)",
                type(e).__name__,
            )

        return result


# The longest pause anyone could mean by "how long to pause between batches of
# songs". Above this it is a leftover from when the field counted albums and
# paced nothing, and it costs a long list half an hour of standing still.
_RATE_LIMIT_PAUSE_PLAUSIBLE_MAX_SEC: float = 30.0


def _migrate_settings(data: ModelSettings) -> bool:
    """Apply one-time upgrade steps to an already-loaded settings model.

    Returns True when something changed, so the caller persists it. Each step is
    guarded by a marker stored in the config, so it runs at most once and never
    overrides a choice the user makes afterwards.
    """
    changed = False

    # ReplayGain became on-by-default. Configs created before that carry an
    # explicit False that is really just the old default, so switch them on once.
    # A user who turns it back off later keeps it off: the marker stops this from
    # firing again.
    if not data.replay_gain_default_migrated:
        data.metadata_replay_gain = True
        data.replay_gain_default_migrated = True
        changed = True

    # The playlist template default grew {folder_path}. Defaults are persisted
    # verbatim, so an untouched install stores the old default string: only
    # that exact value is upgraded. Anything else is a customized template the
    # user owns, and it is never rewritten (they can add {folder_path} where
    # they want it).
    if not data.format_playlist_folder_migrated:
        old_default = "Playlists/{playlist_name}/{list_pos}. {artist_name} - {track_title}"
        if data.format_playlist == old_default:
            data.format_playlist = ModelSettings().format_playlist
        data.format_playlist_folder_migrated = True
        changed = True

    # The two rate-limit fields sat in Advanced while nothing read them, and
    # they asked a different question then ("albums to process"), so a value on
    # disk is a guess about something else that never took effect. Now that
    # they pace real downloads, a leftover of a minute between batches would
    # quietly add half an hour to a long playlist, so a pause no answer to the
    # new question would give goes back to the default.
    #
    # It resets THAT and nothing else, because the marker cannot be relied on
    # to keep this to one run: the marker is a field the previous release does
    # not have, and that release rewrites settings.json from its own model on
    # every launch, so a downgrade and back strips it and this runs again. A
    # pace the user has since tuned is theirs, and stands.
    if not data.api_rate_limit_wired_migrated:
        if data.api_rate_limit_delay_sec > _RATE_LIMIT_PAUSE_PLAUSIBLE_MAX_SEC:
            data.api_rate_limit_delay_sec = ModelSettings().api_rate_limit_delay_sec
        data.api_rate_limit_wired_migrated = True
        changed = True

    return changed


class Settings(BaseConfig, metaclass=SingletonMeta):
    def __init__(self):
        self.cls_model = ModelSettings
        self.file_path = path_file_settings()
        self.read(self.file_path)
        if _migrate_settings(self.data):
            # Same degrade as read()'s write-back: a still-locked file must not
            # abort startup; the migrations live in memory and persist on the
            # next successful save (their markers keep them one-time).
            try:
                self.save()
            except OSError as e:
                logger.warning(
                    "Settings migration persist blocked by another process; continuing in memory (%s)",
                    type(e).__name__,
                )


# Retry policy for api.tidal.com. Every catalog call the download engine makes
# (the track re-fetch, its album, the playback request) went out exactly once:
# tidalapi mounts no adapter, so the first 429 or 5xx failed that track, and a
# failed track fails the collection around it. A 12-track album never noticed;
# a 500-track playlist makes some 1500 calls in a row and noticed every time
# (issue #35). Nothing that REACHED TIDAL is ever sent twice: a status or read
# retry is GET/HEAD only, so a sign-in or a playlist edit is never resubmitted,
# and a connect retry can only repeat a request whose connection never opened.
# TIDAL's own Retry-After wins over the backoff.
_API_RETRY_TOTAL: int = 3
# urllib3's ladder from this factor is 0, 3, 6 seconds: its first retry is
# always immediate (get_backoff_time returns 0 for it), which is the right
# answer to a one-off blip. The jitter spreads the retries after that one, so
# workers that fell back together do not climb the ladder in step.
#
# What it does NOT do, whatever it looks like it promises: de-synchronize
# workers that met one rate limit together. A 429 carrying Retry-After is slept
# for the header's own (capped) value and never touches the backoff at all, so
# all three wake at the same instant by design, TIDAL's design. And on a
# header-less 429 the first retry is the flat 0 above, before any jitter
# applies. The pace gate in download.py is what actually holds workers apart
# under throttling; this is only the ladder's own spread.
_API_RETRY_BACKOFF_SEC: float = 1.5
_API_RETRY_STATUS = (429, 500, 502, 503, 504)
# Longest one Retry-After may hold a call. urllib3 honours the header verbatim
# and caps nothing, so a single answer could park a download worker (and, with
# the queue running one job at a time, the queue behind it) for as long as it
# says. Waiting a little is the point of honouring it; waiting a quarter of an
# hour is a hang with an explanation, and the track is better off failing and
# being retried by hand.
#
# It also bounds how long one call can be held: with _API_RETRY_TOTAL that is
# at most half a minute per request, and only while TIDAL is actively
# throttling. A STOP does not have to wait even that long: the waits below are
# taken on the download's own abort event where there is one, so a stopped job
# comes out of a rate-limit wait at once (see api_waits_wake_for).
_API_RETRY_AFTER_MAX_SEC: float = 10.0

# The event a retry wait on THIS thread may be cut short by. Thread-local
# because the catalog session is shared by everything (searches, the browse
# pages, every download worker) while an abort belongs to one job: the worker
# that is about to make a call is the only place that knows which.
_api_waits = threading.local()


class ApiCallStopped(Exception):
    """A catalog retry refused because the download it belongs to was stopped.

    Raised from the retry wait, which is the last place the ladder can still
    decide not to make the call. Callers treat it like any other failed
    catalog call: the item they were working on is over anyway, which is what
    the STOP said.
    """

    def __init__(self, message: str = "the download was stopped; not retrying") -> None:
        super().__init__(message)


def api_waits_wake_for(event) -> None:
    """Let ``event`` cut short the catalog session's retry waits on this thread.

    urllib3 sleeps between retries on the wall clock, and with the queue
    running one job at a time that sleep is in front of the whole queue: after
    STOP, each worker still finished its ladder (up to 30 seconds per request,
    a minute for the two calls a track costs, and the workers serialize), so
    the next thing the user queued sat at Queued with nothing running for a
    minute or more. Every deliberate wait the app itself takes already wakes
    for a STOP (see Download._sleep_politely); this is the one that did not.

    Called by the engine as each item's API traffic begins, so a pooled thread
    always carries the event of the job it is working for.
    """
    _api_waits.event = event


class _ApiRetry(Retry):
    """A Retry that cannot be told to wait indefinitely, or to fall over.

    Two things happen to Retry-After here, and everything else is urllib3's.
    It is capped, because urllib3's own ceiling is six hours. And a value it
    cannot parse is read as no value at all rather than raised: urllib3 answers
    anything that is not a plain count of seconds or an HTTP date with
    InvalidHeader, and mounting a retry policy is what newly exposed us to
    that, so a proxy's "60s" or a captive portal's prose would leave a catalog
    call as an exception no caller expects in place of the 429 they handle.
    Unreadable means the backoff decides, which is what happens with no header.

    ``Retry.new`` rebuilds through ``type(self)``, so both survive every
    attempt of a retried call.
    """

    def get_retry_after(self, response) -> float | None:
        try:
            after = super().get_retry_after(response)
        except InvalidHeader:
            return None
        return None if after is None else min(after, _API_RETRY_AFTER_MAX_SEC)

    def sleep(self, response=None) -> None:
        """urllib3's own wait, taken on the caller's abort event when it has
        one, so a stopped download is not held by a retry ladder it no longer
        has any reason to finish. Same durations, same order (a Retry-After
        wins over the backoff); only what is waited ON differs.

        And a wait that ends because the job was STOPPED does not return: it
        raises. urllib3 reads a returned sleep as "the wait is over" and fires
        the next attempt at once, so cutting the waits short freed the queue
        (which is what it was for) and turned the ladder into a burst, twenty
        further requests inside a hundredth of a second at the one moment
        TIDAL is already throttling us. This hook is the last place the ladder
        can still decide not to make the call.
        """
        event = getattr(_api_waits, "event", None)
        stopped = event is not None and event.is_set()
        if stopped:
            # Already stopped before the wait even starts, which is also the
            # zero-backoff case (urllib3 would not have waited at all, so the
            # check after the wait below would never see it).
            raise ApiCallStopped
        seconds = None
        if self.respect_retry_after_header and response is not None:
            seconds = self.get_retry_after(response)
        if not seconds:  # no header, or one that says no time at all: urllib3 falls through to the backoff
            seconds = self.get_backoff_time()
        if seconds <= 0:
            return
        if event is None:
            time.sleep(seconds)
            return
        event.wait(seconds)
        if event.is_set():
            raise ApiCallStopped


class _ApiAdapter(HTTPAdapter):
    """The catalog adapter: bounded retries, plus a timeout on every call.

    tidalapi passes no timeout, so a black-holed connection parks a download
    worker forever and, with the queue running one job at a time, the whole
    queue with it. requests only applies a default when the caller gave none,
    which is exactly the gap this fills.
    """

    def send(self, request, **kwargs):
        if kwargs.get("timeout") is None:
            kwargs["timeout"] = REQUESTS_TIMEOUT_SEC
        return super().send(request, **kwargs)


def harden_api_session(session: tidalapi.Session) -> None:
    """Give a tidalapi session the retry and timeout policy it ships without.

    ``raise_on_status`` stays False so the last answer comes back as a
    response: tidalapi's own ``raise_for_status`` then turns it into the very
    same TooManyRequests / HTTPError the callers already handle, and the only
    difference is that it took several tries to get there.
    """
    retry = _ApiRetry(
        total=_API_RETRY_TOTAL,
        connect=_API_RETRY_TOTAL,
        read=_API_RETRY_TOTAL,
        status=_API_RETRY_TOTAL,
        backoff_factor=_API_RETRY_BACKOFF_SEC,
        backoff_jitter=_API_RETRY_BACKOFF_SEC,
        status_forcelist=_API_RETRY_STATUS,
        allowed_methods=frozenset({"GET", "HEAD"}),
        respect_retry_after_header=True,
        raise_on_status=False,
    )
    adapter = _ApiAdapter(max_retries=retry)
    session.request_session.mount("https://", adapter)
    session.request_session.mount("http://", adapter)


class Tidal(BaseConfig, metaclass=SingletonMeta):
    session: tidalapi.Session
    token_from_storage: bool = False
    settings: Settings
    is_pkce: bool

    def __init__(self, settings: Settings = None):
        self.cls_model = ModelToken
        tidal_config: tidalapi.Config = tidalapi.Config(item_limit=10000)
        self.session = tidalapi.Session(tidal_config)
        harden_api_session(self.session)
        self.original_client_id = self.session.config.client_id
        self.original_client_secret = self.session.config.client_secret
        # The PKCE pair is a separate set of fields, and it is the one that the
        # refresh path actually authenticates with when the app is signed in
        # via PKCE (which it always is). The Atmos swap has to move these too,
        # so keep the originals to swap back to.
        self.original_client_id_pkce = self.session.config.client_id_pkce
        self.original_client_secret_pkce = self.session.config.client_secret_pkce
        # Lock to ensure session-switching is thread-safe.
        # This lock protects against a race condition where one thread
        # changes the session credentials while another is using them.
        # It is intentionally held by Download._get_stream_info
        # for the *entire* duration of the credential switch AND
        # the get_stream() call.
        self.stream_lock = Lock()
        # State-tracking flag to prevent redundant, expensive
        # session re-authentication when the session is already in the
        # correct mode (Atmos or Normal).
        self.is_atmos_session = False
        # Called with this session whenever a credential on it has just been
        # minted, so the log redactor can be taught it at the moment it exists
        # (a secret is registered where it is acquired). A hook rather than an
        # import, so the config layer goes on knowing nothing about the UI, and
        # None by default so a headless run needs nothing.
        self.on_session_credentials = None
        self.file_path = path_file_token()
        self.token_from_storage = self.read(self.file_path)

        if settings:
            self.settings = settings
            self.settings_apply()

    def settings_apply(self, settings: Settings = None) -> bool:
        if settings:
            self.settings = settings

        if not self.is_atmos_session:
            self.session.audio_quality = tidalapi.Quality(self.settings.data.quality_audio)
        self.session.video_quality = tidalapi.VideoQuality.high

        return True

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
            except Exception:
                result = False
                # Remove token file. Probably corrupt or invalid.
                if os.path.exists(self.file_path):
                    os.remove(self.file_path)

                print(
                    "Either there is something wrong with your credentials / account or some server problems on TIDALs "
                    "side. Anyway... Try to login again by re-starting this app."
                )

        return result

    def login_finalize(self) -> bool:
        result = self.session.check_login()

        if result:
            self.token_persist()

        return result

    def _note_session_credentials(self) -> None:
        """Tell the listener (if any) that this session's credentials are new.

        Best-effort in every direction: a listener that raises must never take
        a login or a quality switch down with it.
        """
        sink = getattr(self, "on_session_credentials", None)

        if sink is None:
            return

        with contextlib.suppress(Exception):
            sink(self.session)

    def token_persist(self) -> None:
        self.set_option("token_type", self.session.token_type)
        self.set_option("access_token", self.session.access_token)
        self.set_option("refresh_token", self.session.refresh_token)
        self.set_option("expiry_time", self.session.expiry_time)
        self.save()

        # Set restrictive permissions on token file (Unix-based systems only)
        with contextlib.suppress(OSError, NotImplementedError):
            os.chmod(self.file_path, 0o600)

        self._note_session_credentials()

    def _reauthenticate_current_client(self) -> bool:
        """Make the currently set client credentials actually take effect.

        A client swap alone does nothing: ``login_token`` loads the saved
        grant through ``load_oauth_session``, which only contacts TIDAL when
        the stored access pass is already rejected. While that pass is still
        valid (the normal case) the swap is a silent no-op, so the request
        keeps going out under the old client. Forcing a refresh here exchanges
        the saved refresh credential for a fresh access pass under the client
        that is set right now, which is what proves (or disproves) that client.

        The refresh result is deliberately not persisted: the saved sign-in on
        disk stays the user's own, so an Atmos swap can never overwrite it.
        """
        try:
            if not self.login_token(do_pkce=self.is_pkce):
                return False
            refreshed = bool(self.session.token_refresh(self.session.refresh_token))
        except Exception as exc:
            # A category, never the credential: the exception type says what
            # kind of failure it was without naming any client id or endpoint.
            logger.warning("Session re-authentication raised (%s)", type(exc).__name__)
            return False
        else:
            # Not persisted (see above), but very much acquired: every Atmos
            # switch and restore comes through here, so without this the live
            # access pass after the first Atmos album was one the redactor had
            # never been told about.
            self._note_session_credentials()
            return refreshed

    def switch_to_atmos_session(self) -> bool:
        """
        Switches the shared session to Dolby Atmos credentials.
        Only re-authenticates if not already in Atmos mode.

        Returns:
            bool: True if successful or already in Atmos mode, False otherwise.
        """
        # If we are already in Atmos mode, do nothing.
        if self.is_atmos_session:
            return True

        print("Switching session context to Dolby Atmos...")
        # Move BOTH client pairs: the plain fields and the PKCE fields. The app
        # signs in via PKCE, so the refresh path reads the PKCE pair; setting
        # only the plain pair (the old behaviour) left the refresh reaching for
        # the original client and never actually engaged Atmos.
        self.session.config.client_id = ATMOS_CLIENT_ID
        self.session.config.client_secret = ATMOS_CLIENT_SECRET
        self.session.config.client_id_pkce = ATMOS_CLIENT_ID
        self.session.config.client_secret_pkce = ATMOS_CLIENT_SECRET
        self.session.audio_quality = ATMOS_REQUEST_QUALITY
        # Raised HERE, not after the re-authentication below. The flag is what
        # stops a Settings save from writing the user's stereo tier over the
        # Atmos request on the shared session, and the re-authentication is two
        # network round trips (tens of seconds while TIDAL throttles): a save
        # landing in that window used to sail through the gate, and the Atmos
        # get_stream that followed asked at a tier the Atmos client never
        # requests. Lowered again by the restore below if the switch fails.
        self.is_atmos_session = True

        # Re-authenticate under the new client (a real refresh, not just a load).
        if not self._reauthenticate_current_client():
            print("Warning: Atmos session authentication failed.")
            logger.warning("Dolby Atmos session authentication failed; restoring the normal session")
            # Try to switch back to normal to be safe
            self.restore_normal_session(force=True)
            return False

        print("Session is now in Atmos mode.")
        logger.info("Dolby Atmos session engaged")
        return True

    def restore_normal_session(self, force: bool = False) -> bool:
        """
        Restores the shared session to the original user credentials.
        Only re-authenticates if not already in Normal mode.

        Args:
            force: If True, forces restoration even if already in Normal mode.

        Returns:
            bool: True if successful or already in Normal mode, False otherwise.
        """
        # If we are already in Normal mode (and not forced), do nothing.
        if not self.is_atmos_session and not force:
            return True

        print("Restoring session context to Normal...")
        self.session.config.client_id = self.original_client_id
        self.session.config.client_secret = self.original_client_secret
        self.session.config.client_id_pkce = self.original_client_id_pkce
        self.session.config.client_secret_pkce = self.original_client_secret_pkce

        # Lowered BEFORE the tier is written, and the tier read right next to
        # its write: while the flag is up a Settings save is held off the
        # session, so leaving it up across the re-authentication below meant a
        # quality change saved during the restore reached the session nowhere
        # at all and the session kept the tier from before it.
        self.is_atmos_session = False
        # Explicitly restore audio quality to user's configured setting
        self.session.audio_quality = tidalapi.Quality(self.settings.data.quality_audio)

        # Re-authenticate under the original client.
        if not self._reauthenticate_current_client():
            print("Warning: Restoring the original session context failed. Please restart the application.")
            logger.warning("Restoring the normal session failed")
            return False

        print("Session is now in Normal mode.")
        logger.info("Normal session restored")
        return True

    def login(self, fn_print: Callable, fn_input: Callable) -> bool:
        is_token = self.login_token()
        result = False

        if is_token:
            fn_print("Yep, looks good! You are logged in.")

            result = True
        elif not is_token:
            fn_print("You either do not have a token or your token is invalid.")
            fn_print("No worries, we will handle this...")

            # Login method: PKCE authorization (tidal was being weird and downgrading quality)
            self.session.login_pkce(fn_print)

            is_login = self.login_finalize()

            if is_login:
                fn_print("The login was successful. I have stored your credentials (token).")

                result = True
            else:
                fn_print("Something went wrong. Did you login using your browser correctly? May try again...")

        return result

    def logout(self):
        Path(self.file_path).unlink(missing_ok=True)
        self.token_from_storage = False
        del self.session

        return True

    def is_authentication_error(self, error: Exception) -> bool:
        """Check if an error is related to authentication/OAuth issues.

        Args:
            error (Exception): The exception to check.

        Returns:
            bool: True if the error is authentication-related, False otherwise.
        """
        error_msg = str(error)
        return "401" in error_msg or "OAuth" in error_msg or "token" in error_msg.lower()


class HandlingApp(metaclass=SingletonMeta):
    event_abort: Event = Event()
    event_run: Event = Event()

    def __init__(self):
        self.event_run.set()
