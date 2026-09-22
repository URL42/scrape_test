"""Routes for the idea and investor search modes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from ..db import session
from ..search import ensure_index, idea_search, investor_search, list_investors

router = APIRouter()


@router.get("/api/search/idea")
async def search_idea(
    q: str = Query(..., min_length=2),
    limit: int = Query(40, ge=1, le=200),
) -> dict[str, Any]:
    """Companies matching a concept, ranked by BM25 over their own descriptions."""
    with session() as conn:
        results = idea_search(conn, q, limit=limit)
    return {"query": q, "results": results, "count": len(results)}


@router.get("/api/search/investors")
async def search_investors() -> dict[str, Any]:
    with session() as conn:
        return {"investors": list_investors(conn)}


@router.get("/api/search/investor")
async def search_investor(
    name: str = Query(..., min_length=2),
    limit: int = Query(60, ge=1, le=200),
) -> dict[str, Any]:
    """One fund's announcements, and the companies they recently backed."""
    with session() as conn:
        return investor_search(conn, name, limit=limit)


@router.post("/api/search/reindex")
async def search_reindex() -> dict[str, Any]:
    with session() as conn:
        return {"indexed": ensure_index(conn, rebuild=True)}
