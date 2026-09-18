"""Ingest the YC company directory and resolve free-text names to companies.

Source is the yc-oss mirror, which republishes YC's public directory as a single JSON
document (~6.2k companies, refreshed daily). One request gets the whole universe, which
is why this stage is prefetched rather than fetched per lookup.
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
import time
from typing import Any

import httpx

from ..config import TTL_DIRECTORY, YC_DIRECTORY_URL
from ..db import get_meta, is_fresh, set_meta
from ..http import fetch

_PUNCT = re.compile(r"[^a-z0-9]+")
_SUFFIXES = (" inc", " inc.", " llc", " ltd", " corp", " co", " company", " labs", " ai", " io")


def normalize(name: str) -> str:
    """Fold case, punctuation and common corporate suffixes so 'Acme, Inc.' == 'acme'."""
    text = _PUNCT.sub(" ", (name or "").lower()).strip()
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            bare = _PUNCT.sub(" ", suffix).strip()
            if bare and text.endswith(" " + bare):
                text = text[: -len(bare) - 1].strip()
                changed = True
    return re.sub(r"\s+", " ", text)


async def refresh_directory(
    conn: sqlite3.Connection,
    client: httpx.AsyncClient,
    *,
    force: bool = False,
) -> int:
    """Pull the directory into SQLite. Returns the number of companies stored."""
    last = get_meta(conn, "directory_fetched_at")
    if not force and is_fresh(last, TTL_DIRECTORY):
        return conn.execute("SELECT COUNT(*) AS n FROM companies").fetchone()["n"]

    resp = await fetch(client, YC_DIRECTORY_URL)
    if not resp.ok:
        raise RuntimeError(f"directory fetch failed: HTTP {resp.status_code}")
    companies: list[dict[str, Any]] = json.loads(resp.text)

    now = time.time()
    rows = [
        (
            c.get("id"),
            c.get("slug"),
            c.get("name") or "",
            normalize(c.get("name") or ""),
            json.dumps(c.get("former_names") or []),
            c.get("website"),
            c.get("batch"),
            c.get("status"),
            c.get("team_size"),
            1 if c.get("isHiring") else 0,
            c.get("industry"),
            c.get("subindustry"),
            json.dumps(c.get("tags") or []),
            c.get("one_liner"),
            c.get("long_description"),
            c.get("all_locations"),
            c.get("year_founded"),
            c.get("launched_at"),
            1 if c.get("top_company") else 0,
            now,
        )
        for c in companies
        if c.get("id") and c.get("slug")
    ]
    conn.executemany(
        """INSERT INTO companies (id, slug, name, norm_name, former_names, website, batch,
               status, team_size, is_hiring, industry, subindustry, tags, one_liner,
               long_description, all_locations, year_founded, launched_at, top_company, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(id) DO UPDATE SET
               slug=excluded.slug, name=excluded.name, norm_name=excluded.norm_name,
               former_names=excluded.former_names, website=excluded.website,
               batch=excluded.batch, status=excluded.status, team_size=excluded.team_size,
               is_hiring=excluded.is_hiring, industry=excluded.industry,
               subindustry=excluded.subindustry, tags=excluded.tags,
               one_liner=excluded.one_liner, long_description=excluded.long_description,
               all_locations=excluded.all_locations, year_founded=excluded.year_founded,
               launched_at=excluded.launched_at, top_company=excluded.top_company,
               fetched_at=excluded.fetched_at""",
        rows,
    )
    set_meta(conn, "directory_fetched_at", now)
    set_meta(conn, "directory_count", len(rows))
    conn.commit()
    return len(rows)


def resolve(conn: sqlite3.Connection, query: str) -> tuple[sqlite3.Row | None, list[str]]:
    """Find the company a typed name refers to.

    Returns (company_row_or_None, suggestions). Matching walks from strictest to loosest:
    exact slug, exact normalized name, a former name, then a fuzzy near-miss list. YC
    companies rename often enough that former_names is worth checking - MSPilot alone
    carries four prior names.
    """
    q = (query or "").strip()
    if not q:
        return None, []
    norm = normalize(q)
    slug = _PUNCT.sub("-", q.lower()).strip("-")

    row = conn.execute("SELECT * FROM companies WHERE slug = ?", (slug,)).fetchone()
    if row:
        return row, []

    matches = conn.execute("SELECT * FROM companies WHERE norm_name = ?", (norm,)).fetchall()
    if matches:
        # Prefer an active company when a name collides across batches.
        matches.sort(key=lambda r: (r["status"] != "Active", -(r["team_size"] or 0)))
        return matches[0], []

    for candidate in conn.execute("SELECT * FROM companies WHERE former_names != '[]'").fetchall():
        if any(normalize(fn) == norm for fn in json.loads(candidate["former_names"])):
            return candidate, []

    cursor = conn.execute("SELECT norm_name, name FROM companies")
    all_names = {r["norm_name"]: r["name"] for r in cursor}
    close = difflib.get_close_matches(norm, list(all_names), n=5, cutoff=0.82)
    return None, [all_names[c] for c in close]


def company_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    for key in ("tags", "former_names"):
        d[key] = json.loads(d.get(key) or "[]")
    d["is_hiring"] = bool(d.get("is_hiring"))
    d["top_company"] = bool(d.get("top_company"))
    return d
