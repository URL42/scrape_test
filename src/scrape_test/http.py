"""Shared async HTTP client: per-host throttling, bounded retries, capped body reads.

Why a throttle registry instead of a blanket sleep: GDELT asks for one request every
five seconds and will hand back a 429 if you ignore that, while YC's static pages are
happy at a few per second. One global rate would make us either rude or needlessly slow.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from .config import (
    DEFAULT_THROTTLE,
    HOST_THROTTLE,
    HTTP_RETRIES,
    HTTP_TIMEOUT,
    USER_AGENT,
)

log = logging.getLogger(__name__)

_last_request: dict[str, float] = {}
_host_locks: dict[str, asyncio.Lock] = {}


def _lock_for(host: str) -> asyncio.Lock:
    if host not in _host_locks:
        _host_locks[host] = asyncio.Lock()
    return _host_locks[host]


async def _respect_throttle(host: str) -> None:
    delay = HOST_THROTTLE.get(host, DEFAULT_THROTTLE)
    if delay <= 0:
        return
    async with _lock_for(host):
        elapsed = time.monotonic() - _last_request.get(host, 0.0)
        wait = delay - elapsed
        if wait > 0:
            # A little jitter so repeated calls do not land on an exact cadence.
            await asyncio.sleep(wait + random.uniform(0, 0.15))
        _last_request[host] = time.monotonic()


@dataclass(slots=True)
class Response:
    url: str
    final_url: str
    status_code: int
    text: str
    headers: dict[str, str]

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


class FetchError(Exception):
    """Raised when a URL could not be retrieved after retries."""


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        headers={
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        limits=httpx.Limits(max_connections=8, max_keepalive_connections=4),
    )


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = HTTP_RETRIES,
    accept_status: tuple[int, ...] = (),
    max_bytes: int | None = None,
) -> Response:
    """GET a URL, honouring per-host throttles. Retries on 429/5xx and transport errors.

    `max_bytes` truncates the body and is meant for untrusted third-party pages we only
    fingerprint. Leave it unset for APIs whose full payload we actually need - the YC
    directory alone is ~10MB and silently clipping it yields invalid JSON.
    """
    host = urlsplit(url).netloc
    last_error: Exception | None = None

    for attempt in range(retries):
        await _respect_throttle(host)
        try:
            resp = await client.get(url)
        except httpx.HTTPError as exc:
            last_error = exc
            log.debug("transport error on %s (attempt %d): %s", url, attempt + 1, exc)
        else:
            if resp.status_code in accept_status or resp.is_success:
                body = resp.text
                if max_bytes is not None and len(body) > max_bytes:
                    body = body[:max_bytes]
                return Response(
                    url=url,
                    final_url=str(resp.url),
                    status_code=resp.status_code,
                    text=body,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
            if resp.status_code not in (429, 500, 502, 503, 504):
                # A genuine 404/403 will not improve on retry.
                return Response(
                    url=url,
                    final_url=str(resp.url),
                    status_code=resp.status_code,
                    text="",
                    headers={k.lower(): v for k, v in resp.headers.items()},
                )
            last_error = FetchError(f"HTTP {resp.status_code}")
            log.debug("retryable status %s on %s", resp.status_code, url)

        # Exponential backoff with jitter - but not after the last attempt, which would
        # just delay the inevitable failure by several seconds.
        if attempt < retries - 1:
            await asyncio.sleep((2**attempt) + random.uniform(0, 0.5))

    raise FetchError(f"{url}: {last_error}")
