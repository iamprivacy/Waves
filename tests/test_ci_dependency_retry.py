"""The CI dependency install must retry.

Every build leg installs PySide6, whose wheels are ~165MB, and PyPI does
occasionally cut a transfer short. The signing job needs every leg of the build
matrix, so one dropped connection on one runner leaves the whole release an
unsigned draft: that is what happened to v0.1.12. A bare ``poetry install``
here is therefore a release-level single point of failure, and this guard keeps
the retry from being quietly simplified away.
"""

from __future__ import annotations

from pathlib import Path

import yaml

ACTION = Path(__file__).resolve().parent.parent / ".github/actions/setup-poetry-env/action.yml"


def _install_step() -> dict:
    steps = yaml.safe_load(ACTION.read_text(encoding="utf-8"))["runs"]["steps"]
    step = next((s for s in steps if s.get("name") == "Install dependencies"), None)
    assert step, "setup-poetry-env no longer has an 'Install dependencies' step"
    return step


def test_the_dependency_install_is_retried():
    script = _install_step()["run"]
    assert "poetry install" in script, "the step no longer installs anything"
    assert "for attempt in" in script, (
        "the dependency install is not retried; one dropped PyPI connection on "
        "any runner would fail the whole release again"
    )


def test_a_retry_does_not_reuse_a_truncated_download():
    """A short read can leave a partial wheel in poetry's cache, so retrying
    without clearing it just fails again on the same bad file."""
    assert "poetry cache clear" in _install_step()["run"]


def test_the_step_still_fails_when_every_attempt_fails():
    """Retrying must not turn a genuinely broken install into a green build."""
    script = _install_step()["run"]
    assert "exit 1" in script, "exhausted retries must fail the step, not pass it"
