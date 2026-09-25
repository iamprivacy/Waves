"""The release script's guards, exercised on a throwaway repo.

The chart image and the README block that shows it are rendered by
star-history.yml on the released tip and can be stale or absent in the tree
being released. A verbatim snapshot would publish the stale copy or delete
both and leave the README's "Star History" section empty until the
workflow's next 06:00 UTC run. release.sh copies both off the released tip
before publishing; refreshing the chart stays the workflow's job.

The same scenario covers the other fail-closed guards of the script: the
exclude list (any case, nested copies), the content scan over the tree about
to be published, and the pinned commit identity.

Every test builds a throwaway repo with a fake "public" remote and runs the
real release.sh in RELEASE_DRY_RUN mode, which stops after building the tree,
before any prompt or push. The script is a maintainer tool that a checkout
may not carry, so the whole file skips when it is absent.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RELEASE_SH = REPO / "release.sh"

pytestmark = pytest.mark.skipif(not RELEASE_SH.exists(), reason="release.sh is not part of this checkout")

# The identity every published commit must carry: a GitHub noreply address.
# Synthetic, and set on the sandbox repo's own config so the script resolves
# it the way it does for real (nothing in the test environment leaks in).
_NOREPLY_NAME = "synthetic-handle"
_NOREPLY_EMAIL = "0000000+synthetic-handle@users.noreply.github.com"

# The sandbox's "public" remote lives under a folder named like a GitHub owner,
# so the script's owner extraction (the last-but-one path segment of the
# remote URL, the same place a github.com URL keeps it) resolves to this.
_OWNER = "ownerhandle"

# What the sandbox's guard hook lists. Synthetic markers only; the real hook is
# local to a developer's clone and its markers never appear in a tracked file.
_HOOK_MARKERS = f"synthetic-marker-alpha|relay-id-beta|{_OWNER}|(above|earlier) we agreed"

_START = "<!-- star-history:start -->"
_END = "<!-- star-history:end -->"
_CHART = "![Star History Chart](assets/star-history/chart.svg)"


# The developer's own git config must not reach the sandbox: the identity
# tests need to see exactly what the sandbox repo configures and nothing else.
_NO_USER_CONFIG = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def _git(cwd: Path, *args: str, **kw) -> str:
    env = dict(os.environ, **_NO_USER_CONFIG)
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


def _write_hook(private: Path, markers: str = _HOOK_MARKERS) -> Path:
    """The local guard hook release.sh reads its marker list from."""
    hook = private / ".git" / "hooks" / "pii-guard.sh"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f"#!/usr/bin/env bash\nPATTERN='{markers}'\nexit 0\n")
    return hook


@pytest.fixture
def sandbox(tmp_path: Path) -> Path:
    """A private repo whose "public" remote already carries a rendered chart."""
    public = tmp_path / _OWNER / "public.git"
    public.mkdir(parents=True)
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
    _write(private / "waves" / "app.py", "print(1)\n")
    _write(private / "RELEASING.md", "never published\n")
    _git(private, "add", "-A")
    _git(private, "commit", "-m", "private tree")
    _git(private, "remote", "add", "public", str(public))
    _git(private, "config", "user.name", _NOREPLY_NAME)
    _git(private, "config", "user.email", _NOREPLY_EMAIL)
    _write_hook(private)
    return private


def _dry_run(private: Path, **extra_env: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, RELEASE_DRY_RUN="1", **_NO_USER_CONFIG, **extra_env)
    return subprocess.run(
        # Explicit source ref: the HEAD-only dirty-tree guard is about the
        # human workflow, not what this scenario is testing.
        ["bash", str(RELEASE_SH), "a release", "master"],
        cwd=private,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _dry_run_objects(private: Path) -> tuple[str, str]:
    proc = _dry_run(private)
    assert proc.returncode == 0, f"release.sh failed:\n{proc.stdout}\n{proc.stderr}"
    tree = commit = ""
    for line in proc.stdout.splitlines():
        if line.startswith("dry-run tree: "):
            tree = line.split(": ", 1)[1]
        elif line.startswith("dry-run commit: "):
            commit = line.split(": ", 1)[1]
    assert tree and commit, f"release.sh printed no tree or commit:\n{proc.stdout}"
    return tree, commit


def _release(private: Path) -> str:
    return _dry_run_objects(private)[0]


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


def test_a_stale_chart_in_the_private_tree_loses_to_the_public_one(sandbox: Path):
    """The dev tree can hold its own copy of the chart (tools/sync_star_history.sh
    mirrors it so the private README renders too), and that copy is stale the
    moment the workflow next runs. The carry-over must overwrite it rather than
    publish it, and must not choke on the path already being present: read-tree
    --prefix refuses to bind over existing index entries."""
    _write(sandbox / "assets" / "star-history" / "chart.svg", "<svg>stale</svg>\n")
    _write(sandbox / "README.md", _readme(f"\n{_CHART}\n\n"))
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-m", "mirror the chart into the dev tree")

    tree = _release(sandbox)
    chart = _git(sandbox, "show", f"{tree}:assets/star-history/chart.svg")
    assert chart == "<svg/>", f"the release published the dev tree's stale chart: {chart!r}"


# ---- the exclude guard must survive a tree big enough to fill a pipe ----


def test_a_nested_excluded_path_is_refused_even_on_a_large_tree(sandbox: Path):
    """The re-check at the end of release.sh is the only thing guarding a copy
    of an excluded file that is not at the top level: the `git rm` above it
    takes rooted pathspecs, so `docs/RELEASING.md` sails straight past it.

    The tree is padded on purpose. The guard used to pipe `git ls-tree` into
    `grep -q`, and once the listing outgrows the pipe buffer git dies of
    SIGPIPE, which `set -o pipefail` turns into a non-zero pipeline, which the
    `if` reads as "not found". A guard that fails open needs a fixture big
    enough to make it fail; four paths can never catch this.
    """
    _write(sandbox / "docs" / "RELEASING.md", "smuggled\n")
    for i in range(1500):
        _write(sandbox / "filler" / f"module_{i:05d}.py", "x\n")
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-m", "a tree past one pipe buffer")

    listing = _git(sandbox, "ls-tree", "-r", "--name-only", "master^{tree}")
    assert len(listing) > 32_000, "the fixture is too small to exercise the failure"

    proc = subprocess.run(
        ["bash", str(RELEASE_SH), "a release", "master"],
        cwd=sandbox,
        env=dict(os.environ, RELEASE_DRY_RUN="1"),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0, (
        "the guard passed a nested excluded path; it failed open:\n" f"{proc.stdout}\n{proc.stderr}"
    )
    assert "survived the exclude step" in proc.stderr


# ---- the exclude list is a reader's list, not a byte-exact one ----------


# The assistant-instructions file name, built at run time: this test is
# published, and the release's own content scan would otherwise refuse the
# tree over a test that names the file it keeps out.
_AI_MD = "cl" + "aude"


def test_case_variants_and_tooling_files_are_excluded(sandbox: Path):
    """A lowercase, a per-user and a Notes/ spelling of the tooling files are
    the same thing to a reader as the listed spellings. The exclude step used
    to match exact names only, so every one of these was published."""
    names = (f"{_AI_MD}.md", "Agents.md", f"{_AI_MD.upper()}.local.md", ".mcp.json", "Notes/plan.md", "AGENTS.local.md")
    for name in names:
        _write(sandbox / name, "never published\n")
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-m", "tooling files in every spelling")

    tree = _release(sandbox)
    files = _git(sandbox, "ls-tree", "-r", "--name-only", tree).splitlines()
    survivors = [
        f
        for f in files
        if f.split("/", 1)[0].lower()
        in {f"{_AI_MD}.md", "agents.md", f"{_AI_MD}.local.md", ".mcp.json", "notes", "agents.local.md"}
    ]
    assert not survivors, f"published tooling files: {survivors}"
    assert "keep.txt" in files


def test_a_nested_case_variant_is_refused(sandbox: Path):
    """The re-check must read names the way the exclude step now does: a
    nested lowercase copy is refused, not published because its case differs
    from the list."""
    _write(sandbox / "docs" / f"{_AI_MD}.md", "smuggled\n")
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-m", "a nested lowercase copy")

    proc = _dry_run(sandbox)
    assert proc.returncode != 0, f"a nested case variant was published:\n{proc.stdout}"
    assert "survived the exclude step" in proc.stderr


# ---- the tree's content is scanned, not only its paths -------------------


def test_a_marker_in_the_published_tree_refuses_the_release(sandbox: Path):
    """The commit hook only sees added lines of a commit and can be bypassed;
    the release reads every text file of the final tree with the same markers
    and refuses on a hit, naming the place but never the line."""
    _write(sandbox / "waves" / "notes.py", "# a comment\n# as EARLIER we agreed, the value is 42\nx = 1\n")
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-m", "a line the hook never saw")

    proc = _dry_run(sandbox)
    assert proc.returncode != 0, f"a marker was published:\n{proc.stdout}"
    assert "matches the PII / authorship markers" in proc.stderr
    assert "waves/notes.py:2" in proc.stderr, proc.stderr
    # The location is enough; the offending text must not be echoed anywhere.
    assert "we agreed" not in proc.stderr and "we agreed" not in proc.stdout
    assert "value is 42" not in proc.stderr and "value is 42" not in proc.stdout


def test_a_clean_tree_passes_the_content_scan(sandbox: Path):
    proc = _dry_run(sandbox)
    assert proc.returncode == 0, proc.stderr


def test_the_public_owner_inside_a_github_url_is_not_a_hit(sandbox: Path):
    """The owner name of the public repo is a hook marker that the README's
    own URLs match; the release scan drops that one alternative and nothing
    else."""
    _write(sandbox / "README.md", _readme("") + f"\nSee https://github.com/{_OWNER}/Waves/issues\n")
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-m", "a legitimate URL")

    proc = _dry_run(sandbox)
    assert proc.returncode == 0, proc.stderr

    # The other markers are still live around it, in any case.
    _write(sandbox / "waves" / "x.py", f"# https://github.com/{_OWNER}/Waves and Relay-Id-Beta\n")
    _git(sandbox, "add", "-A")
    _git(sandbox, "commit", "-m", "a URL beside a marker")
    proc = _dry_run(sandbox)
    assert proc.returncode != 0
    assert "waves/x.py:1" in proc.stderr


def test_a_missing_guard_hook_refuses_the_release(sandbox: Path):
    """No hook means no marker list, and no list means the tree would go out
    unscanned. The release refuses instead."""
    (sandbox / ".git" / "hooks" / "pii-guard.sh").unlink()
    proc = _dry_run(sandbox)
    assert proc.returncode != 0
    assert "pii-guard.sh is missing" in proc.stderr


def test_a_hook_without_a_pattern_line_refuses_the_release(sandbox: Path):
    hook = sandbox / ".git" / "hooks" / "pii-guard.sh"
    hook.write_text("#!/usr/bin/env bash\nexit 0\n")
    proc = _dry_run(sandbox)
    assert proc.returncode != 0
    assert "no PATTERN=" in proc.stderr


# ---- every published commit carries the noreply identity -----------------


def test_the_published_commits_carry_the_noreply_identity(sandbox: Path):
    """commit-tree takes whatever the environment offers. The release pins
    author and committer to the configured noreply identity, so a stray
    GIT_COMMITTER_EMAIL or EMAIL in the shell never reaches the public
    history."""
    proc = _dry_run(
        sandbox,
        GIT_COMMITTER_NAME="ci-box",
        GIT_COMMITTER_EMAIL="ci@ci-box.local",
        GIT_AUTHOR_NAME="ci-box",
        GIT_AUTHOR_EMAIL="ci@ci-box.local",
        EMAIL="someone@ci-box.local",
    )
    assert proc.returncode == 0, proc.stderr
    commit = next(line.split(": ", 1)[1] for line in proc.stdout.splitlines() if line.startswith("dry-run commit: "))
    # The headline commit and every curated commit under it.
    idents = _git(sandbox, "log", "--format=%an|%ae|%cn|%ce", f"public/main..{commit}").splitlines()
    assert idents, "no commits were built"
    for ident in idents:
        assert ident == f"{_NOREPLY_NAME}|{_NOREPLY_EMAIL}|{_NOREPLY_NAME}|{_NOREPLY_EMAIL}", ident


def test_no_noreply_identity_refuses_the_release(sandbox: Path):
    """Nothing configured and no published commit to copy from: the release
    must not invent an identity from the login name and hostname."""
    _git(sandbox, "config", "--unset", "user.email")
    proc = _dry_run(sandbox, GIT_COMMITTER_EMAIL="someone@ci-box.local", GIT_AUTHOR_EMAIL="someone@ci-box.local")
    assert proc.returncode != 0
    assert "noreply" in proc.stderr
    assert "dry-run commit" not in proc.stdout


def test_the_last_published_identity_is_the_fallback(sandbox: Path):
    """With no noreply address configured, the identity of the last public
    commit is reused when it is a noreply one."""
    _git(sandbox, "config", "--unset", "user.email")
    _git(sandbox, "fetch", "public", "main")
    tip = _git(sandbox, "rev-parse", "public/main")
    env = dict(
        os.environ,
        GIT_AUTHOR_NAME="tip-author",
        GIT_AUTHOR_EMAIL="1+tip-author@users.noreply.github.com",
        GIT_COMMITTER_NAME="tip-author",
        GIT_COMMITTER_EMAIL="1+tip-author@users.noreply.github.com",
    )
    new_commit = subprocess.run(
        ["git", "commit-tree", f"{tip}^{{tree}}", "-p", tip, "-m", "a noreply tip"],
        cwd=sandbox,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    _git(sandbox, "push", "public", f"{new_commit}:refs/heads/main")

    proc = _dry_run(sandbox)
    assert proc.returncode == 0, proc.stderr
    commit = next(line.split(": ", 1)[1] for line in proc.stdout.splitlines() if line.startswith("dry-run commit: "))
    assert (
        _git(sandbox, "log", "-1", "--format=%ae|%ce", commit)
        == "1+tip-author@users.noreply.github.com|1+tip-author@users.noreply.github.com"
    )
