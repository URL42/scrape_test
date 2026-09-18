"""Shared test fixtures and helpers."""

from __future__ import annotations

import time

import pytest

from scrape_test import db as db_mod
from scrape_test.brief import Brief


def sample_brief(**over) -> Brief:
    """A valid Brief. Override any field to exercise validation failures."""
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


@pytest.fixture
def brief_factory():
    return sample_brief


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """A throwaway database with one company (id=1, 'Acme')."""
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "t.db")
    db_mod.init_db()
    with db_mod.session() as conn:
        conn.execute(
            "INSERT INTO companies (id, slug, name, norm_name, website, batch, status, "
            "team_size, is_hiring, fetched_at) VALUES (1,'acme','Acme','acme',"
            "'https://acme.test','Fall 2025','Active',12,1,?)",
            (time.time(),),
        )
    return tmp_path
