"""Regression tests for _collection_incomplete_reason (partial-download DONE).

A finished collection download used to be reported as a clean "done" whenever
any track was written, so a 19-of-20 album (one track failed) rode its successes
to a green done and hid the missing track. The outcome is now judged from the
per-track counters: any failure surfaces the shortfall; an all-owned collection
(only ownership skips, no failures) is still a real success.
"""

from __future__ import annotations

import pytest

from tidaler.waves_ui.backend import _collection_incomplete_reason


@pytest.mark.parametrize(
    ("write_count", "ok_count", "fail_count", "expected"),
    [
        # write, ok, fail -> reason (None means "real success")
        (20, 20, 0, None),  # every new track downloaded
        (0, 10, 0, None),  # every track already owned (skips count as ok)
        (5, 5, 0, None),  # a small fully-successful new download
        (19, 19, 1, "1 of 20 tracks failed"),  # THE FIX: was a silent green done
        (0, 3, 2, "2 of 5 tracks failed"),  # partly owned, some new tracks failed
        (0, 0, 5, "5 of 5 tracks failed"),  # every track failed
        (0, 0, 0, "no tracks were downloaded"),  # nothing handled at all
    ],
)
def test_collection_incomplete_reason(write_count, ok_count, fail_count, expected):
    assert _collection_incomplete_reason(write_count, ok_count, fail_count) == expected


def test_a_single_failure_is_surfaced_even_with_many_successes():
    # Explicit guard for the reported bug: successes must not mask a failure.
    assert _collection_incomplete_reason(99, 99, 1) == "1 of 100 tracks failed"
