"""Job postings for a single YC company.

YC's company pages are an Inertia.js app: the server renders the full page payload into a
`data-page` attribute on the root div. That means no headless browser and no Algolia key -
one plain GET and an HTML-unescape gets us structured JSON, including the curated
`skills` array that is our best tech-stack signal.

robots.txt disallows `/companies?*` (the faceted search UI) but allows `/companies/<slug>`,
which is the only path we touch.
"""

from __future__ import annotations

import asyncio
import html as html_mod
import json
import logging
import os
import re
import sqlite3
import time
from typing import Any

import httpx

from ..config import TTL_JOBS, YC_COMPANY_URL
from ..db import is_fresh
from ..extract.tooling import ToolHit, detect_tools, merge_hits
from ..feeds.discover import load_posts, store_yc_items
from ..http import fetch

log = logging.getLogger(__name__)

_DATA_PAGE = re.compile(r'data-page="([^"]*)"')


class JobsUnavailable(Exception):
    """The company's jobs page could not be read."""


def extract_inertia_payload(html: str) -> dict[str, Any]:
    match = _DATA_PAGE.search(html)
    if not match:
        raise JobsUnavailable("no data-page payload on page (YC may have changed layout)")
    try:
        return json.loads(html_mod.unescape(match.group(1)))
    except json.JSONDecodeError as exc:
        raise JobsUnavailable(f"data-page was not valid JSON: {exc}") from exc


ENGINEERING_ROLES = {"eng", "engineering"}

# Roles whose descriptions are worth a request. Measured across 39 non-engineering
# postings: product named tooling in 3 of 4 descriptions (Jira, Notion, GitHub), while
# marketing (13) and operations (22) named nothing at all. Product managers live in the
# issue tracker and write the docs, so they say which ones.
# Override with SCRAPE_TEST_DESC_ROLES="eng,product,design".
DEFAULT_DESCRIPTION_ROLES = {"eng", "engineering", "product"}


def description_roles() -> set[str]:
    raw = os.environ.get("SCRAPE_TEST_DESC_ROLES", "")
    if not raw.strip():
        return DEFAULT_DESCRIPTION_ROLES
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def is_engineering(posting: dict[str, Any]) -> bool:
    """Engineering only - this drives the hiring-volume signal, not description fetching."""
    role = (posting.get("role") or "").strip().lower()
    pretty = (posting.get("pretty_role") or "").strip().lower()
    return role in ENGINEERING_ROLES or pretty == "engineering"


# YC's role slug is sometimes plainly wrong - Ooak Data files an "ML engineer" posting
# under Operations - so a technical or product title overrides the slug.
TITLE_HINTS = (
    "engineer",
    "developer",
    "architect",
    "product manager",
    "devops",
    "sre",
    "head of engineering",
    "head of product",
    "technical program",
    "platform lead",
)


def wants_description(posting: dict[str, Any]) -> bool:
    """Whether this posting's description is worth fetching."""
    roles = description_roles()
    role = (posting.get("role") or "").strip().lower()
    pretty = (posting.get("pretty_role") or "").strip().lower()
    if role in roles or pretty in roles:
        return True
    title = (posting.get("title") or "").lower()
    return any(hint in title for hint in TITLE_HINTS)


def _normalize_posting(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "yc_job_id": raw.get("id"),
        "title": raw.get("title"),
        "role": raw.get("role"),
        "pretty_role": raw.get("prettyRole"),
        "skills": raw.get("skills") or [],
        "salary_range": raw.get("salaryRange"),
        "equity_range": raw.get("equityRange"),
        "min_experience": raw.get("minExperience"),
        "location": raw.get("location"),
        "job_type": raw.get("type"),
        "visa": raw.get("visa"),
        "url": f"https://www.ycombinator.com{raw['url']}" if raw.get("url") else None,
        "created_at_rel": raw.get("createdAt"),
        "last_active_rel": raw.get("lastActive"),
        "description": raw.get("description"),
    }


async def fetch_description(client: httpx.AsyncClient, job_url: str) -> str | None:
    """Pull one posting's description.

    The jobs list payload carries no description - only the individual job page does, and
    that is where companies actually name their tooling. Rollstack's AI Software Engineer
    posting says "Issue tracking with Linear" in prose while its `skills` array is empty.
    """
    try:
        resp = await fetch(client, job_url, retries=2)
    except Exception as exc:  # noqa: BLE001 - one missing description must not fail a lookup
        log.debug("description fetch failed for %s: %s", job_url, exc)
        return None
    if not resp.ok:
        return None
    try:
        payload = extract_inertia_payload(resp.text)
    except JobsUnavailable:
        return None
    return (payload.get("props", {}).get("job") or {}).get("description")


async def enrich_descriptions(client: httpx.AsyncClient, postings: list[dict[str, Any]]) -> None:
    """Fetch descriptions for the roles worth reading, concurrently.

    Engineering and product only by default: those two name the tooling, while marketing,
    ops, sales and finance postings essentially never do. Restricting this keeps most of
    the extra requests off the wire without losing signal.
    """
    targets = [p for p in postings if wants_description(p) and p.get("url")]
    if not targets:
        return
    results = await asyncio.gather(
        *(fetch_description(client, p["url"]) for p in targets), return_exceptions=True
    )
    for posting, result in zip(targets, results, strict=True):
        if isinstance(result, str):
            posting["description"] = result


def _normalize_news(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "yc_news",
        "title": (raw.get("title") or "").strip(),
        "url": raw.get("url"),
        "published": raw.get("date"),
        "summary": "",
    }


def _normalize_launch(raw: dict[str, Any]) -> dict[str, Any]:
    body = re.sub(r"<[^>]+>", " ", str(raw.get("body") or ""))
    return {
        "source": "yc_launch",
        "title": (raw.get("title") or "").strip(),
        "url": (f"https://www.ycombinator.com/launches/{raw['slug']}" if raw.get("slug") else None),
        "published": raw.get("created_at"),
        "summary": re.sub(r"\s+", " ", f"{raw.get('tagline') or ''} {body}").strip()[:2000],
    }


async def fetch_company_page(
    client: httpx.AsyncClient, slug: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch the company profile once and return (postings, yc_items).

    The profile page carries the same `jobPostings` as /jobs *plus* YC's own curated
    `newsItems` and any Launch YC post - so reading it instead of /jobs costs nothing and
    yields strictly more. Launch bodies are kept because they are company-written prose,
    another place tooling gets named.
    """
    resp = await fetch(client, YC_COMPANY_URL.format(slug=slug))
    if resp.status_code == 404:
        # No profile is a valid answer, not a failure; it gets cached like any other.
        return [], []
    if not resp.ok:
        raise JobsUnavailable(f"HTTP {resp.status_code} for {slug}")
    props = extract_inertia_payload(resp.text).get("props", {})

    normalized = [_normalize_posting(p) for p in (props.get("jobPostings") or []) if p.get("id")]
    await enrich_descriptions(client, normalized)

    yc_items = [_normalize_news(n) for n in (props.get("newsItems") or []) if n.get("title")]
    yc_items += [_normalize_launch(x) for x in (props.get("launches") or []) if x.get("title")]
    return normalized, yc_items


def store_jobs(conn: sqlite3.Connection, company_id: int, postings: list[dict[str, Any]]) -> None:
    now = time.time()
    # Record the fetch on the company row, not just on the postings. A company with no
    # open roles stores zero rows, so a MAX(fetched_at) over those rows is NULL and the
    # cache looks permanently cold - which would refetch on every single lookup, for
    # exactly the companies where the request is pure waste.
    conn.execute("UPDATE companies SET jobs_fetched_at = ? WHERE id = ?", (now, company_id))
    # Replace wholesale: a posting that disappeared from YC is a closed role, and leaving
    # it behind would inflate the hiring signal.
    conn.execute("DELETE FROM job_postings WHERE company_id = ?", (company_id,))
    conn.executemany(
        """INSERT OR REPLACE INTO job_postings (yc_job_id, company_id, title, role, pretty_role,
               skills, salary_range, equity_range, min_experience, location, job_type, visa,
               url, created_at_rel, last_active_rel, description, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (
                p["yc_job_id"],
                company_id,
                p["title"],
                p["role"],
                p["pretty_role"],
                json.dumps(p["skills"]),
                p["salary_range"],
                p["equity_range"],
                p["min_experience"],
                p["location"],
                p["job_type"],
                p["visa"],
                p["url"],
                p["created_at_rel"],
                p["last_active_rel"],
                p.get("description"),
                now,
            )
            for p in postings
        ],
    )
    conn.commit()


def load_jobs(conn: sqlite3.Connection, company_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM job_postings WHERE company_id = ? ORDER BY pretty_role, title",
        (company_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["skills"] = json.loads(d.get("skills") or "[]")
        out.append(d)
    return out


def jobs_cache_age(conn: sqlite3.Connection, company_id: int) -> float | None:
    """When we last asked YC about this company - regardless of how many roles came back."""
    row = conn.execute(
        "SELECT jobs_fetched_at AS t FROM companies WHERE id = ?", (company_id,)
    ).fetchone()
    return row["t"] if row and row["t"] else None


async def get_jobs(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    company_id: int,
    slug: str,
    *,
    force: bool = False,
) -> tuple[list[dict[str, Any]], bool, list[dict[str, Any]]]:
    """Return (postings, from_cache, yc_items). Write-through cache keyed on TTL_JOBS."""
    if not force and is_fresh(jobs_cache_age(conn, company_id), TTL_JOBS):
        return (
            load_jobs(conn, company_id),
            True,
            load_posts(conn, company_id, sources=("yc_news", "yc_launch")),
        )
    postings, yc_items = await fetch_company_page(client, slug)
    store_jobs(conn, company_id, postings)
    store_yc_items(conn, company_id, yc_items)
    return (
        load_jobs(conn, company_id),
        False,
        load_posts(conn, company_id, sources=("yc_news", "yc_launch")),
    )


def tools_from_jobs(
    postings: list[dict[str, Any]], extra: list[dict[str, Any]] | None = None
) -> list[ToolHit]:
    """Mine job descriptions (and any Launch YC body) for named tooling."""
    groups = [
        detect_tools(p.get("description") or "", source=p.get("title") or "role") for p in postings
    ]
    for item in extra or []:
        if item.get("source") == "yc_launch" and item.get("summary"):
            groups.append(detect_tools(item["summary"], source="YC launch post"))
    return merge_hits(groups)


def stack_from_jobs(postings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Aggregate the per-posting skills arrays into a ranked stack for the company."""
    counts: dict[str, int] = {}
    for p in postings:
        for skill in p.get("skills") or []:
            counts[skill] = counts.get(skill, 0) + 1
    return [
        {"skill": s, "mentions": c}
        for s, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    ]
