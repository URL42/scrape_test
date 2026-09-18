"""Detect tooling named in free text - primarily job descriptions.

This is the strongest signal the scraper has. Website fingerprinting infers from a public
surface; a job description saying "Issue tracking with Linear" is the company stating its
internal stack outright. Rollstack's AI Software Engineer posting does exactly that while
its structured `skills` array is empty, which is what motivated this module.

The catalog is organised around what Atlassian sells against. It is a floor, not a
ceiling: description excerpts are also handed to the brief so the model can flag tools
nobody listed here.

Precision matters more than recall. Several product names are ordinary English - "Linear"
(linear algebra), "Monday" (the weekday), "Notion" (an idea), "Height", "Rally", "Coda".
Those are marked ambiguous and require nearby tooling context plus a blocklist check, or
they are dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# category -> product -> (aliases, ...)
CATALOG: dict[str, dict[str, tuple[str, ...]]] = {
    "issue_tracking": {  # competes with Jira
        "Jira": ("jira", "jira software"),
        "Linear": ("linear",),
        "Asana": ("asana",),
        "ClickUp": ("clickup", "click up"),
        "Monday.com": ("monday.com", "monday com"),
        "Shortcut": ("shortcut", "clubhouse.io"),
        "Height": ("height.app", "height"),
        "Trello": ("trello",),
        "Basecamp": ("basecamp",),
        "Azure DevOps": ("azure devops", "azure boards", "vsts"),
        "YouTrack": ("youtrack",),
        "Pivotal Tracker": ("pivotal tracker",),
        "GitHub Projects": ("github projects", "github issues"),
        "GitLab Issues": ("gitlab issues",),
    },
    "knowledge_base": {  # competes with Confluence
        "Confluence": ("confluence",),
        "Notion": ("notion",),
        "Coda": ("coda",),
        "Slab": ("slab",),
        "Guru": ("getguru", "guru"),
        "GitBook": ("gitbook",),
        "Slite": ("slite",),
        "Nuclino": ("nuclino",),
        "Almanac": ("almanac",),
        "Document360": ("document360",),
    },
    "source_control": {  # competes with Bitbucket
        "Bitbucket": ("bitbucket",),
        "GitHub": ("github",),
        "GitLab": ("gitlab",),
        "Azure Repos": ("azure repos",),
        "Gerrit": ("gerrit",),
    },
    "ci_cd": {  # competes with Bamboo / Bitbucket Pipelines
        "Jenkins": ("jenkins",),
        "CircleCI": ("circleci", "circle ci"),
        "GitHub Actions": ("github actions",),
        "GitLab CI": ("gitlab ci", "gitlab-ci"),
        "Buildkite": ("buildkite",),
        "TeamCity": ("teamcity",),
        "Travis CI": ("travis ci", "travis-ci"),
        "Harness": ("harness.io",),
        "Drone CI": ("drone ci",),
        "ArgoCD": ("argocd", "argo cd"),
    },
    "ai_search": {  # competes with Rovo / the teamwork graph
        "Glean": ("glean.com", "glean"),
        "Dashworks": ("dashworks",),
        "Moveworks": ("moveworks",),
        "Onyx": ("onyx", "danswer"),
        "Unleash": ("unleash.so",),
        "Qatalog": ("qatalog",),
    },
    "service_desk": {  # competes with Jira Service Management
        "Zendesk": ("zendesk",),
        "ServiceNow": ("servicenow", "service now"),
        "Freshservice": ("freshservice", "freshdesk"),
        "HaloITSM": ("haloitsm", "halo itsm"),
        "Intercom": ("intercom",),
    },
    "incident": {  # competes with Opsgenie / Compass
        "PagerDuty": ("pagerduty", "pager duty"),
        "Opsgenie": ("opsgenie",),
        "incident.io": ("incident.io",),
        "FireHydrant": ("firehydrant",),
        "Rootly": ("rootly",),
        "Blameless": ("blameless",),
    },
    "whiteboard_discovery": {  # competes with Whiteboards / Jira Product Discovery
        "Miro": ("miro",),
        "FigJam": ("figjam",),
        "Mural": ("mural",),
        "Lucidchart": ("lucidchart", "lucid chart"),
        "Productboard": ("productboard", "product board"),
        "Aha!": ("aha!",),
        "Canny": ("canny.io",),
        "Dovetail": ("dovetail",),
    },
    "work_management": {
        "Smartsheet": ("smartsheet",),
        "Wrike": ("wrike",),
        "Airtable": ("airtable",),
        "Teamwork.com": ("teamwork.com",),
    },
}

# Products whose names are ordinary English. These need nearby tooling context and must
# not match a known false-friend phrase.
AMBIGUOUS = {
    "Linear",
    "Monday.com",
    "Height",
    "Notion",
    "Guru",
    "Coda",
    "Slab",
    "Mural",
    "Harness",
    "Shortcut",
    "Almanac",
    "Onyx",
    "Glean",
    "Canny",
    "Drone CI",
    "Aha!",
}

# Phrases where the product name is definitely not the product.
FALSE_FRIENDS = (
    "linear algebra",
    "linear regression",
    "linear model",
    "linear programming",
    "linear transformation",
    "linear combination",
    "linear layer",
    "linear scaling",
    "linear search",
    "linear time",
    "linear equations",
    "non-linear",
    "nonlinear",
    "linear interpolation",
    "linear system",
    "generalized linear",
    "monday through",
    "monday to",
    "monday-friday",
    "monday - friday",
    "every monday",
    "on monday",
    "monday morning",
    "monday standup",
    "monday stand-up",
    "notion of",
    "notion that",
    "the notion",
    "any notion",
    "no notion",
    "height of",
    "height and width",
    "full height",
    "line height",
    "max height",
    "harness the",
    "harness our",
    "harness this",
    "harness that",
    "harnessing",
    "shortcut to",
    "shortcuts",
    "keyboard shortcut",
    "no shortcut",
    "take shortcut",
    "coda to",
    "guru of",
    "a guru",
    "tech guru",
    "glean insights",
    "glean information",
    "glean from",
    "gleaning",
    "mural of",
    "slab of",
    "drone footage",
    "drone imagery",
)

# Words that make a nearby product name credible as tooling.
CONTEXT_TERMS = (
    "issue",
    "track",
    "ticket",
    "project management",
    "sprint",
    "backlog",
    "board",
    "roadmap",
    "workflow",
    "tool",
    "tooling",
    "stack",
    "wiki",
    "documentation",
    "docs",
    "knowledge base",
    "repo",
    "repository",
    "version control",
    "ci/cd",
    "pipeline",
    "deploy",
    "on-call",
    "oncall",
    "incident",
    "postmortem",
    "help desk",
    "helpdesk",
    "ticketing",
    "planning",
    "standup",
    "stand-up",
    "agile",
    "scrum",
    "kanban",
    "we use",
    "we run",
    "our team uses",
    "familiarity with",
    "experience with",
    "proficiency",
    "comfortable with",
    "collaborate",
    "manage",
    "product management",
)

# Phrasing that shows the company states this is what they use, rather than a passing
# mention. Used to grade evidence strength.
USAGE_PATTERNS = (
    r"\bwe use\b",
    r"\bwe're using\b",
    r"\bwe are using\b",
    r"\bour (?:team |eng |)stack\b",
    r"\bwe run\b",
    r"\bour tooling\b",
    r"\bwe work (?:in|with)\b",
    r"\btracking with\b",
    r"\bmanaged (?:in|with)\b",
    r"\bdocumented (?:in|with)\b",
    r"\bhosted (?:in|on)\b",
    r"\bexperience with\b",
    r"\bfamiliarity with\b",
    r"\bproficiency (?:in|with)\b",
    r"\bcomfortable with\b",
    r"\bknowledge of\b",
)

_WS = re.compile(r"\s+")
CONTEXT_WINDOW = 90


@dataclass(slots=True)
class ToolHit:
    product: str
    category: str
    evidence: str
    strength: str  # "stated" = usage/requirement phrasing nearby; "mentioned" otherwise
    sources: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "product": self.product,
            "category": self.category,
            "evidence": self.evidence,
            "strength": self.strength,
            "sources": self.sources,
        }


def _clean(text: str) -> str:
    return _WS.sub(" ", text).strip()


def _alias_pattern(alias: str) -> re.Pattern[str]:
    # Escape, but let a literal space match any whitespace run.
    escaped = re.escape(alias).replace(r"\ ", r"\s+")
    lead = r"(?<![\w.])" if alias[0].isalnum() else r"(?<!\w)"
    trail = r"(?![\w])" if alias[-1].isalnum() else ""
    return re.compile(lead + escaped + trail, re.I)


_COMPILED: dict[tuple[str, str], list[re.Pattern[str]]] = {
    (category, product): [_alias_pattern(a) for a in aliases]
    for category, products in CATALOG.items()
    for product, aliases in products.items()
}


def detect_tools(text: str, *, source: str = "") -> list[ToolHit]:
    """Find tooling named in free text, with the surrounding sentence as evidence."""
    if not text:
        return []
    lowered = text.lower()
    hits: dict[str, ToolHit] = {}

    for (category, product), patterns in _COMPILED.items():
        for pattern in patterns:
            for match in pattern.finditer(text):
                start = max(0, match.start() - CONTEXT_WINDOW)
                end = min(len(text), match.end() + CONTEXT_WINDOW)
                window = text[start:end]
                window_lower = window.lower()

                if product in AMBIGUOUS:
                    span_lo = max(0, match.start() - 30)
                    span_hi = min(len(text), match.end() + 30)
                    near = lowered[span_lo:span_hi]
                    if any(ff in near for ff in FALSE_FRIENDS):
                        continue
                    if not any(term in window_lower for term in CONTEXT_TERMS):
                        continue

                strength = (
                    "stated"
                    if any(re.search(p, window_lower) for p in USAGE_PATTERNS)
                    else "mentioned"
                )
                existing = hits.get(product)
                if existing is None or (existing.strength == "mentioned" and strength == "stated"):
                    hits[product] = ToolHit(
                        product=product,
                        category=category,
                        evidence=_clean(window),
                        strength=strength,
                        sources=[source] if source else [],
                    )
                elif source and source not in existing.sources:
                    existing.sources.append(source)
                break

    return sorted(hits.values(), key=lambda h: (h.category, h.product))


def merge_hits(groups: list[list[ToolHit]]) -> list[ToolHit]:
    """Combine hits across several documents, keeping the strongest evidence."""
    merged: dict[str, ToolHit] = {}
    for group in groups:
        for hit in group:
            existing = merged.get(hit.product)
            if existing is None:
                merged[hit.product] = ToolHit(
                    hit.product, hit.category, hit.evidence, hit.strength, list(hit.sources)
                )
            else:
                for s in hit.sources:
                    if s not in existing.sources:
                        existing.sources.append(s)
                if existing.strength == "mentioned" and hit.strength == "stated":
                    existing.strength = "stated"
                    existing.evidence = hit.evidence
    return sorted(merged.values(), key=lambda h: (h.category, h.product))


# Which detected products mean an incumbent Atlassian footprint.
ATLASSIAN_PRODUCTS = {
    "Jira",
    "Confluence",
    "Bitbucket",
    "Trello",
    "Opsgenie",
    "Statuspage",
    "Compass",
}


def split_atlassian(hits: list[ToolHit]) -> tuple[list[ToolHit], list[ToolHit]]:
    """Return (atlassian_hits, competitor_hits)."""
    ours = [h for h in hits if h.product in ATLASSIAN_PRODUCTS]
    theirs = [h for h in hits if h.product not in ATLASSIAN_PRODUCTS]
    return ours, theirs
