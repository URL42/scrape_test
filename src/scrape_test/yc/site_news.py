"""Read a company's own announcements: RSS/Atom feed, or the blog/news page itself.

Google News is thin on small startups, but those companies still announce funding,
launches and partnerships on their own site - and engineering blog posts often name the
stack outright. Three strategies, cheapest first:

  1. A feed declared in the homepage <link rel="alternate">.
  2. Common feed paths (/feed, /rss.xml, /blog/rss.xml, ...).
  3. sitemap.xml, which is static XML and therefore works even when the blog itself is
     client-side rendered - Rollstack and Vanta both publish no feed and render their
     blog in JS, but their sitemaps list every post with a lastmod date.
  4. Scraping the /blog or /news page for post links.

Step 4 is deliberately last: parsing bespoke marketing HTML is brittle - a naive scrape
returns megamenu items like "Financial Services" instead of articles - so it only runs
when the tidier options find nothing.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx
from selectolax.parser import HTMLParser

from ..config import TTL_POSTS
from ..db import is_fresh
from ..http import FetchError, fetch

log = logging.getLogger(__name__)

MAX_BODY = 1_500_000
FEED_PATHS = (
    "/feed",
    "/rss",
    "/rss.xml",
    "/feed.xml",
    "/atom.xml",
    "/blog/rss.xml",
    "/blog/feed",
    "/blog/feed.xml",
    "/news/rss.xml",
)
PAGE_PATHS = ("/blog", "/news", "/press", "/newsroom", "/company/news", "/blog/")
MAX_FEED_ATTEMPTS = len(FEED_PATHS)  # the blog-specific paths sit at the end
MAX_PAGE_ATTEMPTS = 3
MAX_POSTS = 15

# Links that look like navigation rather than a post.
_SKIP_LINK = re.compile(
    r"(?:^|/)(?:tag|tags|category|categories|author|page|subscribe|rss|feed|search|"
    r"privacy|terms|contact|about|careers|pricing|login|signup)(?:/|$)",
    re.I,
)
_DATE_IN_URL = re.compile(r"/20\d{2}[/-]\d{1,2}")
CHROME_WORDS = ("nav", "menu", "header", "footer", "dropdown", "megamenu")


@dataclass(slots=True)
class Post:
    title: str
    url: str
    published: str | None = None
    summary: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "published": self.published,
            "summary": self.summary,
        }


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text or "")).strip()


def declared_feeds(html: str, base_url: str) -> list[str]:
    """Feeds the page advertises in <link rel="alternate">."""
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001 - malformed marketing HTML is common
        return []
    out: list[str] = []
    for node in tree.css("link[rel]"):
        attrs = node.attributes
        rel = (attrs.get("rel") or "").lower()
        ctype = (attrs.get("type") or "").lower()
        href = attrs.get("href")
        if href and "alternate" in rel and ("rss" in ctype or "atom" in ctype):
            out.append(urljoin(base_url, href))
    return out


def parse_feed(text: str) -> list[Post]:
    feed = feedparser.parse(text)
    posts: list[Post] = []
    for entry in feed.entries[:MAX_POSTS]:
        title = _clean(getattr(entry, "title", ""))
        link = getattr(entry, "link", "") or ""
        if not title or not link:
            continue
        posts.append(
            Post(
                title=title,
                url=link,
                published=getattr(entry, "published", None) or getattr(entry, "updated", None),
                summary=_clean(getattr(entry, "summary", ""))[:400],
            )
        )
    return posts


def _in_chrome(node: Any) -> bool:
    """True if the link sits in nav/header/footer - i.e. it is site chrome, not content."""
    parent = node.parent
    depth = 0
    while parent is not None and depth < 12:
        tag = (parent.tag or "").lower()
        if tag in ("nav", "header", "footer"):
            return True
        attrs = parent.attributes
        blob = " ".join(attrs.get(a) or "" for a in ("class", "id", "role")).lower()
        if any(w in blob for w in CHROME_WORDS):
            return True
        parent = parent.parent
        depth += 1
    return False


def scrape_post_links(html: str, page_url: str) -> list[Post]:
    """Last resort: pull post links out of a blog/news page.

    The hard part is telling a post from a navigation item - a product megamenu has deep
    paths and long labels too, and a naive scrape returns "Financial Services" and
    "Prebuilt payment UIs" instead of articles. Two rules do most of the work: ignore
    anything inside nav/header/footer chrome, and require the link to live *under* the
    section we fetched (a post on /blog is at /blog/<slug>; a megamenu item is not).
    """
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return []
    split_page = urlsplit(page_url)
    host = split_page.netloc
    section = split_page.path.rstrip("/") or ""
    seen: set[str] = set()
    posts: list[Post] = []

    for node in tree.css("a[href]"):
        href = node.attributes.get("href") or ""
        title = _clean(node.text())
        if not href or len(title) < 18 or len(title) > 200:
            continue
        if _in_chrome(node):
            continue
        absolute = urljoin(page_url, href)
        split = urlsplit(absolute)
        if split.netloc and split.netloc != host:
            continue
        path = split.path.rstrip("/")
        if _SKIP_LINK.search(path) or path == section:
            continue
        # Must live under the section we fetched, or carry a date in the URL.
        under_section = bool(section) and path.startswith(section + "/")
        if not under_section and not _DATE_IN_URL.search(path):
            continue
        # A post slug has substance; "/blog/tag" style stubs do not.
        slug = path.rsplit("/", 1)[-1]
        if len(slug) < 8 or slug.isdigit():
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        posts.append(Post(title=title, url=absolute))
        if len(posts) >= MAX_POSTS:
            break
    return posts


# Path segments that hold company-published content.
CONTENT_SEGMENTS = (
    "blog",
    "news",
    "press",
    "article",
    "articles",
    "resources",
    "insights",
    "stories",
    "updates",
    "post",
    "posts",
    "newsroom",
    "library",
    "guides",
)
_SITEMAP_URL = re.compile(
    r"<url>\s*<loc>([^<]+)</loc>(?:\s*<lastmod>([^<]+)</lastmod>)?", re.I | re.S
)
_SITEMAP_CHILD = re.compile(r"<sitemap>\s*<loc>([^<]+)</loc>", re.I | re.S)
MAX_CHILD_SITEMAPS = 3


def _title_from_slug(path: str) -> str:
    slug = path.rstrip("/").rsplit("/", 1)[-1]
    slug = re.sub(r"[-_]+", " ", slug).strip()
    return slug[:1].upper() + slug[1:] if slug else ""


def parse_sitemap(xml: str, base_url: str) -> list[Post]:
    """Pull company-content URLs out of a sitemap, newest first.

    Sitemaps carry no titles, so these are derived from the slug. Less polished than a
    feed headline but perfectly readable, and the lastmod date makes recency reliable.
    """
    host = urlsplit(base_url).netloc
    entries: list[tuple[str, str, str]] = []  # (section, url, lastmod)
    for loc, lastmod in _SITEMAP_URL.findall(xml):
        loc = loc.strip()
        split = urlsplit(loc)
        if split.netloc and split.netloc != host:
            continue
        segments = [seg for seg in split.path.split("/") if seg]
        if len(segments) < 2:
            continue
        slug = segments[-1]
        if len(slug) < 8 or "-" not in slug:
            continue
        entries.append(("/" + segments[0].lower(), loc, (lastmod or "").strip()))

    if not entries:
        return []

    by_section: dict[str, list[tuple[str, str]]] = {}
    for section, loc, lastmod in entries:
        by_section.setdefault(section, []).append((loc, lastmod))

    named = {s: v for s, v in by_section.items() if s.lstrip("/") in CONTENT_SEGMENTS}
    pool = named or by_section
    section = max(pool, key=lambda k: len(pool[k]))
    chosen = pool[section]
    # lastmod is ISO-8601, so a plain string sort is chronological.
    chosen.sort(key=lambda pair: pair[1], reverse=True)
    return [
        Post(title=_title_from_slug(urlsplit(loc).path), url=loc, published=lastmod or None)
        for loc, lastmod in chosen[:MAX_POSTS]
        if _title_from_slug(urlsplit(loc).path)
    ]


async def _try(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await fetch(client, url, retries=1, max_bytes=MAX_BODY)
    except FetchError:
        return None
    return resp.text if resp.ok and resp.text else None


async def fetch_company_posts(
    client: httpx.AsyncClient, website: str | None, *, homepage_html: str | None = None
) -> tuple[list[Post], str]:
    """Return (posts, how_we_found_them)."""
    from .fingerprint import normalize_url

    base = normalize_url(website)
    if not base:
        return [], "no website on file"

    html = homepage_html
    if html is None:
        html = await _try(client, base)
    if html:
        for feed_url in declared_feeds(html, base)[:2]:
            text = await _try(client, feed_url)
            if text and (posts := parse_feed(text)):
                return posts[:MAX_POSTS], f"declared feed: {feed_url}"

    for path in FEED_PATHS[:MAX_FEED_ATTEMPTS]:
        text = await _try(client, urljoin(base, path))
        if text and (posts := parse_feed(text)):
            return posts[:MAX_POSTS], f"feed at {path}"

    sitemap = await _try(client, urljoin(base, "/sitemap.xml"))
    if sitemap:
        children = _SITEMAP_CHILD.findall(sitemap)
        if children:
            for child in children[:MAX_CHILD_SITEMAPS]:
                text = await _try(client, child.strip())
                if text and (posts := parse_sitemap(text, base)):
                    return posts, f"sitemap: {child.strip()}"
        elif posts := parse_sitemap(sitemap, base):
            return posts, "sitemap.xml"

    for path in PAGE_PATHS[:MAX_PAGE_ATTEMPTS]:
        page_url = urljoin(base, path)
        text = await _try(client, page_url)
        if text and (posts := scrape_post_links(text, page_url)):
            return posts[:MAX_POSTS], f"scraped {path}"

    return [], "no feed or blog page found"


def store_posts(conn: sqlite3.Connection, company_id: int, posts: list[Post]) -> None:
    """Replace the company's own-site posts. Leaves YC-sourced rows alone."""
    now = time.time()
    conn.execute("UPDATE companies SET posts_fetched_at = ? WHERE id = ?", (now, company_id))
    conn.execute(
        "DELETE FROM company_posts WHERE company_id = ? AND source = 'own_site'",
        (company_id,),
    )
    conn.executemany(
        """INSERT OR IGNORE INTO company_posts
               (company_id, source, title, url, published, summary, fetched_at)
           VALUES (?,'own_site',?,?,?,?,?)""",
        [(company_id, p.title, p.url, p.published, p.summary, now) for p in posts],
    )
    conn.commit()


def store_yc_items(
    conn: sqlite3.Connection, company_id: int, items: list[dict[str, Any]]
) -> None:
    """Replace YC-sourced items: their curated news list and any Launch YC post."""
    now = time.time()
    conn.execute(
        "DELETE FROM company_posts WHERE company_id = ? AND source IN ('yc_news','yc_launch')",
        (company_id,),
    )
    conn.executemany(
        """INSERT OR IGNORE INTO company_posts
               (company_id, source, title, url, published, summary, fetched_at)
           VALUES (?,?,?,?,?,?,?)""",
        [
            (
                company_id, i["source"], i["title"], i.get("url"),
                i.get("published"), i.get("summary") or "", now,
            )
            for i in items
        ],
    )
    conn.commit()


def load_posts(
    conn: sqlite3.Connection,
    company_id: int,
    sources: tuple[str, ...] = ("own_site",),
) -> list[dict[str, Any]]:
    placeholders = ",".join("?" for _ in sources)
    rows = conn.execute(
        "SELECT source, title, url, published, summary FROM company_posts "
        f"WHERE company_id = ? AND source IN ({placeholders}) ORDER BY id",
        (company_id, *sources),
    ).fetchall()
    return [dict(r) for r in rows]


async def get_company_posts(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    company_id: int,
    website: str | None,
    *,
    force: bool = False,
) -> tuple[list[dict[str, Any]], bool, str]:
    """Return (posts, from_cache, how_found). Write-through cache on TTL_POSTS."""
    if not force and is_fresh(posts_cache_age(conn, company_id), TTL_POSTS):
        return load_posts(conn, company_id), True, "cached"
    posts, how = await fetch_company_posts(client, website)
    store_posts(conn, company_id, posts)
    return load_posts(conn, company_id), False, how


def posts_cache_age(conn: sqlite3.Connection, company_id: int) -> float | None:
    row = conn.execute(
        "SELECT posts_fetched_at AS t FROM companies WHERE id = ?", (company_id,)
    ).fetchone()
    return row["t"] if row and row["t"] else None
