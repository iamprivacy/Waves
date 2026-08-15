"""The matching brain: how Waves decides that two records name the same music.

Every "do I already have this?" question is answered here, and only here: the
library badge compares a TIDAL album against the folders scanned out of the
user's own music library, and both sides are reduced to the same normalised
strings before anything is compared.

The rule this module lives by: it matches STRINGS and plain dicts, never a
catalog's objects. A caller pulls a title, an artist name, a year and a track
count out of whatever it holds (a tidalapi album, a row of scanned tags) and
asks a question here; the answers never depend on where the facts came from.
That is what lets the library index, the GUI bridge and a future second source
share one definition of "the same album" instead of drifting into three.

Pure standard library, with no Qt and no tidalapi import, so it unit tests
without the GUI stack (the same property that makes ownership.py and
library_index.py testable, and the reason the bridge keeps only the small
object-to-strings adapters that feed this).

Two biases run through the whole module, and both are deliberate:

* **Canonicalise, never guess.** Cross-catalog comparison folds punctuation the
  two catalogs tag differently for the same release, symmetrically on both
  sides, so it can only ever fix a spurious mismatch. There is no fuzzy
  distance anywhere: a match is exact on normalised text.
* **When unsure, keep both.** A missed match costs a badge. A wrong match tells
  the user they own a record they do not. So every decision here fails towards
  the harmless side, and towards showing nothing.
"""

from __future__ import annotations

import re
import unicodedata

# --- Identity: artists ---------------------------------------------------------

# TIDAL's canonical "Various Artists" entity is id 2935, but localized markets
# serve a compilation's credit under a different id with a translated name (e.g.
# id 9174206 for the Japanese "ヴァリアス・アーティスト"), so we match a
# multilingual name marker. The shared placeholder image is the generic "no
# picture" art (used by obscure real artists too), so it is deliberately not a
# signal here.
_VARIOUS_ARTISTS_RE = re.compile(
    r"various\s+artist|verschiedene\s+interpreten|multi[\s-]?interpr|varios\s+artistas"
    r"|v[áa]rios\s+artistas|artisti\s+vari|ヴァリアス|群星",
    re.IGNORECASE,
)


# The abbreviated forms a local tagger writes, which the phrase pattern above
# cannot look for as a substring ("va" appears inside ordinary artist names).
# Anchored to the WHOLE name, so only a folder credited exactly "VA" / "V.A." /
# "V/A" / "Various" is refused. A real artist called "Va" losing its badge is the
# harmless direction: refusing presence only ever leaves a live download button.
_VARIOUS_ARTISTS_SHORT_RE = re.compile(r"^\s*(v\.?\s*/?\s*a\.?|various|varios|diversos)\s*$", re.IGNORECASE)


def is_various_artists(name: str) -> bool:
    """True when an artist name is a 'Various Artists' placeholder in any of the
    languages TIDAL serves it in, or in the abbreviated forms local taggers use.
    Presence matching uses this to refuse to match compilations at all: the
    normalised "various artists" key collides across completely unrelated
    compilations, so a match there means nothing.

    Testing the streaming side alone is sufficient: a candidate only reaches the
    comparison when its presence_key artist is EQUAL to the streaming one, so a
    Various-Artists folder can only ever be offered against a Various-Artists
    album."""
    text = name or ""
    return bool(_VARIOUS_ARTISTS_RE.search(text) or _VARIOUS_ARTISTS_SHORT_RE.match(text))


# " & " / " + " and " and " are one conjunction spelled three ways (Simon &
# Garfunkel vs Simon and Garfunkel; Florence + The Machine). Whitespace is
# required on BOTH sides, which is the whole safety story: AC/DC, AT&T, "1+1"
# and Ed Sheeran's bare "+" are untouched.
_AND_RE = re.compile(r"\s(?:&|\+)\s")


def _fold_and(text: str) -> str:
    """Fold a free-standing ampersand or plus to the word it spells."""
    return _AND_RE.sub(" and ", text)


# Multi-artist tag separators: a semicolon however spaced (taggers write
# "A;B" and "A; B"), and a slash only when SPACED on both sides, so AC/DC and
# GZA/Genius keep their names. Never a comma: "Earth, Wind and Fire" is one
# band. The first segment is the primary credit, which is also how TIDAL
# credits a collaboration album.
_ARTIST_SPLIT_RE = re.compile(r"\s*;\s*|\s+/\s+")

# A featuring credit riding in the fields the two catalogs disagree on: TIDAL
# writes the guest into the track title ("Song (feat. B)"), a tagger writes it
# into the artist ("A feat. B"), and either side may write neither. Both
# patterns demand a guest after the marker, so an artist or song literally
# named "Feat" or "Featuring You" (the marker with nothing before or after it)
# is untouched. The artist form additionally demands text BEFORE the marker.
_FEAT_ARTIST_RE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring)\s+(.+)$", re.IGNORECASE)
_FEAT_TITLE_RE = re.compile(r"\s*[\(\[]\s*(?:feat\.?|ft\.?|featuring)\s+([^\)\]]+)[\)\]]", re.IGNORECASE)
# How one guest list separates its names, every way credits are punctuated.
_GUEST_SPLIT_RE = re.compile(r"\s*(?:,|;|/|&|\+|\band\b)\s*", re.IGNORECASE)


def feat_guests(title: str, artist: str) -> frozenset:
    """The guest names a featuring credit carries, normalised, from wherever
    the catalog wrote them (the artist's tail, the title's parenthetical), as
    evidence for the track matcher: stripping the credit from the KEY is what
    lets "Song" find "Song (feat. B)", and comparing the guests is what stops
    "Song (feat. B)" claiming "Song (feat. C)", a different recording."""
    chunks = []
    m = _FEAT_ARTIST_RE.search(canon(artist or ""))
    if m:
        chunks.append(m.group(1))
    chunks.extend(m.group(1) for m in _FEAT_TITLE_RE.finditer(canon(title or "")))
    out = set()
    for chunk in chunks:
        for part in _GUEST_SPLIT_RE.split(chunk):
            part = re.sub(r"\s+", " ", part).strip(" .'\"").lower()
            if part:
                out.add(part)
    return frozenset(out)


def norm_artist(name: str) -> str:
    """Lowercased, whitespace-collapsed artist name for stable grouping, with
    a free-standing "&"/"+" folded to "and", the credit cut to its primary
    artist at a tag separator, and a leading "The" dropped (a library tagged
    "Beatles" and TIDAL's "The Beatles" are one artist). Each fold keeps the
    original when it would leave nothing: an empty artist key would become a
    title-only bucket, which matches far too much."""
    text = re.sub(r"\s+", " ", _fold_and(name or "")).strip().lower()
    head = _ARTIST_SPLIT_RE.split(text, maxsplit=1)[0].strip()
    if head:
        text = head
    # The featuring credit is presentation, not identity: "A feat. B" is A's
    # record. The guest is not thrown away, feat_guests reads it back out as
    # gate evidence on the track side.
    stripped = _FEAT_ARTIST_RE.sub("", text).strip()
    if stripped:
        text = stripped
    if text.startswith("the ") and text[4:].strip():
        text = text[4:].strip()
    return text


# --- Identity: titles ----------------------------------------------------------

_VERSION_TOKEN_RE = re.compile(r"[\[(]\s*(explicit|clean|e)\s*[\])]", re.IGNORECASE)


def norm_title(title: str) -> str:
    """Title with explicit/clean markers stripped (deluxe/remaster kept) and a
    free-standing "&"/"+" folded to "and", same as artists."""
    text = _fold_and(_VERSION_TOKEN_RE.sub("", title or "").lower())
    return re.sub(r"\s+", " ", text).strip(" -.–—")


# Qualifiers that mark a genuinely DIFFERENT release; an edition whose qualifier
# matches one of these is never collapsed into another (it keeps its own group).
_EDITION_KEEP_RE = re.compile(
    r"remaster|remix|\bmix\b|re-?record|taylor'?s version|anniversar|special edition"
    r"|collector|\blive\b|acoustic|unplugged|instrumental|\bdemo|\bmono\b|\bstereo\b"
    r"|reissue|re-?release|karaoke|commentary"
    # A parenthetical of "special" ALONE (with optional filler): every other
    # grouping tag already matches as a bare word, and without this the two
    # spellings of one release split into different buckets ("Album (Special)"
    # peeled to "album", "Album (Special Edition)" kept as "album (special)")
    # and never met. Deliberately NOT a bare \bspecial\b, which would also keep
    # unparseable tails like "(A Special Something)" the detector must leave
    # literal.
    r"|[\(\[]\s*special\s*(?:edition|version)?\s*[\)\]]",
    re.IGNORECASE,
)
# A trailing parenthetical / bracketed group. We deliberately do NOT treat a
# trailing " - …" as a qualifier, many real titles contain a dash (year ranges
# like "1967 – 1970", "Live - 1970"), and stripping it would mangle the base.
_EDITION_QUAL_RE = re.compile(r"\s*[\(\[][^\)\]]*[\)\]]\s*$")


def strip_edition_quals(title: str) -> str:
    """Peel trailing parenthetical / bracketed edition qualifiers so every edition
    variant of one album shares a base title, UNLESS a qualifier names a
    genuinely different release (remaster / anniversary / live / …), which is
    kept so it groups separately."""
    text, prev = title or "", None
    while text != prev:
        prev = text
        m = _EDITION_QUAL_RE.search(text)
        if not m or _EDITION_KEEP_RE.search(m.group(0)):
            break
        text = text[: m.start()]
    return re.sub(r"\s+", " ", text).strip(" -.–—")


# --- Cross-catalog canonicalisation --------------------------------------------
# NFKC folds compatibility forms (composed vs decomposed diacritics); this table
# then folds the punctuation TIDAL and a local tagger write differently for the
# SAME release: curly vs straight quotes/apostrophes (the most common real miss)
# and the dash family to a plain hyphen. Applied to BOTH sides, so it is
# canonicalisation, not fuzzy matching, and can only fix a spurious mismatch,
# never create a match.
_CANON_PUNCT = str.maketrans(
    {
        "’": "'",
        "‘": "'",
        "‚": "'",
        "‛": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "…": "...",
        "–": "-",
        "—": "-",
        "‒": "-",
        "―": "-",
        "−": "-",
    }
)


# Letters the accent strip cannot reach because they never decompose: each is
# its own codepoint, not a base plus a mark. Both cases, so the fold is
# case-agnostic before the norms lowercase.
_DIACRITIC_XLIT = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "đ": "d",
        "Đ": "D",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "ß": "ss",
        "ẞ": "SS",
    }
)


def _fold_diacritics(s: str) -> str:
    """Strip accents from LATIN letters only: Bjork finds Björk, Motorhead
    finds Motörhead, because the two catalogs routinely disagree on whether a
    name wears its marks. The Latin guard is load-bearing, not an
    optimisation: Japanese voicing marks (dakuten) are combining marks too,
    and a global strip would fold バ into ハ, colliding genuinely different
    kana titles and breaking the Various-Artists marker family. A mark on a
    non-Latin base is left exactly where it was."""
    out = []
    latin_base = False
    for ch in unicodedata.normalize("NFD", s):
        if unicodedata.category(ch) == "Mn":
            if latin_base:
                continue
        else:
            latin_base = ord(ch) < 0x0250
        out.append(ch)
    return unicodedata.normalize("NFC", "".join(out)).translate(_DIACRITIC_XLIT)


def canon(s: str) -> str:
    """Canonicalise a title/artist for cross-catalog comparison: NFKC, fold
    curly quotes to straight and the dash family to a hyphen, then strip
    accents from Latin letters. Symmetric on both catalogs, so it only ever
    fixes a spurious mismatch, never invents a match."""
    return _fold_diacritics(unicodedata.normalize("NFKC", s or "").translate(_CANON_PUNCT))


# A trailing " - <edition words>" that some taggers use where TIDAL uses a
# parenthetical ("Album - Deluxe Edition" vs "Album (Deluxe Edition)"). The house
# strip_edition_quals deliberately leaves dash tails alone (to protect year
# ranges like "1967 - 1970"), so we peel it here ONLY when the tail names a
# collapse-edition word, which a bare year or number range never does.
_DASH_EDITION_RE = re.compile(
    r"\s+-\s+[^-]*\b(?:deluxe|expanded|edition|version|bonus)\b.*$",
    re.IGNORECASE,
)


def strip_edition_quals_ext(title: str) -> str:
    """strip_edition_quals (parenthetical/bracket editions) plus a presence-local
    peel of a dash-form edition tail. Kept SEPARATE from the shared helper so a
    future app-wide album grouping is not perturbed. A dash tail is stripped only
    when it names a collapse-edition word AND is not a KEEP marker
    (remaster/live/anniversary/...), so genuinely distinct releases stay distinct."""
    text = strip_edition_quals(title)
    m = _DASH_EDITION_RE.search(text)
    if m and not _EDITION_KEEP_RE.search(m.group(0)):
        text = strip_edition_quals(text[: m.start()])
    return re.sub(r"\s+", " ", text).strip(" -.–—")


# --- Keys ----------------------------------------------------------------------


def presence_key(title: str, artist: str) -> tuple[str, str]:
    """The cross-catalog album key: (base title, normalised artist), each canon'd
    first. BOTH components must be equal for a match.

    Collapse-class qualifiers (deluxe, expanded, ...) are peeled so an edition
    still finds its family. Keep-class ones (remaster, live, acoustic, ...)
    name a genuinely different release group and stay in the key, but spelled
    CANONICALLY rather than verbatim: "(2009 Remaster)", "(Remastered 2009)"
    and "(Remastered)" must all file under one bucket or no gate downstream
    ever gets to compare them (the year, and any finer edition difference, is
    the gate's job, not the key's). A tail the edition vocabulary does not
    recognise keeps today's behaviour: peeled from the key by the qualifier
    strip, verbatim in the gate."""
    text = strip_edition_quals_ext(norm_title(canon(title)))
    base, tags, _years = edition_key(text)
    # Ordinals stay OUT of the key on purpose, exactly like years: "(20th
    # Anniversary Edition)" and "(Anniversary Edition)" must share a bucket or
    # no gate ever compares them. The ordinal stays in the tag set, so
    # same_edition still tells a 20th from a 25th.
    keep = set(tags) & _EDITION_GROUPING_TAGS
    if keep:
        base = f"{base} ({' '.join(sorted(keep))})"
    return (base, norm_artist(canon(artist)))


# A folder name that says "this is one disc of a set", in the two layouts a real
# library uses: a disc folder INSIDE the album ("Album/CD2") and a disc suffix on
# a sibling folder ("Album (Disc 2)"). Anchored, and the number is required: a
# band called Disc or an album called "Discovery" must not read as a disc. The
# suffix form additionally requires a separator or bracket between the stem and
# the marker: without one, any title ending in "…cd<digits>" ("ABCD2") read as a
# disc folder, and the bare "dis" stem made "Paradis 2" one too, so two numbered
# sibling volumes sharing a series tag could sum into a false complete copy.
# The number a disc marker carries, in the three ways libraries write it:
# digits ("CD2"), spelled out ("Disc Two"), Roman ("CD II"). Digits may glue
# to the keyword; the word forms demand a separator, or "Disci" would read as
# disc 1. Longest Roman alternatives first, or "xii" would match as "x".
_DISC_WORD_NUMS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "i": 1,
    "ii": 2,
    "iii": 3,
    "iv": 4,
    "v": 5,
    "vi": 6,
    "vii": 7,
    "viii": 8,
    "ix": 9,
    "x": 10,
    "xi": 11,
    "xii": 12,
}
_DISC_NUM = r"(?:[\s._-]*(\d{1,2})|[\s._-]+(xii|xi|x|ix|viii|vii|vi|v|iv|iii|ii|i|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve))"
# Deliberately NOT here: part/pt/vol/volume. "Vol. 2" and "Part 2" are real
# ALBUM names ("Greatest Hits Vol. 2"), and a folder named that way sitting
# directly under an artist folder is indistinguishable, by name, from a disc
# folder nested inside an album: grouping it summed two sloppily series-tagged
# sibling volumes into a false complete copy of an album the user does not
# own, the exact failure this grouper exists to avoid. A genuine "Album/Vol 2"
# disc layout still joins through the files' own disc tags (_discs_by_tag),
# which is evidence a folder name can never be.
_DISC_ONLY = re.compile(r"^(?:cd|disc|disk)" + _DISC_NUM + r"$", re.IGNORECASE)
_DISC_SUFFIX = re.compile(r"^(.*?)(?:[\s._-]+|[\s._-]*[\(\[])(?:cd|disc|disk)" + _DISC_NUM + r"[\)\]]?$", re.IGNORECASE)


def _disc_num_value(digits: str | None, word: str | None) -> int:
    """The int a matched disc marker names, from whichever form it took."""
    if digits:
        return int(digits)
    return _DISC_WORD_NUMS[(word or "").lower()]


def disc_group(folder: str) -> tuple[str, str] | None:
    """If ``folder`` is one disc of a multi-disc set, the group every disc of
    that set shares: ``(album_folder, stem)``. None when there is no disc marker.

    THE PROBLEM: the scanner calls any directory that directly holds audio files
    an album, so a two-disc release indexes as two albums, each with half the
    tracks. The pill then reads "9 OF 18" for a record the user owns in full.

    THE CARE REQUIRED: the fix is to add those halves up, and adding up the
    wrong things is how a live Download button becomes an inert one, which is
    the failure this whole feature is built to avoid. In particular Waves itself
    creates "_NN" suffixed folders when a name collides, so two PARTIAL attempts
    at one album can sit side by side under the same parent with the same tags;
    summing anything that merely shares a parent and a title would read those as
    a complete copy and lock the user out of an album they do not have.

    So nothing is grouped without an EXPLICIT disc marker in the folder name.
    Both accepted layouts carry one, and a duplicate-download folder never does.
    """
    folder = (folder or "").rstrip("/\\")
    if not folder:
        return None
    parent, _, name = folder.replace("\\", "/").rpartition("/")
    if not name:
        return None
    if _DISC_ONLY.match(name):
        # "…/Album/CD2": every disc lives inside the album folder, which is the
        # group, and is also the folder worth revealing.
        return (parent, "") if parent else None
    m = _DISC_SUFFIX.match(name)
    if m and m.group(1).strip():
        # "…/Artist/Album (Disc 2)": the discs are siblings, so the group is the
        # parent plus the name with the marker removed.
        return parent, m.group(1).strip().casefold()
    return None


def _disc_number(folder: str) -> int | None:
    """The disc number a folder name carries, or None without a marker. What
    stops two copies of the SAME disc summing as if they were different discs:
    "Album (Disc 1)" beside "Album [CD 1]" (two rips of one disc, two naming
    styles) share a group, and adding them up claimed a complete album the user
    owns only half of."""
    folder = (folder or "").rstrip("/\\")
    name = folder.replace("\\", "/").rpartition("/")[2]
    m = _DISC_ONLY.match(name)
    if m:
        return _disc_num_value(m.group(1), m.group(2))
    m = _DISC_SUFFIX.match(name)
    if m and m.group(1).strip():
        return _disc_num_value(m.group(2), m.group(3))
    return None


def _parent_folder(candidate: dict) -> str:
    """The folder holding a candidate's own folder, the scope every disc of one
    set shares in both accepted layouts."""
    return str(candidate.get("id", "") or "").rstrip("/\\").replace("\\", "/").rpartition("/")[0]


def _disc_years_join(best: dict, c: dict) -> bool:
    """Whether two discs' year tags let them join one set. Both dated: within
    one (a boxed set's discs are routinely ripped from pressings a year
    apart). Both undated: agreement, as before. EXACTLY ONE dated: joined
    only when both discs declare the same nonzero disc_total, the release's
    own shape standing in as the witness the year could not give. Without
    that second witness the one-sided case stays refused: an undated disc
    from a different pressing once sailed through the survivor filter and
    completed a set it does not belong to, and refusing costs a partial pill
    and a live button, the fail-safe direction."""
    ya, yb = to_year_int(best.get("year")), to_year_int(c.get("year"))
    if ya is not None and yb is not None:
        return abs(ya - yb) <= 1
    if ya is None and yb is None:
        return True
    ta, tb = _as_int(best.get("disc_total")), _as_int(c.get("disc_total"))
    return ta > 0 and ta == tb


def _same_release(best: dict, survivors: list) -> list:
    """The survivors that are the same release as ``best`` down to its edition:
    the title matching with its qualifiers intact, and the year tags allowing
    the join (_disc_years_join: agreement, or the release's own declared
    shape vouching for a one-sided silence)."""
    want = str(best.get("title", "") or "")
    return [c for c in survivors if same_edition(str(c.get("title", "") or ""), want) and _disc_years_join(best, c)]


def _discs_by_name(best: dict, survivors: list) -> tuple[list, str] | None:
    """The set the FOLDER NAMES place ``best`` in, as (discs, reveal target), or
    None when the names do not describe one. Only folders carrying an explicit
    disc marker are ever grouped (see disc_group for why nothing looser is
    safe)."""
    group = disc_group(str(best.get("id", "") or ""))
    if group is None:
        return None
    discs = [c for c in _same_release(best, survivors) if disc_group(str(c.get("id", "") or "")) == group]
    if len(discs) < 2:
        return None
    # Every disc must be a DIFFERENT disc: two rips of disc 1 in two naming
    # styles share the group and the tags, and summing them claimed a complete
    # copy of an album the user owns one disc of, twice.
    nums = [_disc_number(str(c.get("id", "") or "")) for c in discs]
    if None in nums or len(set(nums)) != len(nums):
        return None
    if _short_of_its_discs(discs):
        return None
    # Reveal the album, not disc 2 of it. For "Album/CD2" the album folder holds
    # the set; for sibling "Album (Disc 2)" folders no single folder does, so the
    # chosen disc stays the reveal target.
    return discs, (group[0] if group[1] == "" else str(best.get("id", "") or ""))


def _discs_by_tag(best: dict, survivors: list) -> tuple[list, str] | None:
    """The set the DISC TAGS place ``best`` in, as (discs, reveal target), or
    None when the files never said.

    The folder names miss the sets nobody spelled "CD2": a bonus disc, a
    "Reprise" half, discs named after their subtitles. The files themselves
    almost always know, and asking the release rather than its spelling is what
    the rest of the matcher already does. The safety story is unchanged, because
    what makes a group trustworthy is not the evidence but the DISTINCTNESS
    check below: two half-finished download attempts at one album declare the
    same disc (or none), and are refused either way."""
    if _as_int(best.get("disc_no")) <= 0:
        return None
    parent = _parent_folder(best)
    total = _as_int(best.get("disc_total"))
    discs = [
        c
        for c in _same_release(best, survivors)
        if _parent_folder(c) == parent and _as_int(c.get("disc_no")) > 0 and _as_int(c.get("disc_total")) == total
    ]
    if len(discs) < 2:
        return None
    nums = [_as_int(c.get("disc_no")) for c in discs]
    if len(set(nums)) != len(nums) or _short_of_its_discs(discs):
        return None
    return discs, str(best.get("id", "") or "")


def _short_of_its_discs(discs: list) -> bool:
    """True when the discs found are fewer than the set SAYS it has. A two-disc
    pile of a three-disc release is missing a third of the record, and adding it
    up claimed a complete copy of an album the user cannot play through."""
    totals = {_as_int(c.get("disc_total")) for c in discs} - {0}
    return len(totals) == 1 and len(discs) < totals.pop()


def _declared_total(discs: list) -> int:
    """The tracks a joined set DECLARES: the sum, believed only when every disc
    carries a claim. One silent disc makes the sum an undercount, and an
    undercount is exactly what would call a short copy complete."""
    claims = [_as_int(c.get("declared")) for c in discs]
    return sum(claims) if all(claims) else 0


def _join_discs(best: dict, survivors: list) -> tuple[dict, int, int]:
    """Add up the discs of a multi-disc set, returning (candidate, track count,
    declared track count).

    A multi-disc release indexes as one album PER DISC, because the scanner
    calls any folder holding audio an album. So the best candidate is one disc
    of it and its track count is half the record. Two kinds of evidence can say
    that discs belong together, the folder names and the files' own disc tags;
    the names are tried first because they also name a better reveal target.

    Returns the candidate unchanged, its own count and its own claim when there
    is no set.
    """
    found = _discs_by_name(best, survivors) or _discs_by_tag(best, survivors)
    if found is None:
        return best, _as_int(best.get("tracks")), _as_int(best.get("declared"))
    discs, reveal = found
    # The set's runtime is its discs' runtimes summed, believed only when every
    # disc has one: a set missing one disc's minutes would refute true matches.
    runtimes = [_as_int(c.get("runtime")) for c in discs]
    runtime = sum(runtimes) if all(r > 0 for r in runtimes) else 0
    return (
        dict(best, id=reveal, runtime=runtime),
        sum(_as_int(c.get("tracks")) for c in discs),
        _declared_total(discs),
    )


def gate_title(title: str) -> str:
    """The stricter title form the completeness gate compares on: canon'd and
    stripped of explicit/clean markers ONLY.

    presence_key deliberately peels edition qualifiers so a deluxe copy on disk
    still finds the standard album and the pill can say you have something. That
    same collapse is far too loose to claim a copy is COMPLETE: it folds
    "Untitled (Black Is)" into "Untitled (Rise)", and every "Vol. …" of a series
    into one another. Keeping the qualifiers here means an edition difference
    costs a live download button, never a wrong claim of ownership."""
    return norm_title(canon(title))


# --- The edition detector --------------------------------------------------------
# gate_title keeps qualifiers verbatim, which is right about WHAT they say and
# wrong about HOW catalogs spell it. Measured against a real 11k-album library:
# 2394 titles end in a qualifier, and the heaviest families are one edition in
# several coats ("Deluxe" / "Deluxe Edition" / "Deluxe Version" were 274 rows
# of the same thing; "(2011 Remaster)" and "(Remastered 2011)" likewise). A
# verbatim compare fails every cross-spelling, so a copy that IS the edition
# on screen could never be proven whenever the two catalogs dressed it
# differently.
#
# The detector reads a trailing qualifier into a SET of edition tags plus any
# years, and only for vocabulary it knows. Synonyms collapse INSIDE a class
# (filler words like "edition"/"version" carry no meaning and drop out);
# classes never collapse into each other ({deluxe} != {super, deluxe} !=
# {expanded}, and acoustic/live/instrumental never equal the studio cut). A
# tail containing ANY word it does not know is not an edition tail at all and
# stays a literal part of the title, which is what keeps "Untitled (Black Is)"
# and "Untitled (Rise)" apart: recognition can only fold spellings of the same
# thing, never two different things.

# One canonical tag per recognised edition word. Plurals and hyphen variants
# map to the same tag; "super" is its own tag so a super deluxe stays a
# different set from a deluxe.
_EDITION_TAG_WORDS = {
    "deluxe": "deluxe",
    "super": "super",
    "remaster": "remaster",
    "remastered": "remaster",
    "remasters": "remaster",
    "expanded": "expanded",
    "special": "special",
    "legacy": "legacy",
    "collector": "collector",
    "collector's": "collector",
    "collectors": "collector",
    "anniversary": "anniversary",
    "live": "live",
    "acoustic": "acoustic",
    "unplugged": "unplugged",
    "instrumental": "instrumental",
    "instrumentals": "instrumental",
    "demo": "demo",
    "demos": "demo",
    "mono": "mono",
    "stereo": "stereo",
    "reissue": "reissue",
    "re-issue": "reissue",
    "rerelease": "reissue",
    "re-release": "reissue",
    "stripped": "stripped",
    "reimagined": "reimagined",
    "redux": "redux",
    "bonus": "bonus",
}
# Words that shape the phrase but not the release: "Deluxe Edition", "Deluxe
# Version" and "Deluxe" are one edition. "track(s)" is here for "Bonus Track
# Version"; a tail of nothing but filler has no tag word and stays literal.
_EDITION_FILLER_WORDS = {"edition", "version", "the", "and", "track", "tracks"}
# The tags that name a genuinely different release GROUP and therefore belong
# in presence_key (the same split _EDITION_KEEP_RE makes for the verbatim
# strip): a live album is not its studio album's family, but a deluxe is.
# The rest (deluxe, super, expanded, bonus, legacy, stripped, reimagined,
# redux) collapse out of the key, exactly as the strip already collapsed
# them, and only the GATE tells those editions apart.
_EDITION_GROUPING_TAGS = frozenset(
    {
        "remaster",
        "anniversary",
        "live",
        "acoustic",
        "unplugged",
        "instrumental",
        "demo",
        "mono",
        "stereo",
        "reissue",
        "collector",
        "special",
    }
)
_EDITION_YEAR_RE = re.compile(r"^(19|20)\d\d$")
# "20th" in "20th Anniversary": the ordinal is part of WHICH edition it is,
# so it joins the tag set and a 20th never equals a 25th.
_EDITION_ORDINAL_RE = re.compile(r"^\d+(st|nd|rd|th)$")
# The same trailing-group shape strip_edition_quals peels, and the dash form
# some taggers use instead ("Album - Deluxe Edition"). The dash tail is only
# ever an edition here if every word parses, so "Live - 1970" stays literal.
_EDITION_TAIL_PAREN_RE = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]\s*$")
_EDITION_TAIL_DASH_RE = re.compile(r"\s+-\s+([^-]+)$")


def _parse_edition_tail(tail: str) -> tuple[frozenset, frozenset] | None:
    """One trailing group as ``(tags, years)``, or None when any word in it is
    not recognised edition vocabulary (the tail is then part of the title).
    Segments ("Deluxe Edition/Remastered") merge into one set: the qualifier
    describes one release however it punctuates."""
    tags: set = set()
    years: set = set()
    words = re.split(r"[\s/;,+]+", tail.strip().lower())
    for word in words:
        word = word.strip(".'\"")
        if not word:
            continue
        if word in _EDITION_TAG_WORDS:
            tags.add(_EDITION_TAG_WORDS[word])
        elif _EDITION_ORDINAL_RE.match(word):
            tags.add(word)
        elif _EDITION_YEAR_RE.match(word):
            years.add(int(word))
        elif word not in _EDITION_FILLER_WORDS:
            return None
    if not tags:
        # Years or filler alone ("(2015 Edition)", "(1970)") name something
        # this vocabulary cannot identify; keeping them literal is the safe
        # direction.
        return None
    return frozenset(tags), frozenset(years)


def edition_key(title: str) -> tuple[str, frozenset, frozenset]:
    """``(base, tags, years)`` for a gate-normalised title: every trailing
    group that parses as edition vocabulary is folded into the tag set, and
    the first one that does not stops the peel and stays in the base."""
    text = gate_title(title)
    tags: frozenset = frozenset()
    years: frozenset = frozenset()
    while True:
        m = _EDITION_TAIL_PAREN_RE.search(text) or _EDITION_TAIL_DASH_RE.search(text)
        if not m:
            break
        parsed = _parse_edition_tail(m.group(1))
        if parsed is None:
            break
        tags |= parsed[0]
        years |= parsed[1]
        text = text[: m.start()].rstrip(" -.")
    return text, tags, years


def same_edition(a: str, b: str) -> bool:
    """Whether two titles name the SAME release down to its edition, spelled
    however each catalog spells it. The bases must be equal and the tag sets
    must be equal; the years must agree when both sides carry one (a bare
    "(Remastered)" matches "(2011 Remaster)", but 2009 never matches 2015:
    those are different masters). Verbatim equality still passes untouched,
    so this can only widen gate_title's compare across spellings of one
    edition, never across editions."""
    base_a, tags_a, years_a = edition_key(a)
    base_b, tags_b, years_b = edition_key(b)
    if base_a != base_b or tags_a != tags_b:
        return False
    return not years_a or not years_b or years_a == years_b


# A plausible release year anywhere in a date string, not glued to more digits.
# Tags write dates every way there is ("1999", "1999-03-01", "01/03/1999",
# "(p) 2004"), and the old leading-4-chars parse turned "01/03/1999" into no
# year and a bare "97" into the year 97, an int that then ACTIVELY rejected
# every candidate in the survivor filter. A year the parser cannot trust must
# read as absent (which never rejects), not as a wrong number (which always
# does).
_YEAR_ANYWHERE_RE = re.compile(r"(?<!\d)(?:19|20)\d\d(?!\d)")


def to_year_int(value):
    """A release year as an int (the first plausible 4-digit year found in the
    string), or None if absent or unparseable, so a missing year on either side
    never rejects a candidate."""
    m = _YEAR_ANYWHERE_RE.search(str(value)) if value is not None else None
    return int(m.group(0)) if m else None


# --- Quality of a local copy ---------------------------------------------------

# Codecs whose presence means the album needs no quality upgrade from a lossless
# source (bit depth aside). Everything else is lossy and TIDAL, whose base tier
# is 16-bit FLAC, always has better.
LOSSLESS_CODECS = frozenset(
    {"flac", "alac", "wav", "aiff", "aif", "aifc", "ape", "wv", "tta", "tak", "ofr", "dsf", "dff"}
)

# Codecs whose extension-derived name is not its display name. Everything else
# (flac, alac, mp3, aac, wma, ape, tta, tak, wav...) reads correctly uppercased,
# and an unknown codec still gets codec.upper() so no file type ever shows as a
# blank or unrecognized badge. "ogg"/"aif" cover rows scanned before the
# container/alias was resolved at capture (library_index normalizes new scans).
_CODEC_DISPLAY = {
    "wv": "WAVPACK",
    "aif": "AIFF",
    "aifc": "AIFF",
    "ogg": "OGG",
    "oga": "OGG",
    "spx": "SPEEX",
    "mpc": "MUSEPACK",
    "ofr": "OPTIMFROG",
}


def local_quality_label(codec: str, bitrate: int, bits: int, rate: int = 0) -> str:
    """A short human label for a local copy's quality, in the badge's language:
    lossless shows the codec plus whatever marks it above CD grade (bit depth
    over 16, sample rate over 48 kHz), lossy shows the codec and bitrate in
    KBPS. The rate matters on its own: a 16-bit/96 kHz file is hi-res despite
    its CD bit depth, and without it the badge undersold it as plain lossless.
    DSD is special-cased (1-bit at megahertz rates, so bit depth and KHZ read
    wrong): the DSD64/128/256 family name is the convention. Empty codec (no
    quality captured) yields no label."""
    codec = (codec or "").strip().lower()
    if not codec:
        return ""
    if codec in ("dsf", "dff"):
        return f"DSD{round(rate / 44100)}" if rate >= 2822400 else "DSD"
    name = _CODEC_DISPLAY.get(codec, codec.upper())
    if codec in LOSSLESS_CODECS:
        label = name
        if bits and bits > 16:
            label += f" {bits}-BIT"
        if rate and rate > 48000:
            label += f" {rate / 1000:g}KHZ"
        return label
    return name + (f" {bitrate}KBPS" if bitrate else "")


def local_quality_class(codec: str, bitrate: int, bits: int, rate: int = 0) -> str:
    """Coarse at-a-glance quality class for the badge color language: 'hires',
    'lossless', 'high' (healthy lossy), 'low' (small lossy), or '' when no
    quality was captured. DSD is always hi-res; a lossy copy with an unknown
    bitrate reads 'high' (benefit of the doubt, the common store-bought case)."""
    codec = (codec or "").strip().lower()
    if not codec:
        return ""
    if codec in ("dsf", "dff"):
        return "hires"
    if codec in LOSSLESS_CODECS:
        return "hires" if (bits and bits > 16) or (rate and rate > 48000) else "lossless"
    return "low" if bitrate and bitrate < 256 else "high"


def _as_int(value) -> int:
    """A quality number from an index row, or 0. The scanner writes ints, but
    index rows also arrive from older cache files and cross a bridge boundary,
    so a stray string like "320kbps" must cost a blank readout, not a
    ValueError inside a badge resolve."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# --- Album presence (cross-catalog, false-positive biased) ----------------------

# Play length is the identity witness no tag has to carry, so it works on the
# undated folders no other clause can prove. Tolerances: a track's length may
# differ by a moment between masterings (encoder padding, fade trims), so each
# track is granted 2 seconds (the same bar the lyrics client has matched LRCLIB
# candidates on for months), and an album the sum of its tracks' grants with a
# small floor. Refutation uses a far wider bar than proof on purpose: a hidden
# track or a long silence gap should not refute an otherwise-proven match, so
# only a disagreement no mastering difference explains (minutes, not moments)
# rules identity out.
_TRACK_DURATION_TOL_S = 2


def _album_duration_tol(tracks: int) -> int:
    return max(5, _TRACK_DURATION_TOL_S * int(tracks or 0))


def _year_veto_survivors(candidates: list, ty: int | None, tt: int, want_len: int) -> list:
    """The candidates a disagreeing year does not disqualify. The length is
    the stronger witness overriding a disagreeing year: remasters are
    routinely tagged with the ORIGINAL release's year, so a copy whose track
    count and summed seconds both agree with the release on screen is that
    release, whatever its year tag says. No durations on record, no override:
    for same-titled DIFFERENT albums (a self-titled series), the year is then
    the only separating fact and must keep rejecting, and their runtimes
    differ by whole songs anyway, so a real length never vouches for the
    wrong one."""

    def length_agrees(c: dict, c_tracks: int) -> bool:
        return (
            tt > 0
            and c_tracks == tt
            and want_len > 0
            and _as_int(c.get("runtime")) > 0
            and abs(_as_int(c.get("runtime")) - want_len) <= _album_duration_tol(tt)
        )

    def length_vouches(c) -> bool:
        if length_agrees(c, _as_int(c.get("tracks"))):
            return True
        # One disc of a multi-disc set can never vouch alone (its own count
        # and seconds are half the record), and a per-candidate veto silently
        # outlawed the length witness for every multi-disc remaster wearing
        # the original's year: the joined set is what must answer, summed
        # count and summed seconds together. Joined against ALL candidates,
        # not the survivors, because the sibling discs wear the same lying
        # year and are being vetoed by this same loop.
        joined, joined_tracks, _ = _join_discs(c, candidates)
        return length_agrees(joined, joined_tracks)

    survivors = []
    for c in candidates:
        cy = to_year_int(c.get("year"))
        if ty is not None and cy is not None and abs(cy - ty) > 1 and not length_vouches(c):
            continue
        survivors.append(c)
    return survivors


def decide_presence(title, artist, year, tracks, index, duration=0) -> dict:
    """Decide whether an album is present in a local album index (the scanned
    music folder).

    ``index`` maps presence_key -> a list of candidate dicts ``{"title": <str>,
    "year": <str|int|None>, "tracks": <int>, "id": <str>}`` (plus the optional
    quality facts ``codec``/``bitrate``/``bits``/``rate`` and the release's own
    declared shape ``declared``/``disc_no``/``disc_total``). Returns ``{"present",
    "partial", "sure", "full", "local_album_id", "local_tracks", "local_year"}``
    and the ``local_*`` quality readout.

    PRESENCE (which lights the pill) stays generous: a key match whose year does
    not actively contradict. Being wrong here costs a misleading badge.

    Beyond presence the verdict splits into two INDEPENDENT axes:

    ``sure`` is IDENTITY: this really is the same album (a year on both sides
    agreeing within one, or the folder's summed play length ``runtime``
    matching ``duration`` (TIDAL's total seconds) over the exact same track
    count; plus the title matching with its edition qualifiers intact). Its
    absence is the badge's "?".

    ``full`` is COVERAGE: the local copy holds at least every track. Its absence
    is what a caller renders as N OF M or a partial button.

    ``partial`` False remains the strict bar, both axes at once, because being
    wrong THERE costs the user a download they cannot start (the claim button,
    the bulk skip gate). Whatever the matcher cannot prove reports partial, so
    the caller keeps a live button.

    Refused outright: Various-Artists albums (the normalised "various artists"
    key collides across unrelated comps) and albums credited to nobody (a
    title-only match means nothing).
    """
    hidden = {"present": False, "partial": False, "sure": False, "full": False}
    hidden.update({"local_album_id": "", "local_tracks": 0, "local_year": "", "local_declared": 0})
    hidden["local_runtime"] = 0
    hidden.update({"local_quality": "", "local_codec": "", "local_lossless": False, "local_bits": 0, "local_rate": 0})
    hidden["local_class"] = ""
    if not index or is_various_artists(artist):
        return hidden
    # An empty artist makes presence_key a title-only key, which matches any
    # local folder that happens to share the title.
    if not norm_artist(canon(artist)):
        return hidden
    # A title that normalises to nothing ("-", "...", a lone explicit marker)
    # makes it an artist-only key: every such album of the artist shares it,
    # and gate_title is empty too, so same_edition would wave two DIFFERENT
    # punctuation-only titles through each other. The track side refuses the
    # same way (see decide_track_presence).
    if not norm_title(canon(title)):
        return hidden
    candidates = index.get(presence_key(title, artist))
    if not candidates:
        return hidden
    ty = to_year_int(year)
    # _as_int, not int(): tracks crosses the QML bridge and a garbage value
    # must degrade to "never said", not raise inside a badge resolve.
    tt = _as_int(tracks)
    want_len = _as_int(duration)

    survivors = _year_veto_survivors(candidates, ty, tt, want_len)
    if not survivors:
        return hidden
    # Prefer a candidate that can actually satisfy the gate (same title down to
    # its edition, however each catalog spells it) over a merely longer one, so
    # holding both "Album" and "Album (Deluxe)" still resolves against the
    # edition being viewed.
    want_title = str(title or "")
    best = max(
        survivors,
        key=lambda c: (same_edition(str(c.get("title", "") or ""), want_title), _as_int(c.get("tracks"))),
    )
    best, local_tracks, declared = _join_discs(best, survivors)
    if local_tracks <= 0:
        return hidden
    by = to_year_int(best.get("year"))
    # The duration witness. Only a copy holding EXACTLY the tracks on screen
    # can testify: a superset (a deluxe copy of the standard album) naturally
    # runs longer without being a different record, so count parity is the
    # precondition, not part of the verdict. Both sides must have spoken (0 is
    # "never said", never evidence), and refutation gets the far wider bar the
    # tolerance comment above explains.
    have_len = _as_int(best.get("runtime"))
    testifies = tt > 0 and local_tracks == tt and want_len > 0 and have_len > 0
    length_agrees = testifies and abs(have_len - want_len) <= _album_duration_tol(tt)
    length_refutes = testifies and abs(have_len - want_len) > 3 * _album_duration_tol(tt)
    # The verdict has two INDEPENDENT axes, and conflating them once made a
    # 12-of-12 undated folder read "partially in library" (nothing partial
    # about it, the match was merely unproven).
    #
    # SURE is identity: is this really the same album? Years present on both
    # sides and agreeing within one, and the title still matching with its
    # edition qualifiers intact (same_edition: spellings of one edition fold,
    # different editions never do). A row with no "title" (an index built
    # before this field existed) fails it, which is the safe direction.
    #
    # FULL is coverage: does the local copy hold at least every track?
    #
    # Every clause here is a false positive reproduced against a real library.
    # Identity can now be sworn by either witness: agreeing years, or agreeing
    # play length over the exact same track count. An undated folder whose
    # every second matches the release on screen is that release; an undated
    # folder with no length to offer still must not satisfy every same-titled
    # album. And whichever witness proved it, an outright length contradiction
    # (same count, minutes apart) unswears it: that is a different recording
    # wearing the same name.
    years_prove = ty is not None and by is not None and abs(by - ty) <= 1
    sure = (
        (years_prove or length_agrees)
        and not length_refutes
        and same_edition(str(best.get("title", "") or ""), want_title)
        # A release whose own files say it holds FEWER tracks than the one on
        # screen is a different release, however well the title and year agree:
        # a ten-track album on disk is not the eleven-track edition being
        # viewed. Only a shortfall rules identity out, never a surplus, because
        # a deluxe copy still contains the standard's tracks.
        and not (declared > 0 and tt > 0 and declared < tt)
    )
    # The count a complete copy has to reach. Normally TIDAL's, but tidalapi
    # reports None/-1 when numberOfTracks is absent, and with nothing to compare
    # against coverage could never be proven at all, so a complete album read
    # "partially in library" forever. The release's own claim settles it without
    # TIDAL, once identity is settled: a copy holding every track its files SAY
    # the release has is a complete copy. Unproven and unnumbered, nothing can.
    needed = tt if tt > 0 else (declared if sure else 0)
    full = needed > 0 and local_tracks >= needed  # short by even one track is not the album
    # Whatever TIDAL says the album holds, a folder holding fewer files than its
    # OWN release claims is short a track, and a copy short a track is not the
    # album. This is the half of the claim TIDAL's number cannot make: it counts
    # a different edition's tracks, the tags count this copy's missing ones.
    if declared > 0 and local_tracks < declared:
        full = False
    codec = str(best.get("codec", "") or "").lower()
    return {
        "present": True,
        # The strict bar (both axes at once), kept for the callers whose being
        # wrong costs a download: the claim button and the bulk skip gate.
        "partial": not (sure and full),
        "sure": sure,
        "full": full,
        "local_album_id": str(best.get("id", "") or ""),
        "local_tracks": local_tracks,
        # What the local release SAYS it holds, so a caller can spell out a
        # shortfall TIDAL's own count cannot see (14 of a 63-track set beside a
        # 14-track edition on screen).
        "local_declared": declared,
        "local_year": str(best.get("year", "") or ""),
        # The copy's summed seconds (0 when its files never said), so a caller
        # arbitrating an unproven match can hand a third party the one fact
        # that pins the copy to an edition.
        "local_runtime": have_len,
        # Quality of the local copy (from its representative file), so the badge
        # can say WHAT you have.
        "local_quality": local_quality_label(
            codec,
            _as_int(best.get("bitrate")),
            _as_int(best.get("bits")),
            _as_int(best.get("rate")),
        ),
        "local_codec": codec,
        "local_lossless": codec in LOSSLESS_CODECS,
        "local_bits": _as_int(best.get("bits")),
        "local_rate": _as_int(best.get("rate")),
        "local_class": local_quality_class(
            codec,
            _as_int(best.get("bitrate")),
            _as_int(best.get("bits")),
            _as_int(best.get("rate")),
        ),
    }


# --- Track presence (per-file rows, exact on normalised text) -------------------


def track_key(title: str, artist: str) -> tuple[str, str]:
    """The cross-catalog TRACK key: (normalised title, normalised artist), each
    canon'd first. Unlike the album key the title keeps its edition qualifiers:
    "Song (Acoustic)" and "Song" are different recordings, and a track pill that
    lit for the wrong one would claim a copy the user does not have. Only the
    explicit/clean markers are folded (norm_title), the same as albums, plus
    the featuring parenthetical: "Song (feat. B)" and a local "Song" are one
    recording spelled two ways, and the guest list travels separately
    (feat_guests) so different guests still never claim each other."""
    return (norm_title(_FEAT_TITLE_RE.sub("", canon(title or ""))), norm_artist(canon(artist)))


def _agreeing_guests(want_guests: frozenset, candidates: list) -> list:
    """The candidates whose featuring credit does not contradict the one on
    screen. The key strips featuring credits so "Song" finds "Song (feat.
    B)", and this is the price of that width, paid in full: when BOTH sides
    name guests and none overlap, those are different recordings ("feat. B"
    vs "feat. C") and the candidate is refused outright. One-sided silence
    still matches, which is the whole point of the strip."""
    if not want_guests:
        return candidates
    return [c for c in candidates if not (have := frozenset(c.get("guests") or ())) or not want_guests.isdisjoint(have)]


_TRACK_CLASS_ORDER = {"hires": 4, "lossless": 3, "high": 2, "low": 1, "": 0}


def _track_length_word(want_len: int, c: dict) -> bool | None:
    """The file's own testimony on one track candidate: True when its play
    length matches the track on screen within the 2-second bar the lyrics
    client has matched LRCLIB candidates on, False when it contradicts beyond
    any mastering difference, None when either side never said. A matching
    length is proof in itself: title, artist and seconds together name a
    recording (an edition qualifier lives in the track's title, so an acoustic
    cut never even shares the key)."""
    have = _as_int(c.get("length"))
    if want_len <= 0 or have <= 0:
        return None
    if abs(have - want_len) <= _TRACK_DURATION_TOL_S:
        return True
    return False if abs(have - want_len) > 5 * _TRACK_DURATION_TOL_S else None


def decide_track_presence(title, artist, index, album="", album_year="", duration=0) -> dict:
    """Decide whether one TRACK is present in the local per-track index.

    ``index`` maps track_key -> a list of candidate dicts ``{"id": <album
    folder path>, "codec", "bitrate", "bits", "rate", "album", "album_year"}``.
    Returns ``{present, sure, local_album_id}`` plus the ``local_*`` quality
    readout of the best copy.

    There is no completeness bar here because there is nothing to complete: a
    track is either on disk or not. ``present`` is therefore the loosest thing
    this module reports, and it is deliberately so: it answers "a song by this
    name and artist is somewhere in your library", which is what a hedged pill
    wants to say. It is NOT an answer to "you already have this album's copy",
    and no caller may read it as one. Still refused: an empty artist (a
    title-only key matches any local song that shares the title) and
    Various-Artists credits, same as albums.

    ``sure`` is that stricter question, and the IDENTITY axis the album verdict
    reports: the copy sits in the release the caller named. A track carries no
    year of its own and its title plus artist match every edition, compilation
    and re-recording that share them, so the holding folder is the only
    evidence there is. When the caller names the album the track belongs to
    (``album``/``album_year``), a candidate whose folder agrees on both
    (edition-qualified title, years within one) proves the match. Callers that
    name no album get sure False, which is the safe direction.

    The file's own play length only ever REFUTES. A copy minutes from the
    track on screen is a different recording wearing the same name, whatever
    its folder says, so ``duration`` (TIDAL's seconds) can throw a candidate
    out. It cannot vouch for one: seconds prove a RECORDING, and every
    compilation, best-of and re-release carries the same recording to the
    second. Letting length prove alone is what made a copy filed under one
    album answer for another (issue #24): the pill went green on a compilation
    whose tracks were nowhere on disk, and the bulk claim gate, which rides
    this axis, skipped them out of an album the user had explicitly asked for.

    Candidate choice follows that proof rather than fighting it: among copies
    whose folder agrees, the best quality wins; only when none agrees does the
    best copy overall answer, unproven. Otherwise a stray high-bitrate single
    would outrank the album copy sitting right there and report the whole
    match unproven."""
    hidden = {"present": False, "sure": False, "local_album_id": "", "local_quality": "", "local_class": ""}
    if not index or is_various_artists(artist) or not norm_artist(canon(artist)):
        return hidden
    if not norm_title(canon(title)):
        return hidden
    candidates = index.get(track_key(title, artist))
    if not candidates:
        return hidden
    candidates = _agreeing_guests(feat_guests(title, artist), candidates)
    if not candidates:
        return hidden

    def quality_rank(c):
        return (
            _TRACK_CLASS_ORDER.get(
                local_quality_class(
                    str(c.get("codec", "") or ""),
                    _as_int(c.get("bitrate")),
                    _as_int(c.get("bits")),
                    _as_int(c.get("rate")),
                ),
                0,
            ),
            _as_int(c.get("bitrate")),
        )

    want_album = str(album or "")
    ty = to_year_int(album_year)
    want_len = _as_int(duration)

    def proves(c) -> bool:
        if _track_length_word(want_len, c) is False:
            # A copy minutes from the track on screen is a different recording
            # wearing the same name, whatever its folder says.
            return False
        if not gate_title(want_album) or ty is None:
            return False
        by = to_year_int(c.get("album_year"))
        return by is not None and abs(by - ty) <= 1 and same_edition(str(c.get("album", "") or ""), want_album)

    proven = [c for c in candidates if proves(c)]
    best = max(proven or candidates, key=quality_rank)
    codec = str(best.get("codec", "") or "").lower()
    bitrate = _as_int(best.get("bitrate"))
    bits = _as_int(best.get("bits"))
    rate = _as_int(best.get("rate"))
    return {
        "present": True,
        "sure": bool(proven),
        "local_album_id": str(best.get("id", "") or ""),
        "local_quality": local_quality_label(codec, bitrate, bits, rate),
        "local_class": local_quality_class(codec, bitrate, bits, rate),
    }


def build_artist_rollup(album_index: dict) -> dict:
    """Roll the album-presence index up per artist: normalised-artist ->
    ``{"present": True, "albums": <distinct albums>, "tracks": <summed>,
    "lossless": <any lossless copy>}``. Groups on the artist half of the
    presence_key, counts each album key once (dedups editions, best track
    count), and drops Various-Artists buckets so a compilation never inflates a
    real artist's tally. Derived on demand from the in-memory index, so it needs
    no separate scan or persisted state."""
    rollup: dict = {}
    for (_title_key, artist_key), facts in (album_index or {}).items():
        if not artist_key or is_various_artists(artist_key):
            continue
        # A multi-disc set indexes one folder PER DISC, all in this one
        # bucket, and a bare max() dedups them like editions: an 18-track
        # double album tallied 9. Sum the set's distinct disc positions (best
        # copy per position, so duplicate editions of a disc still count
        # once), then let a single-folder copy outbid the sum if it holds
        # more.
        discs: dict[int, int] = {}
        plain = 0
        for f in facts:
            n = _as_int(f.get("tracks"))
            d = _as_int(f.get("disc_no"))
            if d > 0:
                discs[d] = max(discs.get(d, 0), n)
            else:
                plain = max(plain, n)
        best_tracks = max(plain, sum(discs.values()))
        lossless = any(str(f.get("codec", "") or "").lower() in LOSSLESS_CODECS for f in facts)
        r = rollup.get(artist_key)
        if r is None:
            rollup[artist_key] = {"present": True, "albums": 1, "tracks": best_tracks, "lossless": lossless}
        else:
            r["albums"] += 1
            r["tracks"] += best_tracks
            r["lossless"] = r["lossless"] or lossless
    return rollup
