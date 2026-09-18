"""Brief tests. No credentials exist in CI or on this machine, so the model client is
injected - these cover prompt assembly, parsing, caching and the failure paths, but they
do NOT prove a live API call succeeds."""

from __future__ import annotations

import time

import pytest

from scrape_test import brief as brief_mod
from scrape_test import db as db_mod
from scrape_test.brief import (
    Brief,
    BriefUnavailable,
    build_payload,
    generate_brief,
    load_brief,
    payload_hash,
    store_brief,
)

COMPANY = {
    "id": 1, "name": "Acme", "batch": "Fall 2025", "status": "Active", "team_size": 18,
    "industry": "B2B", "subindustry": "Infra", "tags": ["AI"], "website": "https://acme.test",
    "one_liner": "Ships widgets", "long_description": "A longer description.",
}
JOBS = [{
    "pretty_role": "Engineering", "role": "eng", "title": "Senior Backend Engineer",
    "skills": ["Python", "PostgreSQL", "Kubernetes"], "salary_range": "$150K - $200K",
    "equity_range": "0.1%", "min_experience": "5+ years", "location": "SF",
    "last_active_rel": "3 days",
}]
STACK = [{"skill": "Python", "mentions": 1}, {"skill": "Kubernetes", "mentions": 1}]
FP = {"detected": {"source_control": [{"product": "GitHub", "confidence": "strong",
                                       "evidence": "github.com/acme"}]}, "error": None}
SCORE = {
    "total": 61.3, "confidence": "high", "rules_version": "test-1",
    "signals": [{"key": "eng_hiring_volume", "label": "Engineering hiring", "points": 8.0,
                 "weight": 20.0, "strength": 0.4, "reason": "1 open engineering role"}],
}
ARTICLES = [{"title": "Acme raises $20M Series A", "source": "TechCrunch",
             "published": "2026-09-01", "url": "https://tc.test/acme"}]


def sample_brief(**over) -> Brief:
    data = {
        "headline": "Series A plus backend hiring means coordination load is about to jump.",
        "news_summary": "Acme raised a $20M Series A this month.",
        "interpretation": "An 18-person team hiring senior backend engineers post-raise.",
        "priority": "pursue now",
        "recommended_action": "Reach out referencing the raise.",
        "news_hook": "The $20M Series A",
        "talking_points": ["Hiring senior backend engineers", "Kubernetes in the stack"],
        "risks": ["May already use an incumbent internally"],
        "evidence_gaps": ["No Atlassian detected publicly, which proves nothing"],
        "email_subject": "Scaling after the Series A",
        "email_body": "Congratulations on the raise.\n\nSaw you are hiring backend engineers.",
    }
    data.update(over)
    return Brief(**data)


class FakeResponse:
    def __init__(self, parsed, stop_reason="end_turn"):
        self.parsed_output = parsed
        self.stop_reason = stop_reason


class FakeMessages:
    def __init__(self, response):
        self._response = response
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class FakeClient:
    def __init__(self, response):
        self.messages = FakeMessages(response)


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "b.db")
    db_mod.init_db()
    with db_mod.session() as conn:
        conn.execute(
            "INSERT INTO companies (id, slug, name, norm_name, fetched_at) "
            "VALUES (1,'acme','Acme','acme',?)",
            (time.time(),),
        )
    return tmp_path


class TestPayload:
    def _payload(self):
        return build_payload(COMPANY, JOBS, STACK, FP, SCORE, ARTICLES)

    def test_includes_the_facts_the_model_must_ground_on(self):
        p = self._payload()
        for expected in ("Acme", "Fall 2025", "Senior Backend Engineer", "Kubernetes",
                         "GitHub", "61.3", "Acme raises $20M Series A"):
            assert expected in p, f"missing {expected!r} from payload"

    def test_marks_fingerprint_confidence(self):
        assert "strong evidence" in self._payload()

    def test_handles_a_company_with_no_data(self):
        p = build_payload(COMPANY, [], [], {"detected": {}, "error": None}, SCORE, [])
        assert "No open roles listed on YC." in p
        assert "No recent coverage found" in p
        assert "No stack data" in p


class TestHash:
    def test_same_inputs_same_hash(self):
        a = build_payload(COMPANY, JOBS, STACK, FP, SCORE, ARTICLES)
        b = build_payload(COMPANY, JOBS, STACK, FP, SCORE, ARTICLES)
        assert payload_hash(a) == payload_hash(b)

    def test_new_article_changes_the_hash(self):
        a = build_payload(COMPANY, JOBS, STACK, FP, SCORE, ARTICLES)
        b = build_payload(COMPANY, JOBS, STACK, FP, SCORE, ARTICLES + [
            {"title": "Acme launches", "source": "VB", "published": "2026-09-10"}])
        assert payload_hash(a) != payload_hash(b), "stale brief would be served"


class TestGenerate:
    async def test_returns_parsed_brief_and_sends_the_payload(self):
        client = FakeClient(FakeResponse(sample_brief()))
        result = await generate_brief("PAYLOAD", client=client)
        assert result.priority == "pursue now"
        call = client.messages.parse.__self__.calls[0]
        assert call["messages"][0]["content"] == "PAYLOAD"
        assert call["output_format"] is Brief
        assert call["thinking"] == {"type": "adaptive"}

    async def test_system_prompt_carries_the_grounding_rules(self):
        client = FakeClient(FakeResponse(sample_brief()))
        await generate_brief("P", client=client)
        system = client.messages.parse.__self__.calls[0]["system"]
        assert "Jira, Confluence, and Rovo" in system
        assert "public surface only" in system

    async def test_refusal_is_surfaced_not_swallowed(self):
        client = FakeClient(FakeResponse(sample_brief(), stop_reason="refusal"))
        with pytest.raises(BriefUnavailable, match="declined"):
            await generate_brief("P", client=client)

    async def test_missing_parsed_output_raises(self):
        client = FakeClient(FakeResponse(None))
        with pytest.raises(BriefUnavailable, match="no parseable"):
            await generate_brief("P", client=client)

    async def test_no_credentials_gives_an_actionable_message(self, monkeypatch):
        monkeypatch.setattr(brief_mod, "credentials_available", lambda: False)
        with pytest.raises(BriefUnavailable, match="ANTHROPIC_API_KEY"):
            await generate_brief("P")


class TestCache:
    def test_round_trip(self, temp_db):
        b = sample_brief()
        with db_mod.session() as conn:
            store_brief(conn, 1, "hash-a", b)
            hit = load_brief(conn, 1, "hash-a")
        assert hit is not None
        assert hit["brief"]["headline"] == b.headline
        assert hit["cached"] is True

    def test_different_hash_is_a_miss(self, temp_db):
        """A changed payload must not serve the old brief."""
        with db_mod.session() as conn:
            store_brief(conn, 1, "hash-a", sample_brief())
            assert load_brief(conn, 1, "hash-b") is None

    def test_regenerating_replaces_rather_than_duplicates(self, temp_db):
        with db_mod.session() as conn:
            store_brief(conn, 1, "hash-a", sample_brief())
            store_brief(conn, 1, "hash-b", sample_brief(headline="Updated"))
            n = conn.execute("SELECT COUNT(*) AS n FROM briefs").fetchone()["n"]
            hit = load_brief(conn, 1, "hash-b")
        assert n == 1
        assert hit["brief"]["headline"] == "Updated"


class TestSchema:
    def test_priority_is_constrained(self):
        with pytest.raises(ValueError):
            sample_brief(priority="definitely call them")

    def test_news_hook_is_optional(self):
        assert sample_brief(news_hook=None).news_hook is None
