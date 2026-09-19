"""Universe sources: where candidate companies come from before any scanning.

Two, with complementary strengths. YC's directory has excellent metadata (batch, team
size, tags) but only covers YC alumni. Hacker News' monthly "Who is hiring" threads cover
the wider market and are reachable through HN's public Algolia API, but carry no size or
stage data - so those candidates are sized later by their open-role count instead.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import sqlite3
from typing import Any

import httpx

from ..http import FetchError, fetch
from .icp import Candidate, from_yc_row, is_ai_first

log = logging.getLogger(__name__)

HN_SEARCH = (
    "https://hn.algolia.com/api/v1/search_by_date"
    "?query=%22Ask%20HN%3A%20Who%20is%20hiring%22&tags=story&hitsPerPage=20"
)
HN_ITEM = "https://hn.algolia.com/api/v1/items/{item_id}"

_URL = re.compile(r"https?://([a-z0-9.-]+\.[a-z]{2,})(?:/\S*)?", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPLIT = re.compile(r"\s*[|–—]\s*")

# Hosts that are an ATS, a form or a social link rather than the company's own site.
SKIP_HOSTS = (
    "news.ycombinator", "greenhouse.io", "lever.co", "ashbyhq", "workable", "recruitee",
    "linkedin", "notion.so", "google.com", "docs.google", "forms.gle", "twitter.com",
    "x.com", "github.com", "bit.ly", "airtable.com", "typeform", "wellfound",
)


def yc_candidates(conn: sqlite3.Connection) -> list[Candidate]:
    """Every active YC company, unfiltered. The ICP filter is applied separately so the
    rejection reason can be recorded."""
    rows = conn.execute("SELECT * FROM companies WHERE status = 'Active'").fetchall()
    return [from_yc_row(r) for r in rows]


def _clean(raw: str) -> str:
    return re.sub(r"\s+", " ", html_mod.unescape(_TAG.sub(" ", raw))).strip()


def parse_hn_post(text: str) -> Candidate | None:
    """HN hiring posts lead with "Company | Role | Location". Roughly three quarters give
    a usable company name and domain; the rest are skipped rather than guessed at."""
    body = _clean(text)
    if not body:
        return None
    head = body[:200]
    name = _SPLIT.split(head)[0]
    name = re.sub(r"\s*https?://\S+", "", name).strip(" -–—|()")
    name = re.sub(r"\s*\(\s*\)\s*$", "", name).strip()
    # Drop a dangling "(Series B" left by splitting on the pipe.
    if name.count("(") > name.count(")"):
        name = name[: name.rfind("(")].strip()
    if not (2 < len(name) < 48):
        return None

    hosts = [m.group(1).lower().removeprefix("www.") for m in _URL.finditer(body)]
    domain = next((h for h in hosts if not any(s in h for s in SKIP_HOSTS)), None)
    if not domain:
        return None

    return Candidate(
        name=name, domain=domain, source="hn",
        one_liner=body[:300], is_hiring=True,
    )


async def hn_candidates(
    client: httpx.AsyncClient, *, threads: int = 3, ai_only: bool = True
) -> list[Candidate]:
    """Candidates from the most recent "Who is hiring" threads."""
    try:
        resp = await fetch(client, HN_SEARCH, retries=2)
    except FetchError as exc:
        log.warning("HN search failed: %s", exc)
        return []
    if not resp.ok:
        return []

    try:
        hits = json.loads(resp.text).get("hits", [])
    except json.JSONDecodeError:
        return []
    ids = [
        h["objectID"]
        for h in hits
        if "who is hiring" in (h.get("title") or "").lower() and h.get("objectID")
    ][:threads]

    seen: dict[str, Candidate] = {}
    for item_id in ids:
        try:
            item = await fetch(client, HN_ITEM.format(item_id=item_id), retries=2)
        except FetchError as exc:
            log.warning("HN thread %s failed: %s", item_id, exc)
            continue
        if not item.ok:
            continue
        try:
            payload: dict[str, Any] = json.loads(item.text)
        except json.JSONDecodeError:
            continue
        for child in payload.get("children") or []:
            if not child.get("text"):
                continue
            cand = parse_hn_post(child["text"])
            if cand is None:
                continue
            if ai_only and not is_ai_first(None, cand.one_liner):
                continue
            seen.setdefault(cand.domain or cand.name, cand)
    return list(seen.values())
