"""Regression guard: user content that is logged must be marked as content.

THE BUG
-------
``diagnostics.content()`` and the whole ``«…»`` content tier existed, were
tested in isolation, and were promised in the README and in the in-app help
("Also hide titles and searches"), but had **zero production call sites**. With
nothing marked, ``_CONTENT_RE`` matched nothing, ``scrub_content`` was an
identity function on every real line, and an export taken with
``redact_content=True`` shipped the user's raw search terms and media names
under a header reading ``content_redacted=True``.

Search terms reach disk even with verbose off: ``devlog.done`` re-emits at
WARNING when a search exceeds its budget, and ``_CrumbDumpHandler`` replays the
whole INFO ring at WARNING on any ERROR.

THE FIX marks the search needle and the engine's media-name log lines. These
tests fence the call sites (the thing that was missing), not the redactor
mechanism, which ``test_diagnostics_redactor.py`` already covers.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from tidaler.waves_ui import diagnostics

# Modules whose logging carries user-chosen text. Per the project's diagnostics
# convention, search terms and track, album or artist names are content.
_MARKED_CALL = re.compile(r"(?:diagnostics\.content|log_content)\(")


_MARKERS = {"log_content", "diagnostics.content", "content"}


def _source_of(func) -> str:
    return inspect.getsource(func)


def _dotted(node) -> str:
    """ "self.fn_logger.info" for an attribute chain, "" for anything else."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _is_log_call(node: ast.Call) -> bool:
    parts = _dotted(node.func).split(".")
    return len(parts) > 1 and parts[-2] in {"fn_logger", "logger"}


def _unmarked_media_names(node, marked: bool = False) -> list[str]:
    """Every name_builder_* call under ``node`` that no content marker wraps."""
    found: list[str] = []
    if isinstance(node, ast.Call):
        name = _dotted(node.func)
        if name in _MARKERS:
            marked = True
        elif name.split(".")[-1].startswith("name_builder_") and not marked:
            found.append(name)
    for child in ast.iter_child_nodes(node):
        found += _unmarked_media_names(child, marked)
    return found


def test_content_has_production_call_sites():
    """The whole content tier is dead code without callers. This is the guard
    that failed silently before: zero call sites tree-wide."""
    from tidaler import download
    from tidaler.waves_ui import backend

    for module in (backend, download):
        source = inspect.getsource(module)
        assert _MARKED_CALL.search(source), (
            f"{module.__name__} logs user content but marks none of it; "
            "the 'also hide titles and searches' switch silently becomes a no-op"
        )


def test_the_search_needle_is_marked_wherever_it_is_logged():
    """Both search log lines carry the raw needle. Neither may pass it bare."""
    from tidaler.waves_ui import backend

    source = inspect.getsource(backend.WavesBridge.search)
    logged_needles = [line for line in source.splitlines() if "needle=" in line]
    assert logged_needles, "the search needle is no longer logged; update this guard"
    for line in logged_needles:
        assert _MARKED_CALL.search(line), f"unmarked search needle reaches the log: {line.strip()}"


def test_engine_media_name_logs_are_marked():
    """The download engine logs the track and list names it is working on at
    INFO, which feeds the always-on breadcrumb ring."""
    from tidaler import download

    source = inspect.getsource(download)
    for fragment in ("Downloaded item", "Finished list"):
        line = next(ln for ln in source.splitlines() if fragment in ln and "fn_logger" in ln)
        assert _MARKED_CALL.search(line), f"unmarked media name reaches the log: {line.strip()}"


def test_no_log_line_in_the_engine_builds_a_media_name_bare():
    """The named-fragment guard above only covers the two lines it names, and
    the wrap kept getting forgotten on new ones (the music-video tagging
    failures shipped unwrapped). This one covers every log call that builds a
    media name, whichever line it lands on. The handler's redactor still
    catches identity PII, but a track or artist name is content: only the
    marker decides whether "also hide titles and searches" can hash it.
    """
    from tidaler import download

    tree = ast.parse(Path(download.__file__).read_text(encoding="utf-8"))
    offenders = [
        f"{download.__name__}:{node.lineno} {name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_log_call(node)
        for name in _unmarked_media_names(node)
    ]
    assert not offenders, f"unmarked media names reach the log: {offenders}"


def test_a_logged_search_term_is_hashed_in_a_redacted_export():
    """End to end on the marker contract: a real search log line, scrubbed the
    way the export scrubs it, must not contain the needle."""
    needle = "aphex twin selected ambient"
    line = f"search done needle={diagnostics.content(needle)} n=137 dur=2.1s"

    # Default (identity-only) pass: content stays readable, as designed.
    assert needle in diagnostics.scrub(line)

    # The export's content pass: the needle is gone, replaced by a stable hash.
    redacted = diagnostics.scrub(line, redact_content=True)
    assert needle not in redacted
    assert "aphex" not in redacted.lower()
    assert re.search(r"«#[0-9a-f]{8}»", redacted), redacted
