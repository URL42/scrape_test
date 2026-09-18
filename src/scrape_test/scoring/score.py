"""Apply the rules in `rules.py` to collected data. No tuning decisions live here."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass
from typing import Any

from .rules import LABELS, RULES_VERSION, SIGNALS, WEIGHTS


@dataclass(slots=True)
class Signal:
    key: str
    label: str
    strength: float
    weight: float
    points: float
    reason: str


@dataclass(slots=True)
class ScoreResult:
    total: float
    confidence: str
    signals: list[Signal]
    rules_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 1),
            "confidence": self.confidence,
            "rules_version": self.rules_version,
            "signals": [asdict(s) for s in self.signals],
        }


def _confidence(jobs: list[dict[str, Any]], fp: dict[str, Any], tools: list[dict[str, Any]]) -> str:
    """How much evidence is behind the number, so a 0 from 'no data' reads differently
    from a 0 from 'genuinely a poor fit'."""
    has_jobs = bool(jobs)
    has_site = bool(fp.get("detected")) and not fp.get("error")
    has_tools = bool(tools)
    if has_tools and (has_jobs or has_site):
        return "high"
    if has_jobs and has_site:
        return "medium"
    if has_jobs or has_site:
        return "low"
    return "low"


def compute_score(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fingerprint: dict[str, Any] | None,
    tools: list[dict[str, Any]] | None = None,
) -> ScoreResult:
    """`tools` are tooling hits mined from job descriptions (see yc/tooling.py)."""
    fp = fingerprint or {}
    tool_hits = tools or []
    signals: list[Signal] = []
    earned = 0.0

    for key, fn in SIGNALS.items():
        weight = WEIGHTS.get(key, 0.0)
        strength, reason = fn(company, jobs, fp, tool_hits)
        strength = max(0.0, min(1.0, strength))
        points = strength * weight
        earned += points
        signals.append(
            Signal(
                key=key,
                label=LABELS.get(key, key),
                strength=round(strength, 3),
                weight=weight,
                points=round(points, 2),
                reason=reason,
            )
        )

    # Rescale against the positive ceiling; negative signals can only subtract.
    ceiling = sum(w for w in WEIGHTS.values() if w > 0) or 1.0
    total = max(0.0, min(100.0, (earned / ceiling) * 100.0))

    signals.sort(key=lambda s: -abs(s.points))
    return ScoreResult(
        total=total,
        confidence=_confidence(jobs, fp, tool_hits),
        signals=signals,
        rules_version=RULES_VERSION,
    )


def rescore_all(conn: sqlite3.Connection) -> int:
    """Recompute every score we have the inputs for. Pure local work - no network.

    This is the whole point of keeping rules.py free of I/O: changing a weight and
    re-ranking everything already collected costs a single pass over SQLite.
    """
    from ..yc.directory import company_dict
    from ..yc.fingerprint import load_fingerprint
    from ..yc.jobs import load_jobs

    # UNION already dedupes. Companies with a stored score but no inputs are included so
    # a stale score cannot survive a rules change.
    rows = conn.execute(
        "SELECT company_id FROM job_postings "
        "UNION SELECT company_id FROM site_tech "
        "UNION SELECT company_id FROM scores"
    ).fetchall()

    updated = 0
    for r in rows:
        crow = conn.execute("SELECT * FROM companies WHERE id=?", (r["company_id"],)).fetchone()
        if not crow:
            continue
        company = company_dict(crow)
        result = compute_score(
            company, load_jobs(conn, company["id"]), load_fingerprint(conn, company["id"])
        )
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
