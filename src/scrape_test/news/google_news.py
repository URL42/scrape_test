"""Google News RSS adapter.

Coverage is the best of the free options, especially for small startups. Note the feed's
own copyright notice restricts it to personal, non-commercial use - fine for a research
tool, worth revisiting before this feeds a commercial sales workflow. That is why the
GDELT adapter exists alongside it.
"""

from __future__ import annotations

import html as html_mod
import re
from urllib.parse import quote_plus

import feedparser
import httpx

from ..http import fetch
from .base import Article, context_terms

ENDPOINT = "https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"


class GoogleNewsSource:
    key = "google_news"
    label = "Google News RSS"
    note = "Best coverage. Feed terms state personal, non-commercial use."

    def build_query(self, company: str, context: str) -> str:
        parts = [f'"{company.strip()}"'] if company.strip() else []
        parts.extend(context_terms(context))
        return " ".join(parts)

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
        resp = await fetch(client, ENDPOINT.format(query=quote_plus(query)))
        if not resp.ok:
            return []

        feed = feedparser.parse(resp.text)
        articles: list[Article] = []
        for entry in feed.entries[:limit]:
            source = ""
            if getattr(entry, "source", None) is not None:
                source = getattr(entry.source, "title", "") or ""
            articles.append(
                Article(
                    title=getattr(entry, "title", "").strip(),
                    url=getattr(entry, "link", ""),
                    source=source or "Google News",
                    published=getattr(entry, "published", None),
                    summary=_useful_summary(
                        getattr(entry, "summary", ""), getattr(entry, "title", "")
                    ),
                )
            )
        return articles


def _strip_html(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = html_mod.unescape(text)
    # unescape turns &nbsp; into U+00A0, which survives a plain \s+ collapse in some
    # engines and shows up as stray gaps in the UI.
    text = text.replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _useful_summary(raw_summary: str, title: str) -> str:
    """Google News fills <description> with the headline and publisher again. Showing that
    under the headline is pure noise, so drop a summary that adds nothing."""
    summary = _strip_html(raw_summary)
    if not summary:
        return ""
    norm_summary, norm_title = _normalize(summary), _normalize(title)
    if not norm_title:
        return summary[:400]
    if norm_summary.startswith(norm_title) or norm_title in norm_summary:
        remainder = norm_summary.replace(norm_title, "", 1).strip()
        # Anything left is usually just the publisher name repeated.
        if len(remainder) < 40:
            return ""
    return summary[:400]
