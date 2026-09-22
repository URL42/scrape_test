"""Detect the tooling a company exposes on its public website.

Important limitation, surfaced in the UI as "detected" rather than "uses": this only sees
the public surface - a linked status page, docs site, careers portal or support widget. A
company running Jira purely internally looks identical to one that has never heard of it.
Treat these as directional signals, not facts about the internal stack.

Every hit records the substring that matched so the finding stays auditable.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
from selectolax.parser import HTMLParser

from ..config import MAX_BODY_BYTES, TTL_FINGERPRINT
from ..db import is_fresh
from ..http import FetchError, fetch

# category -> product -> substrings that betray it
RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "atlassian": {
        "Jira": ("atlassian.net/jira", "/jira", "jira.", "atlassian.com/software/jira"),
        "Confluence": ("confluence.", "/confluence", "atlassian.net/wiki"),
        "Bitbucket": ("bitbucket.org", "bitbucket."),
        "Trello": ("trello.com",),
        "Statuspage": ("statuspage.io",),
        "Opsgenie": ("opsgenie.com",),
        "Atlassian (generic)": ("atlassian.net", "atlassian.com"),
    },
    "competing_tools": {
        "Linear": ("linear.app",),
        "Notion": ("notion.so", "notion.site"),
        "Asana": ("asana.com",),
        "ClickUp": ("clickup.com",),
        "Monday.com": ("monday.com",),
        "Shortcut": ("shortcut.com", "app.shortcut.com"),
        "Height": ("height.app",),
        "Basecamp": ("basecamp.com",),
        "Productboard": ("productboard.com",),
    },
    "source_control": {
        "GitHub": ("github.com", "github.io"),
        "GitLab": ("gitlab.com",),
    },
    "ats": {
        "Greenhouse": ("greenhouse.io", "boards.greenhouse"),
        "Lever": ("lever.co", "jobs.lever"),
        "Ashby": ("ashbyhq.com",),
        "Workable": ("workable.com",),
        "Rippling": ("rippling.com",),
        "Work at a Startup": ("workatastartup.com",),
    },
    "docs": {
        "GitBook": ("gitbook.io", "gitbook.com"),
        "ReadMe": ("readme.io",),
        "Mintlify": ("mintlify.com", "mintlify.app"),
        "Docusaurus": ("docusaurus",),
    },
    "support": {
        "Intercom": ("intercom.io", "intercomcdn"),
        "Zendesk": ("zendesk.com",),
        "HubSpot": ("hubspot.com", "hs-scripts.com"),
        "Front": ("frontapp.com",),
    },
    "hosting": {
        "Vercel": ("vercel.app", "vercel.com"),
        "Netlify": ("netlify.app", "netlify.com"),
        "Cloudflare": ("cloudflare.com", "cdn-cgi"),
        "AWS": ("amazonaws.com",),
        "Webflow": ("webflow.com", "webflow.io"),
        "Framer": ("framer.app", "framer.website"),
    },
    "analytics": {
        "Segment": ("segment.com", "segment.io"),
        "PostHog": ("posthog.com",),
        "Amplitude": ("amplitude.com",),
        "Mixpanel": ("mixpanel.com",),
        "Google Analytics": ("googletagmanager.com", "google-analytics.com"),
    },
    "frontend": {
        "Next.js": ("/_next/", "__next_f"),
        "React": ("react-dom", "__reactcontainer"),
        "Vue": ("vue.runtime", "__vue__"),
        "Svelte": ("svelte",),
        "WordPress": ("wp-content", "wp-includes"),
    },
}

_HEADER_HINTS = {
    "x-vercel-id": ("hosting", "Vercel"),
    "x-nf-request-id": ("hosting", "Netlify"),
    "cf-ray": ("hosting", "Cloudflare"),
    "x-amz-cf-id": ("hosting", "AWS"),
    "x-github-request-id": ("source_control", "GitHub"),
}


# Link paths that mean "we integrate with / compare against this", not "we use it".
# Zapier's homepage links /apps/jira-software-cloud/integrations; that is their marketplace
# listing Jira as a connectable app, and counting it as Atlassian usage is simply wrong.
_MARKETPLACE_MARKERS = (
    "/integrations",
    "/integration/",
    "/apps/",
    "/app-directory",
    "/marketplace",
    "/partners",
    "/connect/",
    "/templates",
    "/compare",
    "/alternatives",
    "/vs-",
    "/blog/",
    "/customers",
    "/case-stud",
    "/directory",
)

# A match on one of these means a first-party tenant, not a marketing link.
_FIRST_PARTY = {
    "Jira": (r"https?://[a-z0-9-]+\.atlassian\.net", r"https?://jira\.[a-z0-9-]+\."),
    "Confluence": (
        r"https?://[a-z0-9-]+\.atlassian\.net/wiki",
        r"https?://confluence\.[a-z0-9-]+\.",
    ),
    "Bitbucket": (r"bitbucket\.org/[a-z0-9_-]{2,}",),
    "Statuspage": (r"https?://[a-z0-9-]+\.statuspage\.io",),
    "Trello": (r"trello\.com/b/",),
    "Linear": (r"https?://[a-z0-9-]+\.linear\.app",),
    "Notion": (r"https?://[a-z0-9-]+\.notion\.site",),
    "GitHub": (r"github\.com/[a-z0-9_.-]{2,}",),
    "GitLab": (r"gitlab\.com/[a-z0-9_.-]{2,}",),
    "Greenhouse": (r"boards\.greenhouse\.io/[a-z0-9_-]+", r"job-boards\.greenhouse\.io"),
    "Lever": (r"jobs\.lever\.co/[a-z0-9_-]+",),
    "Ashby": (r"jobs\.ashbyhq\.com/[a-z0-9_-]+",),
}

# Where a match came from. Script/header/iframe hits mean the code is actually served.
_LOADED_KINDS = {"script", "header", "iframe"}


@dataclass(slots=True)
class Detection:
    category: str
    product: str
    evidence: str
    confidence: str  # "strong" = actually loaded or a first-party tenant; "weak" = a link


def _candidates(html: str, headers: dict[str, str]) -> list[tuple[str, str]]:
    """Collect (kind, text) pairs worth matching, so a hit knows its own provenance."""
    out: list[tuple[str, str]] = [("header", f"{k}: {v}") for k, v in headers.items()]
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001 - malformed markup is common in the wild
        # Lowercase here too: the needles are lowercase, so returning raw headers on this
        # branch would quietly stop matching them.
        fallback = out + [("inline", html[:200_000])]
        return [(kind, text.lower()) for kind, text in fallback if text]

    for sel, kind, attr in (
        ("script[src]", "script", "src"),
        ("link[href]", "link", "href"),
        ("a[href]", "anchor", "href"),
        ("iframe[src]", "iframe", "src"),
        ("meta[content]", "meta", "content"),
    ):
        for node in tree.css(sel):
            value = node.attributes.get(attr) or ""
            if value:
                out.append((kind, value))
    out.append(("inline", html.lower()[:200_000]))
    return [(kind, text.lower()) for kind, text in out if text]


def _is_marketplace_link(kind: str, text: str, window: str) -> bool:
    """Marketplace/comparison context means "we integrate with", not "we use"."""
    scope = window if kind == "inline" else text
    return kind in ("anchor", "link", "inline") and any(m in scope for m in _MARKETPLACE_MARKERS)


def _first_party_hit(product: str, text: str) -> bool:
    return any(re.search(pat, text) for pat in _FIRST_PARTY.get(product, ()))


def detect(html: str, headers: dict[str, str]) -> list[Detection]:
    haystacks = _candidates(html, headers)
    found: dict[tuple[str, str], Detection] = {}

    for header, (category, product) in _HEADER_HINTS.items():
        if header in headers:
            found[(category, product)] = Detection(category, product, f"header {header}", "strong")

    for category, products in RULES.items():
        for product, needles in products.items():
            if (category, product) in found:
                continue
            best: Detection | None = None
            for kind, text in haystacks:
                if not any(n in text for n in needles):
                    continue
                needle = next(n for n in needles if n in text)
                window = _trim_evidence(text, needle)
                if _is_marketplace_link(kind, text, window):
                    continue
                # The inline HTML dump is the noisiest haystack - a third-party code
                # comment or a CSP allowlist can mention anything - so it never yields a
                # strong signal. Strong means the asset is actually served, or the URL is
                # a first-party tenant found in real markup.
                strong = kind in _LOADED_KINDS or (
                    kind in ("anchor", "link", "meta") and _first_party_hit(product, text)
                )
                cand = Detection(category, product, window, "strong" if strong else "weak")
                if strong:
                    best = cand
                    break
                best = best or cand
            if best is not None:
                found[(category, product)] = best
    return list(found.values())


def _trim_evidence(hit: str, needle: str) -> str:
    idx = hit.find(needle)
    start = max(0, idx - 30)
    snippet = hit[start : idx + len(needle) + 30].strip()
    return re.sub(r"\s+", " ", snippet)[:120]


def normalize_url(website: str | None) -> str | None:
    if not website:
        return None
    url = website.strip()
    if not url:
        return None
    if not urlsplit(url).scheme:
        url = f"https://{url}"
    return url


async def fingerprint_site(client: httpx.AsyncClient, website: str | None) -> dict[str, Any]:
    url = normalize_url(website)
    if not url:
        return {
            "url": None,
            "final_url": None,
            "status_code": None,
            "detected": {},
            "error": "no website on file",
        }
    try:
        resp = await fetch(client, url, retries=2, max_bytes=MAX_BODY_BYTES)
    except FetchError as exc:
        return {
            "url": url,
            "final_url": None,
            "status_code": None,
            "detected": {},
            "error": str(exc)[:200],
        }

    if not resp.ok:
        return {
            "url": url,
            "final_url": resp.final_url,
            "status_code": resp.status_code,
            "detected": {},
            "error": f"HTTP {resp.status_code}",
        }

    detections = detect(resp.text, resp.headers)
    grouped: dict[str, list[dict[str, str]]] = {}
    for d in detections:
        grouped.setdefault(d.category, []).append(
            {"product": d.product, "evidence": d.evidence, "confidence": d.confidence}
        )
    for items in grouped.values():
        items.sort(key=lambda i: i["product"])

    return {
        "url": url,
        "final_url": resp.final_url,
        "status_code": resp.status_code,
        "detected": grouped,
        "error": None,
    }


def store_fingerprint(conn: sqlite3.Connection, company_id: int, result: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO site_tech (company_id, url, final_url, status_code, detected,
                                  error, fetched_at)
           VALUES (?,?,?,?,?,?,?)
           ON CONFLICT(company_id) DO UPDATE SET
               url=excluded.url, final_url=excluded.final_url, status_code=excluded.status_code,
               detected=excluded.detected, error=excluded.error, fetched_at=excluded.fetched_at""",
        (
            company_id,
            result["url"],
            result["final_url"],
            result["status_code"],
            json.dumps(result["detected"]),
            result["error"],
            time.time(),
        ),
    )
    conn.commit()


def load_fingerprint(conn: sqlite3.Connection, company_id: int) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM site_tech WHERE company_id = ?", (company_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["detected"] = json.loads(d.get("detected") or "{}")
    return d


async def get_fingerprint(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    company_id: int,
    website: str | None,
    *,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Return (fingerprint, from_cache)."""
    cached = load_fingerprint(conn, company_id)
    if not force and cached and is_fresh(cached.get("fetched_at"), TTL_FINGERPRINT):
        return cached, True
    result = await fingerprint_site(client, website)
    store_fingerprint(conn, company_id, result)
    stored = load_fingerprint(conn, company_id)
    return (stored or result), False
