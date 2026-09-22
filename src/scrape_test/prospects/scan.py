"""The prospect scan: walk a candidate universe, read each job board, rank the results.

Runs in the background because it is minutes of polite HTTP, and resumable because a
failure two thirds of the way through should not cost the whole sweep - a company already
scanned in this run is skipped on a retry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from typing import Any

import httpx

from ..ats import ATSUnavailable, discover_board, fetch_board
from ..db import session
from ..scoring.products import lead_product, priority, product_fit
from ..scoring.timing import timing_score, timing_signals
from ..whatsnew import funding_age_for, recently_funded
from ..yc.tooling import detect_tools, merge_hits, split_atlassian
from .icp import Candidate, prospect_score, qualifies
from .sources import hn_candidates, yc_candidates

log = logging.getLogger(__name__)

CONCURRENCY = 4
MAX_POSTINGS_SCANNED = 400


def start_run(conn: sqlite3.Connection, total: int) -> int:
    cur = conn.execute(
        "INSERT INTO scan_runs (status, total, done, found, started_at) "
        "VALUES ('running', ?, 0, 0, ?)",
        (total, time.time()),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


def update_run(conn: sqlite3.Connection, run_id: int, **fields: Any) -> None:
    if not fields:
        return
    sets = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(f"UPDATE scan_runs SET {sets} WHERE id = ?", (*fields.values(), run_id))
    conn.commit()


def latest_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def store_prospect(conn: sqlite3.Connection, record: dict[str, Any]) -> None:
    conn.execute(
        """INSERT INTO prospects (name, domain, source, external_id, batch, team_size,
               one_liner, in_profile, reject_reason, board_provider, board_token,
               board_found_via, open_roles, atlassian, competitors, score, verdict,
               reasons, error, scanned_at, funding_age_days, timing_score,
               timing_signals, products, lead_product, priority)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(source, name, domain) DO UPDATE SET
               batch=excluded.batch, team_size=excluded.team_size,
               one_liner=excluded.one_liner, in_profile=excluded.in_profile,
               reject_reason=excluded.reject_reason,
               board_provider=excluded.board_provider, board_token=excluded.board_token,
               board_found_via=excluded.board_found_via, open_roles=excluded.open_roles,
               atlassian=excluded.atlassian, competitors=excluded.competitors,
               score=excluded.score, verdict=excluded.verdict, reasons=excluded.reasons,
               error=excluded.error, scanned_at=excluded.scanned_at,
               funding_age_days=excluded.funding_age_days,
               timing_score=excluded.timing_score,
               timing_signals=excluded.timing_signals, products=excluded.products,
               lead_product=excluded.lead_product, priority=excluded.priority""",
        (
            record["name"], record["domain"], record["source"], record.get("external_id"),
            record.get("batch"), record.get("team_size"), record.get("one_liner"),
            1 if record.get("in_profile") else 0, record.get("reject_reason"),
            record.get("board_provider"), record.get("board_token"),
            record.get("board_found_via"), record.get("open_roles"),
            json.dumps(record.get("atlassian") or []),
            json.dumps(record.get("competitors") or []),
            record.get("score"), record.get("verdict"),
            json.dumps(record.get("reasons") or []), record.get("error"), time.time(),
            record.get("funding_age_days"), record.get("timing_score"),
            json.dumps(record.get("timing_signals") or []),
            json.dumps(record.get("products") or {}), record.get("lead_product"),
            record.get("priority"),
        ),
    )
    conn.commit()


async def scan_one(client: httpx.AsyncClient, c: Candidate) -> dict[str, Any]:
    """Read one company's board and rank it. Never raises: a failure is a recorded row."""
    base: dict[str, Any] = {
        "name": c.name, "domain": c.domain, "source": c.source,
        "external_id": c.external_id, "batch": c.batch, "team_size": c.team_size,
        "one_liner": (c.one_liner or "")[:400],
    }
    if not c.domain:
        return {**base, "in_profile": False, "verdict": "unscannable",
                "reject_reason": "no website on file"}

    try:
        board = await discover_board(client, c.domain)
    except Exception as exc:  # noqa: BLE001 - one bad site must not stop the sweep
        return {**base, "in_profile": False, "verdict": "unscannable",
                "error": f"discovery failed: {exc}"[:200]}

    texts: list[tuple[str, str]] = []
    if board is not None:
        try:
            postings = await fetch_board(client, board)
        except ATSUnavailable as exc:
            return {**base, "in_profile": False, "verdict": "unscannable",
                    "error": str(exc)[:200],
                    "board_provider": board.provider, "board_token": board.token}
        texts = [(p.title[:40], p.description) for p in postings[:MAX_POSTINGS_SCANNED]]
        base |= {
            "board_provider": board.provider, "board_token": board.token,
            "board_found_via": board.found_via,
        }
    elif c.source == "yc" and c.external_id:
        # Most small YC companies never set up Greenhouse or Ashby - they hire through
        # YC's own board. Those postings carry descriptions we already know how to read,
        # so falling back here is the difference between scanning a third of the list and
        # scanning most of it.
        texts = await _yc_fallback_texts(client, c)
        base |= {"board_provider": "yc", "board_token": c.external_id,
                 "board_found_via": "YC job board (no ATS found)"}

    if not texts:
        return {**base, "in_profile": False, "verdict": "unscannable",
                "reject_reason": "no readable job postings"}

    hits = merge_hits([detect_tools(text, source=title, company=c.name) for title, text in texts])
    ours, theirs = split_atlassian(hits)
    open_roles = len(texts)

    in_profile, reason = qualifies(c, open_roles=open_roles)
    atlassian = [h.as_dict() for h in ours]
    competitors = [h.as_dict() for h in theirs]
    score, verdict, reasons = prospect_score(c, atlassian, competitors, open_roles)

    # The join: the digest knows who raised and when, so the timing score can use it.
    with session() as conn:
        funded_age = funding_age_for(conn, c.name)

    jobs_for_scoring = [
        {"title": title, "description": text, "role": "", "pretty_role": "", "location": ""}
        for title, text in texts
    ]
    signals = timing_signals(
        jobs_for_scoring, funding_age_days=funded_age, open_roles=open_roles
    )
    t_score = timing_score(signals)
    fits = product_fit(jobs_for_scoring, competitors + atlassian, None)
    lead, lead_score = lead_product(fits)

    return {
        **base,
        "in_profile": in_profile,
        "reject_reason": None if in_profile else reason,
        "open_roles": open_roles,
        "atlassian": atlassian,
        "competitors": competitors,
        "score": score if in_profile else 0.0,
        "verdict": verdict if in_profile else "out of profile",
        "reasons": reasons,
        "funding_age_days": funded_age,
        "timing_score": t_score,
        "timing_signals": signals,
        "products": fits,
        "lead_product": lead,
        "priority": priority(lead_score, t_score) if in_profile else 0.0,
    }


async def _yc_fallback_texts(
    client: httpx.AsyncClient, c: Candidate
) -> list[tuple[str, str]]:
    """Job text from YC's own board, for companies with no ATS of their own."""
    from ..yc.jobs import get_jobs

    try:
        with session() as conn:
            row = conn.execute(
                "SELECT id FROM companies WHERE slug = ?", (c.external_id,)
            ).fetchone()
            if not row:
                return []
            jobs, _cached, _yc = await get_jobs(conn, client, row["id"], c.external_id or "")
    except Exception as exc:  # noqa: BLE001
        log.debug("YC fallback failed for %s: %s", c.name, exc)
        return []
    return [
        ((j.get("title") or "role")[:40], j.get("description") or "")
        for j in jobs
        if (j.get("description") or "").strip()
    ]


async def gather_candidates(
    client: httpx.AsyncClient, *, use_yc: bool = True, hn_threads: int = 3
) -> list[Candidate]:
    """Both universes, ICP-filtered where the metadata allows it.

    YC candidates are filtered up front on batch, size and hiring. HN candidates carry no
    size or stage data, so they are only filtered on being AI-shaped here and sized later
    by their open-role count.
    """
    out: list[Candidate] = []
    if use_yc:
        with session() as conn:
            out.extend(c for c in yc_candidates(conn) if qualifies(c)[0])
    if hn_threads:
        out.extend(await hn_candidates(client, threads=hn_threads))

    # Companies the digest saw get funded. These are the freshest leads available - the
    # announcement is days old - and they arrive with their funding date attached.
    with session() as conn:
        for row in recently_funded(conn):
            name = (row.get("company") or "").strip()
            if not name:
                continue
            out.append(
                Candidate(
                    name=name, domain=None, source="digest",
                    one_liner=(row.get("title") or "")[:300], is_hiring=True,
                )
            )

    seen: dict[str, Candidate] = {}
    for c in out:
        seen.setdefault((c.domain or c.name).lower(), c)
    return list(seen.values())


async def run_scan(
    client: httpx.AsyncClient, *, use_yc: bool = True, hn_threads: int = 3
) -> int:
    """Execute a full sweep. Returns the run id."""
    candidates = await gather_candidates(client, use_yc=use_yc, hn_threads=hn_threads)
    with session() as conn:
        run_id = start_run(conn, len(candidates))

    semaphore = asyncio.Semaphore(CONCURRENCY)
    done = 0
    found = 0

    async def worker(c: Candidate) -> None:
        nonlocal done, found
        async with semaphore:
            record = await scan_one(client, c)
        with session() as conn:
            store_prospect(conn, record)
            done += 1
            if record.get("verdict") == "prospect":
                found += 1
            update_run(conn, run_id, done=done, found=found, current=c.name)

    try:
        await asyncio.gather(*(worker(c) for c in candidates))
    except Exception as exc:  # noqa: BLE001
        with session() as conn:
            update_run(conn, run_id, status="failed", error=str(exc)[:300],
                       finished_at=time.time())
        raise
    with session() as conn:
        update_run(conn, run_id, status="done", finished_at=time.time(), current=None)
    return run_id


def load_prospects(
    conn: sqlite3.Connection, *, only_prospects: bool = True, limit: int = 300
) -> list[dict[str, Any]]:
    where = "WHERE verdict = 'prospect'" if only_prospects else ""
    rows = conn.execute(
        f"SELECT * FROM prospects {where} "
        "ORDER BY COALESCE(priority, 0) DESC, score DESC, open_roles DESC LIMIT ?",
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        for key in ("atlassian", "competitors", "reasons", "timing_signals"):
            d[key] = json.loads(d.get(key) or "[]")
        d["products"] = json.loads(d.get("products") or "{}")
        out.append(d)
    return out
