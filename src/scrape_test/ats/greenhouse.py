"""Greenhouse job board API. Public and documented for embedding."""

from __future__ import annotations

import json

import httpx

from ..http import FetchError, fetch
from .base import ATSUnavailable, Posting, clean_html

ENDPOINT = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


class GreenhouseProvider:
    key = "greenhouse"
    label = "Greenhouse"

    def board_url(self, token: str) -> str:
        return f"https://boards.greenhouse.io/{token}"

    async def fetch(self, client: httpx.AsyncClient, token: str) -> list[Posting]:
        try:
            # Boards can be large - Stripe's is ~5MB - so no body cap here.
            resp = await fetch(client, ENDPOINT.format(token=token), retries=2)
        except FetchError as exc:
            raise ATSUnavailable(f"greenhouse/{token}: {exc}") from exc
        if resp.status_code == 404:
            raise ATSUnavailable(f"greenhouse/{token}: no such board")
        if not resp.ok:
            raise ATSUnavailable(f"greenhouse/{token}: HTTP {resp.status_code}")
        try:
            payload = json.loads(resp.text)
        except json.JSONDecodeError as exc:
            raise ATSUnavailable(f"greenhouse/{token}: bad JSON") from exc

        out = []
        for job in payload.get("jobs", []):
            offices = job.get("offices") or []
            out.append(
                Posting(
                    title=(job.get("title") or "").strip(),
                    url=job.get("absolute_url"),
                    location=(job.get("location") or {}).get("name"),
                    department=(
                        (job.get("departments") or [{}])[0].get("name")
                        if job.get("departments")
                        else None
                    ),
                    updated_at=job.get("updated_at"),
                    description=clean_html(job.get("content")),
                    extra={"offices": [o.get("name") for o in offices if o.get("name")]},
                )
            )
        return out
