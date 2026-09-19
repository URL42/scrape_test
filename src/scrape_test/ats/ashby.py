"""Ashby job board API. Public posting API used by their own embeds."""

from __future__ import annotations

import json

import httpx

from ..http import FetchError, fetch
from .base import ATSUnavailable, Posting, clean_html

ENDPOINT = "https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true"


class AshbyProvider:
    key = "ashby"
    label = "Ashby"

    def board_url(self, token: str) -> str:
        return f"https://jobs.ashbyhq.com/{token}"

    async def fetch(self, client: httpx.AsyncClient, token: str) -> list[Posting]:
        try:
            resp = await fetch(client, ENDPOINT.format(token=token), retries=2)
        except FetchError as exc:
            raise ATSUnavailable(f"ashby/{token}: {exc}") from exc
        if resp.status_code == 404:
            raise ATSUnavailable(f"ashby/{token}: no such board")
        if not resp.ok:
            raise ATSUnavailable(f"ashby/{token}: HTTP {resp.status_code}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise ATSUnavailable(f"ashby/{token}: bad JSON") from exc

        out = []
        for job in payload.get("jobs", []):
            body = job.get("descriptionHtml") or job.get("descriptionPlain") or ""
            out.append(
                Posting(
                    title=(job.get("title") or "").strip(),
                    url=job.get("jobUrl") or job.get("applyUrl"),
                    location=job.get("location"),
                    department=job.get("department") or job.get("team"),
                    updated_at=job.get("publishedAt") or job.get("updatedAt"),
                    description=clean_html(body),
                    extra={
                        k: job[k]
                        for k in ("employmentType", "isRemote", "compensation")
                        if job.get(k) is not None
                    },
                )
            )
        return out
