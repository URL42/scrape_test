"""Per-product fit: which of Jira, Confluence, Loom or Rovo to lead with.

One blended score cannot tell a rep what to open with. The evidence differs by product -
a company hiring across six timezones is a Loom and Confluence conversation; one running
Linear with three engineering teams is a Jira conversation. Scoring them separately means
the lead product falls out of the data instead of being guessed.

Pure, like the rest of scoring/: no network, no database.
"""

from __future__ import annotations

import re
from typing import Any

PRODUCTS = ("Jira", "Confluence", "Loom", "Rovo")

# Competitors whose presence argues *for* a given product.
DISPLACES: dict[str, set[str]] = {
    "Jira": {"Linear", "Asana", "ClickUp", "Monday.com", "Shortcut", "Height", "Trello",
             "Basecamp", "Azure DevOps", "YouTrack", "Pivotal Tracker", "GitHub Projects"},
    "Confluence": {"Notion", "Coda", "Slab", "Guru", "GitBook", "Slite", "Nuclino",
                   "Almanac", "Document360"},
    "Loom": set(),                       # no catalog competitor; evidence is how they work
    "Rovo": {"Glean", "Dashworks", "Moveworks", "Onyx", "Unleash", "Qatalog"},
}

AGILE = re.compile(r"\b(sprint|scrum|kanban|agile|backlog|standup|stand-up|retro|epic)\b", re.I)
MULTI_TEAM = re.compile(
    r"\b(cross[- ]functional|multiple teams|other teams|stakeholders|squad|"
    r"platform team|partner teams|across teams)\b", re.I
)
DOCS = re.compile(
    r"\b(documentation|technical writing|tech writer|runbook|knowledge base|wiki|"
    r"onboarding docs|internal docs|design doc)\b", re.I
)
COMPLIANCE = re.compile(r"\b(soc\s?2|iso\s?27001|hipaa|gdpr|audit|compliance)\b", re.I)
ASYNC_WORK = re.compile(
    r"\b(async|asynchronous|remote[- ]first|fully remote|distributed team|"
    r"time ?zones|written communication|video|screen recording|demo)\b", re.I
)
CUSTOMER_FACING = re.compile(
    r"\b(customer success|solutions engineer|sales engineer|onboarding|enablement|"
    r"support engineer|implementation)\b", re.I
)
AI_WORK = re.compile(
    r"\b(llm|rag|embedding|vector|ai agent|agentic|knowledge graph|semantic search|"
    r"machine learning|data platform)\b", re.I
)

TOOL_SPRAWL_THRESHOLD = 4


def _corpus(jobs: list[dict[str, Any]]) -> tuple[str, str]:
    body = " ".join(
        f"{j.get('title') or ''} {j.get('description') or ''}" for j in jobs
    )
    locations = " ".join((j.get("location") or "") for j in jobs)
    return body, locations


def _timezone_spread(locations: str) -> int:
    """A rough count of distinct regions named across postings."""
    regions = {
        "us": (
            r"\b(usa|united states|remote \(us|san francisco|new york|nyc|austin|"
            r"seattle|boston)\b"
        ),
        "emea": r"\b(uk|london|berlin|amsterdam|paris|dublin|madrid|lisbon|emea|europe)\b",
        "apac": r"\b(india|bangalore|singapore|sydney|tokyo|apac|australia)\b",
        "latam": r"\b(brazil|argentina|mexico|latam|colombia)\b",
    }
    return sum(1 for pattern in regions.values() if re.search(pattern, locations, re.I))


def product_fit(
    jobs: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    company: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Score each product 0-100 with the evidence that earned it."""
    body, locations = _corpus(jobs)
    named = {t["product"] for t in tools}
    stated = {t["product"] for t in tools if t.get("strength") == "stated"}
    eng_roles = sum(
        1 for j in jobs
        if (j.get("role") or "").lower() in ("eng", "engineering")
        or (j.get("pretty_role") or "").lower() == "engineering"
    )
    spread = _timezone_spread(locations)
    out: dict[str, dict[str, Any]] = {}

    for product in PRODUCTS:
        score = 0.0
        why: list[str] = []
        rivals = DISPLACES[product] & named
        rivals_stated = DISPLACES[product] & stated

        if rivals_stated:
            score += 45
            why.append(f"uses {', '.join(sorted(rivals_stated))}")
        elif rivals:
            score += 22
            why.append(f"names {', '.join(sorted(rivals))}")

        if product == "Jira":
            if eng_roles >= 3:
                score += 20
                why.append(f"{eng_roles} engineering roles open")
            elif eng_roles:
                score += 10
                why.append(f"{eng_roles} engineering role(s) open")
            if AGILE.search(body):
                score += 20
                why.append("sprint/agile language in postings")
            if MULTI_TEAM.search(body):
                score += 15
                why.append("cross-team coordination named")

        elif product == "Confluence":
            if DOCS.search(body):
                score += 25
                why.append("documentation work named")
            if COMPLIANCE.search(body):
                score += 20
                why.append("compliance or audit work named")
            if spread >= 2:
                score += 15
                why.append(f"hiring across {spread} regions")

        elif product == "Loom":
            if ASYNC_WORK.search(body):
                score += 35
                why.append("async or remote-first working named")
            if spread >= 2:
                score += 30
                why.append(f"hiring across {spread} regions")
            if CUSTOMER_FACING.search(body):
                score += 20
                why.append("customer-facing roles that demo and onboard")

        elif product == "Rovo":
            if len(named) >= TOOL_SPRAWL_THRESHOLD:
                score += 35
                why.append(f"{len(named)} distinct tools detected - knowledge is scattered")
            if AI_WORK.search(body):
                score += 30
                why.append("AI-native team, already sold on the premise")
            if DOCS.search(body):
                score += 10
                why.append("documentation work named")

        out[product] = {
            "score": round(max(0.0, min(100.0, score)), 1),
            "why": why or ["no supporting evidence found"],
        }
    return out


def lead_product(fits: dict[str, dict[str, Any]]) -> tuple[str | None, float]:
    best = max(fits.items(), key=lambda kv: kv[1]["score"], default=None)
    if not best or best[1]["score"] <= 0:
        return None, 0.0
    return best[0], best[1]["score"]


def priority(fit: float, timing: float) -> float:
    """Multiplicative, not additive.

    High fit with no trigger is a nurture; a strong trigger at a company with no problem
    is noise. Only both together mean "call them this week", and multiplying is the only
    combination that says so. The square root keeps the result on a readable 0-100 scale.
    """
    return round(((max(fit, 0.0) * max(timing, 0.0)) ** 0.5), 1)
