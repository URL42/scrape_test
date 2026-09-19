"""Lever postings API. Public JSON feed behind their hosted boards."""

from __future__ import annotations

import json

import httpx

from ..http import FetchError, fetch
from .base import ATSUnavailable, Posting, clean_html

ENDPOINT = "https://api.lever.co/v0/postings/{token}?mode=json"


class LeverProvider:
    key = "lever"
    label = "Lever"

    def board_url(self, token: str) -> str:
        return f"https://jobs.lever.co/{token}"

    async def fetch(self, client: httpx.AsyncClient, token: str) -> list[Posting]:
        try:
            resp = await fetch(client, ENDPOINT.format(token=token), retries=2)
        except FetchError as exc:
            raise ATSUnavailable(f"lever/{token}: {exc}") from exc
        if resp.status_code == 404:
            raise ATSUnavailable(f"lever/{token}: no such board")
        if not resp.ok:
            raise ATSUnavailable(f"lever/{token}: HTTP {resp.status_code}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise ATSUnavailable(f"lever/{token}: bad JSON") from exc
        if not isinstance(payload, list):
            raise ATSUnavailable(f"lever/{token}: unexpected payload")

        out = []
        for job in payload:
            categories = job.get("categories") or {}
            body = " ".join(
                filter(
                    None,
                    [
                        job.get("descriptionPlain") or job.get("description"),
                        " ".join(
                            (s.get("text") or "") + " " + (s.get("content") or "")
                            for s in (job.get("lists") or [])
                        ),
                        job.get("additionalPlain") or job.get("additional") or "",
                    ],
                )
            )
            out.append(
                Posting(
                    title=(job.get("text") or "").strip(),
                    url=job.get("hostedUrl") or job.get("applyUrl"),
                    location=categories.get("location"),
                    department=categories.get("team") or categories.get("department"),
                    updated_at=str(job.get("createdAt") or ""),
                    description=clean_html(body),
                )
            )
        return out
