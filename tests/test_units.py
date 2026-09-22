"""Unit tests. These lean deliberately on the bugs found while building, so they stay fixed."""

from __future__ import annotations

import html as html_mod
import json
from datetime import UTC

import pytest

from scrape_test.extract.fingerprint import detect
from scrape_test.news.base import context_terms
from scrape_test.news.google_news import GoogleNewsSource, _useful_summary
from scrape_test.scoring.rules import (
    is_engineering,
    parse_batch,
    parse_relative_days,
)
from scrape_test.scoring.score import compute_score
from scrape_test.yc.directory import normalize
from scrape_test.yc.jobs import extract_inertia_payload


class TestNormalize:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Stripe", "stripe"),
            ("  STRIPE, Inc. ", "stripe"),
            ("Acme, Inc.", "acme"),
            ("Foo Labs", "foo"),
            ("Bar.io", "bar"),
            ("Twenty-Three", "twenty three"),
        ],
    )
    def test_folds_case_punctuation_and_suffixes(self, raw, expected):
        assert normalize(raw) == expected


class TestRelativeTime:
    @pytest.mark.parametrize(
        "raw,days",
        [
            ("2 months", 60.0),
            ("about 2 months", 60.0),
            ("5 days", 5.0),
            ("1 year", 365.0),
            ("3 weeks", 21.0),
        ],
    )
    def test_parses(self, raw, days):
        assert parse_relative_days(raw) == days

    @pytest.mark.parametrize(
        "raw,days",
        [("a month ago", 30.0), ("an hour ago", 0.04), ("a day ago", 1.0), ("a week ago", 7.0)],
    )
    def test_parses_worded_singulars(self, raw, days):
        """YC writes the singular as a word; a digit-only pattern misses it and silently
        zeroes the freshness signal."""
        assert parse_relative_days(raw) == days

    def test_none_and_garbage(self):
        assert parse_relative_days(None) is None
        assert parse_relative_days("whenever") is None


class TestBatchRecencyIsNotPinnedToAYear:
    """A hardcoded current year silently re-weights every batch each January."""

    def test_current_year_batch_scores_highest(self):
        from datetime import datetime

        from scrape_test.scoring.rules import signal_recent_batch

        this_year = datetime.now(UTC).year
        fresh, _ = signal_recent_batch({"batch": f"Winter {this_year}"}, [], {}, [])
        old, _ = signal_recent_batch({"batch": "Winter 2009"}, [], {}, [])
        assert fresh == 1.0
        assert old < fresh


class TestBatch:
    def test_parses_season_and_year(self):
        assert parse_batch("Fall 2025") == (2025, 3)
        assert parse_batch("Winter 2009") == (2009, 0)

    def test_unknown(self):
        assert parse_batch(None) is None
        assert parse_batch("IPO 2020") is None


class TestEngineeringRole:
    """YC's role slug is 'eng' while the display value is 'Engineering'. Matching only one
    silently zeroed the highest-weighted signal, so pin both."""

    def test_slug_form(self):
        assert is_engineering({"role": "eng", "pretty_role": "Engineering"})

    def test_display_form_only(self):
        assert is_engineering({"role": None, "pretty_role": "Engineering"})

    def test_non_engineering(self):
        assert not is_engineering({"role": "sales", "pretty_role": "Sales"})


class TestFingerprint:
    def test_marketplace_link_is_not_usage(self):
        """Zapier links /apps/jira-software-cloud/integrations because it integrates with
        Jira, not because it runs Jira. That must not register as an Atlassian footprint."""
        html = '<a href="/apps/jira-software-cloud/integrations">Jira</a>'
        products = [d.product for d in detect(html, {})]
        assert "Jira" not in products

    def test_first_party_tenant_is_strong(self):
        html = '<a href="https://acme.atlassian.net/wiki/home">Docs</a>'
        found = {d.product: d for d in detect(html, {})}
        assert "Confluence" in found
        assert found["Confluence"].confidence == "strong"

    def test_script_src_is_strong(self):
        html = '<script src="https://cdn.segment.com/analytics.js"></script>'
        found = {d.product: d for d in detect(html, {})}
        assert found["Segment"].confidence == "strong"

    def test_inline_mention_is_only_weak(self):
        """A third-party code comment mentioning github.com must not read as strong."""
        html = "<div>see https://github.com/relumetech/powerups for details</div>"
        found = {d.product: d for d in detect(html, {})}
        assert found["GitHub"].confidence == "weak"

    def test_header_hint(self):
        found = {d.product: d for d in detect("<html></html>", {"x-vercel-id": "abc"})}
        assert found["Vercel"].confidence == "strong"


class TestInertiaPayload:
    def test_extracts_escaped_json(self):
        payload = {"props": {"jobPostings": [{"id": 1, "title": "Eng"}]}}
        raw = html_mod.escape(json.dumps(payload), quote=True)
        html = f'<div id="app" data-page="{raw}"></div>'
        assert extract_inertia_payload(html)["props"]["jobPostings"][0]["id"] == 1

    def test_missing_payload_raises(self):
        from scrape_test.yc.jobs import JobsUnavailable

        with pytest.raises(JobsUnavailable):
            extract_inertia_payload("<html><body>nope</body></html>")


class TestNewsHelpers:
    def test_context_terms_drops_stopwords(self):
        assert context_terms("the funding of a fintech") == ["funding", "fintech"]

    def test_redundant_summary_suppressed(self):
        title = "Stripe Rolls Out Treasury"
        assert _useful_summary("Stripe Rolls Out Treasury&nbsp;&nbsp; Reuters", title) == ""

    def test_real_summary_kept(self):
        title = "Stripe Rolls Out Treasury"
        body = "The payments company said the product will reach Australian businesses first."
        assert _useful_summary(body, title) == body

    def test_query_quotes_company(self):
        q = GoogleNewsSource().build_query("Acme Corp", "funding rounds")
        assert q.startswith('"Acme Corp"')
        assert "funding" in q


class TestScoring:
    def _company(self, **kw):
        base = {"name": "Test", "batch": "Fall 2025", "team_size": 20}
        base.update(kw)
        return base

    def test_no_data_is_low_confidence(self):
        r = compute_score(self._company(), [], {})
        assert r.confidence == "low"

    def test_engineering_hiring_raises_score(self):
        jobs = [{"role": "eng", "pretty_role": "Engineering", "skills": ["Python", "React"]}]
        with_jobs = compute_score(self._company(), jobs, {"detected": {"x": []}})
        without = compute_score(self._company(), [], {"detected": {"x": []}})
        assert with_jobs.total > without.total

    def test_existing_atlassian_penalises(self):
        fp_clean = {"detected": {"hosting": [{"product": "AWS", "confidence": "strong"}]}}
        fp_jira = {
            "detected": {
                "hosting": [{"product": "AWS", "confidence": "strong"}],
                "atlassian": [{"product": "Jira", "confidence": "strong"}],
            }
        }
        jobs = [{"role": "eng", "pretty_role": "Engineering", "skills": ["Go"]}]
        assert (
            compute_score(self._company(), jobs, fp_jira).total
            < compute_score(self._company(), jobs, fp_clean).total
        )

    def test_score_never_negative(self):
        fp = {"detected": {"atlassian": [{"product": "Jira", "confidence": "strong"}]}}
        r = compute_score(self._company(batch="Summer 2008", team_size=90000), [], fp)
        assert r.total >= 0.0

    def test_breakdown_covers_every_weight(self):
        from scrape_test.scoring.rules import WEIGHTS

        r = compute_score(self._company(), [], {})
        assert {s.key for s in r.signals} == set(WEIGHTS)


class TestGdeltQuery:
    """GDELT rejects parentheses around a single term outright, so the grouping is
    conditional rather than unconditional."""

    def _q(self, company, context):
        from scrape_test.news.gdelt import GdeltSource

        return GdeltSource().build_query(company, context)

    def test_single_context_term_is_not_parenthesised(self):
        q = self._q("Coinbase", "crypto")
        assert q == '"Coinbase" crypto'

    def test_multiple_terms_are_or_grouped(self):
        q = self._q("Coinbase", "crypto exchange")
        assert q == '"Coinbase" (crypto OR exchange)'

    def test_company_only(self):
        assert self._q("Coinbase", "") == '"Coinbase"'

    def test_context_only(self):
        assert self._q("", "crypto") == "crypto"
