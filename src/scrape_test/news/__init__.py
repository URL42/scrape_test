"""News adapters. Register new sources here and they appear in the UI automatically."""

from __future__ import annotations

from .base import Article, NewsSource
from .gdelt import GdeltSource
from .google_news import GoogleNewsSource

SOURCES: dict[str, NewsSource] = {src.key: src for src in (GoogleNewsSource(), GdeltSource())}


def get_source(key: str) -> NewsSource:
    if key not in SOURCES:
        raise KeyError(f"unknown news source {key!r}; have {sorted(SOURCES)}")
    return SOURCES[key]


__all__ = ["Article", "GdeltSource", "GoogleNewsSource", "NewsSource", "SOURCES", "get_source"]
