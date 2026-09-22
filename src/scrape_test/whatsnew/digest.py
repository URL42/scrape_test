"""Fetch, classify and store the what's-new digest."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sqlite3
import time
from typing import Any

import httpx

from ..yc.site_news import fetch_company_posts
from .classify import classify, relevance
from .dates import age_days
from .sources import Source, sources_for

log = logging.getLogger(__name__)

CONCURRENCY = 8
PER_SOURCE_TIMEOUT = 35.0


async def _one(
    client: httpx.AsyncClient, sem: asyncio.Semaphore, src: Source
) -> list[dict[str, Any]]:
    async with sem:
        try:
            posts, _how = await asyncio.wait_for(
                fetch_company_posts(client, src.domain), timeout=PER_SOURCE_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 - one dead feed must not stop the digest
            log.debug("digest source %s failed: %s", src.name, exc)
            return []

    out = []
    for p in posts:
        c = classify(p.title, p.summary)
        # A fund announcing its own new fund is not a portfolio company.
        # "Introducing Greylock 18" should not read as an investment in "Greylock".
        if c.company and _norm_name(c.company) == _norm_name(src.name):
            c.company = None
        out.append({
            "source_name": src.name, "source_kind": src.kind, "source_region": src.region,
            "title": p.title, "url": p.url, "published": p.published,
            "summary": (p.summary or "")[:600],
            "tags": c.tags, "company": c.company, "amount": c.amount,
            "round_stage": c.round_stage, "relevance": relevance(c),
        })
    return out


async def build_digest(
    client: httpx.AsyncClient,
    *,
    kinds: tuple[str, ...] = ("vc", "press"),
    regions: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(CONCURRENCY)
    groups = await asyncio.gather(
        *(_one(client, sem, s) for s in sources_for(kinds, regions))
    )
    items = [item for g in groups for item in g]
    items.sort(key=lambda i: -i["relevance"])
    return items


def store_digest(conn: sqlite3.Connection, items: list[dict[str, Any]]) -> int:
    now = time.time()
    conn.executemany(
        """INSERT INTO digest_items
               (source_name, source_kind, source_region, title, url, published, summary,
                tags, company, amount, round_stage, relevance, fetched_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(url) DO UPDATE SET
               title=excluded.title, summary=excluded.summary, tags=excluded.tags,
               company=excluded.company, amount=excluded.amount,
               round_stage=excluded.round_stage, relevance=excluded.relevance,
               fetched_at=excluded.fetched_at""",
        [
            (
                i["source_name"], i["source_kind"], i["source_region"], i["title"],
                i["url"], i["published"], i["summary"], json.dumps(i["tags"]),
                i["company"], i["amount"], i["round_stage"], i["relevance"], now,
            )
            for i in items
            if i.get("url")
        ],
    )
    conn.commit()
    return len(items)


def load_digest(
    conn: sqlite3.Connection,
    *,
    tag: str | None = None,
    funded_only: bool = False,
    limit: int = 120,
) -> list[dict[str, Any]]:
    clauses, params = [], []
    if funded_only:
        clauses.append("company IS NOT NULL")
    if tag:
        clauses.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = conn.execute(
        f"SELECT * FROM digest_items {where} ORDER BY relevance DESC, fetched_at DESC LIMIT ?",
        (*params, limit),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        out.append(d)
    return out


def recently_funded(conn: sqlite3.Connection, limit: int = 300) -> list[dict[str, Any]]:
    """Named companies from funding announcements - the freshest prospect source there is.

    Each carries the age of its announcement, which is what the timing score consumes.
    """
    rows = conn.execute(
        "SELECT company, amount, round_stage, source_name, title, url, published "
        "FROM digest_items WHERE company IS NOT NULL AND tags LIKE '%\"funding\"%' "
        "ORDER BY fetched_at DESC LIMIT ?",
        (limit,),
    ).fetchall()
    seen: dict[str, dict[str, Any]] = {}
    for r in rows:
        record = dict(r)
        record["age_days"] = age_days(record.get("published"))
        # Keep the freshest announcement when a company appears more than once.
        key = record["company"].lower()
        prior = seen.get(key)
        if prior is None or (record["age_days"] or 1e9) < (prior["age_days"] or 1e9):
            seen[key] = record
    return list(seen.values())


def funding_age_for(conn: sqlite3.Connection, company_name: str) -> float | None:
    """Days since this company's most recent funding announcement, if we saw one.

    Matched on a normalised name: announcements say "Axiamatic" where a directory says
    "Axiamatic Inc." An exact match would miss most of them.
    """
    target = _norm_name(company_name)
    if not target:
        return None
    rows = conn.execute(
        "SELECT company, published FROM digest_items "
        "WHERE company IS NOT NULL AND tags LIKE '%\"funding\"%'"
    ).fetchall()
    best: float | None = None
    for r in rows:
        if _norm_name(r["company"]) != target:
            continue
        age = age_days(r["published"])
        if age is not None and (best is None or age < best):
            best = age
    return best


def _norm_name(name: str | None) -> str:
    if not name:
        return ""
    text = re.sub(r"[^a-z0-9 ]+", " ", name.lower())
    text = re.sub(
        r"\b(inc|llc|ltd|limited|corp|co|gmbh|bv|ab|oy|sa|plc|ai|io|labs|technologies|"
        r"tech|software|systems|group|holdings)\b",
        " ",
        text,
    )
    return re.sub(r"\s+", "", text)
