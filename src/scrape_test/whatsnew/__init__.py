"""The what's-new digest: VC announcements and tech press, classified for sales use."""

from __future__ import annotations

from .classify import Classified, classify, extract_company, relevance
from .digest import build_digest, load_digest, recently_funded, store_digest
from .sources import ALL_SOURCES, KNOWN_GAPS, PRESS_SOURCES, VC_SOURCES, Source, sources_for

__all__ = [
    "ALL_SOURCES", "Classified", "KNOWN_GAPS", "PRESS_SOURCES", "Source", "VC_SOURCES",
    "build_digest", "classify", "extract_company", "load_digest", "recently_funded",
    "relevance", "sources_for", "store_digest",
]
