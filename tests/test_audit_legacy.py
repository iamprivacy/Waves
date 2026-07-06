"""Audit regression tests for the legacy tidaler entry points.

Covers the audited update-check bugs in ``tidaler.__init__``:

* A failed check (no network / bad response) must report **no** update
  available, instead of the previous behaviour where ``update_available()``
  returned ``True`` for any non-``v0.0.0`` current version and surfaced a bogus
  ``v0.0.0`` "update" that linked to the raw GitHub API URL.
* ``latest_version_information()`` must not leak the raw API URL on failure (the
  version dialog's Download button would otherwise open it).

All hermetic: ``requests.get`` is monkeypatched, no network is touched.
"""

import requests

import tidaler
from tidaler import (
    VERSION_CHECK_FAILED,
    latest_version_information,
    update_available,
)


class _FakeResponse:
    """Minimal stand-in for ``requests.Response`` used by these tests."""

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def _patch_get(monkeypatch, fn) -> None:
    """Patch the ``requests.get`` used inside ``tidaler`` with ``fn``."""
    monkeypatch.setattr(tidaler.requests, "get", fn)


class TestLatestVersionInformation:
    def test_network_failure_reports_check_failed(self, monkeypatch) -> None:
        """On a requests failure the sentinel version is returned, no URL leaks."""

        def _boom(*args, **kwargs):
            raise requests.RequestException("no network")

        _patch_get(monkeypatch, _boom)

        info = latest_version_information()

        assert info.version == VERSION_CHECK_FAILED
        # The raw API URL must NOT be surfaced (Download button would open it).
        assert info.url == ""

    def test_malformed_response_reports_check_failed(self, monkeypatch) -> None:
        """A response missing expected keys is treated as a failed check."""

        def _incomplete(*args, **kwargs):
            return _FakeResponse({"unexpected": "shape"})

        _patch_get(monkeypatch, _incomplete)

        info = latest_version_information()

        assert info.version == VERSION_CHECK_FAILED
        assert info.url == ""

    def test_success_parses_release(self, monkeypatch) -> None:
        """A well-formed response is parsed into a ReleaseLatest."""

        def _ok(*args, **kwargs):
            return _FakeResponse(
                {
                    "tag_name": "v9.9.9",
                    "html_url": "https://github.com/OWNER/REPO/releases/tag/v9.9.9",
                    "body": "notes",
                }
            )

        _patch_get(monkeypatch, _ok)

        info = latest_version_information()

        assert info.version == "v9.9.9"
        assert info.url.endswith("/v9.9.9")
        assert info.release_info == "notes"


class TestUpdateAvailable:
    def test_network_failure_reports_no_update(self, monkeypatch) -> None:
        """A failed check must NOT be reported as an available update."""

        def _boom(*args, **kwargs):
            raise requests.RequestException("no network")

        _patch_get(monkeypatch, _boom)

        available, info = update_available()

        assert available is False
        assert info.version == VERSION_CHECK_FAILED

    def test_newer_release_reports_update(self, monkeypatch) -> None:
        """A latest tag different from the current version is an update."""

        def _ok(*args, **kwargs):
            return _FakeResponse(
                {
                    "tag_name": "v999.0.0",
                    "html_url": "https://github.com/OWNER/REPO/releases/tag/v999.0.0",
                    "body": "notes",
                }
            )

        _patch_get(monkeypatch, _ok)

        available, info = update_available()

        assert available is True
        assert info.version == "v999.0.0"

    def test_same_version_reports_no_update(self, monkeypatch) -> None:
        """The current version equalling the latest tag is not an update."""
        current_tag = f"v{tidaler.__version__}"

        def _ok(*args, **kwargs):
            return _FakeResponse(
                {
                    "tag_name": current_tag,
                    "html_url": "https://github.com/OWNER/REPO/releases/tag/" + current_tag,
                    "body": "notes",
                }
            )

        _patch_get(monkeypatch, _ok)

        available, _info = update_available()

        assert available is False
