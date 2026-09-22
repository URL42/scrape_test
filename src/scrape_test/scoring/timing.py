"""Timing signals: is now the moment, rather than is this a good fit.

Fit answers "do they have the problem". It cannot answer "why this week", and a list
ranked on fit alone looks identical in January and June. These are the trigger events
that open a buying window, each decaying with age - a funding round eight months ago is
history, the same round last month is a reason to call.

Kept pure like rules.py: no network, no database, so re-scoring is instant.
"""

from __future__ import annotations

import re
from typing import Any

# Weight, and the half-life in days after which the signal is worth half as much.
TIMING_WEIGHTS: dict[str, tuple[float, float]] = {
    "just_funded":      (40.0, 120.0),  # strongest: headcount and tooling budget follow
    "first_of_role":    (25.0, 180.0),  # "1st Product Manager" = process became a job
    "tool_migration":   (25.0,  90.0),  # an open buying window, briefly
    "leadership_hire":  (15.0, 120.0),  # new VP Eng re-evaluates tooling in 90 days
    "hiring_surge":     (15.0,  90.0),
    "compliance_push":  (10.0, 180.0),  # SOC2/ISO work needs audit trails and docs
    "going_distributed": (10.0, 180.0),
}

# "1st Product Manager", "Founding Engineer", "first engineering manager" - a company
# hiring the first of a role is saying out loud that the work outgrew improvisation.
FIRST_OF_ROLE = re.compile(
    r"\b(?:1st|first|founding)\s+(?:\w+\s+){0,2}"
    r"(engineer|developer|product manager|pm|designer|manager|lead|head)\b",
    re.I,
)
LEADERSHIP = re.compile(
    r"\b(vp of engineering|vp engineering|head of engineering|head of product|"
    r"head of platform|director of engineering|engineering manager|cto|chief technology|"
    r"chief product|head of people)\b",
    re.I,
)
MIGRATION = re.compile(
    r"\b(migrat\w+\s+(?:from|to|off)|moving (?:off|from|to)|switch\w*\s+(?:from|to)|"
    r"replac\w+\s+(?:our|the|legacy)|consolidat\w+\s+(?:our|tooling|tools))\b",
    re.I,
)
COMPLIANCE = re.compile(
    r"\b(soc\s?2|iso\s?27001|hipaa|gdpr|fedramp|pci[- ]dss|audit trail|compliance program)\b",
    re.I,
)
DISTRIBUTED = re.compile(
    r"\b(remote[- ]first|fully remote|distributed team|async[- ]first|"
    r"asynchronous communication|across time ?zones|globally distributed)\b",
    re.I,
)
HIRING_SURGE_ROLES = 12


def decay(age_days: float | None, half_life: float) -> float:
    """1.0 when fresh, 0.5 at one half-life, floor of 0.1 so old news is not worthless."""
    if age_days is None:
        return 0.5          # unknown age: assume middling rather than best or worst
    if age_days <= 0:
        return 1.0
    return max(0.1, 0.5 ** (age_days / half_life))


def _text_of(jobs: list[dict[str, Any]]) -> str:
    parts = []
    for j in jobs:
        parts.append(j.get("title") or "")
        parts.append(j.get("description") or "")
    return " ".join(parts)


def timing_signals(
    jobs: list[dict[str, Any]],
    *,
    funding_age_days: float | None = None,
    open_roles: int | None = None,
) -> list[dict[str, Any]]:
    """Detect trigger events. Returns one entry per signal that fired."""
    text = _text_of(jobs)
    titles = " ".join((j.get("title") or "") for j in jobs)
    found: list[dict[str, Any]] = []

    def add(key: str, reason: str, age: float | None) -> None:
        weight, half_life = TIMING_WEIGHTS[key]
        strength = decay(age, half_life)
        found.append({
            "key": key, "reason": reason, "weight": weight,
            "strength": round(strength, 3), "points": round(weight * strength, 1),
        })

    if funding_age_days is not None:
        add("just_funded", "raised recently", funding_age_days)

    m = FIRST_OF_ROLE.search(titles)
    if m:
        add("first_of_role", f'hiring their {m.group(0).lower()}', None)

    m = MIGRATION.search(text)
    if m:
        add("tool_migration", f'job text mentions "{m.group(0).lower()}"', None)

    m = LEADERSHIP.search(titles)
    if m:
        add("leadership_hire", f"hiring a {m.group(0).lower()}", None)

    if open_roles and open_roles >= HIRING_SURGE_ROLES:
        add("hiring_surge", f"{open_roles} roles open at once", None)

    if COMPLIANCE.search(text):
        add("compliance_push", "compliance work named in postings", None)

    if DISTRIBUTED.search(text):
        add("going_distributed", "remote or async-first working named", None)

    return found


# Normalising against every weight would be wrong: no company ever fires all seven
# triggers at once, so the scale would never leave the low teens. A realistically strong
# company shows two or three, so the ceiling is the three largest weights.
_TOP_WEIGHTS = sorted((w for w, _ in TIMING_WEIGHTS.values()), reverse=True)[:3]
TIMING_CEILING = sum(_TOP_WEIGHTS)


def timing_score(signals: list[dict[str, Any]]) -> float:
    """0-100, where 100 means several strong, fresh triggers at once."""
    if not signals:
        return 0.0
    earned = sum(s["points"] for s in signals)
    return round(max(0.0, min(100.0, earned / TIMING_CEILING * 100.0)), 1)
