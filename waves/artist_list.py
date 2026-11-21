"""Artist lists: the plain .txt or .csv files a follow list moves in.

Releases can follow every name on a list the user picks (Import list) and
save the artists it follows to one (Export list). This module is only the
file side: it turns a file into ``{"name"}`` rows (with ``"id"`` when the
file carries a TIDAL artist id) and writes rows back out. The bridge decides
what to follow; a person reviews it first.

Reading is forgiving, because a list may come from anywhere: a text editor,
Excel in any locale (commas, semicolons, tabs), a UTF-16 "Unicode text"
export, an old cp1252 file. A ``.csv`` is read as a table when it really is
one: a column headed like an artist name is used, else the first column. A
file that is one name a line (or a ``.csv`` that is really one) is read a
line at a time, so "Earth, Wind & Fire" is never split. Writing is strict:
a ``.csv`` gets a header and a BOM (Excel on Windows needs it to see UTF-8),
anything else one name a line.

Standard library plus waves.artist_import's name key. It never logs: the
names are the user's, and the path is theirs too.
"""

from __future__ import annotations

import csv
import io
import os
from collections.abc import Iterable

from waves.artist_import import name_key

# More than any list of artists needs; a file bigger than this is not one.
MAX_BYTES = 2 * 1024 * 1024
MAX_NAMES = 5000

CSV_HEADER = ("Artist", "TIDAL artist id")
_NAME_HEADERS = frozenset({"artist", "artist name", "artists", "album artist", "albumartist", "name"})
# Only a column that says TIDAL: an id matched here is followed with no
# search, so a numeric id from another service (Deezer numbers its artists
# too) under a plain "Artist ID" header must never be taken for one.
_ID_HEADERS = frozenset({"tidal artist id", "tidal id", "tidal_artist_id", "tidal_id"})
_DELIMITERS = ",;\t|"
# A table whose rows are mostly a single cell is a list of names after all.
_TABLE_SHARE = 0.5


class NotAList(ValueError):
    """The file is not text, or far too big to be a list of artists."""

    def __init__(self) -> None:
        super().__init__("not an artist list")


def read_artists(path: str) -> list[dict]:
    """The artists a list file names, in file order, one per name key.

    Raises OSError when the file cannot be read and NotAList (a ValueError)
    when it is not text, or far too big to be a list. An empty list is not an error
    here: the caller says what that means."""
    with open(path, "rb") as fh:
        raw = fh.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise NotAList
    text = decode(raw)
    is_csv = os.path.splitext(path)[1].lower() == ".csv"
    # Excel's "Unicode text" export is a .txt of tab-separated columns.
    rows = _table_rows(text, _DELIMITERS if is_csv else "\t")
    if rows is not None:
        return _clean(_from_table(rows))
    pairs = _from_lines(text, unquote=is_csv)
    if is_csv and pairs and pairs[0][0].lower() in _NAME_HEADERS:
        # A one-column .csv still has its header line.
        pairs = pairs[1:]
    return _clean(pairs)


def decode(raw: bytes) -> str:
    """Bytes to text, the encodings a list is likely to come in tried in
    turn. Raises NotAList for anything that is not text."""
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise NotAList from exc
    if b"\x00" in raw:
        raise NotAList
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return raw.decode("cp1252")
    except UnicodeDecodeError:
        # cp1252 leaves five bytes undefined; latin-1 takes every byte.
        return raw.decode("latin-1")


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines()]


def _wanted(line: str) -> bool:
    # "# a note" is a comment; "#1 Dads" is an artist.
    return bool(line) and line != "#" and not line.startswith("# ")


def _unquote(cell: str) -> str:
    if len(cell) >= 2 and cell[0] == cell[-1] == '"':
        return cell[1:-1].replace('""', '"').strip()
    return cell


def _from_lines(text: str, *, unquote: bool = False) -> list[tuple[str, str]]:
    """One name a line; in a one-column .csv a quoted cell is unquoted."""
    return [(_unquote(ln) if unquote else ln, "") for ln in _lines(text) if _wanted(ln)]


def _table_rows(text: str, delimiters: str) -> list[list[str]] | None:
    """The file as rows of cells, or None when it is really one name a line
    (the sniffer finds no delimiter, or most rows are a single cell)."""
    body = [ln for ln in text.splitlines() if _wanted(ln.strip())]
    if not body or not any(d in ln for ln in body[:50] for d in delimiters):
        return None
    # A header row names the delimiter outright (an Export list file always
    # has one). Otherwise the sniffer guesses it, but never the quote: it
    # can take an apostrophe for one ("Bell 'n' Arc", "'Til Tuesday"), and
    # then every quoted name ("Earth, Wind & Fire") reads back cut apart.
    delimiter = next((d for d in delimiters if d in body[0] and _headed(body[0], d)), "")
    if not delimiter:
        sample = "\n".join(body[:50])
        try:
            delimiter = csv.Sniffer().sniff(sample, delimiters=delimiters).delimiter
        except csv.Error:
            return None
        if delimiter == "," and _comma_names(body):
            return None
    reader = csv.reader(io.StringIO("\n".join(body)), delimiter=delimiter, quotechar='"', doublequote=True)
    rows = [[c.strip() for c in r] for r in reader]
    rows = [r for r in rows if any(r)]
    if not rows:
        return None
    wide = sum(1 for r in rows if len([c for c in r if c]) > 1)
    if wide < len(rows) * _TABLE_SHARE:
        return None
    return rows


def _headed(line: str, delimiter: str) -> bool:
    """Whether ``line`` is a header row: a cell named like the name or id column."""
    cells = {c.strip().strip('"').strip().lower() for c in line.split(delimiter)}
    return bool(cells & (_NAME_HEADERS | _ID_HEADERS))


def _comma_names(body: list[str]) -> bool:
    """A headerless file whose every unquoted comma has a space after it is a
    list of names that hold a comma ("Earth, Wind & Fire", "Tyler, The
    Creator"), not a table: a table's cells sit right after the comma. The
    sniffer finds a steady column count in such a list whenever every name
    happens to hold the same number of commas."""
    seen = False
    for line in body:
        quoted = False
        for i, ch in enumerate(line):
            if ch == '"':
                quoted = not quoted
            elif ch == "," and not quoted:
                if line[i + 1 : i + 2] != " ":
                    return False
                seen = True
    return seen


def _from_table(rows: list[list[str]]) -> list[tuple[str, str]]:
    head = [c.strip().lower() for c in rows[0]]
    name_col = next((i for i, c in enumerate(head) if c in _NAME_HEADERS), None)
    id_col = next((i for i, c in enumerate(head) if c in _ID_HEADERS), None)
    if name_col is None and id_col is None:
        # No header: every row is data, the name in the first column.
        return [(r[0], "") for r in rows if r]
    if name_col is None:
        name_col = 0
    out = []
    for r in rows[1:]:
        name = r[name_col] if name_col < len(r) else ""
        aid = r[id_col] if id_col is not None and id_col < len(r) else ""
        out.append((name, aid))
    return out


def _clean(pairs: Iterable[tuple[str, str]]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for name, aid in pairs:
        name = " ".join(str(name or "").split())
        key = name_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        row = {"name": name}
        aid = str(aid or "").strip()
        if aid.isdigit() and aid.isascii():
            row["id"] = aid
        out.append(row)
        if len(out) >= MAX_NAMES:
            break
    return out


def write_artists(path: str, rows: Iterable[dict]) -> int:
    """Save ``{"name", "id"}`` rows to ``path``, sorted by name: a ``.csv``
    with a header (read back with its ids), anything else one name a line.
    Returns how many were written. OSError reaches the caller."""
    clean = []
    seen: set[str] = set()
    for r in rows:
        name = " ".join(str(r.get("name") or "").split())
        key = name_key(name)
        if not key or key in seen:
            continue
        seen.add(key)
        clean.append((name, str(r.get("id") or "").strip()))
    clean.sort(key=lambda p: (p[0].casefold(), p[0]))
    if os.path.splitext(path)[1].lower() == ".csv":
        with open(path, "w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(CSV_HEADER)
            writer.writerows(clean)
    else:
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.writelines(name + "\n" for name, _ in clean)
    return len(clean)
