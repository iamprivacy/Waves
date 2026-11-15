"""An artist bio for the artists TIDAL has none for: Wikipedia's intro,
else the Discogs profile, found through MusicBrainz.

TIDAL has no biography for most artists. MusicBrainz has none either, but
it knows which artist a TIDAL page is and links that artist to Wikidata
(and so to its Wikipedia article) and to Discogs. With the opt-in setting
(``artist_bio_fallback``, off by default) the artist page asks, in order:

1. Which MusicBrainz artist the TIDAL artist IS. A proof already on record
   (the Releases check's, scope "tidal"), else MusicBrainz's own link from
   the artist's TIDAL page (one lookup by URL, exact), else the Releases
   check's rule (``musicbrainz.resolve_artist``: the same name and at least
   two release titles TIDAL also lists). Anything less is no match: a bio
   is never shown on a guess, since names collide.
2. The English Wikipedia article's intro, by the artist's Wikidata item.
3. When there is none, the Discogs profile, cleaned to plain text.

Every source is keyless, so nothing here can be revoked for everyone at
once; each user's own connection counts against each service's limits,
and a page asks once per artist and then reads the record.

The answer is put on record in the MusicBrainz service's file
(``MBService.bio_put``) with the source's own id, so an expired bio is read
again in one request. Discogs' API terms say nothing it serves may be shown
more than six hours old, so a Discogs bio is kept six hours and a failed
refresh shows nothing rather than an older copy. Each source needs its
credit beside the text (``source`` and ``url`` in the answer).

Pure standard library over one injectable ``fetch`` returning ``(status,
body)``, so tests drive it with canned answers and never touch the network.

Privacy: a request carries a TIDAL artist id, an artist name or a
MusicBrainz, Wikidata, Wikipedia or Discogs id, never an account id, a
token or a local path, and only while the setting is on. Log lines carry
outcomes; a name is logged only as user content, an id never beside one.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from waves import musicbrainz
from waves.mb_service import MB_BASE, Pacer, default_user_agent
from waves.tls import tls_context

logger = logging.getLogger("waves.bio")

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
DISCOGS_API = "https://api.discogs.com"
TIDAL_ARTIST_URL = "https://tidal.com/artist/{}"
TIMEOUT_SEC = 15

# How long each answer is kept. Discogs' API terms cap what is shown at six
# hours old; Wikipedia's text is CC BY-SA and may be kept, re-read monthly.
WIKIPEDIA_TTL_SEC = 30 * 86400
DISCOGS_TTL_SEC = 6 * 3600
NONE_TTL_SEC = 7 * 86400
# MusicBrainz's link from a TIDAL page, kept as long as an artist read.
URL_TTL_SEC = musicbrainz.ARTIST_TTL_SEC

# Discogs allows 25 unauthenticated requests a minute; a lookup makes one.
_DISCOGS_PACER = Pacer(60.0 / 25)


# ---- the answer ---------------------------------------------------------------


def lookup(
    client: musicbrainz.MusicBrainzClient,
    svc,
    tidal_id: str,
    name: str,
    rows: list[dict],
    *,
    fetch: Callable[[str], tuple[int, str]] | None = None,
    alive: Callable[[], bool] | None = None,
) -> dict | None:
    """The fallback bio for a TIDAL artist: ``{text, source, url}`` with
    ``source`` "wikipedia" or "discogs", or "" when there is none (looked,
    no sure artist or no text). None when a service could not be asked:
    nothing is put on record and the next view asks again.

    ``rows`` are the artist's TIDAL releases (``title``, ``date``), the
    evidence the title rule weighs. ``alive`` is the setting: once it reads
    false, no further request leaves."""
    tidal_id = str(tidal_id or "")
    if not tidal_id:
        return None
    fetch = fetch or _http_fetch
    on = alive or (lambda: True)
    rec = svc.bio_get(tidal_id)
    if rec is not None:
        kept = _on_record(svc, tidal_id, rec, fetch, on)
        if kept is not _LOOK_AGAIN:
            return kept
    elif not on():
        return None
    found = find_artist(client, svc, tidal_id, name, rows, alive=on)
    if found is None:
        return None
    mbid, how = found
    if not mbid:
        svc.bio_put(tidal_id, mbid="", source="", ref="", text="", url="", ttl=NONE_TTL_SEC)
        logger.info("artist bio: no sure MusicBrainz artist")
        return {"text": "", "source": "", "url": ""}
    got = _from_artist(client, mbid, fetch, on)
    if got is None:
        return None
    if not got:
        svc.bio_put(tidal_id, mbid=mbid, source="", ref="", text="", url="", ttl=NONE_TTL_SEC)
        logger.info("artist bio: none (matched by %s)", how)
        return {"text": "", "source": "", "url": ""}
    svc.bio_put(tidal_id, mbid=mbid, **got)
    logger.info("artist bio: %s (matched by %s)", got["source"], how)
    return _shown(got)


# _on_record's "nothing usable on record: look from the start".
_LOOK_AGAIN = object()


def _on_record(svc, tidal_id: str, rec: dict, fetch, on):
    """What a bio on record answers: itself while fresh; once expired, its
    source read again by its own id (one request). ``_LOOK_AGAIN`` when the
    record is a "none" past its week or its article or profile is gone."""
    if not rec["expired"]:
        return _shown(rec)
    if not on():
        return None
    if not (rec["source"] and rec["ref"]):
        return _LOOK_AGAIN
    again = _refresh(rec, fetch, on)
    if again is None:
        # A Wikipedia intro a month old is still the artist's; a Discogs
        # profile past six hours may not be shown at all.
        return _shown(rec) if rec["source"] == "wikipedia" else None
    if not again:
        return _LOOK_AGAIN
    svc.bio_put(tidal_id, mbid=rec["mbid"], **again)
    logger.info("artist bio: %s refreshed", again["source"])
    return _shown(again)


def _shown(rec: dict) -> dict:
    return {"text": rec.get("text") or "", "source": rec.get("source") or "", "url": rec.get("url") or ""}


def _refresh(rec: dict, fetch, on) -> dict | None:
    """An expired bio read again from its own source by its own id: the
    new record, {} when it is gone, None when it could not be asked."""
    if rec["source"] == "wikipedia":
        return wikipedia_intro(rec["ref"], fetch, on)
    if rec["source"] == "discogs":
        return discogs_profile(rec["ref"], fetch, on)
    return {}


def _from_artist(client, mbid: str, fetch, on) -> dict | None:
    """The bio of a MusicBrainz artist: Wikipedia, else Discogs. {} when
    neither has one, None when a service could not be asked."""
    body = client.artist(mbid)
    if body is None:
        return None
    links = sources(body)
    title = links["wikipedia"]
    if not title and links["wikidata"]:
        title = wikidata_enwiki(links["wikidata"], fetch, on)
        if title is None:
            return None
    if title:
        got = wikipedia_intro(title, fetch, on)
        if got is None:
            return None
        if got:
            return got
    if links["discogs"]:
        return discogs_profile(links["discogs"], fetch, on)
    return {}


# ---- which artist -------------------------------------------------------------


def find_artist(
    client: musicbrainz.MusicBrainzClient,
    svc,
    tidal_id: str,
    name: str,
    rows: list[dict],
    *,
    alive: Callable[[], bool] | None = None,
) -> tuple[str, str] | None:
    """``(mbid, how)``: the MusicBrainz artist the TIDAL artist is, and by
    what ("linked", "url", "match"); ``("", "none")`` when no candidate is
    sure; None when MusicBrainz could not be asked. A sure answer is put on
    record for the other MusicBrainz features to lean on."""
    tidal_id = str(tidal_id or "")
    have = svc.linked("tidal", tidal_id) if tidal_id else None
    if have and have.get("mbid"):
        return str(have["mbid"]), "linked"
    resource = TIDAL_ARTIST_URL.format(urllib.parse.quote(tidal_id))
    q = urllib.parse.urlencode({"resource": resource, "inc": "artist-rels", "fmt": "json"})
    body = svc.get(f"{MB_BASE}/url?{q}", ttl=URL_TTL_SEC, not_found_answers=True, alive=alive)
    if body is None:
        return None
    ids = {
        str((r.get("artist") or {}).get("id") or "")
        for r in _as_list(body.get("relations"))
        if isinstance(r, dict) and r.get("target-type") == "artist"
    } - {""}
    if len(ids) == 1:
        mbid = ids.pop()
        svc.link("tidal", tidal_id, name, mbid, "url")
        return mbid, "url"
    if len(ids) > 1:
        return "", "none"  # one TIDAL page filed under several artists: not sure
    if not musicbrainz.name_key(name):
        # No name to weigh (a card that asked before its page had one): the
        # title rule was never put, so nothing is known either way.
        return None
    found = musicbrainz.resolve_artist(client, name, rows)
    if found is None:
        return None
    mbid = found[0]
    if not mbid:
        return "", "none"
    svc.link("tidal", tidal_id, name, mbid, "match")
    return mbid, "match"


def sources(artist: dict) -> dict:
    """Where a MusicBrainz artist's bio may be: ``{wikidata, wikipedia,
    discogs}`` (a Wikidata item id, an English Wikipedia title, a Discogs
    artist id), each '' when not linked. Ended links are skipped."""
    out = {"wikidata": "", "wikipedia": "", "discogs": ""}
    for r in _as_list(artist.get("relations") if isinstance(artist, dict) else None):
        if not isinstance(r, dict) or r.get("ended"):
            continue
        res = str((r.get("url") or {}).get("resource") or "")
        kind = r.get("type")
        if kind == "wikidata" and not out["wikidata"]:
            m = re.search(r"/wiki/(Q\d+)$", res)
            out["wikidata"] = m.group(1) if m else ""
        elif kind == "wikipedia" and not out["wikipedia"]:
            m = re.match(r"https?://en\.wikipedia\.org/wiki/(.+)$", res)
            out["wikipedia"] = urllib.parse.unquote(m.group(1)).replace("_", " ") if m else ""
        elif kind == "discogs" and not out["discogs"]:
            m = re.search(r"discogs\.com/(?:[a-z]{2}/)?artist/(\d+)", res)
            out["discogs"] = m.group(1) if m else ""
    return out


# ---- Wikipedia ----------------------------------------------------------------


def wikidata_enwiki(qid: str, fetch, alive=None) -> str | None:
    """The English Wikipedia title of a Wikidata item, '' when it has none,
    None when Wikidata could not be asked."""
    q = urllib.parse.urlencode(
        {"action": "wbgetentities", "ids": qid, "props": "sitelinks", "sitefilter": "enwiki", "format": "json"}
    )
    body = _get_json(f"{WIKIDATA_API}?{q}", fetch, alive)
    if body is None:
        return None
    entity = (body.get("entities") or {}).get(qid) or {}
    return str(((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title") or "")


def wikipedia_intro(title: str, fetch, alive=None) -> dict | None:
    """The article's intro as plain paragraphs, a record for ``bio_put``;
    {} when there is no usable intro (no article, a disambiguation page,
    an empty intro), None when Wikipedia could not be asked."""
    q = urllib.parse.urlencode(
        {
            "action": "query",
            "prop": "extracts|info|pageprops",
            "exintro": 1,
            "explaintext": 1,
            "inprop": "url",
            "ppprop": "disambiguation",
            "redirects": 1,
            "titles": title,
            "format": "json",
            "formatversion": 2,
        }
    )
    body = _get_json(f"{WIKIPEDIA_API}?{q}", fetch, alive)
    if body is None:
        return None
    pages = _as_list((body.get("query") or {}).get("pages"))
    page = pages[0] if pages and isinstance(pages[0], dict) else {}
    if page.get("missing") or "disambiguation" in (page.get("pageprops") or {}):
        return {}
    text = _paragraphs(str(page.get("extract") or ""))
    if not text:
        return {}
    ref = str(page.get("title") or title)
    url = str(page.get("canonicalurl") or page.get("fullurl") or "")
    if not url.startswith("https://en.wikipedia.org/"):
        url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(ref.replace(" ", "_"))
    return {"source": "wikipedia", "ref": ref, "text": text, "url": url, "ttl": WIKIPEDIA_TTL_SEC}


def _paragraphs(text: str) -> str:
    """Plain text with one blank line between paragraphs."""
    paras = [re.sub(r"[ \t\r\f\v]+", " ", p).strip() for p in re.split(r"\n+", text)]
    return "\n\n".join(p for p in paras if p)


# ---- Discogs ------------------------------------------------------------------


def discogs_profile(artist_id: str, fetch, alive=None) -> dict | None:
    """The Discogs artist profile as plain text, a record for ``bio_put``;
    {} when it has none (or nothing is left once cleaned), None when
    Discogs could not be asked."""
    if not str(artist_id).isdigit():
        return {}
    _DISCOGS_PACER.wait(True)
    body = _get_json(f"{DISCOGS_API}/artists/{artist_id}", fetch, alive)
    if body is None:
        return None
    text = clean_discogs(str(body.get("profile") or ""))
    if not text:
        return {}
    url = str(body.get("uri") or "")
    if not url.startswith("https://www.discogs.com/"):
        url = f"https://www.discogs.com/artist/{artist_id}"
    return {"source": "discogs", "ref": str(artist_id), "text": text, "url": url, "ttl": DISCOGS_TTL_SEC}


# Named links keep their name; a numbered one ([r=123], [a123], [m=123],
# [l123]) names nothing Waves can show, so the sentence holding it goes.
_DG_NAMED = re.compile(r"\[(?:a|l)=([^\]]+)\]", re.IGNORECASE)
_DG_URL = re.compile(r"\[url=[^\]]*\](.*?)\[/url\]", re.IGNORECASE | re.DOTALL)
_DG_NUMBERED = re.compile(r"\[(?:[arlm]=?\d+)\]", re.IGNORECASE)
_DG_STYLE = re.compile(r"\[/?(?:b|i|u)\]", re.IGNORECASE)
_DG_ANY = re.compile(r"\[[^\]]*\]")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


def clean_discogs(text: str) -> str:
    """A Discogs profile in plain paragraphs: named links read as their
    name, styling dropped, sentences leaning on a numbered link dropped,
    any other markup removed."""
    text = _DG_URL.sub(r"\1", _DG_NAMED.sub(r"\1", text.replace("\r\n", "\n")))
    text = _DG_STYLE.sub("", text)
    out = []
    for para in re.split(r"\n\s*\n", text):
        kept = [s for s in _SENTENCE.split(para.strip()) if s and not _DG_NUMBERED.search(s)]
        para = _DG_ANY.sub("", " ".join(kept))
        para = re.sub(r"\s+", " ", para).strip()
        if para:
            out.append(para)
    return "\n\n".join(out)


# ---- requests -----------------------------------------------------------------


def _get_json(url: str, fetch, alive=None) -> dict | None:
    """One GET, parsed; {} for a definite not-found (404, 410), None when
    the service could not be asked or the setting went off."""
    if alive is not None and not alive():
        return None
    try:
        status, text = fetch(url)
    except Exception:
        logger.info("artist bio request failed (transient)")
        return None
    if int(status) in (404, 410):
        return {}
    if int(status) != 200:
        logger.info("artist bio request HTTP %d (transient)", int(status))
        return None
    try:
        body = json.loads(text)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _http_fetch(url: str) -> tuple[int, str]:
    req = urllib.request.Request(  # noqa: S310 (https only: every URL is built on the fixed bases above)
        url, headers={"User-Agent": default_user_agent(), "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC, context=tls_context()) as resp:  # noqa: S310
            return int(resp.status), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.code), ""


def _as_list(value) -> list:
    return value if isinstance(value, list) else []
