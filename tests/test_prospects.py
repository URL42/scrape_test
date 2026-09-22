"""ICP filtering and prospect ranking.

The ICP is AI-first companies at YC scale or a little larger that are not yet Atlassian
customers. The load-bearing decision is that ranking rests on *positive* evidence of a
competitor rather than the absence of Atlassian - absence proves nothing, and treating it
as opportunity would bury the real leads under thousands of unknowns.
"""

from __future__ import annotations

import pytest

from scrape_test.prospects.icp import (
    Candidate,
    is_ai_first,
    prospect_score,
    qualifies,
    size_ok,
)
from scrape_test.prospects.sources import parse_hn_post


def tool(product, strength="stated", category="issue_tracking"):
    return {
        "product": product,
        "strength": strength,
        "category": category,
        "evidence": f"we use {product}",
        "sources": ["Eng"],
    }


def cand(**kw):
    base = dict(
        name="Acme",
        domain="acme.com",
        source="yc",
        batch="Winter 2024",
        team_size=40,
        one_liner="AI-native workflow tooling",
        tags=["AI"],
        is_hiring=True,
    )
    base.update(kw)
    return Candidate(**base)


class TestAiDetection:
    @pytest.mark.parametrize(
        "tags,blurb",
        [
            (["AI"], "workflow tool"),
            (["Generative AI"], ""),
            ([], "We build LLM agents"),
            ([], "an AI-native ERP"),
            ([], "deep learning for radiology"),
        ],
    )
    def test_positive(self, tags, blurb):
        assert is_ai_first(tags, blurb)

    @pytest.mark.parametrize(
        "blurb",
        [
            "Payroll for restaurants",
            "A marketplace for used furniture",
            "Air cargo booking",
        ],
    )
    def test_negative(self, blurb):
        assert not is_ai_first([], blurb)


class TestSizing:
    def test_team_size_band(self):
        assert size_ok(40)
        assert not size_ok(3)
        assert not size_ok(900)

    def test_open_roles_stand_in_when_size_unknown(self):
        """HN candidates carry no headcount, so open roles proxy for it."""
        assert size_ok(None, open_roles=12)
        assert not size_ok(None, open_roles=1)
        assert not size_ok(None, open_roles=500)

    def test_no_data_at_all_fails(self):
        assert not size_ok(None, None)


class TestQualifies:
    def test_in_profile(self):
        ok, reason = qualifies(cand())
        assert ok and reason == "in profile"

    def test_rejects_non_ai(self):
        ok, reason = qualifies(cand(tags=[], one_liner="Payroll for restaurants"))
        assert not ok and reason == "not AI-first"

    def test_rejects_old_batch(self):
        ok, reason = qualifies(cand(batch="Summer 2014"))
        assert not ok and "2014" in reason

    def test_rejects_not_hiring_yc(self):
        ok, reason = qualifies(cand(is_hiring=False))
        assert not ok and reason == "not currently hiring"

    def test_hn_candidates_skip_the_hiring_gate(self):
        """Everyone in a 'Who is hiring' thread is hiring by construction."""
        ok, _ = qualifies(cand(source="hn", is_hiring=False, team_size=30, batch=None))
        assert ok


class TestProspectScore:
    def test_existing_customer_is_a_hard_stop(self):
        score, verdict, reasons = prospect_score(cand(), [tool("Jira")], [], 20)
        assert verdict == "existing customer"
        assert score == 0.0
        assert "already uses Jira" in reasons[0]

    def test_stated_competitor_outranks_a_bare_mention(self):
        stated, _, _ = prospect_score(cand(), [], [tool("Linear")], 20)
        mentioned, _, _ = prospect_score(cand(), [], [tool("Linear", "mentioned")], 20)
        assert stated > mentioned

    def test_no_tooling_named_is_not_a_prospect(self):
        """Size and hiring alone describe thousands of companies. Without evidence they
        already buy tooling, a company is an unknown, not a lead."""
        _score, verdict, reasons = prospect_score(cand(), [], [], 20)
        assert verdict == "no signal"
        assert any("unproven buyer" in r for r in reasons)

    def test_competitor_evidence_makes_a_prospect(self):
        score, verdict, _ = prospect_score(cand(), [], [tool("Linear")], 20)
        assert verdict == "prospect"
        assert score > 50

    def test_passing_atlassian_mention_is_surfaced_not_fatal(self):
        _s, verdict, reasons = prospect_score(
            cand(), [tool("Jira", "mentioned")], [tool("Linear")], 20
        )
        assert verdict == "prospect"
        assert any("mentioned in passing" in r for r in reasons)

    def test_non_displaceable_tooling_does_not_qualify(self):
        """GitHub is source control, not a Jira or Confluence competitor."""
        _s, verdict, _ = prospect_score(cand(), [], [tool("GitHub", category="source_control")], 20)
        assert verdict == "no signal"


class TestHnParsing:
    def test_parses_the_standard_format(self):
        post = (
            "Neuralwatt | https://neuralwatt.com | REMOTE | Full-time "
            "Hiring 2 engineers to work on AI infrastructure."
        )
        c = parse_hn_post(post)
        assert c is not None
        assert c.name == "Neuralwatt"
        assert c.domain == "neuralwatt.com"
        assert c.source == "hn"

    def test_skips_ats_and_social_links(self):
        post = "Acme | Engineer | Remote. Apply at https://boards.greenhouse.io/acme"
        assert parse_hn_post(post) is None

    def test_trims_a_dangling_paren(self):
        c = parse_hn_post("Shepherd (Series B | Engineer | NYC https://shepherd.com")
        assert c is not None and c.name == "Shepherd"

    def test_rejects_a_post_with_no_domain(self):
        assert parse_hn_post("Acme Corp | Engineer | Remote, email us") is None


class TestFundingJoin:
    """The digest knows who just raised; the scan has to be able to use it.

    Two bugs made this impossible on the first pass: the funding lookup sat *after* the
    board fetch, so any company we could not scan lost its funding date, and digest
    candidates arrive with a company name and no domain at all - funding announcements
    link to the investor's blog, not the company.
    """

    def test_funding_lookup_matches_on_a_normalised_name(self, tmp_path, monkeypatch):
        import time

        from scrape_test import db as db_mod
        from scrape_test.whatsnew import funding_age_for

        monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "j.db")
        db_mod.init_db()
        with db_mod.session() as conn:
            conn.execute(
                "INSERT INTO digest_items (source_name, source_kind, title, url, "
                "published, tags, company, relevance, fetched_at) "
                "VALUES ('Greylock','vc','Introducing Antioch','https://g.test/1',"
                "'Tue, 08 Sep 2026 15:11:00 GMT','[\"funding\"]','Antioch',90,?)",
                (time.time(),),
            )
            # Announcements say "Antioch"; a directory says "Antioch Inc." or "Antioch AI".
            assert funding_age_for(conn, "Antioch") is not None
            assert funding_age_for(conn, "Antioch Inc.") is not None
            assert funding_age_for(conn, "Antioch AI") is not None
            assert funding_age_for(conn, "Something Else") is None

    async def test_domain_resolution_falls_back_to_common_tlds(self):
        import httpx

        from scrape_test.prospects.scan import resolve_domain

        def handler(request):
            if str(request.url).startswith("https://acmeco.ai"):
                return httpx.Response(200, text="x" * 900)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await resolve_domain(client, "AcmeCo") == "acmeco.ai"

    async def test_domain_resolution_gives_up_cleanly(self):
        import httpx

        from scrape_test.prospects.scan import resolve_domain

        def handler(request):
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await resolve_domain(client, "Nonexistent Widget Co") is None
