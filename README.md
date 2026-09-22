# Prospect Scope

A test scraper with a small web front end. One search box takes a **company name** plus
**free-text context** and returns two things at once:

1. **Current events** for that company, from a switchable news backend.
2. **Y Combinator intelligence** for that company — tech stack, hiring signals, publicly
   detectable tooling, and a tunable Atlassian-fit score.

Both halves run concurrently against a single lookup.

## Install

Tested from a clean clone on macOS. Should work anywhere Python 3.12+ runs.

**Prerequisites:** git, and Python 3.12 or newer. `uv` manages the virtualenv and
dependencies - install it if you do not have it:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

(Windows PowerShell: `powershell -c "irm https://astral.sh/uv/install.ps1 | iex"`. If your
machine blocks that, `pipx install uv` or `pip install uv` work too.)

**Then:**

```bash
git clone https://github.com/URL42/scrape_test.git
cd scrape_test
cp .env.example .env
uv sync
uv run scrape-test serve
```

Open http://127.0.0.1:8000. `uv sync` creates the virtualenv and installs from the
committed `uv.lock`, so you get identical versions. The YC directory (~6,200 companies) is
pulled on first start - one request, about a second.

**It runs without an API key.** Everything works except the "so what" brief, which needs
`DEEPSEEK_API_KEY` in `.env` (get one at platform.deepseek.com). The UI shows the brief
button disabled and the rest is unaffected.

### First run, in order

```bash
uv run scrape-test serve
```

1. Press **Refresh feed** - pulls 32 VC and press feeds, ~15 seconds.
2. Press **Run scan** - sweeps the ICP universe, ~5 minutes, progress shown live.
3. Search a company, or switch to **Idea** / **Investor** mode.

Or entirely from the command line:

```bash
uv run scrape-test scan
uv run scrape-test lookup "Stripe"
uv run scrape-test brief "Rollstack"
uv run scrape-test rescore
```

### Running it on a work machine

Things worth knowing before you port this somewhere managed:

**Everything is local.** State lives in `data/scrape_test.db` (SQLite, gitignored).
Nothing is uploaded anywhere. The only outbound call carrying your data is the brief,
which sends the scraped company facts to DeepSeek - skip the API key and even that does
not happen.

**Proxies work unaffected.** The HTTP client honours `HTTP_PROXY`, `HTTPS_PROXY` and
`NO_PROXY` from the environment. Set them as usual.

**Outbound domains.** Corporate filtering may block some of these. The tool degrades
per-source rather than failing, so a blocked feed costs you that feed and nothing else:

| Purpose | Domains |
|---|---|
| YC | `yc-oss.github.io`, `www.ycombinator.com` |
| Job boards | `boards-api.greenhouse.io`, `api.ashbyhq.com`, `api.lever.co` |
| News | `news.google.com`, `api.gdeltproject.org`, `hn.algolia.com` |
| Brief | `api.deepseek.com` |
| Digest | 32 VC and press domains - see [`whatsnew/sources.py`](src/scrape_test/whatsnew/sources.py) |

Plus each scanned company's own website, which is the point.

**Cost.** Scraping is free. Only the brief costs money - roughly 0.4-0.9c per generation
on `deepseek-v4-pro`, cached so re-opening one is free. A full scan spends nothing.

**Port is stated explicitly** (`--port 9000`) if 8000 is taken.

### Verify the install

```bash
uv run pytest -q          # 235 tests, no network needed
uv run ruff check src tests
uv run mypy
```

## Three ways in

The search bar has three modes.

**Company** — the original lookup: YC profile, technographics, news, brief.

**Idea** — companies matching a concept, ranked by BM25 over their own descriptions via
SQLite's built-in FTS5 (no new dependency). *"AI agents for customer support"* returns
Parahelp, FirstSupport.ai, Duckie, Percept.AI; *"developer tools for code review"* returns
PullRequest, Axolo, cubic. Each row shows whether the company has already been scanned and
what its lead product is.

**Investor** — one fund's recent portfolio, newest first, straight from the digest:

```
Antioch      14d   "Introducing Antioch: The Simulation Platform for Physical AI"
Oak          69d   "Introducing Oak: The AI-Native Identity Operating System"
Axiamatic   $54M   "Introducing Axiamatic: AI for Enterprise Transformation"
```

A fund announcing its own new fund is excluded - *"Introducing Greylock 18"* is a fund
raise, not a portfolio company.

## What's new: VC deal flow and tech press

```bash
curl -X POST localhost:8000/api/digest/refresh   # or press "Refresh feed"
```

Pulls **32 curated sources** - 17 US VCs, 9 EMEA VCs, 6 tech press - in about 15 seconds,
classifies every post (`funding`, `ai-native`, `ai-sdlc`, `m&a`, `launch`, `leadership`)
and **parses the company out of funding announcements**:

```
Axiamatic   $54M   Greylock   "Introducing Axiamatic: AI for Enterprise Transformation"
Cylake      $45M   Greylock   "Introducing Cylake: AI-Native Cybersecurity"
Preview            Sequoia    "Partnering with Preview: Lights, Inference, Action"
```

A recent raise is the strongest timing signal there is, and it arrives days after it
happens. The list is in [`whatsnew/sources.py`](src/scrape_test/whatsnew/sources.py) -
edit it for your territory. `KNOWN_GAPS` records the firms whose newsrooms are
JavaScript-only (a16z, Index, Atomico, General Catalyst), so the coverage hole is visible
rather than silent.

## Scoring: fit, timing, and which product to lead with

One blended number could not answer "who should I call this week, and about what". It is
now three things.

**Fit, per product** ([`scoring/products.py`](src/scrape_test/scoring/products.py)) -
because the evidence differs. Linear plus three engineering roles plus sprint language is
a **Jira** conversation; Notion plus SOC 2 work is **Confluence**; async-first hiring
across regions is **Loom**; five tools and an ML team is **Rovo**. The lead product falls
out of the data instead of being guessed.

**Timing** ([`scoring/timing.py`](src/scrape_test/scoring/timing.py)) - the "right now"
axis, each signal decaying with age:

| Signal | Weight | Half-life |
|---|---|---|
| Just funded | 40 | 120d |
| First-of-role hire (*"1st Product Manager"*) | 25 | 180d |
| Tool migration (*"migrating from Jira"*) | 25 | 90d |
| Leadership hire (VP Eng, CTO) | 15 | 120d |
| Hiring surge, compliance push, going distributed | 10-15 | 90-180d |

A company hiring its first PM is saying out loud that coordination became somebody's job.

**The digest and the scan are joined.** A scanned company is matched by normalised name
against funding announcements, so "Axiamatic" in a Greylock post lines up with Axiamatic
in the scan and its announcement date feeds the timing score. Newly funded companies are
also candidates in their own right - the freshest leads available, arriving days after the
round.

**Priority = √(fit × timing)**, multiplicative on purpose. High fit with no trigger is a
nurture; a strong trigger at a company with no problem is noise. Only both together mean
call them this week - the same company scores 79 funded two weeks ago and 66 funded eight
months ago.

## Prospect scan: finding the companies, not just checking one

Looking companies up one at a time is the wrong shape for prospecting. The scan sweeps a
whole universe, reads every company's job board, and ranks what comes back.

```bash
uv run scrape-test scan            # or press "Run scan" in the UI
```

**The ICP** lives in [`prospects/icp.py`](src/scrape_test/prospects/icp.py), tunable the
same way the scoring weights are: AI-first, batch 2022 or later, team 10-200, currently
hiring. Against the YC corpus that cuts 6,237 companies to about 225.

**Two universes.** YC's directory has the metadata (batch, team size, tags) but only
covers YC alumni. Hacker News' monthly "Who is hiring" threads cover the wider market
through HN's public Algolia API - roughly 190 usable companies per thread, about 60% of
them AI. HN carries no headcount, so those candidates are sized by their open-role count
instead.

**The ranking rests on positive evidence, not absence.** "Not yet an Atlassian customer"
is the hardest thing to measure and absence proves nothing - a company that never mentions
Jira might simply not have said. So a named competitor is the top signal: a company saying
*"issue tracking with Linear"* has proven it buys tooling, has the coordination problem,
and is not yours. A company naming no tooling at all is graded **no signal**, not
prospect - otherwise thousands of unknowns bury the real leads. A stated Atlassian product
is a hard stop: that is someone else's expansion conversation.

**Coverage needed a fallback.** Only about a third of small YC companies run Greenhouse,
Ashby or Lever - the rest hire through YC's own board. When no ATS is found for a YC
company, the scan reads its YC postings instead, which took a sample from 3 scannable out
of 10 to 20 out of 20.

## Technographics from any company's job board

The YC directory is a narrow window. Every funded company publishes its whole job corpus
through an applicant tracking system, and the major ones expose **public, documented
endpoints built for embedding job boards** - no credentials, no bot-blocking:

| Company | Roles on YC | Roles via ATS |
|---|---|---|
| Stripe | 3 | **666** (Greenhouse) |
| Vanta | 1 | **93** (Ashby) |
| Ramp | not YC-listed | **148** (Ashby) |
| Discord | not YC-listed | **46** (Greenhouse) |

```bash
curl "localhost:8000/api/technographics?domain=discord.com&company=Discord"
```

Or type a domain into the search box. Leave it blank and a YC company's own website is
used, so the common case needs no extra typing.

Discovery reads the careers page for an embedded board token - Vanta's exposes
`api.ashbyhq.com/posting-api/job-board/vanta` - and falls back to guessing the token from
the domain, which is how Stripe, Ramp and Discord resolve. Greenhouse, Ashby and Lever are
supported; adding another is one module in [`ats/`](src/scrape_test/ats/).

## Tooling named in job descriptions

The strongest signal here, and the reason job *detail* pages are fetched at all. The
structured `skills` array is often empty while the description says outright what the
company runs. Rollstack's AI Software Engineer posting is the canonical case:

> "Issue tracking with **Linear**."

`skills: []` on that posting. A scraper reading only the array sees nothing.

Detection covers what Atlassian sells against - issue trackers, wikis, source hosts, CI,
service desks, on-call, whiteboards and knowledge search - graded **stated** (usage or
requirement phrasing nearby) or **mentioned**, and every hit keeps the sentence it came
from so you can judge it yourself. The catalog is in
[`yc/tooling.py`](src/scrape_test/yc/tooling.py) and is a floor, not a ceiling: the brief
is explicitly asked to flag products the catalog misses.

**Precision is the hard part**, and it got harder at ATS scale - 666 postings will bury a
real signal in noise. Four classes of false positive are handled:

- **Ordinary English.** *Linear* (linear algebra), *Monday* (the weekday), *Notion* (an
  idea), *Height*, *Guru*, *Harness*, *Shortcut*. These need nearby tooling context and are
  checked against a false-friend list; a test asserts ten such sentences detect nothing.
- **The company's own name.** "Linear" appeared in 25 of Linear's own postings and
  "Airtable" in 15 of Airtable's - boilerplate, now suppressed.
- **Lists of examples.** *"Experience with project management tools such as Asana,
  Monday.com, or Jira"* means the role needs *a* tracker, not that this company runs Jira.
  Demoted to `mentioned`. Stripe's ten Jira hits are all of this kind.
- **Integration lists.** *"integrates with Jira"* is their product connecting to it.

A **migration is not an enumeration**: *"migrating from Jira to Linear"* names both tools
deliberately, and is the single most interesting sentence a prospect can write, so both
stay `stated`.

Scoring trusts this over the website: a posting naming a competitor scores higher than a
website hit, because the former is the company describing its own workflow and the latter
is an inference from marketing HTML.

## YC's own news and launches

The company *profile* page carries the same `jobPostings` as `/jobs`, **plus** `newsItems`
(a YC-curated press list) and any Launch YC post. Reading the profile instead of `/jobs`
costs exactly the same one request and yields strictly more, so that is what the scraper
now fetches.

The curated list is far less noisy than a news search - for Rollstack it is the $11M
Series A announcement, the TechCrunch write-up and a Demo Day round-up, with no
disambiguation problem. Launch YC bodies are company-written prose, so they are mined for
tooling alongside job descriptions.

## The company's own news

Google News is thin on small startups, but they still announce funding and launches on
their own site - and engineering posts often name the stack. Four strategies, cheapest
first: a feed declared in the homepage, common feed paths, **sitemap.xml**, then scraping
the blog page.

The sitemap earns its place. Rollstack and Vanta publish no feed and render their blogs in
JavaScript, so both a feed lookup and a static scrape come back empty - but their sitemaps
list every post with a `lastmod` date. Page scraping is deliberately last: a naive scrape
returns megamenu items like "Financial Services" instead of articles, so it ignores
nav/header/footer chrome and requires links to live under the section being read.

## Confidence means "how informative", not "how much"

The first version of the confidence grade asked whether job and website data *existed*.
That read **high** for a company with two year-old postings and a Cloudflare hit - inputs
that support almost no conclusion. The brief itself caught this, writing of Vanta: *"Score's
'confidence high' is misleading because inputs are thin."* It was right.

Confidence is now earned by evidence that could actually change the call - tooling named
outright (+3), descriptions actually read (+2), a posting active in the last 3 months (+1),
website tooling that bears on coordination (+1) - and penalised when every posting is over
six months old (-2), since stale evidence alone must not reach "high". Generic hosting and
analytics detections earn nothing.

Crucially the grade now **explains itself**: `confidence_reasons` comes back with the score
and is shown in the UI, so a misleading label can be spotted rather than trusted. Vanta now
reads **low**, citing stale postings and irrelevant website tooling.

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

Dependencies run one way, and the two packages that matter most depend on nothing:

```
src/scrape_test/
  scoring/      rules · timing · products · score      → imports nothing internal
  llm/          deepseek · claude · base               → imports nothing internal
  http.py       async client: per-host throttle, retries, proxy-aware
  db.py         SQLite schema, migrations, session
  config.py     throttles, TTLs, endpoints

  extract/      tooling (text → products) · fingerprint (site → tech)
  feeds/        discovery chain: declared feed → paths → sitemap → scrape
  ats/          greenhouse · ashby · lever · discover
  news/         google_news · gdelt (switchable behind one protocol)

  yc/           directory · jobs                       ← YC-specific only
  whatsnew/     sources · classify · dates · digest
  prospects/    icp (tune this) · sources · scan
  search.py     idea (FTS5) and investor modes
  brief.py      the "so what" brief
  rescore.py    score persistence and re-ranking

  routes/       company · digest · scan · search · deps
  api.py        app wiring only (75 lines)
  cli.py        refresh · lookup · scan · brief · rescore · serve
web/            index.html + app.js + style.css (vanilla, no build step)
```

`scoring/` and `llm/` import nothing from the rest of the project. That is the property
worth protecting: it is why `rescore` runs in under a second, why the rules are testable
without a network, and why changing a weight is safe.

## Development

```bash
uv run pytest          # 227 tests (unit, async, ATS, tooling precision, LLM, env)
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
- The brief's tests inject a fake model client. They cover prompt assembly, schema
  rendering, validation, the DeepSeek retry loop, caching and the failure paths - but
  they do not exercise a live API call against either provider.
