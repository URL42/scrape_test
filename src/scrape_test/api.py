"""FastAPI application: wiring only.

Every endpoint lives in `routes/`. This module owns the app's lifecycle - one shared
HTTP client, the database schema, the startup directory pull - and mounts the front end.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import WEB_DIR
from .db import init_db, session
from .http import make_client
from .routes import company, digest, scan, search
from .routes.deps import set_client
from .yc.directory import refresh_directory

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    client = make_client()
    set_client(client)
    # Populate the directory before the first lookup; it is a single request.
    with session() as conn:
        try:
            await refresh_directory(conn, client)
        except Exception as exc:  # noqa: BLE001 - startup should not hard-fail offline
            log.warning("directory refresh failed at startup: %s", exc)
    yield
    set_client(None)
    await client.aclose()


app = FastAPI(title="scrape-test", lifespan=lifespan)
app.include_router(company.router)
app.include_router(digest.router)
app.include_router(scan.router)
app.include_router(search.router)


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
