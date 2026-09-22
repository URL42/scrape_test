"""Shared state for the route modules: the HTTP client and the background tasks.

One client is created at startup and reused, because every route does outbound HTTP
and the per-host throttles live on it. The task handles are module state so a second
scan cannot be started while one is running.
"""

from __future__ import annotations

import asyncio

import httpx
from fastapi import HTTPException

_client: httpx.AsyncClient | None = None
scan_task: asyncio.Task[int] | None = None
digest_task: asyncio.Task[int] | None = None


def set_client(c: httpx.AsyncClient | None) -> None:
    global _client
    _client = c


def client() -> httpx.AsyncClient:
    if _client is None:
        raise HTTPException(503, "HTTP client not ready")
    return _client


def running(task: asyncio.Task[int] | None) -> bool:
    return task is not None and not task.done()
