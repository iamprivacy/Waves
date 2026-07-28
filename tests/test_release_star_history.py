"""A release must not blank the star-history chart.

The chart image and the README block that shows it are produced ON the public
repo by star-history.yml and exist nowhere in the private tree. release.sh
publishes a verbatim snapshot of that tree, so every release deleted both, and
the published README carried an empty "Star History" section until the
workflow's next 06:00 UTC run. Every release reopened that window.

release.sh now copies both off the public tip before publishing. Refreshing the
chart is still the workflow's job; the release simply stops undoing it.

The scenario builds a throwaway repo with a fake "public" remote and runs the
real release.sh in RELEASE_DRY_RUN mode, which stops after building the tree,
before any prompt or push.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RELEASE_SH = REPO / "release.sh"

_START = "<!-- star-history:start -->"
_END = "<!-- star-history:end -->"
_CHART = "![Star History Chart](assets/star-history/chart.svg)"


def _git(cwd: Path, *args: str, **kw) -> str:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@example.invalid",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@example.invalid",
    )
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True, **kw
    ).stdout.strip()


def _write(path: Path, text: str) -> None:
    """Write, then backdate. Files created in the same second as the index are
    "racily clean" to git: diff-index --quiet reports them as modified, and the
    sandbox would look dirty to release.sh's own guard."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    old = os.stat(path).st_mtime - 10
    os.utime(path, (old, old))


def _readme(block: str) -> str:
    return f"# Waves\n\n## Star History\n\n{_START}\n{block}{_END}\n\n## Acknowledgments\n"


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A private repo whose "public" remote already carries a rendered chart."""
    public = tmp_path / "public.git"
    public.mkdir()
    _git(public, "init", "--bare", "-b", "main")

    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    _write(seed / "README.md", _readme(f"\n{_CHART}\n\n"))
    _write(seed / "assets" / "star-history" / "chart.svg", "<svg/>\n")
    _write(seed / "keep.txt", "published\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "public tip with the rendered chart")
    _git(seed, "remote", "add", "origin", str(public))
    _git(seed, "push", "origin", "main")

    private = tmp_path / "private"
    private.mkdir()
    _git(private, "init", "-b", "master")
    # The private tree: markers with nothing between them, no chart asset.
    _write(private / "README.md", _readme(""))
    _write(private / "keep.txt", "published\n")
    # Something new, so the snapshot actually differs from the public tip.
    _write(private / "tidaler" / "app.py", "print(1)\n")
    _write(private / "RELEASING.md", "never published\n")
    _git(private, "add", "-A")
    _git(private, "commit", "-m", "private tree")
    _git(private, "remote", "add", "public", str(public))
    return private


def _release(private: Path) -> str:
    env = dict(os.environ, RELEASE_DRY_RUN="1")
    proc = subprocess.run(
        # Explicit source ref: the HEAD-only dirty-tree guard is about the
        # human workflow, not what this scenario is testing.
        ["bash", str(RELEASE_SH), "a release", "master"],
        cwd=private,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, f"release.sh failed:\n{proc.stdout}\n{proc.stderr}"
    for line in proc.stdout.splitlines():
        if line.startswith("dry-run tree: "):
            return line.split(": ", 1)[1]
    raise AssertionError(f"release.sh printed no tree:\n{proc.stdout}")


def test_the_release_keeps_the_rendered_chart(sandbox: Path):
    tree = _release(sandbox)
    files = _git(sandbox, "ls-tree", "-r", "--name-only", tree).splitlines()
    assert "assets/star-history/chart.svg" in files, "the release deleted the chart the workflow rendered"

    readme = _git(sandbox, "show", f"{tree}:README.md")
    assert _CHART in readme, "the release published an empty Star History section"
    assert readme.count(_START) == 1 and readme.count(_END) == 1, "the markers were duplicated"
    # Nothing else about the README changed.
    assert "## Acknowledgments" in readme


def test_the_exclude_list_still_applies(sandbox: Path):
    """The copy-back happens inside the same index the excludes are applied to,
    so it must not smuggle anything past them."""
    tree = _release(sandbox)
    files = _git(sandbox, "ls-tree", "-r", "--name-only", tree).splitlines()
    assert "RELEASING.md" not in files
    assert "keep.txt" in files


def test_a_public_tip_with_no_chart_yet_is_left_alone(sandbox: Path):
    """Before the workflow's first run there is nothing to carry over, and the
    release must publish the private README as-is rather than fail."""
    _git(sandbox, "fetch", "public", "main")
    tip = _git(sandbox, "rev-parse", "public/main")
    # Rewrite the public tip so it has an empty block and no asset.
    tmp_readme = _readme("")
    empty = tmp_readme
    blob = subprocess.run(
        ["git", "hash-object", "-w", "--stdin"],
        cwd=sandbox,
        input=empty,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(sandbox, "read-tree", f"{tip}^{{tree}}")
    _git(sandbox, "update-index", "--cacheinfo", f"100644,{blob},README.md")
    _git(sandbox, "rm", "--cached", "-r", "--quiet", "assets")
    new_tree = _git(sandbox, "write-tree")
    new_commit = _git(sandbox, "commit-tree", new_tree, "-m", "chartless tip")
    _git(sandbox, "push", "--force", "public", f"{new_commit}:refs/heads/main")

    tree = _release(sandbox)
    readme = _git(sandbox, "show", f"{tree}:README.md")
    assert _START in readme and _END in readme
    assert readme == tmp_readme.strip()
