"""The what's-new digest: VC announcements and tech press, classified for sales use."""

from __future__ import annotations

from .classify import Classified, classify, extract_company, relevance
from .dates import age_days, parse_published
from .digest import (
    build_digest,
    funding_age_for,
    load_digest,
    recently_funded,
    store_digest,
)
from .sources import (
    ALL_SOURCES,
    KNOWN_GAPS,
    PRESS_SOURCES,
    VC_SOURCES,
    Source,
    sources_for,
)

__all__ = [
    "ALL_SOURCES",
    "KNOWN_GAPS",
    "PRESS_SOURCES",
    "VC_SOURCES",
    "Classified",
    "Source",
    "age_days",
    "build_digest",
    "classify",
    "extract_company",
    "funding_age_for",
    "load_digest",
    "parse_published",
    "recently_funded",
    "relevance",
    "sources_for",
    "store_digest",
]
