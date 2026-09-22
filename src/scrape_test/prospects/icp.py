"""The ideal customer profile. Tune this file; nothing here touches the network.

Same idea as scoring/rules.py: every arguable threshold lives in one place so the target
list can be re-cut without re-scanning. The profile being described is AI-first companies
at YC scale or a little larger, early/VC stage, that are not yet Atlassian customers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# --------------------------------------------------------------------------------------
# CRITERIA - the dial board.
# --------------------------------------------------------------------------------------
MIN_BATCH_YEAR = 2022          # YC batch recency; older cohorts have settled their tooling
TEAM_SIZE_MIN = 10             # below this, coordination pain has not started
TEAM_SIZE_MAX = 200            # above this, an incumbent is near-certain
REQUIRE_HIRING = True
REQUIRE_ACTIVE = True

# Open roles as a stand-in for headcount, for sources with no team-size data (HN).
# Imperfect, but it separates a 4-role seed startup from a 300-role scale-up.
PROXY_ROLES_MIN = 2
PROXY_ROLES_MAX = 120

# A company that raised this recently is worth holding even with no job data yet: it has
# not had time to post roles. Scanning it once and discarding it throws away the freshest
# timing signal the tool can get.
WATCHLIST_MAX_FUNDING_AGE_DAYS = 120.0
# How long to wait before looking again. Roughly the lag between closing a round and the
# hiring page filling up.
WATCHLIST_RESCAN_DAYS = 42.0

AI_TAGS = {
    "ai", "generative ai", "machine learning", "ai assistant", "aiops",
    "conversational ai", "computer vision", "nlp", "ml", "data labeling",
    "ai-enhanced learning", "ai-powered drug discovery", "data science",
    "data engineering", "big data", "llm", "ai infrastructure",
}

# Matched against the company blurb when tags are absent or thin.
AI_TEXT = re.compile(
    r"\b(ai|a\.i\.|llm|llms|genai|gen-ai|machine learning|deep learning|agentic|"
    r"ai-native|ai-first|ai-powered|neural|transformer|rag|foundation model)\b",
    re.I,
)

# Products that mean "already a customer" - a hard exclude for a net-new motion.
ATLASSIAN_PRODUCTS = {
    "Jira", "Confluence", "Bitbucket", "Trello", "Opsgenie", "Statuspage", "Compass",
}

# Competitor presence is the strongest *positive* qualifier. A company saying "issue
# tracking with Linear" proves it buys tooling, has the problem, and is not yours yet -
# which is far better evidence than the absence of any Atlassian mention.
DISPLACEABLE = {
    "Linear", "Asana", "ClickUp", "Monday.com", "Shortcut", "Height", "Basecamp",
    "Notion", "Coda", "Slab", "Guru", "GitBook", "Slite", "Nuclino", "Productboard",
    "Aha!", "Canny", "Azure DevOps", "YouTrack", "Pivotal Tracker",
}


@dataclass(slots=True)
class Candidate:
    """A company to scan, from any universe source."""

    name: str
    domain: str | None
    source: str                       # "yc" | "hn"
    external_id: str | None = None
    batch: str | None = None
    team_size: int | None = None
    one_liner: str | None = None
    tags: list[str] = field(default_factory=list)
    is_hiring: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "domain": self.domain, "source": self.source,
            "external_id": self.external_id, "batch": self.batch,
            "team_size": self.team_size, "one_liner": self.one_liner,
            "tags": self.tags, "is_hiring": self.is_hiring,
        }


def batch_year(batch: str | None) -> int | None:
    if not batch:
        return None
    m = re.search(r"(20\d{2})", batch)
    return int(m.group(1)) if m else None


def is_ai_first(
    tags: list[str] | None, one_liner: str | None, description: str | None = None
) -> bool:
    """Tag match first, then the blurb. YC tags ~2,300 active companies as AI-ish, but the
    tagging is uneven, so the text check catches the rest."""
    if tags and {t.lower() for t in tags} & AI_TAGS:
        return True
    blob = f"{one_liner or ''} {description or ''}"
    return bool(blob.strip() and AI_TEXT.search(blob))


def size_ok(team_size: int | None, open_roles: int | None = None) -> bool:
    """Team size where known; otherwise open-role count as a proxy."""
    if team_size:
        return TEAM_SIZE_MIN <= team_size <= TEAM_SIZE_MAX
    if open_roles is not None:
        return PROXY_ROLES_MIN <= open_roles <= PROXY_ROLES_MAX
    return False


def qualifies(c: Candidate, *, open_roles: int | None = None) -> tuple[bool, str]:
    """Return (in_profile, reason). The reason is kept so a rejected company can be
    argued with rather than silently dropped."""
    if not is_ai_first(c.tags, c.one_liner):
        return False, "not AI-first"
    year = batch_year(c.batch)
    if year is not None and year < MIN_BATCH_YEAR:
        return False, f"batch {year} predates {MIN_BATCH_YEAR}"
    if REQUIRE_HIRING and c.source == "yc" and not c.is_hiring:
        return False, "not currently hiring"
    if not size_ok(c.team_size, open_roles):
        got = c.team_size or open_roles
        return False, f"size {got} outside {TEAM_SIZE_MIN}-{TEAM_SIZE_MAX}"
    return True, "in profile"


# Prospect ranking for a net-new motion. Deliberately different from the general
# Atlassian-fit score: here, an existing Atlassian footprint is disqualifying rather than
# merely negative, and a named competitor is the single best qualifier.
PROSPECT_WEIGHTS = {
    "displaceable_stated": 45.0,   # "issue tracking with Linear" - proven buyer, not ours
    "displaceable_named": 20.0,    # competitor named, weaker phrasing
    "hiring_engineers": 15.0,      # open roles = budget and growth
    "size_band": 15.0,             # squarely in the coordination-pain window
    "recent_batch": 10.0,          # tooling decisions still live
}


def watchlist_candidate(funding_age_days: float | None, open_roles: int | None) -> bool:
    """True when fit is *unmeasured* rather than measured and poor.

    A company that raised ten days ago and has posted nothing is not a bad fit - we simply
    cannot see yet. Scoring it zero next to genuinely poor fits loses the best lead in the
    list, so it is held and looked at again once postings appear.
    """
    if funding_age_days is None:
        return False
    if funding_age_days > WATCHLIST_MAX_FUNDING_AGE_DAYS:
        return False
    return not open_roles


def prospect_score(
    candidate: Candidate,
    atlassian: list[dict[str, Any]],
    competitors: list[dict[str, Any]],
    open_roles: int,
) -> tuple[float, str, list[str]]:
    """Return (score, verdict, reasons).

    Verdict is "existing customer", "prospect" or "unqualified". An Atlassian product the
    company states it uses is a hard stop - that is an expansion conversation for someone
    else, not a net-new lead.
    """
    reasons: list[str] = []
    stated_atl = [a["product"] for a in atlassian if a.get("strength") == "stated"]
    if stated_atl:
        return 0.0, "existing customer", [f"already uses {', '.join(sorted(stated_atl))}"]

    earned = 0.0
    displaceable = [c for c in competitors if c["product"] in DISPLACEABLE]
    stated = [c["product"] for c in displaceable if c.get("strength") == "stated"]
    named = [c["product"] for c in displaceable]

    if stated:
        earned += PROSPECT_WEIGHTS["displaceable_stated"]
        reasons.append(f"states it uses {', '.join(sorted(set(stated)))}")
    elif named:
        earned += PROSPECT_WEIGHTS["displaceable_named"]
        reasons.append(f"names {', '.join(sorted(set(named)))}")
    else:
        reasons.append("no competing tooling named - unproven buyer")

    if open_roles >= PROXY_ROLES_MIN:
        earned += PROSPECT_WEIGHTS["hiring_engineers"]
        reasons.append(f"{open_roles} open roles")

    if size_ok(candidate.team_size, open_roles):
        earned += PROSPECT_WEIGHTS["size_band"]
        reasons.append(
            f"team {candidate.team_size}" if candidate.team_size else "size proxy in band"
        )

    year = batch_year(candidate.batch)
    if year and year >= MIN_BATCH_YEAR:
        earned += PROSPECT_WEIGHTS["recent_batch"]
        reasons.append(f"{candidate.batch}")

    weak_atl = [a["product"] for a in atlassian]
    if weak_atl:
        reasons.append(f"Atlassian mentioned in passing ({', '.join(sorted(set(weak_atl)))})")

    ceiling = sum(PROSPECT_WEIGHTS.values()) - PROSPECT_WEIGHTS["displaceable_named"]
    total = max(0.0, min(100.0, earned / ceiling * 100.0))

    # A company that names no tooling at all is not a qualified prospect - it is an
    # unknown. Size and hiring alone describe thousands of companies; the thing that makes
    # this list worth working is positive evidence they already buy tooling that is not
    # ours. Ranking unknowns as prospects would bury the real leads.
    if not named:
        verdict = "no signal"
    elif total >= 35:
        verdict = "prospect"
    else:
        verdict = "unqualified"
    return total, verdict, reasons


def from_yc_row(row: Any) -> Candidate:
    tags = json.loads(row["tags"] or "[]")
    website = row["website"]
    domain = None
    if website:
        domain = re.sub(r"^https?://", "", website).split("/")[0].removeprefix("www.")
    return Candidate(
        name=row["name"], domain=domain, source="yc", external_id=row["slug"],
        batch=row["batch"], team_size=row["team_size"], one_liner=row["one_liner"],
        tags=tags, is_hiring=bool(row["is_hiring"]),
    )


def current_year() -> int:
    return datetime.now(UTC).year
