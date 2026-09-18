"""GDELT DOC 2.0 adapter.

Free, no API key, and without the personal-use restriction Google News carries. The
tradeoff is a hard rate limit (one request per five seconds - it returns a plain-text 429
rather than JSON if you exceed it) and thinner coverage of very small companies.
"""

from __future__ import annotations

import json
import os
from urllib.parse import urlencode

import httpx

from ..http import fetch
from .base import Article, context_terms

ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"

# Empty = use GDELT's default window (~3 months). See the note in search().
GDELT_TIMESPAN = os.environ.get("GDELT_TIMESPAN", "")


class GdeltSource:
    key = "gdelt"
    label = "GDELT"
    note = "No usage restriction. Throttled to 1 request / 5s; weaker on small startups."

    def build_query(self, company: str, context: str) -> str:
        name = company.strip()
        query = f'"{name}"' if name else ""
        terms = context_terms(context, limit=4)
        if terms:
            # OR the context rather than ANDing it: GDELT returns nothing at all if every
            # term must appear, which is easy to trip over with a free-text box.
            # It also rejects parentheses around a single term outright
            # ("Parentheses may only be used around OR'd statements"), so only group
            # when there is something to OR.
            fragment = f"({' OR '.join(terms)})" if len(terms) > 1 else terms[0]
            query = f"{query} {fragment}" if query else fragment
        return query.strip()

    async def search(
        self,
        client: httpx.AsyncClient,
        company: str,
        context: str,
        *,
        limit: int = 25,
    ) -> list[Article]:
        query = self.build_query(company, context)
        if not query:
            return []
        params = {
            "query": query,
            "mode": "ArtList",
            "maxrecords": str(min(limit, 250)),
            "format": "json",
            "sort": "datedesc",
        }
        # `timespan` is deliberately omitted. GDELT only accepts short unit codes, and an
        # unrecognised value such as "3months" or "1month" is not rejected - it returns
        # HTTP 200 with zero articles, which looks exactly like "no coverage". The default
        # window is already about three months, so leaving it off is both safer and what
        # we wanted. Set GDELT_TIMESPAN to a verified code (e.g. "1w") to narrow it.
        if GDELT_TIMESPAN:
            params["timespan"] = GDELT_TIMESPAN
        resp = await fetch(client, f"{ENDPOINT}?{urlencode(params)}", accept_status=(429,))
        if resp.status_code == 429 or not resp.ok:
            raise RuntimeError(
                "GDELT rate-limited this request (it allows one every 5 seconds). "
                "Wait a moment and retry, or switch to Google News."
            )
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            # GDELT signals overuse with a plain-text body, not a JSON error object.
            raise RuntimeError(f"GDELT returned non-JSON: {resp.text[:160]}") from exc

        articles: list[Article] = []
        for item in payload.get("articles", [])[:limit]:
            articles.append(
                Article(
                    title=(item.get("title") or "").strip(),
                    url=item.get("url", ""),
                    source=item.get("domain", "") or "GDELT",
                    published=_fmt_date(item.get("seendate")),
                    summary="",
                    extra={
                        k: item[k]
                        for k in ("language", "sourcecountry", "socialimage")
                        if item.get(k)
                    },
                )
            )
        return articles


def _fmt_date(raw: str | None) -> str | None:
    """GDELT hands back 20260917T023204Z; render it readably."""
    if not raw or len(raw) < 15:
        return raw
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]} {raw[9:11]}:{raw[11:13]}Z"
