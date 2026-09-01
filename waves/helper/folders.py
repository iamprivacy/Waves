"""TIDAL playlist-folder tree: sweep and {folder_path} template handling.

The v2 folder endpoints only ever return one level at a time, and tidalapi's
parsed objects cannot be trusted for tree structure (Folder.parent_folder_id
is never set by parse() and stays at the constructor default "root"), so the
walk tracks parent paths itself. Playlist objects carry no folder reference at
all: the id-to-path map built here is the only source of folder attribution,
including for playlists downloaded individually.

Everything in this module is bridge-independent on purpose so it applies
unchanged to any backend layout.
"""

from __future__ import annotations

import contextlib
import logging
import pathlib
from dataclasses import dataclass, field

from pathvalidate import sanitize_filename
from tidalapi import Playlist, Session
from tidalapi.exceptions import TooManyRequests
from tidalapi.playlist import Folder

from waves.helper.path import sanitize_name_component

logger = logging.getLogger("waves.folders")

FOLDER_PATH_TOKEN = "{folder_path}"  # noqa: S105 - a path-template token, not a secret

# The v2 collection endpoints cap page size at 50 (documented in tidalapi).
_PAGE_LIMIT = 50
# Safety rail against a cyclic or absurd tree; TIDAL's UI nests far shallower.
_MAX_DEPTH = 8


@dataclass
class FolderNode:
    """One folder with its resolved display path ("Country/Bluegrass")."""

    folder: Folder
    id: str
    name: str
    path: str
    parent_path: str
    # tidalapi's parsed Folder.parent_folder_id is never set (stays "root"),
    # so the true parent is recorded here during the walk.
    parent_id: str = "root"
    playlists: list[Playlist] = field(default_factory=list)
    subfolder_count: int = 0


@dataclass
class FolderTree:
    """Result of a full sweep. partial means a rate limit cut the walk short."""

    nodes: list[FolderNode] = field(default_factory=list)
    playlist_paths: dict[str, str] = field(default_factory=dict)
    partial: bool = False

    def node_by_id(self, folder_id: str) -> FolderNode | None:
        for node in self.nodes:
            if node.id == folder_id:
                return node
        return None

    def folder_path_of(self, playlist_id: str) -> str:
        return self.playlist_paths.get(str(playlist_id), "")

    def children_of(self, folder_id: str) -> list[FolderNode]:
        """Direct subfolders, matched by the recorded parent id (paths are not
        unique: two sibling folders may share a name)."""
        return [n for n in self.nodes if n.parent_id == folder_id]

    def playlists_under(self, folder_id: str) -> list[Playlist]:
        """Every playlist in the folder and all of its subfolders, in walk
        order (a folder "download all" is recursive)."""
        node = self.node_by_id(folder_id)
        if node is None:
            return []
        result = list(node.playlists)
        pending = [node.id]
        while pending:
            current = pending.pop(0)
            for child in self.children_of(current):
                result.extend(child.playlists)
                pending.append(child.id)
        return result


def _page_folders(session: Session, parent_id: str) -> list[Folder]:
    result: list[Folder] = []
    offset = 0
    while True:
        batch = session.user.favorites.playlist_folders(limit=_PAGE_LIMIT, offset=offset, parent_folder_id=parent_id)
        if not batch:
            break
        result.extend(batch)
        if len(batch) < _PAGE_LIMIT:
            break
        offset += _PAGE_LIMIT
    return result


def _page_playlists(node: FolderNode) -> tuple[list[Playlist], bool]:
    """All playlists directly inside a folder, and whether the listing is short.

    Folder.items() is not paginated by tidalapi and a single malformed item in
    the v2 payload raises out of the whole page (Playlist.parse subscripts
    hard), so a failing page is logged and skipped rather than failing the
    sweep.

    That skip abandons every REMAINING page too, not just the failed one, so it
    has to be reported: a folder of 60 whose offset=50 page fails otherwise
    caches as a complete folder of 50. The tile would read "50 playlists",
    DOWNLOAD ALL would queue 50 and the rollup would finish green, leaving the
    user believing the folder is fully on disk. The second element is the
    caller's cue to mark the tree partial, exactly as a 429 does.
    """
    result: list[Playlist] = []
    offset = 0
    while True:
        try:
            batch = node.folder.items(offset=offset, limit=_PAGE_LIMIT)
        except TooManyRequests:
            raise
        except Exception:
            logger.exception("Skipping unparsable folder items page (folder id %s, offset %d)", node.id, offset)
            return result, True
        if not batch:
            break
        result.extend(batch)
        if len(batch) < _PAGE_LIMIT:
            break
        offset += _PAGE_LIMIT
    return result, False


def walk_playlist_tree(session: Session, root_folders: list[Folder] | None = None) -> FolderTree:
    """Breadth-first sweep of the whole playlist-folder tree.

    Args:
        session: TIDAL session.
        root_folders: already-fetched root folders to reuse (the library sweep
            has them in hand); fetched here when None.

    Returns:
        FolderTree with every folder (all levels), each folder's playlists,
        and the playlist-id-to-folder-path map. On a 429, or on a folder whose
        items listing was cut short by an unparsable page, the tree returned is
        whatever was walked so far, with partial=True.
    """
    tree = FolderTree()
    try:
        level = root_folders if root_folders is not None else _page_folders(session, "root")
        pending: list[tuple[Folder, str, str]] = [(f, "", "root") for f in level]
        depth = 0
        while pending and depth < _MAX_DEPTH:
            next_level: list[tuple[Folder, str, str]] = []
            for folder, parent_path, parent_id in pending:
                name = str(getattr(folder, "name", "") or "")
                node = FolderNode(
                    folder=folder,
                    id=str(folder.id),
                    name=name,
                    path=f"{parent_path}/{name}" if parent_path else name,
                    parent_path=parent_path,
                    parent_id=parent_id,
                )
                tree.nodes.append(node)
                subfolders = _page_folders(session, node.id)
                node.subfolder_count = len(subfolders)
                next_level.extend((sub, node.path, node.id) for sub in subfolders)
                node.playlists, truncated = _page_playlists(node)
                if truncated:
                    tree.partial = True
                for playlist in node.playlists:
                    tree.playlist_paths[str(playlist.id)] = node.path
            pending = next_level
            depth += 1
    except TooManyRequests:
        logger.warning("Folder sweep rate limited, returning partial tree (%d folders)", len(tree.nodes))
        tree.partial = True
    return tree


def sanitize_folder_path(
    folder_path: str,
    illegal_replacement: str = "",
    illegal_map: dict[str, str] | None = None,
) -> str:
    """Per-segment sanitization of a folder display path.

    Each segment goes through the same sanitizer as every other template token,
    stand-ins included, so illegal characters and Windows reserved names are
    handled per directory and a folder is spelled the way the rest of the
    library is. Separators arriving inside the raw value split into segments
    here and can never change depth later; "." and ".." segments and empties are
    dropped, so a hostile payload cannot escape the base path.

    Args:
        folder_path (str): The folder's display path, separators included.
        illegal_replacement (str, optional): Text written where a rejected
            character is removed. Defaults to "", plain removal.
        illegal_map (dict[str, str] | None, optional): Per-character stand-ins,
            applied before the general one. Defaults to None.

    Returns:
        str: The sanitized path, "/" separated, possibly empty.
    """
    segments = []
    for raw in folder_path.replace("\\", "/").split("/"):
        cleaned = sanitize_name_component(raw, illegal_replacement, illegal_map)
        if not cleaned or cleaned in (".", ".."):
            continue
        # Braces become parentheses. This value is spliced into the template
        # BEFORE format_path_media runs, and that formatter builds its match
        # list from the already-substituted string, so a folder someone named
        # "Best of {artist_name}" would be read as a placeholder and expanded:
        # one playlist scattered across a directory per artist. There is no
        # escape to lean on either, the formatter matches with a regex rather
        # than str.format, so "{{" would survive into the path as-is.
        # {folder_path} is the only value injected ahead of the formatter, so
        # this is the only place it can happen.
        segments.append(cleaned.replace("{", "(").replace("}", ")"))
    return "/".join(segments)


def _legacy_folder_path(folder_path: str) -> str:
    """The spelling 0.1.17 gave a folder path: bare sanitize_filename plus a
    strip per segment, no stand-ins and no spacing tidy. Kept only so a
    library built by that release can be recognized on disk and its folders
    reused (see _prefer_existing_folder_spelling); never used for a new
    folder."""
    segments = []
    for raw in folder_path.replace("\\", "/").split("/"):
        cleaned = sanitize_filename(raw).strip()
        if not cleaned or cleaned in (".", ".."):
            continue
        segments.append(cleaned.replace("{", "(").replace("}", ")"))
    return "/".join(segments)


def _prefer_existing_folder_spelling(template: str, folder_path: str, value: str, base_path) -> str:
    """An existing library keeps the folder spelling it already has.

    The {folder_path} level is baked into the template before the download
    engine runs, so the older-spelling fallback every other directory level
    gets (_keep_existing_layout) can never see it: by the time an item is
    formatted the folder is literal text. Without this, the stand-ins and the
    spacing tidy respelled a folder a 0.1.17 library already uses, and the
    next download of any playlist inside it wrote a complete second copy under
    the new spelling, invisible to skip_existing.

    The probe only runs when it can be answered honestly: a known base path, a
    literal (token-free) template prefix, and a legacy spelling of the same
    depth (a folder whose old spelling dropped a segment entirely points at an
    ancestor, which exists whether or not anything was downloaded, the same
    non-evidence _keep_existing_collection_layout refuses).
    """
    if not base_path or not value:
        return value
    legacy = _legacy_folder_path(folder_path)
    if not legacy or legacy == value or legacy.count("/") != value.count("/"):
        return value
    prefix = template.split(FOLDER_PATH_TOKEN, 1)[0]
    if "{" in prefix:
        return value
    with contextlib.suppress(OSError):
        if (pathlib.Path(str(base_path)).expanduser() / prefix / legacy).is_dir():
            return legacy
    return value


def apply_folder_path(
    template: str,
    folder_path: str,
    illegal_replacement: str = "",
    illegal_map: dict[str, str] | None = None,
    base_path=None,
) -> str:
    """Substitute {folder_path} in a path template before formatting.

    This runs in the bridge, ahead of format_path_media, because that
    formatter sanitizes every substituted value with the slashes deleted; the
    folder path is the one value whose separators must survive. The sanitized
    value gets a trailing slash so "Playlists/{folder_path}{playlist_name}"
    reads naturally; an empty path leaves "Playlists//name", which pathlib
    collapses.

    A leading one does not collapse, though. The token reference shows a sample
    of "Country/Bluegrass/", which invites writing "{folder_path}/{name}/...",
    and for a playlist in no folder that renders as "/name/...". The download
    path is built as ``Path(path_base) / relative``, and an absolute right-hand
    side REPLACES path_base rather than joining to it, so the files would land
    at the filesystem root instead of in the download folder. Strip it, unless
    the template itself was written absolute (the user's own business, and
    already the behaviour before {folder_path} existed).

    Args:
        template (str): The path template, possibly holding {folder_path}.
        folder_path (str): The folder's display path.
        illegal_replacement (str, optional): Text written where a rejected
            character is removed. Defaults to "", plain removal.
        illegal_map (dict[str, str] | None, optional): Per-character stand-ins.
            Defaults to None.
        base_path (optional): The download base directory. When given, a folder
            a 0.1.17 library already spelled differently on disk keeps that
            spelling (see _prefer_existing_folder_spelling). Defaults to None,
            no disk probe (the settings preview shows the preferred spelling).

    Returns:
        str: The template with {folder_path} substituted.
    """
    if FOLDER_PATH_TOKEN not in template:
        return template
    value = sanitize_folder_path(folder_path, illegal_replacement, illegal_map)
    value = _prefer_existing_folder_spelling(template, folder_path, value, base_path)
    if value:
        value += "/"
    out = template.replace(FOLDER_PATH_TOKEN, value)
    return out if template.startswith(("/", "\\")) else out.lstrip("/\\")
