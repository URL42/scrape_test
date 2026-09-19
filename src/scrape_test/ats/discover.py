"""Find which ATS a company uses, and its board token, starting from a domain.

Companies embed their job board on a careers page, so the token is usually sitting in the
HTML: Vanta's careers page contains api.ashbyhq.com/posting-api/job-board/vanta, and
Rollstack's contains jobs.ashbyhq.com/rollstack. Failing that, most boards are named after
the company, so the domain label is a decent guess worth one probe per provider.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx

from ..http import FetchError, fetch
from .ashby import AshbyProvider
from .base import ATSProvider, ATSUnavailable, Posting
from .greenhouse import GreenhouseProvider
from .lever import LeverProvider

log = logging.getLogger(__name__)

PROVIDERS: dict[str, ATSProvider] = {
    p.key: p for p in (GreenhouseProvider(), AshbyProvider(), LeverProvider())
}

CAREERS_PATHS = (
    "/careers", "/jobs", "/careers/", "/company/careers", "/about/careers",
    "/join-us", "/work-with-us", "/company/jobs",
)
MAX_CAREERS_PROBES = 4
MAX_BODY = 2_000_000

# Board tokens as they appear embedded in careers pages.
TOKEN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]+)", re.I)),
    (
        "greenhouse",
        re.compile(
            r"(?:job-)?boards\.greenhouse\.io/(?:embed/job_board\?for=)?([a-z0-9_-]+)", re.I
        ),
    ),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"api\.lever\.co/v0/postings/([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.lever\.co/([a-z0-9_-]+)", re.I)),
)

# Tokens that are the platform's own marketing, not a customer board.
TOKEN_BLOCKLIST = {"embed", "js", "v1", "boards", "www", "api", "static", "assets", "job_board"}


@dataclass(slots=True)
class Board:
    provider: str
    token: str
    found_via: str

    @property
    def url(self) -> str:
        return PROVIDERS[self.provider].board_url(self.token)


def extract_tokens(html: str) -> list[tuple[str, str]]:
    """Every (provider, token) pair embedded in a page, most specific pattern first."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for provider, pattern in TOKEN_PATTERNS:
        for match in pattern.finditer(html):
            token = match.group(1).lower()
            if token in TOKEN_BLOCKLIST or len(token) < 2:
                continue
            key = (provider, token)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def domain_label(website: str) -> str:
    """acme-corp.io -> acmecorp; a reasonable guess at an unlisted board token."""
    host = urlsplit(website if "//" in website else f"https://{website}").netloc
    host = host.split(":")[0].removeprefix("www.")
    label = host.split(".")[0] if host else ""
    return re.sub(r"[^a-z0-9]+", "", label.lower())


async def _get(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        resp = await fetch(client, url, retries=1, max_bytes=MAX_BODY)
    except FetchError:
        return None
    return resp.text if resp.ok and resp.text else None


async def discover_board(
    client: httpx.AsyncClient, website: str, *, homepage_html: str | None = None
) -> Board | None:
    """Find the company's job board. Reads the homepage and a few careers paths."""
    base = website if "//" in website else f"https://{website}"

    pages: list[tuple[str, str]] = []
    html = homepage_html or await _get(client, base)
    if html:
        pages.append(("homepage", html))
    for path in CAREERS_PATHS[:MAX_CAREERS_PROBES]:
        text = await _get(client, urljoin(base, path))
        if text:
            pages.append((path, text))
            if extract_tokens(text):
                break

    for where, text in pages:
        for provider, token in extract_tokens(text):
            return Board(provider=provider, token=token, found_via=f"{where} link")

    # Nothing embedded - try the domain label against each provider. One probe each.
    guess = domain_label(base)
    if guess:
        for key, impl in PROVIDERS.items():
            try:
                postings = await impl.fetch(client, guess)
            except ATSUnavailable:
                continue
            if postings:
                return Board(provider=key, token=guess, found_via="guessed from domain")
    return None


async def fetch_board(client: httpx.AsyncClient, board: Board) -> list[Posting]:
    return await PROVIDERS[board.provider].fetch(client, board.token)
