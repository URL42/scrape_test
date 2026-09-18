"""Job postings for a single YC company.

YC's company pages are an Inertia.js app: the server renders the full page payload into a
`data-page` attribute on the root div. That means no headless browser and no Algolia key -
one plain GET and an HTML-unescape gets us structured JSON, including the curated
`skills` array that is our best tech-stack signal.

robots.txt disallows `/companies?*` (the faceted search UI) but allows `/companies/<slug>`,
which is the only path we touch.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import sqlite3
import time
from typing import Any

import httpx

from ..config import TTL_JOBS, YC_JOBS_URL
from ..db import is_fresh
from ..http import fetch

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
    }


async def fetch_jobs(client: httpx.AsyncClient, slug: str) -> list[dict[str, Any]]:
    resp = await fetch(client, YC_JOBS_URL.format(slug=slug))
    if resp.status_code == 404:
        # No jobs page is a valid answer, not a failure; it gets cached like any other.
        return []
    if not resp.ok:
        raise JobsUnavailable(f"HTTP {resp.status_code} for {slug}")
    payload = extract_inertia_payload(resp.text)
    postings = payload.get("props", {}).get("jobPostings") or []
    return [_normalize_posting(p) for p in postings if p.get("id")]


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
               url, created_at_rel, last_active_rel, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
) -> tuple[list[dict[str, Any]], bool]:
    """Return (postings, from_cache). Write-through cache keyed on TTL_JOBS."""
    if not force and is_fresh(jobs_cache_age(conn, company_id), TTL_JOBS):
        return load_jobs(conn, company_id), True
    postings = await fetch_jobs(client, slug)
    store_jobs(conn, company_id, postings)
    return load_jobs(conn, company_id), False


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
