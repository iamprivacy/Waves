"""A stand-in for TIDAL's v2 playlist-folder listing that behaves like the live one.

The live endpoint (my-collection/playlists/folders) IGNORES ``offset``: every
offset returns the first page. The next page is reached only through the
``cursor`` value a response carries, and a listing that fits on one page comes
back without one. This fake does exactly that, so a pager that leans on offset
sees the first page forever, as the reporter of #59 did.

Rows are raw item dicts shaped like the live payload; the session's parsers
turn them into plain objects with an ``id``.
"""

from __future__ import annotations

from types import SimpleNamespace

from tidalapi.exceptions import TooManyRequests


def playlist_row(pid: str, broken: bool = False) -> dict:
    return {"itemType": "PLAYLIST", "data": {} if broken else {"uuid": pid}}


def folder_row(fid: str, name: str) -> dict:
    return {"itemType": "FOLDER", "name": name, "data": {"id": fid}}


class CollectionServer:
    """Serve listings by cursor.

    folders: parent id -> list of (id, name)
    playlists: folder id -> list of playlist ids (or raw rows)
    by_order: optional {order: [ids]} for the root playlists, per sort key
    rate_limit: folder ids whose listing raises TooManyRequests
    fail: folder ids whose listing request fails outright
    cursor_on_last_page: hand out a cursor on the final page too (the root
        listing always carries the key), so the empty page after it must end
        the pass
    stuck_cursor: always hand back the same cursor (a server that never moves)
    """

    def __init__(
        self,
        folders: dict | None = None,
        playlists: dict | None = None,
        by_order: dict | None = None,
        count: int | None = None,
        rate_limit: set | None = None,
        fail: set | None = None,
        cursor_on_last_page: bool = False,
        stuck_cursor: bool = False,
    ):
        self.folders = folders or {}
        self.playlists = playlists or {}
        self.by_order = by_order or {}
        self.rate_limit = rate_limit or set()
        self.fail = fail or set()
        self.cursor_on_last_page = cursor_on_last_page
        self.stuck_cursor = stuck_cursor
        self.calls: list[dict] = []
        self._count = count

    # The live request surface tidalapi exposes: session.request.request(...).
    def request(self, method, endpoint, base_url=None, params=None):
        assert method == "GET"
        assert endpoint == "my-collection/playlists/folders"
        params = dict(params or {})
        self.calls.append(params)
        assert len(self.calls) < 2000, "paging never stopped"
        folder_id = params["folderId"]
        if folder_id in self.rate_limit:
            raise TooManyRequests()
        if folder_id in self.fail:
            raise RuntimeError("listing request failed")
        rows = self._rows(folder_id, params.get("includeOnly"), params.get("order"))
        limit = int(params.get("limit", 50))
        # offset is deliberately ignored, as the live server does.
        start = int(params["cursor"]) if params.get("cursor") else 0
        page = rows[start : start + limit]
        end = start + len(page)
        payload = {"items": page, "totalNumberOfItems": len(rows)}
        if self.stuck_cursor:
            payload["cursor"] = "1"
        elif end < len(rows) or (self.cursor_on_last_page and page):
            payload["cursor"] = str(end)
        return SimpleNamespace(json=lambda: payload)

    def _rows(self, folder_id, include_only, order):
        if include_only == "FOLDER":
            return [folder_row(fid, name) for fid, name in self.folders.get(folder_id, [])]
        if folder_id == "root" and order in self.by_order:
            source = self.by_order[order]
        else:
            source = self.playlists.get(folder_id, [])
        return [r if isinstance(r, dict) else playlist_row(r) for r in source]

    def count(self) -> int:
        if self._count is not None:
            return self._count
        return len(self.folders.get("root", [])) + len(self.playlists.get("root", []))

    def session(self, mixes=None) -> SimpleNamespace:
        def parse_playlist(item):
            data = item.get("data", item)
            return SimpleNamespace(id=data["uuid"], num_tracks=1)

        def parse_folder(item):
            return SimpleNamespace(id=item["data"]["id"], name=item.get("name"))

        def playlist():
            return SimpleNamespace(parse_factory=lambda data: SimpleNamespace(id=data["uuid"]))

        return SimpleNamespace(
            request=SimpleNamespace(request=self.request),
            config=SimpleNamespace(api_v2_location="https://example.invalid/v2/"),
            parse_playlist=parse_playlist,
            parse_folder=parse_folder,
            playlist=playlist,
            user=SimpleNamespace(favorites=SimpleNamespace(get_playlists_count=self.count)),
            mixes=mixes or (lambda: SimpleNamespace(categories=[])),
        )
