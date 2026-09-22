"""Idea and investor search."""

from __future__ import annotations

import json
import time

import pytest

from scrape_test import db as db_mod
from scrape_test.search import ensure_index, idea_search, investor_search, list_investors


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "s.db")
    db_mod.init_db()
    rows = [
        (
            1,
            "parahelp",
            "Parahelp",
            "parahelp",
            "AI support agent for customer service",
            "Build an AI support agent that resolves tickets",
            '["AI","Customer Support"]',
        ),
        (
            2,
            "cubic",
            "Cubic",
            "cubic",
            "AI-powered code review platform",
            "Automated code review for engineering teams",
            '["AI","Developer Tools"]',
        ),
        (
            3,
            "farmly",
            "Farmly",
            "farmly",
            "Marketplace for organic produce",
            "Connecting farms to restaurants",
            '["Marketplace"]',
        ),
    ]
    with db_mod.session() as conn:
        conn.executemany(
            "INSERT INTO companies (id, slug, name, norm_name, one_liner, "
            "long_description, tags, status, batch, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,'Active','Winter 2024',?)",
            [(*r, time.time()) for r in rows],
        )
        conn.executemany(
            "INSERT INTO digest_items (source_name, source_kind, source_region, title, "
            "url, published, summary, tags, company, amount, relevance, fetched_at) "
            "VALUES (?,'vc','us',?,?,?,'',?,?,?,?,?)",
            [
                (
                    "Greylock",
                    "Introducing Antioch: Simulation for Physical AI",
                    "https://g.test/1",
                    "Tue, 08 Sep 2026 15:11:00 GMT",
                    json.dumps(["funding", "ai-native"]),
                    "Antioch",
                    None,
                    90,
                    time.time(),
                ),
                (
                    "Greylock",
                    "Introducing Greylock 18",
                    "https://g.test/2",
                    "Tue, 05 May 2026 15:11:00 GMT",
                    json.dumps(["funding"]),
                    None,
                    "$1.5 billion",
                    40,
                    time.time(),
                ),
                (
                    "Sequoia",
                    "Partnering with Preview",
                    "https://s.test/1",
                    "Wed, 10 Sep 2026 00:00:00 -0000",
                    json.dumps(["funding", "ai-native"]),
                    "Preview",
                    None,
                    85,
                    time.time(),
                ),
            ],
        )
    return tmp_path


class TestIdeaSearch:
    def test_finds_by_concept_not_exact_name(self, corpus):
        with db_mod.session() as conn:
            ensure_index(conn, rebuild=True)
            hits = idea_search(conn, "AI agents for customer support")
        assert hits and hits[0]["name"] == "Parahelp"

    def test_ranks_the_relevant_company_first(self, corpus):
        with db_mod.session() as conn:
            ensure_index(conn, rebuild=True)
            hits = idea_search(conn, "code review for engineers")
        assert hits[0]["name"] == "Cubic"

    def test_unrelated_query_does_not_return_everything(self, corpus):
        with db_mod.session() as conn:
            ensure_index(conn, rebuild=True)
            names = [h["name"] for h in idea_search(conn, "organic produce farms")]
        assert names[0] == "Farmly"

    def test_punctuation_does_not_break_the_query(self, corpus):
        """FTS5 treats quotes and asterisks as syntax; free text must be sanitised."""
        with db_mod.session() as conn:
            ensure_index(conn, rebuild=True)
            assert idea_search(conn, 'AI "agents" (support)* -- now') is not None

    def test_empty_query_returns_nothing(self, corpus):
        with db_mod.session() as conn:
            assert idea_search(conn, "  ") == []


class TestInvestorSearch:
    def test_lists_investors_with_coverage(self, corpus):
        with db_mod.session() as conn:
            names = [i["source_name"] for i in list_investors(conn)]
        assert "Greylock" in names and "Sequoia" in names

    def test_returns_portfolio_newest_first(self, corpus):
        with db_mod.session() as conn:
            r = investor_search(conn, "Greylock")
        assert [e["company"] for e in r["portfolio"]] == ["Antioch"]

    def test_excludes_the_fund_announcing_its_own_fund(self, corpus):
        """'Introducing Greylock 18' is a fund raise, not a portfolio company."""
        with db_mod.session() as conn:
            r = investor_search(conn, "Greylock")
        assert all(e["company"] != "Greylock" for e in r["portfolio"])

    def test_partial_name_matches(self, corpus):
        with db_mod.session() as conn:
            assert investor_search(conn, "sequo")["portfolio"]

    def test_unknown_investor_is_empty_not_an_error(self, corpus):
        with db_mod.session() as conn:
            assert investor_search(conn, "Nonexistent Capital")["portfolio"] == []
