"""Routes for the what's-new digest: VC and tech-press announcements."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

import scrape_test.routes.deps as deps

from ..db import session
from ..whatsnew import KNOWN_GAPS, build_digest, load_digest, recently_funded, store_digest
from .deps import client, running

router = APIRouter()


@router.post("/api/digest/refresh")
async def refresh_digest(
    kinds: str = Query("vc,press"),
    regions: str = Query(""),
) -> dict[str, Any]:
    """Pull every curated VC and press feed, classify, store. Takes ~15 seconds."""
    if running(deps.digest_task):
        raise HTTPException(409, "A digest refresh is already running.")

    kind_tuple = tuple(k.strip() for k in kinds.split(",") if k.strip())
    region_tuple = tuple(r.strip() for r in regions.split(",") if r.strip())

    async def runner() -> int:
        items = await build_digest(client(), kinds=kind_tuple, regions=region_tuple)
        with session() as conn:
            return store_digest(conn, items)

    deps.digest_task = asyncio.create_task(runner())
    return {"started": True, "kinds": kind_tuple, "regions": region_tuple}


@router.get("/api/digest")
async def digest(
    tag: str = Query(""),
    funded_only: bool = Query(False),
    limit: int = Query(120, ge=1, le=500),
) -> dict[str, Any]:
    is_running = running(deps.digest_task)
    with session() as conn:
        items = load_digest(conn, tag=tag or None, funded_only=funded_only, limit=limit)
        funded = recently_funded(conn)
        counts = {
            r["source_kind"]: r["n"]
            for r in conn.execute(
                "SELECT source_kind, COUNT(*) AS n FROM digest_items GROUP BY source_kind"
            )
        }
    return {
        "items": items, "funded_companies": funded, "counts": counts,
        "running": is_running, "known_gaps": list(KNOWN_GAPS),
    }
