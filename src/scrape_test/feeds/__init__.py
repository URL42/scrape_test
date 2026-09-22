"""Finding and reading a site's published content, whoever owns the site.

The discovery chain - declared feed, common feed paths, sitemap.xml, then the page
itself - works the same for a startup's blog and a venture fund's newsroom, which is why
this no longer lives under `yc`.
"""

from __future__ import annotations

from .discover import (
    Post,
    declared_feeds,
    fetch_company_posts,
    get_company_posts,
    load_posts,
    parse_feed,
    parse_sitemap,
    scrape_post_links,
    store_posts,
    store_yc_items,
)

__all__ = [
    "Post",
    "declared_feeds",
    "fetch_company_posts",
    "get_company_posts",
    "load_posts",
    "parse_feed",
    "parse_sitemap",
    "scrape_post_links",
    "store_posts",
    "store_yc_items",
]
