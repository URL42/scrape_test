"""Classify a feed post and pull the company out of it.

The point of the digest is not to read the news - it is to turn an investor's
announcement into a lead. "Our seed investment in Atira, the AI orchestration layer" names
a company that was funded days ago, which is the strongest timing signal this tool can
get. So every post is tagged, and funding posts are mined for the company name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

FUNDING = re.compile(
    r"\b(raises?|raised|raising|seed round|series\s+[a-f]\b|pre-seed|funding round|"
    r"closes?\s+\$|secures?\s+\$|led (?:the|our)\s+round|our investment in|"
    r"investment in|invested in|investing in|partnering with|welcoming|"
    r"we(?:'re| are) backing|backing|led .{0,20}round|co-led|"
    r"announcing our|introducing|joins? our portfolio|new investment)\b",
    re.I,
)
MONEY = re.compile(r"[$€£]\s?\d+(?:\.\d+)?\s?(?:m|bn|b|k|million|billion)?\b", re.I)
ROUND = re.compile(r"\b(pre-seed|seed|series\s+[a-f]|growth|bridge)\b", re.I)

AI_NATIVE = re.compile(
    r"\b(ai-native|ai native|ai-first|ai first|genai|generative ai|llm|llms|"
    r"foundation model|agentic|ai agent|ai agents|autonomous agent|multimodal|"
    r"machine learning|deep learning|neural|\bai\b)\b",
    re.I,
)
AI_SDLC = re.compile(
    r"\b(ai (?:for |in )?(?:software|coding|code|dev|development|engineering)|"
    r"code generation|codegen|copilot|ai pair|developer productivity|devtool|dev tool|"
    r"software engineering agent|coding agent|ai sdlc|autonomous software|"
    r"code review|ci/cd|devops|platform engineering)\b",
    re.I,
)
MA = re.compile(r"\b(acquires?|acquisition|acquired|majority stake|merger)\b", re.I)
LAUNCH = re.compile(
    r"\b(launch(?:es|ing|ed)?|introducing|unveil(?:s|ing)?|now available)\b", re.I
)
HIRING_LEADER = re.compile(
    r"\b(joins as|appoints?|names?\s+\w+\s+as|new (?:cto|cpo|ceo|vp of|head of))\b", re.I
)

# A company name: one to three capitalised words, allowing dots and ampersands.
_NAME = r"([A-Z][\w.&'-]*(?:\s+[A-Z][\w.&'-]*){0,2})"

# How a VC announcement names the company. The prefix is matched case-insensitively with
# a scoped flag, while the name capture stays case-sensitive - capitalisation is precisely
# what distinguishes "Atira" from the words around it.
COMPANY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?i:\b(?:our|the)\s+(?:seed\s+|series\s+[a-f]\s+|pre-seed\s+)?"
        r"invest(?:ment|ed) in\s+)" + _NAME
    ),
    re.compile(r"(?i:\bwhy we invested in\s+)" + _NAME),
    re.compile(r"(?i:\binvesting in\s+)" + _NAME),
    re.compile(r"(?i:\bpartnering with\s+)" + _NAME),
    re.compile(r"(?i:\bintroducing\s+)" + _NAME),
    re.compile(r"(?i:\bwelcoming\s+)" + _NAME),
    re.compile(r"(?i:\bbacking\s+)" + _NAME),
    re.compile(r"(?i:\bacquires?\s+(?:a\s+)?(?:majority\s+stake\s+in\s+)?)" + _NAME),
    re.compile(r"^" + _NAME + r"(?i:\s+(?:raises|raised|secures|closes|lands|nabs|banks)\b)"),
)

# Words that look like a company name but are not one.
NOT_A_COMPANY = {
    "the", "our", "we", "a", "an", "new", "this", "that", "why", "how", "what",
    "introducing", "announcing", "partnering", "welcoming", "backing", "today",
    "ai", "the ai", "series", "seed", "europe", "us", "uk",
}


@dataclass(slots=True)
class Classified:
    tags: list[str] = field(default_factory=list)
    company: str | None = None
    amount: str | None = None
    round_stage: str | None = None

    @property
    def is_funding(self) -> bool:
        return "funding" in self.tags

    def as_dict(self) -> dict[str, Any]:
        return {
            "tags": self.tags, "company": self.company,
            "amount": self.amount, "round_stage": self.round_stage,
        }


def extract_company(title: str) -> str | None:
    for pattern in COMPANY_PATTERNS:
        m = pattern.search(title)
        if not m:
            continue
        name = m.group(1).strip(" .,:;-–—'\"")
        # Trim a trailing descriptor: "Atira, the AI orchestration layer" -> "Atira"
        name = re.split(r"\s*[,:]\s*", name)[0].strip()
        # Drop a leading possessive: "Germany's Sustainabill" -> "Sustainabill"
        name = re.sub(r"^\S+['\u2019]s\s+", "", name).strip()
        if not name or name.lower() in NOT_A_COMPANY or len(name) < 2:
            continue
        if len(name) > 40:
            continue
        return name
    return None


def classify(title: str, summary: str = "") -> Classified:
    blob = f"{title} {summary}"
    tags: list[str] = []
    if FUNDING.search(blob) or MONEY.search(title):
        tags.append("funding")
    if MA.search(blob):
        tags.append("m&a")
    if AI_NATIVE.search(blob):
        tags.append("ai-native")
    if AI_SDLC.search(blob):
        tags.append("ai-sdlc")
    if LAUNCH.search(title):
        tags.append("launch")
    if HIRING_LEADER.search(blob):
        tags.append("leadership")

    amount = None
    m = MONEY.search(blob)
    if m:
        amount = m.group(0).strip()
    stage = None
    r = ROUND.search(blob)
    if r:
        stage = r.group(1).title()

    company = (
        extract_company(title) if {"funding", "m&a"} & set(tags) else None
    )
    return Classified(tags=tags, company=company, amount=amount, round_stage=stage)


def relevance(c: Classified) -> int:
    """Rough ordering for a sales reader: funded AI companies first."""
    score = 0
    if c.is_funding:
        score += 40
        if c.company:
            score += 25          # a named company is actionable; a think-piece is not
    if "ai-native" in c.tags:
        score += 20
    if "ai-sdlc" in c.tags:
        score += 15
    if "m&a" in c.tags:
        score += 10
    if "leadership" in c.tags:
        score += 10
    if "launch" in c.tags:
        score += 5
    return score
