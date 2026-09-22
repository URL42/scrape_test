"""Routes for the prospect scan and the ranked list it produces."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException, Query

import scrape_test.routes.deps as deps

from ..db import session
from ..prospects import latest_run, load_prospects, run_scan
from ..prospects.icp import PROSPECT_WEIGHTS
from ..prospects.scan import due_for_rescan
from .deps import client, running

router = APIRouter()


@router.post("/api/scan")
async def start_scan(
    hn_threads: int = Query(3, ge=0, le=12),
    use_yc: bool = Query(True),
) -> dict[str, Any]:
    """Kick off a prospect sweep in the background.

    Minutes of polite HTTP, so it runs as a task and the UI polls /api/scan/status.
    """
    if running(deps.scan_task):
        raise HTTPException(409, "A scan is already running.")

    async def runner() -> int:
        return await run_scan(client(), use_yc=use_yc, hn_threads=hn_threads)

    deps.scan_task = asyncio.create_task(runner())
    return {"started": True, "use_yc": use_yc, "hn_threads": hn_threads}


@router.get("/api/scan/status")
async def scan_status() -> dict[str, Any]:
    with session() as conn:
        run = latest_run(conn)
    is_running = running(deps.scan_task)
    failed = None
    if deps.scan_task is not None and deps.scan_task.done():
        exc = deps.scan_task.exception()
        failed = str(exc)[:300] if exc else None
    return {"run": run, "running": is_running, "error": failed}


@router.get("/api/prospects")
async def prospects(
    only_prospects: bool = Query(True),
    verdict: str = Query(""),
    limit: int = Query(300, ge=1, le=2000),
) -> dict[str, Any]:
    with session() as conn:
        rows = load_prospects(
            conn, only_prospects=only_prospects, verdict=verdict or None, limit=limit
        )
        due = due_for_rescan(conn)
        counts = {
            r["verdict"]: r["n"]
            for r in conn.execute(
                "SELECT verdict, COUNT(*) AS n FROM prospects GROUP BY verdict"
            )
        }
    return {
        "prospects": rows, "counts": counts, "weights": PROSPECT_WEIGHTS,
        "due_for_rescan": len(due),
    }
