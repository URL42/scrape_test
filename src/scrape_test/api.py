"""FastAPI app: one lookup endpoint that fans out to news and YC in parallel."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .ats import ATSUnavailable, discover_board, fetch_board
from .brief import (
    BriefUnavailable,
    active_provider,
    build_payload,
    credentials_available,
    generate_brief,
    load_brief,
    payload_hash,
    store_brief,
)
from .config import WEB_DIR
from .db import get_meta, init_db, session
from .http import make_client
from .news import SOURCES, get_source
from .scoring import RULES_VERSION, WEIGHTS, compute_score
from .scoring.score import rescore_all, store_score
from .yc.directory import company_dict, refresh_directory, resolve
from .yc.fingerprint import get_fingerprint, load_fingerprint
from .yc.jobs import get_jobs, load_jobs, stack_from_jobs, tools_from_jobs
from .yc.site_news import get_company_posts, load_posts
from .yc.tooling import detect_tools, merge_hits, split_atlassian

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
            jobs, jobs_cached, yc_items = await get_jobs(
                conn, client(), c["id"], c["slug"], force=refresh
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("jobs fetch failed for %s: %s", c["slug"], exc)
            jobs, jobs_cached, yc_items = [], False, []
        fp, fp_cached = await get_fingerprint(conn, client(), c["id"], c["website"], force=refresh)
        try:
            posts, posts_cached, posts_via = await get_company_posts(
                conn, client(), c["id"], c["website"], force=refresh
            )
        except Exception as exc:  # noqa: BLE001 - own-site news is a bonus, never fatal
            log.warning("company posts failed for %s: %s", c["slug"], exc)
            posts, posts_cached, posts_via = [], False, str(exc)[:120]

        tools = [t.as_dict() for t in tools_from_jobs(jobs, yc_items)]
        score = compute_score(c, jobs, fp, tools)
        store_score(conn, c["id"], score)

        return {
            "found": True,
            "company": c,
            "yc_url": f"https://www.ycombinator.com/companies/{c['slug']}",
            "jobs": jobs,
            "stack": stack_from_jobs(jobs),
            "tools": tools,
            "fingerprint": fp,
            "posts": posts,
            "posts_via": posts_via,
            "yc_news": [i for i in yc_items if i["source"] == "yc_news"],
            "yc_launches": [i for i in yc_items if i["source"] == "yc_launch"],
            "score": score.as_dict(),
            "cached": {
                "jobs": jobs_cached,
                "fingerprint": fp_cached,
                "posts": posts_cached,
            },
        }


@app.get("/api/technographics")
async def technographics(
    domain: str = Query(..., min_length=3),
    company: str = Query("", description="Company name, to suppress self-references"),
    limit: int = Query(400, ge=1, le=2000),
) -> JSONResponse:
    """Technographics for any company, from its public job board.

    Independent of YC: works for any company with a Greenhouse, Ashby or Lever board,
    which is most funded startups. These are the platforms' own public embed endpoints -
    no credentials, no bot-blocking - and they carry far more text than YC exposes.
    """
    board = await discover_board(client(), domain)
    if board is None:
        raise HTTPException(
            404,
            f"No Greenhouse, Ashby or Lever board found for {domain!r}. The company may "
            "use another ATS, or link its board from a page we did not read.",
        )
    try:
        postings = await fetch_board(client(), board)
    except ATSUnavailable as exc:
        raise HTTPException(502, str(exc)) from exc

    name = company or board.token
    hits = merge_hits(
        [
            detect_tools(p.description, source=p.title[:40], company=name)
            for p in postings[:limit]
        ]
    )
    ours, theirs = split_atlassian(hits)
    departments: dict[str, int] = {}
    for p in postings:
        departments[p.department or "Unspecified"] = (
            departments.get(p.department or "Unspecified", 0) + 1
        )

    return JSONResponse({
        "domain": domain,
        "board": {
            "provider": board.provider, "token": board.token,
            "url": board.url, "found_via": board.found_via,
        },
        "job_count": len(postings),
        "scanned": min(len(postings), limit),
        "departments": dict(sorted(departments.items(), key=lambda kv: -kv[1])[:12]),
        "atlassian": [h.as_dict() for h in ours],
        "competitors": [h.as_dict() for h in theirs],
        "postings": [p.as_dict() for p in postings[:40]],
    })


@app.post("/api/brief")
async def brief(
    company: str = Query(..., min_length=1),
    context: str = Query(""),
    source: str = Query("google_news"),
    regenerate: bool = Query(False),
) -> JSONResponse:
    """Generate the 'so what' brief for one company.

    On demand only - each generation costs money, so it never rides along with a lookup.
    Cached against a hash of its own inputs; only `regenerate` forces a new call.
    """
    with session() as conn:
        row, suggestions = resolve(conn, company)
        if row is None:
            raise HTTPException(404, f"{company!r} is not in the YC directory.")
        c = company_dict(row)

        jobs = load_jobs(conn, c["id"])
        fp = load_fingerprint(conn, c["id"]) or {}
        posts = load_posts(conn, c["id"])
        yc_items = load_posts(conn, c["id"], sources=("yc_news", "yc_launch"))
        tools = [t.as_dict() for t in tools_from_jobs(jobs, yc_items)]
        score = compute_score(c, jobs, fp, tools).as_dict()

    news = await _news_block(company, context, source, limit=12)
    payload = build_payload(
        c,
        jobs,
        stack_from_jobs(jobs),
        fp,
        score,
        news["articles"],
        tools=tools,
        posts=posts,
    )
    digest = payload_hash(payload)

    if not regenerate:
        with session() as conn:
            cached = load_brief(conn, c["id"], digest)
        if cached:
            return JSONResponse({"company": c["name"], **cached})

    try:
        result = await generate_brief(payload)
    except BriefUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc

    with session() as conn:
        store_brief(conn, c["id"], digest, result)

    return JSONResponse(
        {
            "company": c["name"],
            "brief": result.model_dump(),
            "model": active_provider().model,
            "created_at": time.time(),
            "cached": False,
        }
    )


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
        "brief": {
            "available": credentials_available(),
            "provider": active_provider().label,
            "model": active_provider().model,
        },
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
