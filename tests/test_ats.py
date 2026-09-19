"""ATS discovery and provider tests.

These endpoints are the technographic source: public, documented job-board APIs with no
credentials and no bot-blocking. Network calls are faked - none of this proves a live
board fetch works, though the providers were verified against real boards while building
(Stripe 666 jobs on Greenhouse, Vanta 93 on Ashby, Ramp 148 on Ashby).
"""

from __future__ import annotations

import json

import httpx
import pytest

from scrape_test.ats import (
    AshbyProvider,
    ATSUnavailable,
    GreenhouseProvider,
    LeverProvider,
    discover_board,
    extract_tokens,
)
from scrape_test.ats.base import clean_html
from scrape_test.ats.discover import domain_label


class TestTokenExtraction:
    @pytest.mark.parametrize(
        "html,expected",
        [
            (
                '<script src="https://boards-api.greenhouse.io/v1/boards/stripe/jobs">',
                ("greenhouse", "stripe"),
            ),
            ('<a href="https://boards.greenhouse.io/discord">Jobs</a>', ("greenhouse", "discord")),
            ('fetch("https://api.ashbyhq.com/posting-api/job-board/vanta")', ("ashby", "vanta")),
            ('<a href="https://jobs.ashbyhq.com/rollstack">Careers</a>', ("ashby", "rollstack")),
            ('<a href="https://jobs.lever.co/acme">Open roles</a>', ("lever", "acme")),
        ],
    )
    def test_finds_embedded_board_tokens(self, html, expected):
        assert expected in extract_tokens(html)

    def test_ignores_platform_boilerplate(self):
        """'embed' and 'js' are the platform's own paths, not a customer board."""
        html = '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme">'
        tokens = extract_tokens(html)
        assert ("greenhouse", "embed") not in tokens
        assert ("greenhouse", "js") not in tokens

    def test_no_tokens_in_an_ordinary_page(self):
        assert extract_tokens("<html><body><p>We are hiring!</p></body></html>") == []


class TestDomainLabel:
    @pytest.mark.parametrize(
        "site,expected",
        [
            ("https://www.vanta.com", "vanta"),
            ("rollstack.com", "rollstack"),
            ("https://acme-corp.io/careers", "acmecorp"),
            ("https://sub.example.co.uk", "sub"),
        ],
    )
    def test_guesses_a_board_token(self, site, expected):
        assert domain_label(site) == expected


class TestCleanHtml:
    def test_strips_tags_and_entities(self):
        assert clean_html("<p>We use <b>Jira</b>&nbsp;daily</p>") == "We use Jira daily"

    def test_handles_none(self):
        assert clean_html(None) == ""


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class TestProviders:
    async def test_greenhouse_parses_a_board(self):
        payload = {
            "jobs": [
                {
                    "title": "Staff Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "Remote"},
                    "departments": [{"name": "Engineering"}],
                    "updated_at": "2026-09-01",
                    "content": "<p>We use &lt;b&gt;Jira&lt;/b&gt;</p>",
                    "offices": [{"name": "SF"}],
                }
            ]
        }

        def handler(request):
            assert "boards-api.greenhouse.io/v1/boards/acme" in str(request.url)
            assert "content=true" in str(request.url)
            return httpx.Response(200, text=json.dumps(payload))

        async with _client(handler) as client:
            jobs = await GreenhouseProvider().fetch(client, "acme")
        assert len(jobs) == 1
        assert jobs[0].title == "Staff Engineer"
        assert jobs[0].department == "Engineering"
        assert "Jira" in jobs[0].description

    async def test_ashby_parses_a_board(self):
        payload = {
            "jobs": [
                {
                    "title": "Backend Engineer",
                    "jobUrl": "https://jobs.ashbyhq.com/acme/1",
                    "location": "NYC",
                    "department": "Eng",
                    "publishedAt": "2026-09-02",
                    "descriptionHtml": "<p>Issue tracking with Linear</p>",
                }
            ]
        }

        def handler(request):
            assert "api.ashbyhq.com/posting-api/job-board/acme" in str(request.url)
            return httpx.Response(200, text=json.dumps(payload))

        async with _client(handler) as client:
            jobs = await AshbyProvider().fetch(client, "acme")
        assert jobs[0].description == "Issue tracking with Linear"

    async def test_lever_parses_a_board(self):
        payload = [
            {
                "text": "Platform Engineer",
                "hostedUrl": "https://jobs.lever.co/acme/1",
                "categories": {"location": "Berlin", "team": "Infra"},
                "descriptionPlain": "We use Jenkins.",
                "lists": [{"text": "Stack", "content": "<li>Bitbucket</li>"}],
            }
        ]

        def handler(request):
            return httpx.Response(200, text=json.dumps(payload))

        async with _client(handler) as client:
            jobs = await LeverProvider().fetch(client, "acme")
        assert "Jenkins" in jobs[0].description
        assert "Bitbucket" in jobs[0].description

    async def test_missing_board_is_actionable(self):
        def handler(request):
            return httpx.Response(404)

        async with _client(handler) as client:
            with pytest.raises(ATSUnavailable, match="no such board"):
                await GreenhouseProvider().fetch(client, "nope")

    async def test_bad_json_is_not_a_crash(self):
        def handler(request):
            return httpx.Response(200, text="<html>not json</html>")

        async with _client(handler) as client:
            with pytest.raises(ATSUnavailable, match="bad JSON"):
                await AshbyProvider().fetch(client, "acme")


class TestDiscovery:
    async def test_finds_a_board_linked_from_the_careers_page(self):
        def handler(request):
            url = str(request.url)
            if url.rstrip("/").endswith("acme.com"):
                return httpx.Response(200, text="<html>home</html>")
            if "/careers" in url:
                return httpx.Response(
                    200, text='<a href="https://jobs.ashbyhq.com/acmeco">Jobs</a>'
                )
            return httpx.Response(404)

        async with _client(handler) as client:
            board = await discover_board(client, "acme.com")
        assert board is not None
        assert (board.provider, board.token) == ("ashby", "acmeco")
        assert "careers" in board.found_via

    async def test_falls_back_to_guessing_from_the_domain(self):
        def handler(request):
            url = str(request.url)
            if "boards-api.greenhouse.io/v1/boards/acme" in url:
                return httpx.Response(200, text=json.dumps({"jobs": [{"title": "Eng"}]}))
            if "ashbyhq" in url or "lever" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no board linked</html>")

        async with _client(handler) as client:
            board = await discover_board(client, "acme.com")
        assert board is not None
        assert board.provider == "greenhouse"
        assert board.found_via == "guessed from domain"

    async def test_returns_none_when_there_is_no_board(self):
        def handler(request):
            url = str(request.url)
            if any(h in url for h in ("greenhouse", "ashby", "lever")):
                return httpx.Response(404)
            return httpx.Response(200, text="<html>nothing here</html>")

        async with _client(handler) as client:
            assert await discover_board(client, "acme.com") is None
