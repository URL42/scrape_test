# Prospect Scope

A test scraper with a small web front end. One search box takes a **company name** plus
**free-text context** and returns two things at once:

1. **Current events** for that company, from a switchable news backend.
2. **Y Combinator intelligence** for that company — tech stack, hiring signals, publicly
   detectable tooling, and a tunable Atlassian-fit score.

Both halves run concurrently against a single lookup.

## Quick start

```bash
uv sync && uv run scrape-test serve
```

Then open http://127.0.0.1:8000. The YC directory (~6,200 companies) is pulled on first
start; it is a single request and takes under a second.

Command line, if you prefer it:

```bash
uv run scrape-test lookup "MSPilot"
```

## How it gets the data

No headless browser is involved. Every target here exposes structured data directly, and
driving a stealth browser would be slower and more brittle for nothing.

| Stage | Source | Cost | Yields |
|---|---|---|---|
| Directory | [yc-oss mirror](https://yc-oss.github.io/api) of YC's public directory | 1 request, prefetched | batch, team size, hiring flag, tags, website |
| Jobs | `ycombinator.com/companies/<slug>/jobs` | 1 request, cached 12h | curated `skills[]`, role, salary, equity, seniority, activity |
| Website | the company's own homepage | 1 request, cached 7d | publicly detectable tooling |
| News | Google News RSS **or** GDELT | 1 request per search | recent coverage |

The jobs page is an [Inertia.js](https://inertiajs.com) app: the server embeds the entire
page payload in a `data-page` attribute. One plain GET and an HTML-unescape yields
structured JSON — including the skills array YC curates per posting, which is a far
cleaner stack signal than parsing prose out of job descriptions.

YC's `robots.txt` disallows `/companies?*` (the faceted search UI). We never touch it; the
only path used is `/companies/<slug>/jobs`, which is allowed. Requests are throttled
per-host, and GDELT's documented one-request-per-five-seconds limit is respected.

## Choosing a news backend

Both ship, and the dropdown switches between them:

- **Google News RSS** — best coverage, especially for small startups. Note its feed terms
  state it is provided for *personal, non-commercial* use. Fine for a research tool; worth
  revisiting before it feeds a commercial sales workflow.
- **GDELT** — free, no key, no personal-use restriction, richer metadata. Rate-limited to
  one request per five seconds and noticeably thinner on small startups.

Adding a third is a matter of implementing `NewsSource` in `src/scrape_test/news/` and
registering it in `SOURCES`; the UI picks it up automatically.

## Tuning the score

All the arguable judgement lives in one file: **`src/scrape_test/scoring/rules.py`**.

```python
WEIGHTS = {
    "eng_hiring_volume":    20.0,   # open engineering roles
    "team_size_threshold":  15.0,   # ~10-40 is where coordination breaks down
    "competing_tools":      15.0,   # Linear/Notion/Asana = displaceable
    "stack_complexity":     10.0,   # polyglot stack = integration surface
    "job_freshness":        10.0,   # postings active recently = live budget
    "recent_batch":         10.0,   # newer batch = greenfield tooling
    "already_atlassian":   -25.0,   # existing customer: expansion, not net-new
}
```

Nothing in that file touches the network or the database, so re-scoring everything you
have already collected is instant:

```bash
uv run scrape-test rescore
```

Edit a weight, run that, refresh the page. `POST /api/rescore` does the same thing over
HTTP if you would rather not leave the browser. Bump `RULES_VERSION` when you change the
logic so stored scores stay comparable. Every score in the UI expands into a per-signal
breakdown showing exactly which facts produced which points.

## What the website fingerprint can and cannot tell you

It reads the **public surface only** — a linked status page, docs site, careers portal,
support widget, or a script the page actually loads. A company running Jira purely
internally is invisible to it. Absence of a signal is not evidence of absence.

Detections are labelled by confidence, and the distinction is load-bearing:

- **strong** — the asset is genuinely served (a `<script src>`, a response header) or the
  URL is a first-party tenant such as `acme.atlassian.net`.
- **weak** — the product was merely mentioned somewhere in the markup.

Links that live under marketplace or comparison paths (`/integrations`, `/apps/`,
`/compare`, …) are discarded entirely. Zapier's homepage links
`/apps/jira-software-cloud/integrations` because it *integrates with* Jira, and counting
that as Atlassian usage would be wrong — and would wrongly subtract 25 points. Hover any
chip in the UI to see the exact evidence that produced it.

## Layout

```
src/scrape_test/
  config.py     tunables: throttles, TTLs, endpoints
  db.py         SQLite schema and helpers
  http.py       async client: per-host throttle, retries, optional body cap
  news/         base.py (protocol) + google_news.py + gdelt.py
  yc/           directory.py, jobs.py, fingerprint.py
  scoring/      rules.py (tune this) + score.py (applies it)
  api.py        FastAPI
  cli.py        refresh · lookup · rescore · serve
web/            index.html + app.js + style.css (vanilla, no build step)
```

## Development

```bash
uv run pytest          # 48 tests (unit + async cache/retry coverage)
uv run ruff check src tests
uv run mypy
```

## Known limitations

- The YC half only resolves companies **in the YC directory**. Any other company returns
  news only, with a "did you mean" list for near-miss spellings.
- `skills[]` exists only on postings for engineering roles. A company not hiring engineers
  yields no stack data, and the score is marked **low confidence** rather than quietly
  scored as zero.
- Posting activity (`lastActive`) is absent on some YC postings, which degrades the
  freshness signal to zero for those companies. (Wordy forms like "a month ago" *are*
  parsed - that was a separate bug, now fixed.)
- Scoring weights are a starting hypothesis, not a validated model. Tune them.
