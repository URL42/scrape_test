"""Score persistence and re-ranking - the I/O half of scoring.

`scoring/` is deliberately pure: no network, no database, no clock beyond the current
year. That is what makes re-ranking instant, the rules trivially testable, and a weight
change safe to try. Anything that needs to read or write therefore lives here instead of
being smuggled into scoring behind a function-level import.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict

from .extract.fingerprint import load_fingerprint
from .scoring import RULES_VERSION, compute_score
from .scoring.score import ScoreResult
from .yc.directory import company_dict
from .yc.jobs import load_jobs, tools_from_jobs


def rescore_all(conn: sqlite3.Connection) -> int:
    """Recompute every score we have the inputs for. Returns the number updated.

    This is the whole point of keeping rules.py free of I/O: changing a weight and
    re-ranking everything already collected costs a single pass over SQLite.
    """
    # UNION already dedupes. Companies with a stored score but no inputs are included so
    # a stale score cannot survive a rules change.
    rows = conn.execute(
        "SELECT company_id FROM job_postings "
        "UNION SELECT company_id FROM site_tech "
        "UNION SELECT company_id FROM scores"
    ).fetchall()

    updated = 0
    for r in rows:
        crow = conn.execute(
            "SELECT * FROM companies WHERE id=?", (r["company_id"],)
        ).fetchone()
        if not crow:
            continue
        company = company_dict(crow)
        jobs = load_jobs(conn, company["id"])
        tools = [t.as_dict() for t in tools_from_jobs(jobs)]
        result = compute_score(company, jobs, load_fingerprint(conn, company["id"]), tools)
        store_score(conn, company["id"], result)
        updated += 1
    return updated


def store_score(conn: sqlite3.Connection, company_id: int, result: ScoreResult) -> None:
    conn.execute(
        """INSERT INTO scores (company_id, total, confidence, breakdown, rules_version, computed_at)
           VALUES (?,?,?,?,?,?)
           ON CONFLICT(company_id) DO UPDATE SET
               total=excluded.total, confidence=excluded.confidence,
               breakdown=excluded.breakdown, rules_version=excluded.rules_version,
               computed_at=excluded.computed_at""",
        (
            company_id,
            round(result.total, 1),
            result.confidence,
            json.dumps([asdict(s) for s in result.signals]),
            result.rules_version,
            time.time(),
        ),
    )
    conn.commit()


__all__ = ["RULES_VERSION", "rescore_all", "store_score"]
