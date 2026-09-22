"""Three ways in: by company, by idea, or by investor.

Company lookup already existed. The other two answer different questions a rep actually
asks - "who is doing anything in this space?" and "what has this fund been buying?" -
and both run over data already collected rather than new scraping.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from .whatsnew.dates import age_days

# FTS5 ships with SQLite, so idea search gets BM25 ranking with no new dependency.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS company_fts USING fts5(
    name, one_liner, long_description, tags, slug UNINDEXED, tokenize='porter'
);
"""

_FTS_SPECIAL = re.compile(r'["\'*^():-]')


def ensure_index(conn: sqlite3.Connection, *, rebuild: bool = False) -> int:
    """Build the idea-search index over the company corpus."""
    conn.executescript(FTS_SCHEMA)
    count = conn.execute("SELECT COUNT(*) AS n FROM company_fts").fetchone()["n"]
    if count and not rebuild:
        return count
    conn.execute("DELETE FROM company_fts")
    rows = conn.execute(
        "SELECT slug, name, one_liner, long_description, tags FROM companies"
    ).fetchall()
    conn.executemany(
        "INSERT INTO company_fts (name, one_liner, long_description, tags, slug) "
        "VALUES (?,?,?,?,?)",
        [
            (
                r["name"] or "",
                r["one_liner"] or "",
                (r["long_description"] or "")[:4000],
                " ".join(json.loads(r["tags"] or "[]")),
                r["slug"],
            )
            for r in rows
        ],
    )
    conn.commit()
    return len(rows)


def _fts_query(text: str) -> str:
    """Turn free text into a safe FTS5 query: every word optional, ranked by BM25."""
    words = [w for w in _FTS_SPECIAL.sub(" ", text).split() if len(w) > 1]
    return " OR ".join(f'"{w}"' for w in words[:12])


def idea_search(conn: sqlite3.Connection, text: str, limit: int = 40) -> list[dict[str, Any]]:
    """Companies whose description matches an idea, best match first."""
    query = _fts_query(text)
    if not query:
        return []
    ensure_index(conn)
    rows = conn.execute(
        "SELECT f.slug, f.name, f.one_liner, bm25(company_fts) AS rank "
        "FROM company_fts f WHERE company_fts MATCH ? ORDER BY rank LIMIT ?",
        (query, limit),
    ).fetchall()

    out = []
    for r in rows:
        company = conn.execute(
            "SELECT slug, name, batch, status, team_size, is_hiring, website, tags, "
            "one_liner FROM companies WHERE slug = ?",
            (r["slug"],),
        ).fetchone()
        if not company:
            continue
        d = dict(company)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["is_hiring"] = bool(d.get("is_hiring"))
        # bm25 returns a negative score where more negative is a better match.
        d["match"] = round(-float(r["rank"]), 2)
        prospect = conn.execute(
            "SELECT score, priority, verdict, lead_product FROM prospects "
            "WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (company["name"],),
        ).fetchone()
        d["prospect"] = dict(prospect) if prospect else None
        out.append(d)
    return out


def list_investors(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Investors we have digest coverage for, with how much each has published."""
    rows = conn.execute(
        "SELECT source_name, source_kind, source_region, COUNT(*) AS posts, "
        "SUM(CASE WHEN company IS NOT NULL THEN 1 ELSE 0 END) AS named "
        "FROM digest_items WHERE source_kind = 'vc' "
        "GROUP BY source_name ORDER BY named DESC, posts DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def investor_search(
    conn: sqlite3.Connection, name: str, limit: int = 60
) -> dict[str, Any]:
    """Everything one investor has announced, and the companies they named.

    Their recent portfolio additions are the point: each is a company that raised days or
    weeks ago, which is the strongest timing signal this tool has.
    """
    rows = conn.execute(
        "SELECT * FROM digest_items WHERE source_kind = 'vc' "
        "AND LOWER(source_name) LIKE LOWER(?) ORDER BY relevance DESC LIMIT ?",
        (f"%{name}%", limit),
    ).fetchall()

    posts: list[dict[str, Any]] = []
    portfolio: dict[str, dict[str, Any]] = {}
    for r in rows:
        d = dict(r)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["age_days"] = age_days(d.get("published"))
        posts.append(d)
        company = d.get("company")
        if company and "funding" in d["tags"]:
            key = company.lower()
            prior = portfolio.get(key)
            if prior is None or (d["age_days"] or 1e9) < (prior["age_days"] or 1e9):
                portfolio[key] = {
                    "company": company, "amount": d.get("amount"),
                    "round_stage": d.get("round_stage"), "title": d.get("title"),
                    "url": d.get("url"), "published": d.get("published"),
                    "age_days": d["age_days"], "tags": d["tags"],
                }

    for entry in portfolio.values():
        scanned = conn.execute(
            "SELECT score, priority, verdict, lead_product, open_roles FROM prospects "
            "WHERE LOWER(name) = LOWER(?) LIMIT 1",
            (entry["company"],),
        ).fetchone()
        entry["prospect"] = dict(scanned) if scanned else None

    ranked = sorted(portfolio.values(), key=lambda e: (e["age_days"] is None, e["age_days"]))
    return {"investor": name, "posts": posts, "portfolio": ranked}
