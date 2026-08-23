"""Network work must not run on the GUI thread, nor under a held lock.

THE BUGS
--------
1. ``previewArtist`` and ``downloadArtist`` called ``_get_artist`` in the
   **slot body**. A QML-to-slot call is a synchronous call on the GUI thread,
   and on an ``_objs`` miss ``_get_artist`` issues ``session.artist(...)``,
   which reaches tidalapi's request layer. tidalapi passes no ``timeout``
   anywhere and its session is a bare ``requests.Session``, so the window froze
   for the length of that request. Every sibling (``previewMedia``,
   ``_refetch_for_download``, ``loadArtist``) resolves inside ``work()``. The
   codebase already knew the hazard: ``_try_token_login``'s docstring records a
   synchronous login on the GUI thread hanging the app at launch.

   Misses are ordinary, not exotic: every fresh search clears all ``_objs``
   buckets, and a cache-hit re-search re-emits the payload without repopulating
   them, so any artist card shown from a cached search has no live object.

2. ``_browse_fetch`` held the process-wide, non-reentrant ``_browse_lock``
   across that same untimed request. Only tidalapi's shared page parser needs
   serializing; the request does not. A wedged peer therefore blocked every
   other acquirer, including ``_refetch_for_download``, and because all
   acquirers sit on the shared UI pool, a few blocked workers saturate it and
   take search and artist pages down with Browse.

These are structural guards: they assert *where* the call sits, because the
failure is a freeze, which a functional test cannot observe without hanging.
"""

from __future__ import annotations

import ast
import inspect
import textwrap

from tidaler.waves_ui.backend import WavesBridge


def _slot_ast(name: str) -> ast.FunctionDef:
    source = textwrap.dedent(inspect.getsource(getattr(WavesBridge, name)))
    return ast.parse(source).body[0]


def _nested_function_names(node: ast.FunctionDef) -> set[str]:
    return {n.name for n in node.body if isinstance(n, ast.FunctionDef)}


def _calls_in_body_excluding_nested(node: ast.FunctionDef) -> set[str]:
    """Attribute-call names made directly in the slot body, skipping anything
    inside a nested function (which is what runs on the worker)."""
    names: set[str] = set()
    for statement in node.body:
        if isinstance(statement, ast.FunctionDef):
            continue  # the worker body: this is where slow work belongs
        for sub in ast.walk(statement):
            if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
                names.add(sub.func.attr)
    return names


def _calls_inside_workers(node: ast.FunctionDef) -> set[str]:
    """Attribute-call names made inside ANY nested function of the slot: the
    worker itself, or a sibling it delegates to (downloadArtist's work() wraps
    scan() so a STOP mid-scan has one place to land)."""
    return {
        sub.func.attr
        for nested in node.body
        if isinstance(nested, ast.FunctionDef)
        for sub in ast.walk(nested)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
    }


def test_preview_and_download_artist_resolve_off_the_gui_thread():
    """_get_artist can issue an untimed HTTP request, so it must sit inside the
    worker, not in the slot body Qt runs on the GUI thread."""
    for slot in ("previewArtist", "downloadArtist"):
        node = _slot_ast(slot)
        assert "work" in _nested_function_names(node), f"{slot} no longer has a work() worker; update this guard"

        assert "_get_artist" not in _calls_in_body_excluding_nested(node), (
            f"{slot} resolves the artist on the GUI thread; on an _objs miss that is a "
            "synchronous, untimed network request and the window freezes"
        )
        assert "_get_artist" in _calls_inside_workers(node), f"{slot} no longer resolves the artist at all"


def test_no_slot_resolves_media_objects_on_the_gui_thread():
    """The same rule for the sibling resolvers, so a new slot cannot quietly
    reintroduce the freeze."""
    resolvers = {"_get_artist", "_get_album", "_get_track"}
    offenders: list[str] = []

    for name, member in vars(WavesBridge).items():
        if not callable(member) or name.startswith("__"):
            continue
        try:
            node = _slot_ast(name)
        except (OSError, TypeError, SyntaxError, IndexError):
            continue
        if not isinstance(node, ast.FunctionDef):
            continue
        if "work" not in _nested_function_names(node):
            continue  # no worker: nothing to be off-thread relative to
        hit = resolvers & _calls_in_body_excluding_nested(node)
        if hit:
            offenders.append(f"{name}: {sorted(hit)}")

    assert not offenders, "slots resolving media objects on the GUI thread:\n" + "\n".join(offenders)


def test_browse_fetch_does_not_hold_the_lock_across_the_request():
    """The HTTP request must be issued before the lock is taken."""
    node = _slot_ast("_browse_fetch")

    with_statements = [n for n in ast.walk(node) if isinstance(n, ast.With)]
    assert with_statements, "_browse_fetch no longer takes the browse lock; update this guard"

    lock_block = with_statements[0]
    calls_under_lock = {
        sub.func.attr
        for sub in ast.walk(lock_block)
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
    }
    assert "request" not in calls_under_lock, (
        "_browse_fetch issues its HTTP request while holding the process-wide browse "
        "lock; a wedged peer blocks every other acquirer and saturates the UI pool"
    )

    # ...and the parse, which is the reason the lock exists, must stay inside.
    assert "parse" in calls_under_lock, "the page parse escaped the lock; tidalapi's shared parser is not thread-safe"
