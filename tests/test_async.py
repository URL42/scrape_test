"""Async coverage for the fetch/cache paths. The review found three defects living here,
so the cache branches and failure isolation are pinned explicitly."""

from __future__ import annotations

import time

import httpx
import pytest

from scrape_test import db as db_mod
from scrape_test.http import FetchError, fetch
from scrape_test.yc.jobs import get_jobs, jobs_cache_age, store_jobs


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """Point the whole storage layer at a throwaway database file."""
    path = tmp_path / "t.db"
    monkeypatch.setattr(db_mod, "DB_PATH", path)
    db_mod.init_db()
    with db_mod.session() as conn:
        conn.execute(
            "INSERT INTO companies (id, slug, name, norm_name, website, batch, status, "
            "team_size, is_hiring, fetched_at) VALUES (1,'acme','Acme','acme',"
            "'https://acme.test','Fall 2025','Active',12,1,?)",
            (time.time(),),
        )
    return path


EMPTY_JOBS_HTML = '<div data-page="{&quot;props&quot;:{&quot;jobPostings&quot;:[]}}"></div>'


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestJobsCache:
    def test_zero_postings_still_records_the_fetch(self, temp_db):
        """A company with no open roles must not look permanently cache-cold, or every
        lookup refetches it - precisely where the request is pure waste."""
        with db_mod.session() as conn:
            assert jobs_cache_age(conn, 1) is None
            store_jobs(conn, 1, [])
            assert jobs_cache_age(conn, 1) is not None

    async def test_second_lookup_uses_cache_and_makes_no_request(self, temp_db):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, text=EMPTY_JOBS_HTML)

        async with _client(handler) as client:
            with db_mod.session() as conn:
                jobs, cached, _yc = await get_jobs(conn, client, 1, "acme")
                assert jobs == [] and cached is False
                jobs, cached, _yc = await get_jobs(conn, client, 1, "acme")
                assert cached is True
        assert len(calls) == 1, "cached lookup must not hit the network again"

    async def test_force_bypasses_cache(self, temp_db):
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(200, text=EMPTY_JOBS_HTML)

        async with _client(handler) as client:
            with db_mod.session() as conn:
                await get_jobs(conn, client, 1, "acme")
                await get_jobs(conn, client, 1, "acme", force=True)
        assert len(calls) == 2


class TestFetchRetries:
    async def test_does_not_retry_a_404(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(404)

        async with _client(handler) as client:
            resp = await fetch(client, "https://example.test/x")
        assert resp.status_code == 404
        assert len(calls) == 1, "a 404 will not improve on retry"

    async def test_retries_then_raises_without_a_trailing_sleep(self):
        calls = []

        def handler(request):
            calls.append(1)
            return httpx.Response(503)

        started = time.monotonic()
        async with _client(handler) as client:
            with pytest.raises(FetchError):
                await fetch(client, "https://example.test/x", retries=2)
        elapsed = time.monotonic() - started
        assert len(calls) == 2
        # One backoff between the two attempts, none after the last.
        assert elapsed < 2.5, f"slept after the final attempt ({elapsed:.1f}s)"

    async def test_succeeds_after_a_retryable_failure(self):
        seq = [httpx.Response(503), httpx.Response(200, text="ok")]

        def handler(request):
            return seq.pop(0)

        async with _client(handler) as client:
            resp = await fetch(client, "https://example.test/x", retries=3)
        assert resp.ok and resp.text == "ok"
