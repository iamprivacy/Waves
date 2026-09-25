"""Zero-leakage tests for the diagnostics redactor.

The scrubber is recall-first: a missed identifier in a log a user attaches to
a public issue is strictly worse than an over-redaction. This corpus is
deliberately nasty (identity strings embedded mid-sentence, inside URLs,
JSON, Windows paths, tracebacks) and the assertions are absolute: after
scrubbing, none of the tagged values may survive anywhere in the output.

When a future feature introduces a new secret shape, add it here FIRST and
watch the test fail; then teach the redactor (usually one register_secret call
or one denylist entry).
"""

import getpass
import importlib
import os
import socket
import sys

import pytest


@pytest.fixture()
def diag():
    sys.modules.pop("waves.waves_ui.diagnostics", None)
    module = importlib.import_module("waves.waves_ui.diagnostics")
    yield module
    sys.modules.pop("waves.waves_ui.diagnostics", None)


# ---- identity tier: (input line, leaked fragments that MUST be gone) --------
IDENTITY_CORPUS = [
    # user paths, all OS spellings
    ("could not open /Users/carol.smith/Music/waves/track.flac", ["carol.smith"]),
    ("scan found /home/dave_99/library", ["dave_99"]),
    (r"error at C:\Users\Eve Adams\AppData\Local\Waves\settings.json", ["Eve Adams"]),
    # each half of a spaced profile name, not just the joined string
    (r"error at C:\Users\Eve Adams\AppData\Local\Waves\settings.json", ["Eve", "Adams"]),
    ("opened /home/Ann Lee Park/Music/a.flac", ["Ann", "Lee", "Park"]),
    (r"share path \\SERVER01\Users\frank\music unreachable", ["frank"]),
    ("mixed style C:/Users/gina.h/Downloads failed", ["gina.h"]),
    # a network share named by its host: Windows UNC (both slash spellings,
    # and the doubled form a repr produces) and a GNOME gvfs mount segment.
    # None of these goes through the /Volumes mount-point registration, so the
    # shape itself has to be scrubbed.
    (r"File operation failed (move a.flac -> \\nas01\mediashare\Artist\01.flac)", ["nas01", "mediashare"]),
    ("dialog gave //nas01/mediashare/Artist/01.flac as the folder", ["nas01", "mediashare"]),
    (r"OSError: [Errno 13] Permission denied: '\\\\nas01\\mediashare\\a.flac'", ["nas01", "mediashare"]),
    ("//nas01.local/mediashare on /mnt/music type cifs (rw)", ["nas01", "mediashare"]),
    (
        "folder /run/user/1000/gvfs/smb-share:server=nas01.local,share=mediashare/Artist/a.flac",
        ["nas01", "mediashare"],
    ),
    ("mount sftp:host=nas01.local,user=carol.s/Music unreachable", ["nas01", "carol.s"]),
    ("gvfs domain=WORKGROUP1,server=nas01,share=mediashare listed", ["WORKGROUP1", "nas01", "mediashare"]),
    # network identifiers
    ("connected from 192.168.1.44 to peer", ["192.168.1.44"]),
    ("listening on fe80::1c2a:3bff:fe4d:5e6f%en0", ["fe80::1c2a:3bff:fe4d:5e6f"]),
    ("interface mac AA:BB:CC:DD:EE:0F flapped", ["AA:BB:CC:DD:EE:0F"]),
    # email, including inside a URL query
    ("login as harry.p@example.co.uk failed", ["harry.p@example.co.uk"]),
    ("GET /verify?email=ida-j%40example.com&x=1 -> ok resent to ida-j@example.com", ["ida-j@example.com"]),
    # tokens and secrets in the usual syntaxes
    ('response {"access_token": "eyJhbGciOiJIUzI1NiJ9.payload.sig"} cached', ["eyJhbGciOiJIUzI1NiJ9"]),
    ("header Authorization: Bearer abc123DEF456ghi789 sent", ["abc123DEF456ghi789"]),
    ("retry with api_key=sk_live_9f8e7d6c5b4a3210 next", ["sk_live_9f8e7d6c5b4a3210"]),
    ("cookie sessionid=s3ss10nv4lu3xyz; path=/", ["s3ss10nv4lu3xyz"]),
    ("password = 'hunter2-but-long'", ["hunter2-but-long"]),
    # bare high-entropy blobs
    ("etag 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08", ["9f86d081884c7d659a2f"]),
    ("device 550e8400-e29b-41d4-a716-446655440000 registered", ["550e8400-e29b-41d4-a716-446655440000"]),
    # traceback path
    ('  File "/Users/karl/dev/waves/waves/download.py", line 42', ["karl"]),
    # third-party lines never pass through content(), so a URL query string is
    # the one place a search needle can reach the disk log unmarked: urllib3
    # logs the retried URL verbatim once the API session mounts a Retry policy
    (
        "Retrying (_ApiRetry(total=2)) after connection broken by "
        "'ConnectionResetError(54)': /v1/search?query=my+private+search&limit=3&types=TRACKS",
        ["my+private+search"],
    ),
    ("GET https://api.tidal.com/v1/pages/search?query=lana+del+rey -> 429", ["lana+del+rey"]),
    # A credential after a bare SPACE, which is the arm that had to be narrowed
    # (see the KEEP corpus below for what it was eating). Each of these still
    # has to go.
    ("sending auth token abc123def456ghi next", ["abc123def456ghi"]),
    ("api_key 7f3a91bcd0e4b2 rejected", ["7f3a91bcd0e4b2"]),
    ("set-cookie sessionblob0123456789abcdef stored", ["sessionblob0123456789abcdef"]),
    ("client_secret abcdefghijklmnopqrst rotated", ["abcdefghijklmnopqrst"]),
    ("refresh_token rt-9f8e7d6c5b4a expired", ["rt-9f8e7d6c5b4a"]),
]

# The other direction: what over-redaction must NOT eat. A word after a word is
# far more often a TITLE than a credential, and the bare-space arm treated every
# one of them as a value: a track called "Secret Song" logged as
# "Secret ‹redacted›", which destroys exactly the diagnostic value the content
# marker exists to preserve. (input line, fragments that must SURVIVE)
KEEP_CORPUS = [
    ("playing Secret Song by The Band", ["Secret Song"]),
    ("queued Token Ring live 1998", ["Token Ring"]),
    ("album The Secret History of Rock scanned", ["Secret History"]),
    ("downloading Auth Mode by Cipher", ["Auth Mode"]),
    ("skipped Password Kids single", ["Password Kids"]),
    # The UNC rule must not eat a URL's scheme separator or a doubled slash
    # inside an ordinary path.
    ("GET https://api.tidal.com/v1/tracks/123 -> 200", ["https://api.tidal.com/v1/tracks/123"]),
    ("scanning /Music//Artist/Album took 2s", ["/Music//Artist/Album"]),
]


@pytest.mark.parametrize(("line", "kept"), KEEP_CORPUS, ids=range(len(KEEP_CORPUS)))
def test_ordinary_titles_survive_the_scrub(diag, line, kept):
    out = diag.scrub(line)
    for fragment in kept:
        assert fragment in out, f"over-redacted {fragment!r} into {out!r}"


@pytest.mark.parametrize(("line", "leaks"), IDENTITY_CORPUS, ids=range(len(IDENTITY_CORPUS)))
def test_identity_pii_never_survives(diag, line, leaks):
    out = diag.scrub(line)
    for leak in leaks:
        assert leak not in out, f"leaked {leak!r} in {out!r}"


def test_this_machines_identity_never_survives(diag):
    """The real username, hostname and home directory of the machine running
    the tests must be scrubbed wherever they appear."""
    user = getpass.getuser()
    host = socket.gethostname()
    home = os.path.expanduser("~")
    line = f"probe user={user} host={host} wrote {home}/Music/x.flac and {home}"
    out = diag.scrub(line)
    if len(user) >= 3:
        assert user not in out
    if len(host) >= 3:
        assert host not in out
    assert home not in out


def test_registered_secret_is_replaced_everywhere(diag):
    diag.register_secret("6021985477", "‹account›")
    out = diag.scrub("subscription check for user 6021985477 returned 401 (id=6021985477)")
    assert "6021985477" not in out
    assert "‹account›" in out


def test_registered_share_origin_never_survives(diag):
    """A recorded network-share origin (netmount) carries a hostname and maybe
    a username; once registered it must be gone from every log form it could
    appear in: the raw statfs from-name and the derived mount URL."""
    diag.register_secret("//carol@nas-box._smb._tcp.local/Media", "‹share-origin›")
    diag.register_secret("smb://carol@nas-box._smb._tcp.local/Media", "‹share-origin›")
    out = diag.scrub(
        "mount check: smb://carol@nas-box._smb._tcp.local/Media from //carol@nas-box._smb._tcp.local/Media"
    )
    assert "carol" not in out
    assert "nas-box" not in out
    assert "‹share-origin›" in out


def test_smb_relist_share_and_mount_point_never_survive(diag):
    """The private-relist workaround (smb_relist) derives a share URL and makes
    a mount point under the config dir, and hands the URL to mount_smbfs. A
    timeout there renders the whole argv, and an OSError renders the path, so
    both are registered where they are made. Neither may reach a log, in any
    form the exception text would print them.
    """
    url = "smb://carol@nas-box._smb._tcp.local/Media"
    point = "/Users/carol/Library/Application Support/Waves/relist-mounts/pid-4821"
    diag.register_secret(url, "‹share-origin›")
    diag.register_secret(point, "‹mount-point›")
    out = diag.scrub(
        f"Command '['/sbin/mount_smbfs', '-N', '-o', 'ro,nobrowse,soft', '{url}', '{point}']'"
        f" timed out after 20 seconds; listing {point}/Music failed"
    )
    assert "carol" not in out
    assert "nas-box" not in out
    assert "relist-mounts" not in out
    assert "‹share-origin›" in out
    assert "‹mount-point›" in out


def test_short_secrets_are_ignored(diag):
    diag.register_secret("ab")  # too short: literal-replacing it would shred text
    assert diag.scrub("about") == "about"


def test_scrub_is_idempotent(diag):
    line = "user /Users/carol/x from 10.0.0.7 token=deadbeefcafe1234deadbeefcafe1234"
    once = diag.scrub(line)
    assert diag.scrub(once) == once


def test_timestamps_survive(diag):
    """Clock times must not be eaten by the IPv6 pattern."""
    out = diag.scrub("14:23:01.123  WARN  [slow] search took 2.31s")
    assert "14:23:01" in out


def test_url_query_is_dropped_but_the_path_survives(diag):
    """A query string is dropped whole (it can hold a search term, an email or
    an id), while the path in front of it stays: that is what makes a retry or
    error line diagnosable at all."""
    out = diag.scrub("Retrying after connection broken: /v1/search?query=daft+punk&limit=3")
    assert "daft+punk" not in out
    assert "/v1/search" in out
    assert diag.scrub(out) == out  # idempotent, like every other pass


def test_content_tier_hashes_marked_spans_only(diag):
    line = f"search needle={diag.content('daft punk')} n=137"
    identity_only = diag.scrub(line)
    assert "daft punk" in identity_only  # default: content stays readable
    full = diag.scrub(line, redact_content=True)
    assert "daft punk" not in full
    assert "n=137" in full  # only the marked span is hashed
    # Same content hashes to the same tag, so patterns stay visible.
    assert diag.scrub(line, redact_content=True) == full


def test_breadcrumb_ring_is_bounded_and_drop_oldest(diag):
    ring = diag._BreadcrumbHandler(capacity=5).ring
    for i in range(9):
        ring.append(f"line{i}")
    assert len(ring) == 5
    assert ring[0] == "line4"  # oldest dropped, newest kept


def test_redacting_filter_scrubs_formatted_records(diag):
    import logging

    rec = logging.LogRecord("waves.t", logging.INFO, "", 0, "path %s hit", ("/Users/nina/a.flac",), None)
    assert diag._RedactingFilter().filter(rec) is True
    assert "nina" not in rec.getMessage()


def test_installer_failure_text_shown_to_the_user_is_scrubbed():
    # The FFmpeg gate and the update toast render the failure message of the
    # installer or updater. An OSError names the staging file under the home
    # directory, so the emit went through str(exc) unscrubbed (front-end
    # audit 2026-09-17, G5). Both emits now go through _user_error.
    from waves.waves_ui.backend import _user_error

    exc = PermissionError(13, "Permission denied", "/Users/nina/Library/Application Support/waves/ffmpeg.tmp")
    shown = _user_error(exc, "Install failed")
    assert "nina" not in shown and "/" not in shown
    # Final audit C05: the UI gets a plain sentence for the failure's kind,
    # never the library's own text; the raw exception stays in the log.
    assert shown == "Waves could not write to its install folder"
    raw = OSError("[Errno 13] Permission denied: '/Users/nina/Library/Application Support/waves/ffmpeg.tmp'")
    assert _user_error(raw, "Install failed") == "Install failed"
    assert _user_error(RuntimeError(""), "Install failed") == "Install failed"


def test_content_markers_inside_a_title_cannot_end_the_span(diag):
    """A title carrying a marker character ended the span early, and one over
    400 characters matched nothing, so the content switch left them readable."""
    for needle in ("Nothing » Everything", "Je t'aime «moi non plus", "x" * 900):
        line = f"queued {diag.content(needle)} n=1"
        full = diag.scrub(line, redact_content=True)
        assert "Everything" not in full and "moi non plus" not in full and "xxxx" not in full, needle
        assert "n=1" in full


def test_a_share_mount_point_never_survives(diag):
    """The download folder on a NAS is /Volumes/<ShareName>/...: outside the
    home folder the redactor folds, so every path under it carried the share
    name until the bridge registered the mount point where it notes the share
    origin."""
    diag.register_secret("/Volumes/CarolsMediaShare", "‹mount-point›")
    out = diag.scrub("could not write /Volumes/CarolsMediaShare/Music/Artist/Album/01 Song.flac")
    assert "CarolsMediaShare" not in out
    assert "‹mount-point›" in out
