"""What's-new classification and the timing / per-product scoring that depends on it."""

from __future__ import annotations

import pytest

from scrape_test.scoring.products import lead_product, priority, product_fit
from scrape_test.scoring.timing import decay, timing_score, timing_signals
from scrape_test.whatsnew.classify import classify, extract_company, relevance


class TestCompanyExtraction:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Our seed investment in Atira, the AI orchestration layer", "Atira"),
            ("Partnering with Preview: Lights, Inference, Action", "Preview"),
            ("Introducing Antioch: The Simulation Platform for Physical AI", "Antioch"),
            ("Why we invested in Langdock", "Langdock"),
            ("Welcoming Cursor to the portfolio", "Cursor"),
            ("Acme raises $20M Series A", "Acme"),
            ("Battery Ventures Acquires Majority Stake in Germany's Sustainabill", "Sustainabill"),
        ],
    )
    def test_names_the_company(self, title, expected):
        assert extract_company(title) == expected

    def test_think_pieces_name_nobody(self):
        """A thesis post is not a lead."""
        c = classify("Collective Model Intelligence: The Next Scaling Axis?")
        assert c.company is None
        assert not c.is_funding


class TestClassification:
    def test_tags_funding_and_ai(self):
        c = classify("Acme raises $20M Series A to build AI coding agents")
        assert {"funding", "ai-native", "ai-sdlc"} <= set(c.tags)
        assert c.amount == "$20M"
        assert c.round_stage == "Series A"

    def test_tags_acquisitions(self):
        assert "m&a" in classify("Foo Acquires Majority Stake in Bar").tags

    def test_a_named_funded_company_outranks_a_thesis(self):
        funded = classify("Partnering with Preview: an AI company")
        thesis = classify("Our thoughts on the future of software")
        assert relevance(funded) > relevance(thesis)


class TestTimingDecay:
    def test_fresh_beats_stale(self):
        assert decay(0, 120) == 1.0
        assert decay(120, 120) == pytest.approx(0.5)
        assert decay(1000, 120) == 0.1  # floored, never worthless

    def test_unknown_age_is_middling(self):
        assert decay(None, 120) == 0.5

    def test_recent_funding_scores_higher_than_old_funding(self):
        jobs = [{"title": "Engineer", "description": "build things"}]
        fresh = timing_score(timing_signals(jobs, funding_age_days=14))
        stale = timing_score(timing_signals(jobs, funding_age_days=300))
        assert fresh > stale


class TestTimingSignals:
    def test_first_of_role_hire(self):
        """A company hiring its first PM is saying coordination became a job."""
        sigs = timing_signals([{"title": "1st Product Manager", "description": ""}])
        assert any(s["key"] == "first_of_role" for s in sigs)

    def test_tool_migration(self):
        sigs = timing_signals(
            [{"title": "Eng", "description": "We are migrating from Jira to Linear."}]
        )
        assert any(s["key"] == "tool_migration" for s in sigs)

    def test_leadership_hire(self):
        sigs = timing_signals([{"title": "VP of Engineering", "description": ""}])
        assert any(s["key"] == "leadership_hire" for s in sigs)

    def test_quiet_company_has_no_timing(self):
        assert timing_score(timing_signals([{"title": "Engineer", "description": "code"}])) == 0.0


class TestProductFit:
    def _tool(self, p, strength="stated"):
        return {"product": p, "strength": strength, "category": "x", "evidence": "", "sources": []}

    def test_linear_and_sprints_lead_to_jira(self):
        jobs = [
            {"title": "Engineer", "role": "eng", "description": "sprint planning, backlog"}
            for _ in range(3)
        ]
        fits = product_fit(jobs, [self._tool("Linear")])
        lead, _score = lead_product(fits)
        assert lead == "Jira"

    def test_notion_and_compliance_lead_to_confluence(self):
        jobs = [
            {
                "title": "Tech Writer",
                "role": "ops",
                "description": "documentation, runbooks, SOC 2 audit evidence",
                "location": "London",
            }
        ]
        fits = product_fit(jobs, [self._tool("Notion")])
        assert fits["Confluence"]["score"] > fits["Jira"]["score"]

    def test_distributed_async_work_leads_to_loom(self):
        jobs = [
            {
                "title": "Support Engineer",
                "role": "ops",
                "description": "async-first, written communication, customer onboarding",
                "location": "Remote (US)",
            },
            {"title": "CSM", "role": "ops", "description": "enablement", "location": "London"},
        ]
        fits = product_fit(jobs, [])
        lead, _ = lead_product(fits)
        assert lead == "Loom"

    def test_tool_sprawl_and_ai_lead_to_rovo(self):
        tools = [self._tool(p) for p in ("Linear", "Notion", "Zendesk", "GitHub", "Airtable")]
        jobs = [
            {
                "title": "ML Engineer",
                "role": "eng",
                "description": "RAG pipelines, embeddings, semantic search",
            }
        ]
        fits = product_fit(jobs, tools)
        assert fits["Rovo"]["score"] >= 65

    def test_no_evidence_means_no_lead_product(self):
        lead, score = lead_product(product_fit([], []))
        assert lead is None and score == 0.0


class TestPriority:
    def test_needs_both_fit_and_timing(self):
        """High fit with no trigger is a nurture; a trigger with no fit is noise."""
        assert priority(90, 0) == 0.0
        assert priority(0, 90) == 0.0
        assert priority(90, 90) == pytest.approx(90.0, abs=0.1)

    def test_is_multiplicative_not_additive(self):
        balanced = priority(60, 60)
        lopsided = priority(100, 20)
        assert balanced > lopsided


class TestPublishedDates:
    """Feeds emit RFC 822; sitemaps emit ISO 8601. Both must parse, and anything else
    must return None rather than a guess - a wrong date distorts the timing score."""

    @pytest.mark.parametrize(
        "value",
        [
            "Wed, 29 Jul 2026 15:00:56 +0000",
            "Tue, 08 Sep 2026 15:11:00 GMT",
            "2026-08-26T14:28:35.452Z",
            "2026-09-17",
        ],
    )
    def test_parses_real_formats(self, value):
        from scrape_test.whatsnew.dates import age_days

        assert age_days(value) is not None

    @pytest.mark.parametrize("value", ["garbage", "", None, "last Tuesday"])
    def test_unparseable_is_none_not_a_guess(self, value):
        from scrape_test.whatsnew.dates import age_days

        assert age_days(value) is None

    def test_age_grows_with_distance(self):
        from datetime import UTC, datetime

        from scrape_test.whatsnew.dates import age_days

        now = datetime(2026, 9, 22, tzinfo=UTC)
        recent = age_days("2026-09-15", now=now)
        old = age_days("2026-01-15", now=now)
        assert recent is not None and old is not None and old > recent
