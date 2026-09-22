"""Apply the rules in `rules.py` to collected data. No tuning decisions live here."""

from __future__ import annotations

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
    confidence_reasons: list[str]
    signals: list[Signal]
    rules_version: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "total": round(self.total, 1),
            "confidence": self.confidence,
            "confidence_reasons": self.confidence_reasons,
            "rules_version": self.rules_version,
            "signals": [asdict(s) for s in self.signals],
        }


# Fingerprint categories that actually bear on an Atlassian conversation. Knowing a
# company uses Cloudflare and Google Analytics tells you nothing about how they coordinate
# work, so those must not inflate confidence.
DECISIVE_CATEGORIES = ("atlassian", "competing_tools", "source_control")
STALE_DAYS = 180


def _confidence(
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Grade how *informative* the evidence is, not merely whether it exists.

    The earlier version answered "did we get any jobs and any fingerprint?", which read
    "high" for a company with two year-old postings and three generic website hits. That
    is exactly backwards: those inputs support almost no conclusion. Points are awarded
    for evidence that could actually change the call, and the reasons come back with the
    label so a misleading grade can be spotted rather than trusted.
    """
    from .rules import parse_relative_days

    points = 0
    reasons: list[str] = []

    stated = [t for t in tools if t.get("strength") == "stated"]
    if stated:
        points += 3
        reasons.append(
            f"{len(stated)} tool(s) named outright in job descriptions: "
            + ", ".join(sorted(t["product"] for t in stated))
        )
    elif tools:
        points += 1
        reasons.append(f"{len(tools)} tool(s) mentioned in passing in job text")

    described = [j for j in jobs if (j.get("description") or "").strip()]
    if described:
        points += 2
        reasons.append(f"{len(described)} job description(s) read in full")
    elif jobs:
        reasons.append("job postings found, but no descriptions to read")
    else:
        reasons.append("no open roles listed")

    ages = [
        d for d in (parse_relative_days(j.get("last_active_rel")) for j in jobs) if d is not None
    ]
    if ages and min(ages) <= 90:
        points += 1
        reasons.append("at least one posting active in the last 3 months")
    elif ages and min(ages) > STALE_DAYS:
        # Two points, not one: the "high" threshold is 4, and stale evidence on its own
        # must not be able to reach it. Tooling named in an old posting is still useful,
        # but the company may well have migrated since.
        points -= 2
        reasons.append("every posting is over 6 months old - may not reflect today")

    detected = fp.get("detected") or {}
    decisive = [
        item["product"]
        for category in DECISIVE_CATEGORIES
        for item in detected.get(category, [])
        if item.get("confidence") == "strong"
    ]
    if decisive:
        points += 1
        reasons.append("website shows relevant tooling: " + ", ".join(sorted(decisive)))
    elif detected:
        reasons.append("website tooling detected, but none of it bears on coordination")
    elif fp.get("error"):
        reasons.append(f"website could not be read ({fp['error']})")

    if points >= 4:
        label = "high"
    elif points >= 2:
        label = "medium"
    else:
        label = "low"
    return label, reasons


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
    label, reasons = _confidence(jobs, fp, tool_hits)
    return ScoreResult(
        total=total,
        confidence=label,
        confidence_reasons=reasons,
        signals=signals,
        rules_version=RULES_VERSION,
    )
