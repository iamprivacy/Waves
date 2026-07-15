"""CHANGELOG.md format guard.

Every release section must list its subheadings in the fixed order
Added > Changed > Fixed > Removed (any subset, but never shuffled), so the
notes read the same way in every GitHub Release. The heading text may carry
an emoji accent; only the trailing word is significant.
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
