"""Shared shapes for applicant tracking systems.

These are the technographic goldmine. Every ATS here exposes a public JSON endpoint built
so third parties can render a company's job board - no key, no auth, no bot-blocking to
route around. Using them as intended gives full job descriptions at a scale YC cannot
touch: Stripe publishes 3 roles on YC and 666 on Greenhouse.
"""

from __future__ import annotations

import html as html_mod
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def clean_html(raw: str | None) -> str:
    """ATS descriptions are HTML fragments; the detector wants plain text."""
    if not raw:
        return ""
    return _WS.sub(" ", html_mod.unescape(_TAG.sub(" ", raw))).strip()


@dataclass(slots=True)
class Posting:
    title: str
    url: str | None = None
    location: str | None = None
    department: str | None = None
    updated_at: str | None = None
    description: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "location": self.location,
            "department": self.department,
            "updated_at": self.updated_at,
            "description_chars": len(self.description),
        }


class ATSUnavailable(Exception):
    """The board could not be read."""


class ATSProvider(Protocol):
    key: str
    label: str

    def board_url(self, token: str) -> str: ...

    async def fetch(self, client: httpx.AsyncClient, token: str) -> list[Posting]: ...
