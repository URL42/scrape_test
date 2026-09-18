"""LLM-generated "so what" brief: interpretation, recommended action, and a draft email.

Deliberately on-demand rather than part of every lookup. A brief costs real money on each
generation, and most searches are a glance - firing this automatically would bill for
companies nobody is working. Results are cached against a hash of their own inputs, so
re-opening a brief is free and only an explicit regenerate spends again.

The email this produces is a DRAFT for a human to review and send. Nothing here sends mail.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

from .scoring.rules import WEIGHTS

log = logging.getLogger(__name__)

MODEL = os.environ.get("SCRAPE_TEST_MODEL", "claude-opus-5")
MAX_TOKENS = int(os.environ.get("SCRAPE_TEST_MAX_TOKENS", "8000"))


class BriefUnavailable(Exception):
    """The brief could not be generated (no credentials, API error, or refusal)."""


class Brief(BaseModel):
    """Structured output. Using a schema rather than prose means the UI renders fields
    instead of trying to regex sections out of a paragraph."""

    headline: str = Field(description="The one-line 'so what'. Plain, specific, no hype.")
    news_summary: str = Field(
        description=(
            "2-4 sentences distilling the current news for a seller who has not read it. "
            "What happened, and why it matters commercially. Say so explicitly if there "
            "is no meaningful coverage."
        )
    )
    interpretation: str = Field(
        description="What the combined data actually indicates about this company right now."
    )
    priority: Literal["pursue now", "nurture", "deprioritize"]
    recommended_action: str = Field(description="The single concrete next step, and why that one.")
    news_hook: str | None = Field(
        default=None,
        description=(
            "The specific news item the outreach should lean on, or null when no article "
            "is a genuine hook. Never stretch an unrelated story into a hook."
        ),
    )
    talking_points: list[str] = Field(
        description="3-5 points, each traceable to a specific supplied fact."
    )
    risks: list[str] = Field(description="Reasons this could be a poor fit or a hard sell.")
    evidence_gaps: list[str] = Field(
        description=(
            "What the data does NOT establish. Website fingerprinting sees only the public "
            "surface, and companies not hiring engineers expose no stack at all."
        )
    )
    email_subject: str
    email_body: str = Field(
        description=(
            "4-6 sentences. One specific hook (the news item where there is one), one line "
            "of relevance drawn from their stack or hiring, one soft ask. No fabricated "
            "mutual connections, no invented metrics, no pricing claims."
        )
    )


SYSTEM_PROMPT = """\
You brief an Atlassian account executive before they reach out to a prospect. Their focus \
is Jira, Confluence, and Rovo / the teamwork graph.

You are given real scraped data about a Y Combinator company: directory facts, open job \
postings with a curated skills array, publicly detectable tooling on their website, a \
heuristic Atlassian-fit score with its per-signal breakdown, and recent news headlines.

Rules you must follow:

1. Ground every claim in the supplied data. Do not introduce facts about this company \
from memory or assumption. If you did not receive it, you do not know it.
2. Do not invent Atlassian product capabilities, features, pricing, or customer counts. \
Speak to outcomes the data supports - coordination overhead as a team scales, scattered \
context across tools, finding work across systems. Keep product references general.
3. Website fingerprinting sees the public surface only. "No Atlassian detected" means not \
publicly visible, NOT that they do not use it. Never assert they are not a customer.
4. When the score is low confidence or the inputs are thin, say so in evidence_gaps and \
write a correspondingly tentative email. A confident email on weak evidence is worse than \
no email.
5. The score is a heuristic with hand-set weights, not a validated model. Treat it as one \
input, and disagree with it in your interpretation when the underlying facts warrant.
6. Use the news where it is genuinely relevant - a funding round, a launch, a leadership \
hire, an expansion. If no article is a real hook, set news_hook to null and write the \
email without one. A forced news reference reads worse than none.
7. The email is a draft a human will edit. Write plainly, no exclamation marks, no "I hope \
this finds you well", no invented mutual connections or fake urgency.
"""


def _fmt_jobs(jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return "No open roles listed on YC."
    lines = []
    for j in jobs:
        skills = ", ".join(j.get("skills") or []) or "none listed"
        lines.append(
            f"- [{j.get('pretty_role') or '?'}] {j.get('title') or '?'}\n"
            f"    skills: {skills}\n"
            f"    salary: {j.get('salary_range') or 'n/a'}"
            f" | equity: {j.get('equity_range') or 'n/a'}"
            f" | min exp: {j.get('min_experience') or 'n/a'}\n"
            f"    location: {j.get('location') or 'n/a'}"
            f" | last active: {j.get('last_active_rel') or 'unknown'}"
        )
    return "\n".join(lines)


def _fmt_fingerprint(fp: dict[str, Any]) -> str:
    detected = (fp or {}).get("detected") or {}
    if not detected:
        return f"Nothing detected publicly ({fp.get('error') or 'no signals'})."
    lines = []
    for category, items in sorted(detected.items()):
        rendered = ", ".join(f"{i['product']} ({i.get('confidence', '?')} evidence)" for i in items)
        lines.append(f"- {category.replace('_', ' ')}: {rendered}")
    return "\n".join(lines)


def _fmt_score(score: dict[str, Any]) -> str:
    lines = [
        f"Heuristic Atlassian-fit score: {score.get('total')}/100 "
        f"(confidence: {score.get('confidence')}, rules {score.get('rules_version')})",
        "Signal breakdown (weights are hand-set, not validated):",
    ]
    for s in score.get("signals", []):
        lines.append(
            f"  {s['points']:+.1f} of max {WEIGHTS.get(s['key'], 0):+.0f}  "
            f"{s['label']}: {s['reason']}"
        )
    return "\n".join(lines)


def _fmt_news(articles: list[dict[str, Any]], limit: int = 12) -> str:
    if not articles:
        return "No recent coverage found for this company."
    return "\n".join(
        f"- {a.get('title')} ({a.get('source')}, {a.get('published') or 'undated'})"
        for a in articles[:limit]
    )


def build_payload(
    company: dict[str, Any],
    jobs: list[dict[str, Any]],
    stack: list[dict[str, Any]],
    fingerprint: dict[str, Any],
    score: dict[str, Any],
    articles: list[dict[str, Any]],
) -> str:
    """Assemble the grounded context. Kept pure so it can be tested without an API call."""
    stack_line = (
        ", ".join(f"{s['skill']} (x{s['mentions']})" for s in stack)
        if stack
        else "No stack data - YC lists skills only on engineering postings."
    )
    return f"""\
## Company (YC directory)
Name: {company.get("name")}
Batch: {company.get("batch")} | Status: {company.get("status")}
Team size: {company.get("team_size")}
Industry: {company.get("industry")} / {company.get("subindustry")}
Tags: {", ".join(company.get("tags") or []) or "none"}
Website: {company.get("website")}
One-liner: {company.get("one_liner")}
Description: {(company.get("long_description") or "")[:1200]}

## Open roles ({len(jobs)})
{_fmt_jobs(jobs)}

## Aggregated tech stack (from job postings)
{stack_line}

## Publicly detected tooling (public surface only)
{_fmt_fingerprint(fingerprint)}

## {_fmt_score(score)}

## Recent news
{_fmt_news(articles)}
"""


def payload_hash(payload: str) -> str:
    """Cache key. Hashing the assembled payload means any change to the underlying data,
    or to the model, produces a new brief - and nothing else does."""
    return hashlib.sha256(f"{MODEL}\n{payload}".encode()).hexdigest()


def credentials_available() -> bool:
    """Best-effort check so the UI can disable the button instead of failing on click.

    The SDK resolves credentials from several places, so this errs toward saying yes and
    letting the API surface a real error rather than blocking a working setup.
    """
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    from pathlib import Path

    return (Path.home() / ".config" / "anthropic").exists()


async def generate_brief(payload: str, *, client: Any = None) -> Brief:
    """Call Claude and return a validated Brief.

    `client` is injectable so tests can exercise prompt assembly and parsing without
    spending money or needing credentials.
    """
    import anthropic

    if client is None:
        if not credentials_available():
            raise BriefUnavailable(
                "No Anthropic credentials found. Set ANTHROPIC_API_KEY in the environment "
                "(or run `ant auth login`) and restart the server."
            )
        client = anthropic.AsyncAnthropic()

    try:
        response = await client.messages.parse(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": payload}],
            output_format=Brief,
        )
    except anthropic.AuthenticationError as exc:
        raise BriefUnavailable(f"Anthropic rejected the credentials: {exc}") from exc
    except anthropic.RateLimitError as exc:
        raise BriefUnavailable(f"Rate limited by the Anthropic API: {exc}") from exc
    except anthropic.APIStatusError as exc:
        raise BriefUnavailable(f"Anthropic API error {exc.status_code}: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise BriefUnavailable(f"Could not reach the Anthropic API: {exc}") from exc

    if getattr(response, "stop_reason", None) == "refusal":
        raise BriefUnavailable("The model declined to generate this brief.")

    parsed = getattr(response, "parsed_output", None)
    if parsed is None:
        raise BriefUnavailable("The model returned no parseable structured output.")
    return parsed


# --------------------------------------------------------------------------------------
# Storage
# --------------------------------------------------------------------------------------


def store_brief(conn: sqlite3.Connection, company_id: int, input_hash: str, brief: Brief) -> None:
    conn.execute(
        """INSERT INTO briefs (company_id, input_hash, model, payload, created_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(company_id) DO UPDATE SET
               input_hash=excluded.input_hash, model=excluded.model,
               payload=excluded.payload, created_at=excluded.created_at""",
        (company_id, input_hash, MODEL, brief.model_dump_json(), time.time()),
    )
    conn.commit()


def load_brief(conn: sqlite3.Connection, company_id: int, input_hash: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM briefs WHERE company_id = ? AND input_hash = ?",
        (company_id, input_hash),
    ).fetchone()
    if not row:
        return None
    return {
        "brief": json.loads(row["payload"]),
        "model": row["model"],
        "created_at": row["created_at"],
        "cached": True,
    }
