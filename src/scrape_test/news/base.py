"""Shared shapes for news sources."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "of",
    "for",
    "in",
    "on",
    "to",
    "with",
    "at",
    "is",
    "are",
    "was",
    "were",
    "be",
    "by",
    "from",
    "that",
    "this",
    "it",
    "its",
}


@dataclass(slots=True)
class Article:
    title: str
    url: str
    source: str
    published: str | None = None
    summary: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "source": self.source,
            "published": self.published,
            "summary": self.summary,
            **({"extra": self.extra} if self.extra else {}),
        }


def context_terms(context: str, limit: int = 6) -> list[str]:
    """Pull meaningful keywords out of the free-text context box."""
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9.+#-]{1,}", context.lower())
    seen: list[str] = []
    for w in words:
        if w in _STOPWORDS or w in seen:
            continue
        seen.append(w)
        if len(seen) >= limit:
            break
    return seen


class NewsSource(Protocol):
    key: str
    label: str
    note: str

    def build_query(self, company: str, context: str) -> str:
        """Render the provider-specific query string. Surfaced in the UI for debugging."""
        ...

    async def search(
        self,
        client: httpx.AsyncClient,
        company: str,
        context: str,
        *,
        limit: int,
    ) -> list[Article]: ...
