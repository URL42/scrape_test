"""Extraction from text and web pages. Nothing here knows about Y Combinator.

`tooling` finds named products in free text - job descriptions, launch posts, anything.
`fingerprint` infers a site's public tech from its markup and headers. Both were written
for YC pages and outgrew them: the tooling catalog now runs over Greenhouse, Ashby and
Lever boards too, and fingerprinting runs over any company's homepage.
"""

from __future__ import annotations

from .fingerprint import (
    Detection,
    detect,
    fingerprint_site,
    get_fingerprint,
    load_fingerprint,
    normalize_url,
    store_fingerprint,
)
from .tooling import (
    ATLASSIAN_PRODUCTS,
    CATALOG,
    ToolHit,
    detect_tools,
    merge_hits,
    split_atlassian,
)

__all__ = [
    "ATLASSIAN_PRODUCTS",
    "CATALOG",
    "Detection",
    "ToolHit",
    "detect",
    "detect_tools",
    "fingerprint_site",
    "get_fingerprint",
    "load_fingerprint",
    "merge_hits",
    "normalize_url",
    "split_atlassian",
    "store_fingerprint",
]
