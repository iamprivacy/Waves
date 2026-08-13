"""Do I already have this exact recording somewhere under this folder?

WHY THIS EXISTS
---------------
"Skip existing" answers a question about a PATH: is there already a file where
this download would land? That misses the same song filed somewhere else, which
is what happens when two editions of a release (standard and deluxe, album and
soundtrack) are downloaded separately: every shared song arrives a second time
in the second edition's folder.

Answering the real question ("do I have this recording?") needs an identity that
survives a different folder, a different file name and a different track number.
This module uses the one identifier that does: the ISRC, the industry code for a
specific recording, which Waves already writes into every file's tags and which
tidaler reads off every TIDAL track. Two files with the same ISRC are the same
recording; two files with different ISRCs are not, no matter how alike their
names look. Nothing here parses a file name, so how the user organizes or names
their library is irrelevant, and files Waves did not write count too as long as
whoever tagged them wrote an ISRC (most taggers do).

WHAT IT DELIBERATELY IS NOT
---------------------------
Not a library index. It reads ONE folder subtree on demand (the folder a
download is about to write into, or its parent), caches the answer keyed on that
subtree's directory mtimes, and forgets it when they change. There is no
database, no background scan, and no notion of the user's library as a whole:
that is the library manager's job, and this must not grow into it.

A file with no ISRC tag simply never matches, so an untagged library produces no
skips rather than wrong ones. Unreadable files are treated the same way. The
bias is always "download again rather than wrongly skip": a duplicate wastes
disk, a wrong skip silently loses a song.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterable
from threading import Lock

# Extensions worth opening for tags. Kept in step with the formats Waves can
# write; anything else in a music folder (art, playlists, logs) is skipped
# without a read.
AUDIO_EXTENSIONS = frozenset(
    {".flac", ".mp3", ".m4a", ".mp4", ".aac", ".alac", ".ogg", ".oga", ".opus", ".wav", ".aiff", ".aif", ".aifc"}
)

# Never walk deeper than this from the scan root. A download folder is only ever
# a handful of levels deep (artist / album / disc), so this is a runaway guard
# for a root pointed at something enormous, not a real limit.
MAX_DEPTH = 4


def is_audio(name: str) -> bool:
    return os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS


def normalise_isrc(value) -> str | None:
    """An ISRC in comparable form, or None when there isn't a usable one.

    ISRCs are printed with spaces or dashes about as often as not (``GB-AYE-
    12-34567``), so strip everything that is not alphanumeric and upper-case
    the rest. A value that does not end up 12 characters is not an ISRC and is
    rejected rather than half-matched.
    """
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    if not isinstance(value, str):
        return None
    text = "".join(ch for ch in value if ch.isalnum()).upper()
    return text if len(text) == 12 else None


# How each container spells ISRC when mutagen's "easy" view doesn't expose it:
# ID3 keeps it in a TSRC frame, Vorbis comments in a plain key, MP4 in either a
# freeform iTunes atom or the ©isr atom.
_RAW_ISRC_KEYS = ("TSRC", "isrc", "ISRC", "----:com.apple.iTunes:ISRC", "©isr")


def _tag_text(value) -> str | None:
    """One tag value as text, whatever shape the container hands back (an ID3
    frame, a list, raw bytes), or None when it can't be read as text."""
    if hasattr(value, "text"):  # an ID3 frame
        value = value.text
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", "ignore")
        except Exception:
            return None
    return value if isinstance(value, str) else None


def _isrc_from_raw_tags(tags) -> str | None:
    """ISRC from a container's raw tag mapping, trying each spelling in turn."""
    if tags is None:
        return None
    for key in _RAW_ISRC_KEYS:
        with contextlib.suppress(Exception):  # key absent, or a mapping that raises
            found = normalise_isrc(_tag_text(tags[key]))
            if found:
                return found
    return None


def read_isrc(path: str) -> str | None:
    """The ISRC tagged on one audio file, or None.

    Reads mutagen's format-neutral "easy" view first, then falls back to the
    raw tag keys each container actually uses. Any failure reads as "no ISRC",
    never as an error: this runs over whatever files sit in a user's folder,
    including ones no tagger has touched and ones that are not really audio.
    """
    try:
        import mutagen

        easy = mutagen.File(path, easy=True)
        if easy is not None:
            found = normalise_isrc(easy.get("isrc"))
            if found:
                return found
        audio = mutagen.File(path)
    except Exception:
        return None
    return _isrc_from_raw_tags(getattr(audio, "tags", None)) if audio is not None else None


def _nonempty_file(path: str) -> bool:
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


class RecordingScan:
    """On-demand ISRC map for one folder subtree, cached until it changes.

    ``read_isrc`` and ``walk`` are injected so the matching logic unit-tests
    without real audio files or a real filesystem.
    """

    def __init__(
        self,
        *,
        read_isrc: Callable[[str], str | None] = read_isrc,
        audio_filter: Callable[[str], bool] = is_audio,
        max_depth: int = MAX_DEPTH,
        max_files: int = 20000,
    ) -> None:
        self._read_isrc = read_isrc
        self._is_audio = audio_filter
        self._max_depth = int(max_depth)
        self._max_files = int(max_files)
        self._lock = Lock()
        # root -> (signature, {isrc: path}). One entry per root asked about;
        # a download job only ever asks about one or two.
        self._cache: dict[str, tuple[tuple, dict[str, str]]] = {}

    def forget(self, root: str | None = None) -> None:
        """Drop cached scans (all of them, or one root). Called after a write
        lands so the next lookup sees the file just added."""
        with self._lock:
            if root is None:
                self._cache.clear()
            else:
                self._cache.pop(os.path.normcase(os.path.abspath(root)), None)

    def path_for(self, root: str, isrc: str | None) -> str | None:
        """Path of an existing file under ``root`` holding recording ``isrc``.

        None when there is no ISRC to match on, nothing matches, or the file
        that once matched is gone. The returned path is re-checked on the spot,
        so a deleted copy never counts as owned.
        """
        wanted = normalise_isrc(isrc) if isrc else None
        if not wanted or not root:
            return None
        try:
            index = self._index(root)
        except Exception:
            return None  # an unreadable folder must never gate a download
        found = index.get(wanted)
        return found if found and _nonempty_file(found) else None

    def have(self, root: str, isrc: str | None) -> bool:
        return self.path_for(root, isrc) is not None

    def _index(self, root: str) -> dict[str, str]:
        key = os.path.normcase(os.path.abspath(root))
        signature = self._signature(key)
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] == signature:
                return cached[1]
        # Built outside the lock: reading tags off a network share is slow, and
        # a concurrent duplicate build costs time but cannot corrupt anything
        # (both produce the same map, the later one wins).
        index = self._build(key)
        with self._lock:
            self._cache[key] = (signature, index)
        return index

    def _signature(self, root: str) -> tuple:
        """Cheap "has this subtree changed" stamp: the mtime of every directory
        in it. A file added, removed or renamed changes its directory's mtime,
        which is exactly when a cached map goes stale. Tag edits in place are
        not caught, and do not matter here (an ISRC does not change)."""
        stamps: list[tuple[str, float]] = []
        for directory in self._directories(root):
            try:
                stamps.append((directory, os.stat(directory).st_mtime))
            except OSError:
                continue
        return tuple(sorted(stamps))

    def _directories(self, root: str) -> Iterable[str]:
        if not os.path.isdir(root):
            return []
        found: list[str] = []
        base_depth = root.rstrip(os.sep).count(os.sep)
        for current, subdirs, _files in os.walk(root):
            found.append(current)
            if current.rstrip(os.sep).count(os.sep) - base_depth >= self._max_depth:
                subdirs[:] = []
        return found

    def _build(self, root: str) -> dict[str, str]:
        index: dict[str, str] = {}
        read = 0
        for directory in self._directories(root):
            try:
                entries = sorted(os.listdir(directory))
            except OSError:
                continue
            for name in entries:
                if not self._is_audio(name):
                    continue
                path = os.path.join(directory, name)
                if not _nonempty_file(path):
                    continue
                read += 1
                if read > self._max_files:
                    return index
                found = self._read_isrc(path)
                # First writer wins: a stable answer whichever order the
                # filesystem lists things in.
                if found and found not in index:
                    index[found] = path
        return index
