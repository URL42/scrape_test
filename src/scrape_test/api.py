"""FastAPI app: one lookup endpoint that fans out to news and YC in parallel."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR
from .db import get_meta, init_db, session
from .http import make_client
from .news import SOURCES, get_source
from .scoring import RULES_VERSION, WEIGHTS, compute_score
from .scoring.score import rescore_all, store_score
from .yc.directory import company_dict, refresh_directory, resolve
from .yc.fingerprint import get_fingerprint
from .yc.jobs import get_jobs, stack_from_jobs

log = logging.getLogger(__name__)
_client: httpx.AsyncClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _client
    init_db()
    _client = make_client()
    # Make sure the directory is populated before the first lookup; it is one request.
    with session() as conn:
        try:
            await refresh_directory(conn, _client)
        except Exception as exc:  # noqa: BLE001 - startup should not hard-fail offline
            log.warning("directory refresh failed at startup: %s", exc)
    yield
    await _client.aclose()


app = FastAPI(title="scrape-test", lifespan=lifespan)


def client() -> httpx.AsyncClient:
    if _client is None:
        raise HTTPException(503, "HTTP client not ready")
    return _client


async def _news_block(company: str, context: str, source_key: str, limit: int) -> dict[str, Any]:
    try:
        source = get_source(source_key)
    except KeyError as exc:
        return {"source": source_key, "error": str(exc), "articles": []}
    try:
        articles = await source.search(client(), company, context, limit=limit)
        return {
            "source": source.key,
            "label": source.label,
            "note": source.note,
            "query": source.build_query(company, context),
            "articles": [a.as_dict() for a in articles],
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001 - one source failing must not kill the lookup
        log.warning("news source %s failed: %s", source_key, exc)
        return {"source": source_key, "label": source.label, "articles": [], "error": str(exc)}


async def _yc_block(company: str, refresh: bool) -> dict[str, Any]:
    with session() as conn:
        row, suggestions = resolve(conn, company)
        if row is None:
            return {
                "found": False,
                "suggestions": suggestions,
                "message": f"{company!r} is not in the YC directory - showing news only.",
            }
        c = company_dict(row)
        try:
            jobs, jobs_cached = await get_jobs(conn, client(), c["id"], c["slug"], force=refresh)
        except Exception as exc:  # noqa: BLE001
            log.warning("jobs fetch failed for %s: %s", c["slug"], exc)
            jobs, jobs_cached = [], False
        fp, fp_cached = await get_fingerprint(conn, client(), c["id"], c["website"], force=refresh)
        score = compute_score(c, jobs, fp)
        store_score(conn, c["id"], score)

        return {
            "found": True,
            "company": c,
            "yc_url": f"https://www.ycombinator.com/companies/{c['slug']}",
            "jobs": jobs,
            "stack": stack_from_jobs(jobs),
            "fingerprint": fp,
            "score": score.as_dict(),
            "cached": {"jobs": jobs_cached, "fingerprint": fp_cached},
        }


@app.get("/api/sources")
async def sources() -> dict[str, Any]:
    with session() as conn:
        count = get_meta(conn, "directory_count", 0)
        fetched = get_meta(conn, "directory_fetched_at")
    return {
        "news_sources": [
            {"key": s.key, "label": s.label, "note": s.note} for s in SOURCES.values()
        ],
        "directory": {"companies": count, "fetched_at": fetched},
        "rules_version": RULES_VERSION,
        "weights": WEIGHTS,
    }


@app.get("/api/lookup")
async def lookup(
    company: str = Query(..., min_length=1),
    context: str = Query(""),
    source: str = Query("google_news"),
    limit: int = Query(20, ge=1, le=100),
    refresh: bool = Query(False),
) -> JSONResponse:
    """One company in, news + YC intelligence out. Both halves run concurrently."""
    # Both halves share the one event-loop-bound httpx client, so they run as concurrent
    # tasks rather than in threads - a second event loop would not be able to use it.
    # The sqlite calls inside the YC half are local-file and short enough to inline.
    news, yc = await asyncio.gather(
        _news_block(company, context, source, limit),
        _yc_block(company, refresh),
    )
    return JSONResponse({"query": {"company": company, "context": context}, "news": news, "yc": yc})


@app.post("/api/rescore")
def rescore() -> dict[str, Any]:
    """Recompute every cached score with the current weights. Never touches the network.

    Deliberately a sync `def`: FastAPI runs those on the threadpool, whereas an `async def`
    full of blocking sqlite would freeze every other request for the length of the sweep.
    """
    with session() as conn:
        updated = rescore_all(conn)
    return {"rescored": updated, "rules_version": RULES_VERSION}


class NoCacheStatic(StaticFiles):
    """Serve the front end without browser caching.

    This is a development tool whose HTML/CSS/JS get edited constantly; the default
    caching means an edit silently does not show up until a hard reload, which is a
    genuinely confusing way to lose ten minutes.
    """

    def is_not_modified(self, *args: Any, **kwargs: Any) -> bool:
        return False

    async def get_response(self, path: str, scope: Any) -> Any:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store, must-revalidate"
        return response


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(
        WEB_DIR / "index.html", headers={"Cache-Control": "no-store, must-revalidate"}
    )


if WEB_DIR.exists():
    app.mount("/static", NoCacheStatic(directory=WEB_DIR), name="static")
