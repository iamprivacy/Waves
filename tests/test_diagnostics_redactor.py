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
    sys.modules.pop("tidaler.waves_ui.diagnostics", None)
    module = importlib.import_module("tidaler.waves_ui.diagnostics")
    yield module
    sys.modules.pop("tidaler.waves_ui.diagnostics", None)


# ---- identity tier: (input line, leaked fragments that MUST be gone) --------
IDENTITY_CORPUS = [
    # user paths, all OS spellings
    ("could not open /Users/carol.smith/Music/waves/track.flac", ["carol.smith"]),
    ("scan found /home/dave_99/library", ["dave_99"]),
    (r"error at C:\Users\Eve Adams\AppData\Local\Waves\settings.json", ["Eve Adams"]),
    (r"share path \\SERVER01\Users\frank\music unreachable", ["frank"]),
    ("mixed style C:/Users/gina.h/Downloads failed", ["gina.h"]),
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
    ('  File "/Users/karl/dev/waves/tidaler/download.py", line 42', ["karl"]),
]


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
