"""Tooling detection tests.

Motivated by a real miss: Rollstack's AI Software Engineer posting says "Issue tracking
with Linear" in prose while its structured `skills` array is empty, so the scraper saw
nothing. Precision is the hard part - several product names are ordinary English.
"""

from __future__ import annotations

import pytest

from scrape_test.extract.tooling import (
    detect_tools,
    merge_hits,
    split_atlassian,
)
from scrape_test.scoring.score import compute_score


class TestFalsePositives:
    """Every one of these is a product name used as ordinary English."""

    @pytest.mark.parametrize(
        "text",
        [
            "Strong linear algebra and linear regression experience required.",
            "Familiarity with non-linear optimisation and linear programming.",
            "Standups happen every Monday morning before the sprint.",
            "We reject the notion that speed and quality conflict.",
            "Set the line height and max height on the container.",
            "We harness the power of large language models.",
            "Keyboard shortcuts are a first-class concern.",
            "She is a genuine tech guru.",
            "We glean insights from customer data.",
            "A mural of the founding team hangs in the office.",
        ],
    )
    def test_ordinary_english_is_not_a_tool(self, text):
        assert detect_tools(text) == [], f"false positive in: {text}"


class TestTruePositives:
    def test_the_rollstack_case(self):
        """The exact phrasing that motivated this module."""
        hits = detect_tools("Issue tracking with Linear.", source="AI Software Engineer")
        assert [h.product for h in hits] == ["Linear"]
        assert hits[0].strength == "stated"
        assert hits[0].sources == ["AI Software Engineer"]

    @pytest.mark.parametrize(
        "text,product",
        [
            ("We use Jira for sprint planning.", "Jira"),
            ("Documentation lives in Confluence.", "Confluence"),
            ("Our repos are on Bitbucket.", "Bitbucket"),
            ("We run CI on Buildkite.", "Buildkite"),
            ("On-call is managed with PagerDuty.", "PagerDuty"),
            ("Experience with Notion for our knowledge base.", "Notion"),
            ("We track issues in Shortcut.", "Shortcut"),
            ("Support tickets go through Zendesk.", "Zendesk"),
        ],
    )
    def test_detects_named_tooling(self, text, product):
        assert product in [h.product for h in detect_tools(text)]

    def test_grades_usage_above_bare_mention(self):
        stated = detect_tools("We use Linear for issue tracking.")[0]
        mentioned = detect_tools("Our product integrates with Linear boards and tickets.")[0]
        assert stated.strength == "stated"
        assert mentioned.strength == "mentioned"

    def test_evidence_quotes_the_source(self):
        hit = detect_tools("All issue tracking with Linear, no exceptions.")[0]
        assert "Linear" in hit.evidence


class TestMergeAndSplit:
    def test_merge_prefers_stated_over_mentioned(self):
        weak = detect_tools("Linear boards and ticket workflow", source="a")
        strong = detect_tools("We use Linear for issue tracking.", source="b")
        merged = merge_hits([weak, strong])
        hit = next(h for h in merged if h.product == "Linear")
        assert hit.strength == "stated"
        assert set(hit.sources) == {"a", "b"}

    def test_split_separates_atlassian_from_competitors(self):
        hits = detect_tools("We use Jira and Linear for issue tracking.")
        ours, theirs = split_atlassian(hits)
        assert [h.product for h in ours] == ["Jira"]
        assert [h.product for h in theirs] == ["Linear"]


class TestScoringUsesJobEvidence:
    COMPANY = {"name": "T", "batch": "Fall 2025", "team_size": 20}

    def _tools(self, text):
        return [h.as_dict() for h in detect_tools(text)]

    def test_stated_competitor_beats_a_website_hit(self):
        """A job description is the company describing itself; a site hit is inference."""
        site_only = {
            "detected": {"competing_tools": [{"product": "Linear", "confidence": "strong"}]}
        }
        from_site = compute_score(self.COMPANY, [], site_only, [])
        from_jobs = compute_score(
            self.COMPANY, [], {"detected": {}}, self._tools("We use Linear for issue tracking.")
        )
        site_pts = next(s for s in from_site.signals if s.key == "competing_tools").points
        job_pts = next(s for s in from_jobs.signals if s.key == "competing_tools").points
        assert job_pts > site_pts

    def test_stated_atlassian_in_a_posting_penalises(self):
        clean = compute_score(self.COMPANY, [], {"detected": {}}, [])
        incumbent = compute_score(
            self.COMPANY, [], {"detected": {}}, self._tools("We use Jira for sprint planning.")
        )
        assert incumbent.total < clean.total

    def test_tool_evidence_raises_confidence(self):
        jobs = [{"role": "eng", "pretty_role": "Engineering", "skills": ["Go"]}]
        fp = {"detected": {"hosting": [{"product": "AWS", "confidence": "strong"}]}}
        without = compute_score(self.COMPANY, jobs, fp, [])
        with_tools = compute_score(
            self.COMPANY, jobs, fp, self._tools("We use Linear for issue tracking.")
        )
        assert without.confidence == "low"
        assert with_tools.confidence == "medium"


class TestConfidenceReflectsInformativeness:
    """The old grading answered "is there any data?" and called two year-old postings plus
    a Cloudflare hit "high". These pin the corrected semantics."""

    COMPANY = {"name": "T", "batch": "Fall 2025", "team_size": 20}

    def test_generic_website_tech_does_not_inflate_confidence(self):
        fp = {
            "detected": {
                "hosting": [{"product": "Cloudflare", "confidence": "strong"}],
                "analytics": [{"product": "Google Analytics", "confidence": "strong"}],
            }
        }
        jobs = [{"role": "eng", "pretty_role": "Engineering", "last_active_rel": "1 year"}]
        result = compute_score(self.COMPANY, jobs, fp, [])
        assert result.confidence == "low"
        assert any("bears on coordination" in r for r in result.confidence_reasons)

    def test_stale_postings_are_penalised(self):
        fresh = compute_score(
            self.COMPANY,
            [
                {
                    "role": "eng",
                    "pretty_role": "Engineering",
                    "last_active_rel": "3 days",
                    "description": "We use Linear for issue tracking.",
                }
            ],
            {"detected": {}},
            [{"product": "Linear", "category": "issue_tracking", "strength": "stated"}],
        )
        stale = compute_score(
            self.COMPANY,
            [
                {
                    "role": "eng",
                    "pretty_role": "Engineering",
                    "last_active_rel": "2 years",
                    "description": "We use Linear for issue tracking.",
                }
            ],
            {"detected": {}},
            [{"product": "Linear", "category": "issue_tracking", "strength": "stated"}],
        )
        assert fresh.confidence == "high"
        assert stale.confidence == "medium"
        assert any("over 6 months old" in r for r in stale.confidence_reasons)

    def test_reasons_are_always_present(self):
        result = compute_score(self.COMPANY, [], {}, [])
        assert result.confidence == "low"
        assert result.confidence_reasons
        assert "confidence_reasons" in result.as_dict()


class TestDescriptionRoleFilter:
    """Which roles are worth a request. Measured across 39 non-engineering postings:
    product named tooling in 3 of 4 descriptions; marketing (13) and operations (22)
    named none. PMs live in the tracker and write the docs, so they say which ones."""

    def _p(self, role, pretty):
        return {"role": role, "pretty_role": pretty, "url": "/x"}

    @pytest.mark.parametrize(
        "role,pretty",
        [("eng", "Engineering"), ("product", "Product")],
    )
    def test_included_roles(self, role, pretty, monkeypatch):
        from scrape_test.yc.jobs import wants_description

        monkeypatch.delenv("SCRAPE_TEST_DESC_ROLES", raising=False)
        assert wants_description(self._p(role, pretty))

    @pytest.mark.parametrize(
        "role,pretty",
        [
            ("marketing", "Marketing"),
            ("operations", "Operations"),
            ("sales", "Sales"),
            ("finance", "Finance"),
        ],
    )
    def test_excluded_roles(self, role, pretty, monkeypatch):
        from scrape_test.yc.jobs import wants_description

        monkeypatch.delenv("SCRAPE_TEST_DESC_ROLES", raising=False)
        assert not wants_description(self._p(role, pretty))

    def test_role_set_is_configurable(self, monkeypatch):
        from scrape_test.yc.jobs import wants_description

        monkeypatch.setenv("SCRAPE_TEST_DESC_ROLES", "design,product")
        assert wants_description(self._p("design", "Design"))
        assert not wants_description(self._p("eng", "Engineering"))

    def test_hiring_volume_signal_stays_engineering_only(self, monkeypatch):
        """Widening description fetching must not widen the eng-hiring signal."""
        from scrape_test.yc.jobs import is_engineering

        monkeypatch.setenv("SCRAPE_TEST_DESC_ROLES", "eng,product")
        assert is_engineering(self._p("eng", "Engineering"))
        assert not is_engineering(self._p("product", "Product"))


class TestTitleOverridesRoleSlug:
    """YC's role slug is sometimes wrong - Ooak Data files an "ML engineer" posting under
    Operations - so a technical or product title wins over the slug."""

    def _p(self, role, pretty, title):
        return {"role": role, "pretty_role": pretty, "title": title, "url": "/x"}

    @pytest.mark.parametrize(
        "title",
        [
            "ML engineer",
            "Senior Backend Developer",
            "Staff Architect",
            "Product Manager, Platform",
            "DevOps Lead",
        ],
    )
    def test_technical_title_under_a_wrong_slug_is_still_read(self, title, monkeypatch):
        from scrape_test.yc.jobs import wants_description

        monkeypatch.delenv("SCRAPE_TEST_DESC_ROLES", raising=False)
        assert wants_description(self._p("operations", "Operations", title))

    @pytest.mark.parametrize(
        "title",
        [
            "Chief of Staff",
            "Founding Marketing Lead",
            "Head of People",
            "Account Executive",
            "Tax Expert",
        ],
    )
    def test_non_technical_titles_are_still_skipped(self, title, monkeypatch):
        from scrape_test.yc.jobs import wants_description

        monkeypatch.delenv("SCRAPE_TEST_DESC_ROLES", raising=False)
        assert not wants_description(self._p("operations", "Operations", title))
