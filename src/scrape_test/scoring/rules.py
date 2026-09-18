"""The tunable half of scoring. Everything you would want to argue about lives here.

Design rule: nothing in this file touches the network or the database. Signals are pure
functions of already-collected data, so re-scoring the whole cache after a weight change
is a sub-second operation (`scrape-test rescore`) rather than a re-crawl.

Each signal returns a strength in 0.0-1.0 plus a human-readable reason. The final score is
a weighted sum, rescaled to 0-100. Because negative weights exist, the rescale uses the
positive weight total as the ceiling and clamps at zero.

Bump RULES_VERSION whenever you change weights or logic, so stored scores stay comparable.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

RULES_VERSION = "2026.09.17-1"

# --------------------------------------------------------------------------------------
# WEIGHTS - this is the dial board. Positive pulls a company up the list, negative pushes
# it down. Relative magnitude is what matters, not the absolute numbers.
# --------------------------------------------------------------------------------------
WEIGHTS: dict[str, float] = {
    "eng_hiring_volume": 20.0,  # open engineering roles = teams about to get bigger
    "team_size_threshold": 15.0,  # ~10-40 people is where informal coordination breaks
    "competing_tools": 15.0,  # already buys tooling, and it is displaceable
    "stack_complexity": 10.0,  # polyglot stack = more integration surface
    "job_freshness": 10.0,  # postings active recently = live budget
    "recent_batch": 10.0,  # newer batch = tooling decisions still up for grabs
    "already_atlassian": -25.0,  # existing customer: expansion play, not net-new
}

# Products that indicate an incumbent Atlassian footprint.
ATLASSIAN_PRODUCTS = {
    "Jira",
    "Confluence",
    "Bitbucket",
    "Trello",
    "Statuspage",
    "Opsgenie",
    "Atlassian (generic)",
}

# Team size bands -> strength. The middle band is the classic "we outgrew a spreadsheet"
# moment; very large orgs usually already have an entrenched vendor.
TEAM_SIZE_BANDS: list[tuple[int, int, float, str]] = [
    (0, 4, 0.10, "tiny team, little coordination pain yet"),
    (5, 9, 0.45, "approaching the size where process starts to matter"),
    (10, 40, 1.00, "prime band: informal coordination is breaking down"),
    (41, 150, 0.70, "large enough to need tooling, may already have it"),
    (151, 10**9, 0.25, "enterprise scale, likely an entrenched incumbent"),
]

# YC's machine-readable role slug is "eng", while the display string is "Engineering".
# Match on both: relying on either one alone silently zeroes the strongest signal.
ENGINEERING_ROLE_SLUGS = {"eng", "engineering"}


def is_engineering(job: dict[str, Any]) -> bool:
    role = (job.get("role") or "").strip().lower()
    pretty = (job.get("pretty_role") or "").strip().lower()
    return role in ENGINEERING_ROLE_SLUGS or pretty == "engineering"


_REL_TIME = re.compile(r"(\d+)\s*(day|week|month|year|hour|minute)", re.I)
_UNIT_DAYS = {"minute": 0.0, "hour": 0.04, "day": 1, "week": 7, "month": 30, "year": 365}
# "a month ago" / "an hour ago" - the singular YC renders as a word rather than a digit.
_WORDED_TIME = re.compile(r"\ban?\s+(day|week|month|year|hour|minute)", re.I)
_SEASON_ORDER = {"winter": 0, "spring": 1, "summer": 2, "fall": 3}


def parse_relative_days(value: str | None) -> float | None:
    """'2 months' -> 60.0. YC renders posting ages as relative strings, not timestamps.

    It also writes the singular as a word ('a month ago', 'an hour ago'), which a
    digit-only pattern misses - and a missed parse silently zeroes the freshness signal.
    """
    if not value:
        return None
    text = value.lower()
    match = _REL_TIME.search(text)
    if match:
        return float(match.group(1)) * _UNIT_DAYS[match.group(2).lower()]
    worded = _WORDED_TIME.search(text)
    if worded:
        return float(_UNIT_DAYS[worded.group(1).lower()])
    return 0.0 if "just now" in text else None


def parse_batch(batch: str | None) -> tuple[int, int] | None:
    """'Fall 2025' -> (2025, 3), for ordering batches by recency."""
    if not batch:
        return None
    m = re.match(r"(winter|spring|summer|fall)\s+(\d{4})", batch.strip(), re.I)
    if not m:
        return None
    return int(m.group(2)), _SEASON_ORDER[m.group(1).lower()]


def stated_tools(tools: list[dict[str, Any]], *, exclude: set[str] | None = None) -> list[str]:
    """Products a job description says the company uses. The strongest signal we collect:
    a posting reading "Issue tracking with Linear" is the company stating its own stack,
    which beats inferring from a public website."""
    exclude = exclude or set()
    return [
        t["product"] for t in tools if t.get("strength") == "stated" and t["product"] not in exclude
    ]


def mentioned_tools(tools: list[dict[str, Any]], *, exclude: set[str] | None = None) -> list[str]:
    exclude = exclude or set()
    return [t["product"] for t in tools if t["product"] not in exclude]


def _strong(fingerprint: dict[str, Any], category: str) -> list[str]:
    """Products detected in a category with strong (actually-served) evidence."""
    items = (fingerprint.get("detected") or {}).get(category, [])
    return [i["product"] for i in items if i.get("confidence") == "strong"]


def _any_conf(fingerprint: dict[str, Any], category: str) -> list[str]:
    items = (fingerprint.get("detected") or {}).get(category, [])
    return [i["product"] for i in items]


# --------------------------------------------------------------------------------------
# Signals. Each returns (strength_0_to_1, reason). Add one here and register it in SIGNALS.
# --------------------------------------------------------------------------------------


def signal_eng_hiring_volume(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[float, str]:
    eng = [j for j in jobs if is_engineering(j)]
    n = len(eng)
    if n == 0:
        return 0.0, "no open engineering roles on YC"
    strength = min(1.0, 0.4 + 0.3 * (n - 1))
    return strength, f"{n} open engineering role{'s' if n != 1 else ''}"


def signal_team_size_threshold(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[float, str]:
    size = company.get("team_size")
    if not size:
        return 0.0, "team size unknown"
    for low, high, strength, label in TEAM_SIZE_BANDS:
        if low <= size <= high:
            return strength, f"{size} people - {label}"
    return 0.0, f"{size} people"


def signal_competing_tools(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[float, str]:
    """Rank evidence by how directly the company vouches for it.

    A job description naming a competitor outranks a website hit: the former is the
    company describing its own workflow, the latter is an inference from marketing HTML.
    """
    stated = stated_tools(tools, exclude=ATLASSIAN_PRODUCTS)
    if stated:
        return 1.0, f"job postings name {', '.join(sorted(stated))} - displaceable"

    named = mentioned_tools(tools, exclude=ATLASSIAN_PRODUCTS)
    site_strong = _strong(fp, "competing_tools")
    if site_strong:
        return 0.85, f"site uses {', '.join(sorted(site_strong))} - displaceable"
    if named:
        return 0.5, f"job postings mention {', '.join(sorted(set(named)))}"

    site_weak = _any_conf(fp, "competing_tools")
    if site_weak:
        return 0.35, f"possible {', '.join(sorted(set(site_weak)))} (weak evidence)"
    return 0.0, "no competing project tooling detected"


def signal_stack_complexity(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[float, str]:
    skills = {s for j in jobs for s in (j.get("skills") or [])}
    n = len(skills)
    if n == 0:
        return 0.0, "no stack data (no engineering postings)"
    if n <= 3:
        return 0.3, f"{n} distinct technologies"
    if n <= 7:
        return 0.7, f"{n} distinct technologies"
    return 1.0, f"{n} distinct technologies - broad integration surface"


def signal_job_freshness(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[float, str]:
    raw = (parse_relative_days(j.get("last_active_rel")) for j in jobs)
    ages = [d for d in raw if d is not None]
    if not ages:
        return 0.0, "no posting activity data"
    freshest = min(ages)
    if freshest <= 7:
        return 1.0, "a posting was active within the last week"
    if freshest <= 30:
        return 0.75, "a posting was active within the last month"
    if freshest <= 90:
        return 0.4, "most recent posting activity 1-3 months ago"
    return 0.1, "postings look stale (>3 months)"


def signal_recent_batch(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[float, str]:
    parsed = parse_batch(company.get("batch"))
    if not parsed:
        return 0.0, "batch unknown"
    year, _season = parsed
    # Derived, not hardcoded: a pinned year silently re-weights every batch each January.
    # datetime.now() reads a clock, not I/O, so rules.py stays free of side effects.
    age = datetime.now(UTC).year - year
    if age <= 1:
        return 1.0, f"{company['batch']} - greenfield tooling decisions"
    if age <= 3:
        return 0.7, f"{company['batch']} - tooling still settling"
    if age <= 6:
        return 0.35, f"{company['batch']}"
    return 0.1, f"{company['batch']} - tooling long since chosen"


def signal_already_atlassian(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    fp: dict[str, Any],
    tools: list[dict[str, Any]],
) -> tuple[float, str]:
    """Negative weight: a strong hit means they already pay Atlassian."""
    stated = [
        t["product"]
        for t in tools
        if t.get("strength") == "stated" and t["product"] in ATLASSIAN_PRODUCTS
    ]
    if stated:
        return 1.0, f"job postings name {', '.join(sorted(stated))} - existing customer"

    site_strong = [p for p in _strong(fp, "atlassian") if p in ATLASSIAN_PRODUCTS]
    if site_strong:
        return 0.9, f"site exposes {', '.join(sorted(site_strong))} - expansion, not net-new"

    named = [t["product"] for t in tools if t["product"] in ATLASSIAN_PRODUCTS]
    if named:
        return 0.6, f"job postings mention {', '.join(sorted(set(named)))}"

    skills = {s.lower() for j in jobs for s in (j.get("skills") or [])}
    if {"jira", "confluence", "bitbucket"} & skills:
        return 0.8, "job skills list Atlassian tools"

    weak = _any_conf(fp, "atlassian")
    if weak:
        return 0.2, f"weak/ambiguous Atlassian reference ({', '.join(sorted(set(weak)))})"
    return 0.0, "no Atlassian footprint found in postings or on the site"


# (company, jobs, fingerprint, tools) -> (strength 0..1, human-readable reason)
SignalFn = Callable[
    [dict[str, Any], list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]],
    tuple[float, str],
]

SIGNALS: dict[str, SignalFn] = {
    "eng_hiring_volume": signal_eng_hiring_volume,
    "team_size_threshold": signal_team_size_threshold,
    "competing_tools": signal_competing_tools,
    "stack_complexity": signal_stack_complexity,
    "job_freshness": signal_job_freshness,
    "recent_batch": signal_recent_batch,
    "already_atlassian": signal_already_atlassian,
}

# Human-facing labels for the UI.
LABELS = {
    "eng_hiring_volume": "Engineering hiring",
    "team_size_threshold": "Team size",
    "competing_tools": "Competing tooling",
    "stack_complexity": "Stack complexity",
    "job_freshness": "Posting freshness",
    "recent_batch": "Batch recency",
    "already_atlassian": "Existing Atlassian",
}
