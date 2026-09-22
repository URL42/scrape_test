"""SQLite storage. One file, no ORM - the schema is small enough to read in one sitting."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from .config import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id              INTEGER PRIMARY KEY,
    slug            TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    norm_name       TEXT NOT NULL,
    former_names    TEXT NOT NULL DEFAULT '[]',
    website         TEXT,
    batch           TEXT,
    status          TEXT,
    team_size       INTEGER,
    is_hiring       INTEGER NOT NULL DEFAULT 0,
    industry        TEXT,
    subindustry     TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    one_liner       TEXT,
    long_description TEXT,
    all_locations   TEXT,
    year_founded    INTEGER,
    launched_at     INTEGER,
    top_company     INTEGER NOT NULL DEFAULT 0,
    fetched_at      REAL NOT NULL,
    jobs_fetched_at REAL,
    posts_fetched_at REAL
);
CREATE INDEX IF NOT EXISTS idx_companies_norm ON companies(norm_name);
CREATE INDEX IF NOT EXISTS idx_companies_hiring ON companies(is_hiring);

CREATE TABLE IF NOT EXISTS job_postings (
    yc_job_id       INTEGER PRIMARY KEY,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    title           TEXT,
    role            TEXT,
    pretty_role     TEXT,
    skills          TEXT NOT NULL DEFAULT '[]',
    salary_range    TEXT,
    equity_range    TEXT,
    min_experience  TEXT,
    location        TEXT,
    job_type        TEXT,
    visa            TEXT,
    url             TEXT,
    created_at_rel  TEXT,
    last_active_rel TEXT,
    description     TEXT,
    fetched_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_company ON job_postings(company_id);

CREATE TABLE IF NOT EXISTS site_tech (
    company_id      INTEGER PRIMARY KEY REFERENCES companies(id),
    url             TEXT,
    final_url       TEXT,
    status_code     INTEGER,
    detected        TEXT NOT NULL DEFAULT '{}',
    error           TEXT,
    fetched_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS scores (
    company_id      INTEGER PRIMARY KEY REFERENCES companies(id),
    total           REAL NOT NULL,
    confidence      TEXT NOT NULL,
    breakdown       TEXT NOT NULL DEFAULT '[]',
    rules_version   TEXT NOT NULL,
    computed_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS company_posts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      INTEGER NOT NULL REFERENCES companies(id),
    source          TEXT NOT NULL DEFAULT 'own_site',  -- own_site | yc_news | yc_launch
    title           TEXT NOT NULL,
    url             TEXT,
    published       TEXT,
    summary         TEXT,
    fetched_at      REAL NOT NULL,
    UNIQUE(company_id, source, url)
);
CREATE INDEX IF NOT EXISTS idx_posts_company ON company_posts(company_id);

CREATE TABLE IF NOT EXISTS briefs (
    company_id      INTEGER PRIMARY KEY REFERENCES companies(id),
    input_hash      TEXT NOT NULL,
    model           TEXT NOT NULL,
    payload         TEXT NOT NULL,
    created_at      REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS prospects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    domain          TEXT,
    source          TEXT NOT NULL,
    external_id     TEXT,
    batch           TEXT,
    team_size       INTEGER,
    one_liner       TEXT,
    in_profile      INTEGER NOT NULL DEFAULT 0,
    reject_reason   TEXT,
    board_provider  TEXT,
    board_token     TEXT,
    board_found_via TEXT,
    open_roles      INTEGER,
    atlassian       TEXT NOT NULL DEFAULT '[]',
    competitors     TEXT NOT NULL DEFAULT '[]',
    score           REAL,
    verdict         TEXT,
    reasons         TEXT NOT NULL DEFAULT '[]',
    error           TEXT,
    scanned_at      REAL,
    UNIQUE(source, name, domain)
);
CREATE INDEX IF NOT EXISTS idx_prospects_score ON prospects(score DESC);

CREATE TABLE IF NOT EXISTS scan_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    status          TEXT NOT NULL,          -- running | done | failed | cancelled
    total           INTEGER NOT NULL DEFAULT 0,
    done            INTEGER NOT NULL DEFAULT 0,
    found           INTEGER NOT NULL DEFAULT 0,
    current         TEXT,
    error           TEXT,
    started_at      REAL NOT NULL,
    finished_at     REAL
);

CREATE TABLE IF NOT EXISTS digest_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name     TEXT NOT NULL,
    source_kind     TEXT NOT NULL,
    source_region   TEXT,
    title           TEXT NOT NULL,
    url             TEXT NOT NULL UNIQUE,
    published       TEXT,
    summary         TEXT,
    tags            TEXT NOT NULL DEFAULT '[]',
    company         TEXT,
    amount          TEXT,
    round_stage     TEXT,
    relevance       INTEGER NOT NULL DEFAULT 0,
    fetched_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_digest_rel ON digest_items(relevance DESC);
CREATE INDEX IF NOT EXISTS idx_digest_company ON digest_items(company);

CREATE TABLE IF NOT EXISTS meta (
    key             TEXT PRIMARY KEY,
    value           TEXT NOT NULL,
    updated_at      REAL NOT NULL
);
"""


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Additive migrations for databases created by an earlier version."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(companies)")}
    if "jobs_fetched_at" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN jobs_fetched_at REAL")
    if "posts_fetched_at" not in cols:
        conn.execute("ALTER TABLE companies ADD COLUMN posts_fetched_at REAL")
    job_cols = {r["name"] for r in conn.execute("PRAGMA table_info(job_postings)")}
    if "description" not in job_cols:
        conn.execute("ALTER TABLE job_postings ADD COLUMN description TEXT")
    post_cols = {r["name"] for r in conn.execute("PRAGMA table_info(company_posts)")}
    if post_cols and "source" not in post_cols:
        # The UNIQUE constraint changed, so rebuild rather than ALTER.
        conn.execute("DROP TABLE company_posts")
        conn.executescript(SCHEMA)


def init_db() -> None:
    # sqlite3's own context manager commits but does NOT close, so use session().
    with session() as conn:
        conn.executescript(SCHEMA)
        _migrate(conn)


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    """Own a connection for the block. Commits on clean exit, rolls back on error.

    Individual store_* helpers also commit, so this is the outer safety net rather than
    the only transaction boundary.
    """
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def set_meta(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta(key, value, updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value), time.time()),
    )


def get_meta(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def is_fresh(fetched_at: float | None, ttl: float) -> bool:
    return fetched_at is not None and (time.time() - fetched_at) < ttl
