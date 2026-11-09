"""Paging for TIDAL's v2 playlist-folder listing (my-collection/playlists/folders).

That one endpoint serves the root playlists, the folders at any level and the
playlists inside a folder. It ignores ``offset``: every offset returns the first
page again. The next page is reached through the ``cursor`` value each response
carries, which tidalapi never sends, so the requests are made here.

Every pass is bounded, whatever the server does: a page that brings no new row
ends it, a cursor that does not move ends it, and a hard page cap ends it (and
marks the listing short). Before this, a folder of 50 or more playlists
returned the same full page forever and the library load never finished (#59).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from tidalapi.exceptions import TooManyRequests

logger = logging.getLogger("waves.collection_paging")

LISTING_ENDPOINT = "my-collection/playlists/folders"
# The v2 collection endpoints cap a page at 50.
PAGE_LIMIT = 50
# 200 pages of 50 is 10,000 rows in a single listing, far past any real
# library. The cap only exists so a server that keeps handing out pages can
# never hold the library load open forever.
MAX_PAGES = 200


@dataclass
class Listing:
    """One listing read to its end.

    rows: the parsed rows in the server's order, each id once.
    repeats: rows the pages handed back more than once.
    short: the listing was cut off (page cap, or rows that would not parse),
        so it may be missing rows the account really has.
    """

    rows: list = field(default_factory=list)
    repeats: int = 0
    short: bool = False


def _get(session: Any, params: dict) -> dict:
    response = session.request.request("GET", LISTING_ENDPOINT, base_url=session.config.api_v2_location, params=params)
    return response.json() or {}


def _take_rows(items: list, parse: Callable[[dict], Any], seen: set[str], listing: Listing) -> tuple[int, int]:
    """Parse one page into ``listing``; returns (new rows, unreadable rows)."""
    fresh = 0
    bad = 0
    for item in items:
        try:
            row = parse(item)
        except TooManyRequests:
            raise
        except Exception:
            bad += 1
            continue
        key = str(getattr(row, "id", "") or "")
        if key in seen:
            listing.repeats += 1
            continue
        seen.add(key)
        listing.rows.append(row)
        fresh += 1
    return fresh, bad


def _next_cursor(payload: dict, page_size: int, cursor: str | None) -> tuple[bool, str | None]:
    """Whether the listing is done, and the cursor for the next page (None
    falls back to offset)."""
    next_cursor = payload.get("cursor")
    if next_cursor:
        next_cursor = str(next_cursor)
        return next_cursor == cursor, next_cursor
    return page_size < PAGE_LIMIT, None


def page_listing(
    session: Any,
    folder_id: str,
    include_only: str,
    parse: Callable[[dict], Any],
    order: str = "NAME",
    direction: str | None = None,
) -> Listing:
    """Read one level of the playlist-folder listing, every page, each row once.

    Args:
        session: TIDAL session.
        folder_id: "root" or a folder id.
        include_only: "PLAYLIST" or "FOLDER".
        parse: Turns one raw row (the item dict) into a tidalapi object with
            an ``id``. A row that fails to parse is skipped and the listing
            marked short; a rate limit raised while parsing is passed on.
        order: The server-side sort key.
        direction: "ASC" or "DESC", or None for the server default.

    Returns:
        Listing. TooManyRequests and request failures propagate to the caller.
    """
    base = {"folderId": folder_id, "limit": PAGE_LIMIT, "order": order, "includeOnly": include_only}
    if direction:
        base["orderDirection"] = direction
    listing = Listing()
    seen: set[str] = set()
    skipped = 0
    cursor: str | None = None
    read = 0
    for _page in range(MAX_PAGES):
        params = dict(base)
        if cursor:
            params["cursor"] = cursor
        else:
            # Kept for the first page, and as a fallback should a response
            # ever come without a cursor while the page is still full.
            params["offset"] = read
        payload = _get(session, params)
        items = payload.get("items") or []
        if not items:
            break
        read += len(items)
        fresh, bad = _take_rows(items, parse, seen, listing)
        skipped += bad
        if fresh == 0:
            # Nothing new on the page: the server did not move, so asking again
            # would only bring the same page back. (A page of nothing but
            # unreadable rows ends here too; the skip already marks it short.)
            break
        done, cursor = _next_cursor(payload, len(items), cursor)
        if done:
            break
    else:
        listing.short = True
        logger.warning("Playlist listing stopped at the %d page cap (%d rows)", MAX_PAGES, len(listing.rows))
    if skipped:
        listing.short = True
        logger.warning("Skipped %d unreadable rows in a playlist listing", skipped)
    return listing
