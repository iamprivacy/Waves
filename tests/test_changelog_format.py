"""CHANGELOG.md format guard.

Every release section must list its subheadings in the fixed order
Added > Changed > Fixed > Removed (any subset, but never shuffled), so the
notes read the same way in every GitHub Release. The heading text may carry
an emoji accent; only the trailing word is significant.

An issue a bullet closes must be written as a full link ("[issue #11](url)")
rather than a bare "(#11)": these notes are lifted verbatim into the Release
body and read well outside the repo, where a bare number links to nothing
and does not even say what it refers to.

A bullet also lives on a single line, however long. GitHub renders a Release
body with every newline turned into a line break, so a bullet wrapped across
source lines reaches the Releases page as one ragged fragment per source line
instead of a paragraph.
"""

from __future__ import annotations

import re
from pathlib import Path

CHANGELOG = Path(__file__).resolve().parent.parent / "CHANGELOG.md"

CANONICAL = ["Added", "Changed", "Fixed", "Removed"]


def _sections() -> list[tuple[str, list[str]]]:
    """Return (release heading, [subheading names]) per release section."""
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in CHANGELOG.read_text(encoding="utf-8").splitlines():
        if line.startswith("## "):
            if current:
                sections.append(current)
            current = (line[3:].strip(), [])
        elif line.startswith("### ") and current:
            # Keep only the canonical name: strip any emoji accent.
            match = re.search(r"(Added|Changed|Fixed|Removed)\s*$", line)
            assert match, f"unknown subheading {line!r} under {current[0]!r}"
            current[1].append(match.group(1))
    if current:
        sections.append(current)
    return sections


def test_changelog_has_release_sections():
    assert _sections(), "CHANGELOG.md has no '## ' release sections"


def test_subheadings_are_unique_per_section():
    for heading, subs in _sections():
        assert len(subs) == len(set(subs)), f"duplicate subheading under {heading!r}: {subs}"


def test_subheadings_follow_canonical_order():
    for heading, subs in _sections():
        expected = [name for name in CANONICAL if name in subs]
        assert subs == expected, (
            f"{heading!r} lists subheadings as {subs}; "
            f"they must follow Added > Changed > Fixed > Removed ({expected})"
        )


def test_bullets_are_never_wrapped_across_lines():
    """One bullet, one line: a source newline becomes a break in the Release."""
    lines = CHANGELOG.read_text(encoding="utf-8").splitlines()
    wrapped = []
    in_release = False
    in_fence = False
    for lineno, line in enumerate(lines, 1):
        if line.startswith("## "):
            in_release = True
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        previous = lines[lineno - 2] if lineno > 1 else ""
        if not in_release or in_fence or not line.strip() or not previous.strip():
            continue
        # A bullet may carry an indented block (a command, a follow-up
        # paragraph) below it, but only after a blank line: what is banned is
        # text hanging directly off the line above, which is a wrap.
        if line.startswith(("#", "- ", "<")) or previous.lstrip().startswith("<"):
            continue
        wrapped.append(f"line {lineno}: {line.strip()[:60]}")
    assert not wrapped, "write each bullet on one line, unwrapped:\n" + "\n".join(wrapped)


def test_issue_references_are_labelled_links():
    """No bare '#12' anywhere: an issue is named and linked, or not cited."""
    bare = []
    for lineno, raw in enumerate(CHANGELOG.read_text(encoding="utf-8").splitlines(), 1):
        # Inline code spans quote the rule's own counter-example; they cite
        # nothing, so they are not held to it.
        line = re.sub(r"`[^`]*`", "", raw)
        for match in re.finditer(r"#\d+", line):
            start = match.start()
            # A reference is fine when it reads "issue #N" AND that text is a
            # markdown link ("[issue #N](url)"), which is how they are written.
            named = line[:start].rstrip().endswith("issue")
            linked = "](http" in line[match.end() : match.end() + 80]
            if not (named and linked):
                bare.append(f"line {lineno}: {line.strip()}")
    assert not bare, "cite issues as [issue #N](https://github.com/.../issues/N):\n" + "\n".join(bare)
