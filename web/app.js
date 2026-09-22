"use strict";

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// Escaping makes a URL safe to place in an attribute, but says nothing about its scheme.
// These URLs come from news feeds and YC's `website` field, so a javascript: or data:
// value would survive esc() intact and execute on click. Allow only real web links.
function safeUrl(raw) {
  if (!raw) return null;
  try {
    const u = new URL(String(raw), window.location.origin);
    return (u.protocol === "http:" || u.protocol === "https:") ? u.href : null;
  } catch {
    return null;
  }
}

let jobsData = [];
let currentCompany = null;
let briefAvailable = false;
let searchMode = "company";
let sortState = { key: "pretty_role", dir: 1 };

async function loadSources() {
  try {
    const r = await fetch("/api/sources");
    const d = await r.json();
    $("source").innerHTML = d.news_sources
      .map((s) => `<option value="${esc(s.key)}" title="${esc(s.note)}">${esc(s.label)}</option>`)
      .join("");
    const n = d.directory.companies || 0;
    const when = d.directory.fetched_at
      ? new Date(d.directory.fetched_at * 1000).toLocaleString()
      : "never";
    briefAvailable = !!(d.brief && d.brief.available);
    const briefNote = briefAvailable
      ? `brief: ${d.brief.provider} ${d.brief.model}`
      : `brief: no ${(d.brief && d.brief.provider) || ""} API key`.replace(/\s+/g, " ");
    $("dirmeta").textContent =
      `${n.toLocaleString()} YC companies · refreshed ${when} · rules ${d.rules_version} · ${briefNote}`;
  } catch {
    $("dirmeta").textContent = "could not reach the API";
  }
}

function setStatus(msg, isError) {
  const el = $("status");
  if (!msg) { el.hidden = true; return; }
  el.hidden = false;
  el.textContent = msg;
  el.className = "status" + (isError ? " error" : "");
}

function renderNews(news) {
  const el = $("news");
  $("newssub").textContent = news.label ? `via ${news.label}` : "";
  if (news.error) {
    el.innerHTML = `<p class="empty">${esc(news.error)}</p>`;
    return;
  }
  if (!news.articles.length) {
    el.innerHTML = `<p class="empty">No articles found for this query.</p>`;
    return;
  }
  const q = news.query ? `<p class="notice">Query sent: <code>${esc(news.query)}</code></p>` : "";
  el.innerHTML = q + news.articles.map((a) => {
    const href = safeUrl(a.url);
    return `
    <div class="article">
      ${href ? `<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>`
             : `<span>${esc(a.title)}</span>`}
      <div class="src">${esc(a.source)}${a.published ? " · " + esc(a.published) : ""}</div>
      ${a.summary ? `<p>${esc(a.summary)}</p>` : ""}
    </div>`;
  }).join("");
}

function digestRow(i) {
  const href = safeUrl(i.url);
  const tags = (i.tags || []).map((t) => `<span class="chip ${t === "funding" ? "strong" : ""}">${esc(t)}</span>`).join(" ");
  const when = (i.published || "").slice(0, 16);
  return `<tr>
    <td>${i.company ? `<span class="pname">${esc(i.company)}</span>` : "<span class='toolsrc'>—</span>"}
        ${i.amount ? `<div class="toolsrc">${esc(i.amount)}${i.round_stage ? " · " + esc(i.round_stage) : ""}</div>` : ""}</td>
    <td>${href ? `<a class="ext" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(i.title)}</a>` : esc(i.title)}
        <div class="toolsrc">${esc(i.source_name)}${when ? " · " + esc(when) : ""}</div></td>
    <td>${tags}</td>
  </tr>`;
}

function renderDigest(d) {
  const items = d.items || [];
  if (!items.length) {
    $("digest").innerHTML = `<p class="empty">Nothing stored yet. Press "Refresh feed".</p>`;
    return;
  }
  const funded = (d.funded_companies || []).length;
  $("digest").innerHTML =
    `<div class="chips" style="margin-bottom:10px">
       <span class="chip">${items.length} posts</span>
       <span class="chip strong">${funded} funded companies named</span>
       ${Object.entries(d.counts || {}).map(([k, v]) => `<span class="chip">${esc(k)}<b>${v}</b></span>`).join("")}
     </div>
     <table class="ptable"><thead><tr>
       <th>Company</th><th>Announcement</th><th>Tags</th>
     </tr></thead><tbody>${items.map(digestRow).join("")}</tbody></table>
     <p class="notice">Company names are parsed from announcement titles. ${d.known_gaps.length}
     firms render their newsroom in JavaScript and are not covered
     (${esc(d.known_gaps.slice(0, 4).join(", "))}…).</p>`;
}

async function loadDigest() {
  const params = new URLSearchParams({
    funded_only: $("fundedonly").checked ? "true" : "false",
    tag: $("digesttag").value,
  });
  try {
    const r = await fetch(`/api/digest?${params}`);
    const d = await r.json();
    renderDigest(d);
    if (d.running) {
      $("digeststatus").textContent = "refreshing…";
      setTimeout(loadDigest, 3000);
    } else {
      $("digeststatus").textContent = "";
      $("digestbtn").disabled = false;
      $("digestbtn").textContent = "Refresh feed";
    }
  } catch (err) {
    $("digest").innerHTML = `<p class="empty">${esc(err.message || err)}</p>`;
    $("digestbtn").disabled = false;
  }
}

function verdictClass(v) {
  return { "prospect": "good", "existing customer": "bad", "no signal": "muted" }[v] || "muted";
}

function renderProspects(d) {
  const rows = d.prospects || [];
  const counts = Object.entries(d.counts || {})
    .map(([k, v]) => `<span class="chip">${esc(k)}<b>${v}</b></span>`).join("");
  if (!rows.length) {
    $("prospects").innerHTML = `<div class="chips">${counts}</div>
      <p class="empty">No prospects yet. Run a scan, or tick "show all verdicts".</p>`;
    return;
  }
  const body = rows.map((r) => {
    const stated = (r.competitors || []).filter((c) => c.strength === "stated");
    const comp = (stated.length ? stated : (r.competitors || []).slice(0, 3))
      .map((c) => `<span class="chip ${c.strength === "stated" ? "strong" : "weak"}">${esc(c.product)}</span>`)
      .join(" ") || "<span class='toolsrc'>—</span>";
    const atl = (r.atlassian || []).map((a) => esc(a.product)).join(", ");
    const quote = stated.length ? stated[0].evidence : ((r.competitors || [])[0] || {}).evidence;
    return `<tr>
      <td class="pscore">${(r.score || 0).toFixed(0)}</td>
      <td>
        <div class="pname">${esc(r.name)}</div>
        <div class="toolsrc">${esc(r.batch || r.source)} · ${r.open_roles ?? "?"} roles · ${esc(r.board_provider || "—")}</div>
      </td>
      <td>${comp}${quote ? `<div class="toolev">“${esc(String(quote).slice(0, 130))}”</div>` : ""}</td>
      <td>${atl ? `<span class="chip weak">${esc(atl)}</span>` : "<span class='toolsrc'>none</span>"}</td>
      <td><span class="prio ${verdictClass(r.verdict)}">${esc(r.verdict || "")}</span></td>
    </tr>`;
  }).join("");
  $("prospects").innerHTML = `<div class="chips" style="margin-bottom:10px">${counts}</div>
    <table class="ptable"><thead><tr>
      <th>Fit</th><th>Company</th><th>Competing tooling</th><th>Atlassian</th><th>Verdict</th>
    </tr></thead><tbody>${body}</tbody></table>`;
}

async function loadProspects() {
  try {
    const r = await fetch(`/api/prospects?only_prospects=${$("showall").checked ? "false" : "true"}`);
    renderProspects(await r.json());
  } catch (err) {
    $("prospects").innerHTML = `<p class="empty">${esc(err.message || err)}</p>`;
  }
}

async function pollScan() {
  try {
    const r = await fetch("/api/scan/status");
    const d = await r.json();
    const run = d.run;
    if (run) {
      const pct = run.total ? Math.round((run.done / run.total) * 100) : 0;
      $("scanprogress").textContent =
        `${run.status} · ${run.done}/${run.total} (${pct}%) · ${run.found} prospects` +
        (run.current ? ` · ${run.current}` : "");
    }
    if (d.error) $("scanprogress").textContent = `failed: ${d.error}`;
    await loadProspects();
loadDigest();
    if (d.running) { setTimeout(pollScan, 2500); }
    else { $("scanbtn").disabled = false; $("scanbtn").textContent = "Run scan"; }
  } catch {
    $("scanbtn").disabled = false;
  }
}

function toolRow(t) {
  return `<div class="tool ${esc(t.strength)}">
    <div class="toolhead">
      <span class="toolname">${esc(t.product)}</span>
      <span class="toolcat">${esc(t.category.replace(/_/g, " "))}</span>
      <span class="toolstrength ${esc(t.strength)}">${esc(t.strength)}</span>
    </div>
    <div class="toolev">“${esc(t.evidence)}”</div>
    ${t.sources && t.sources.length
      ? `<div class="toolsrc">${t.sources.length} posting(s): ${esc(t.sources.slice(0, 4).join(", "))}${t.sources.length > 4 ? "…" : ""}</div>`
      : ""}
  </div>`;
}

function renderTech(d) {
  const el = $("tech");
  if (d.error) { el.innerHTML = `<p class="empty">${esc(d.error)}</p>`; return; }
  const depts = Object.entries(d.departments || {})
    .map(([k, v]) => `<span class="chip">${esc(k)}<b>${v}</b></span>`).join("");
  const boardUrl = safeUrl(d.board.url);
  el.innerHTML = `
    <div class="techhead">
      <div>
        <div class="techcount">${d.job_count.toLocaleString()} <span>open roles scanned</span></div>
        <div class="techboard">
          ${boardUrl ? `<a class="ext" href="${esc(boardUrl)}" target="_blank" rel="noopener noreferrer">${esc(d.board.provider)} / ${esc(d.board.token)} ↗</a>` : esc(d.board.provider)}
          <span class="toolsrc">found via ${esc(d.board.found_via)}</span>
        </div>
      </div>
    </div>
    ${depts ? `<h4 class="section">Hiring by department</h4><div class="chips">${depts}</div>` : ""}
    <h4 class="section">Atlassian footprint</h4>
    ${d.atlassian.length
      ? `<div class="tools">${d.atlassian.map(toolRow).join("")}</div>`
      : `<p class="empty">No Atlassian product named in any posting.</p>`}
    <h4 class="section">Competing / adjacent tooling</h4>
    ${d.competitors.length
      ? `<div class="tools">${d.competitors.map(toolRow).join("")}</div>`
      : `<p class="empty">No competing tooling named.</p>`}
    <p class="notice"><b>stated</b> means a posting says the company uses it.
    <b>mentioned</b> means it appeared in a list of examples, an integration list, or in
    passing — much weaker. Read the quote before trusting either.</p>`;
}

async function fetchTech(domain, companyName) {
  if (!domain) {
    $("tech").innerHTML = `<p class="empty">No domain to scan. Enter one above.</p>`;
    return;
  }
  $("tech").innerHTML = `<p class="empty">Scanning ${esc(domain)}'s job board…</p>`;
  const params = new URLSearchParams({ domain, company: companyName || "" });
  try {
    const r = await fetch(`/api/technographics?${params}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `API returned ${r.status}`);
    renderTech(d);
  } catch (err) {
    $("tech").innerHTML = `<p class="empty">${esc(err.message || err)}</p>`;
  }
}

function scoreColor(total) {
  if (total >= 55) return "var(--good)";
  if (total >= 30) return "var(--warn)";
  return "var(--muted)";
}

function renderBreakdown(score) {
  return score.signals.map((s) => {
    const cls = s.points > 0.01 ? "pos" : s.points < -0.01 ? "neg" : "zero";
    const sign = s.points > 0 ? "+" : "";
    return `<div class="sigrow">
      <div class="pts ${cls}">${sign}${s.points.toFixed(1)}</div>
      <div><span class="signame">${esc(s.label)}</span>
        <div class="sigreason">${esc(s.reason)} <span style="opacity:.6">(weight ${s.weight})</span></div>
      </div>
    </div>`;
  }).join("");
}

// Columns whose values read as text but order as numbers. Sorting "$100K - $150K" and
// "$60K - $100K" as strings puts $100K first, and "11+ years" before "6+ years", because
// "1" < "6" lexicographically. Pull the leading magnitude out instead.
const NUMERIC_COLS = new Set(["salary_range", "equity_range", "min_experience", "skills"]);
const SUFFIX = { k: 1e3, m: 1e6, b: 1e9 };

function numericKey(value) {
  if (value == null) return null;
  const m = String(value).match(/(\d[\d,]*(?:\.\d+)?)\s*([kmb])?/i);
  if (!m) return null;
  const n = parseFloat(m[1].replace(/,/g, ""));
  if (Number.isNaN(n)) return null;
  return n * (m[2] ? SUFFIX[m[2].toLowerCase()] : 1);
}

function sortJobs() {
  const k = sortState.key, d = sortState.dir;
  jobsData.sort((a, b) => {
    let x = a[k], y = b[k];
    if (k === "skills") { x = (x || []).length; y = (y || []).length; }
    if (NUMERIC_COLS.has(k)) {
      const nx = typeof x === "number" ? x : numericKey(x);
      const ny = typeof y === "number" ? y : numericKey(y);
      // Rows with no value sort last regardless of direction.
      if (nx == null && ny == null) return 0;
      if (nx == null) return 1;
      if (ny == null) return -1;
      if (nx !== ny) return (nx - ny) * d;
      return 0;
    }
    return String(x ?? "").localeCompare(String(y ?? "")) * d;
  });
}

function jobsTable() {
  if (!jobsData.length) return `<p class="empty">No open roles listed on YC.</p>`;
  const cols = [
    ["pretty_role", "Role"], ["title", "Title"], ["skills", "Stack"],
    ["salary_range", "Salary"], ["equity_range", "Equity"],
    ["min_experience", "Exp"], ["last_active_rel", "Last active"],
  ];
  const head = cols.map(([k, label]) => {
    const arrow = sortState.key === k ? (sortState.dir === 1 ? " ▲" : " ▼") : "";
    return `<th data-key="${k}">${esc(label)}<span class="arrow">${arrow}</span></th>`;
  }).join("");
  const body = jobsData.map((j) => `
    <tr>
      <td>${esc(j.pretty_role)}</td>
      <td>${safeUrl(j.url) ? `<a class="ext" href="${esc(safeUrl(j.url))}" target="_blank" rel="noopener noreferrer">${esc(j.title)}</a>` : esc(j.title)}</td>
      <td class="skills">${(j.skills || []).map(esc).join(", ") || "—"}</td>
      <td>${esc(j.salary_range || "—")}</td>
      <td>${esc(j.equity_range || "—")}</td>
      <td>${esc(j.min_experience || "—")}</td>
      <td>${esc(j.last_active_rel || "—")}</td>
    </tr>`).join("");
  return `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

function bindSort() {
  document.querySelectorAll("#yc th[data-key]").forEach((th) => {
    th.addEventListener("click", () => {
      const k = th.dataset.key;
      sortState.dir = sortState.key === k ? -sortState.dir : 1;
      sortState.key = k;
      sortJobs();
      $("jobswrap").innerHTML = jobsTable();
      bindSort();
    });
  });
}

function renderYC(yc) {
  const el = $("yc");
  if (!yc.found) {
    const sugg = (yc.suggestions || []).length
      ? `<div class="suggestions"><p class="notice">Did you mean:</p>` +
        yc.suggestions.map((s) => `<button type="button" data-name="${esc(s)}">${esc(s)}</button>`).join("") +
        `</div>`
      : "";
    el.innerHTML = `<p class="empty">${esc(yc.message)}</p>${sugg}`;
    el.querySelectorAll("button[data-name]").forEach((b) =>
      b.addEventListener("click", () => { $("company").value = b.dataset.name; $("searchform").requestSubmit(); }));
    return;
  }

  const c = yc.company, s = yc.score, fp = yc.fingerprint || {};
  const badges = [
    c.batch && `<span class="badge">${esc(c.batch)}</span>`,
    c.status && `<span class="badge">${esc(c.status)}</span>`,
    c.team_size && `<span class="badge">${c.team_size} people</span>`,
    c.is_hiring && `<span class="badge hiring">Hiring</span>`,
    c.industry && `<span class="badge">${esc(c.industry)}</span>`,
  ].filter(Boolean).join("");

  const stack = yc.stack.length
    ? `<div class="chips">${yc.stack.map((x) =>
        `<span class="chip">${esc(x.skill)}${x.mentions > 1 ? `<b>×${x.mentions}</b>` : ""}</span>`).join("")}</div>`
    : `<p class="empty">No stack data — YC only lists skills on engineering postings.</p>`;

  const tools = yc.tools || [];
  const toolBlock = tools.length
    ? `<div class="tools">${tools.map((t) => `
        <div class="tool ${esc(t.strength)}">
          <div class="toolhead">
            <span class="toolname">${esc(t.product)}</span>
            <span class="toolcat">${esc(t.category.replace(/_/g, " "))}</span>
            <span class="toolstrength ${esc(t.strength)}">${esc(t.strength)}</span>
          </div>
          <div class="toolev">“${esc(t.evidence)}”</div>
          ${t.sources && t.sources.length
            ? `<div class="toolsrc">from: ${esc(t.sources.join(", "))}</div>` : ""}
        </div>`).join("")}</div>`
    : `<p class="empty">No tooling named in job descriptions.</p>`;

  const ycNews = yc.yc_news || [];
  const ycNewsBlock = ycNews.length
    ? `<ul class="postlist">${ycNews.map((n) => {
        const href = safeUrl(n.url);
        const when = (n.published || "").slice(0, 12);
        const title = href
          ? `<a class="ext" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>`
          : esc(n.title);
        return `<li>${when ? `<span class="postdate">${esc(when)}</span>` : ""}${title}</li>`;
      }).join("")}</ul>`
    : `<p class="empty">YC lists no news for this company.</p>`;

  const launches = yc.yc_launches || [];
  const launchBlock = launches.length
    ? launches.map((l) => {
        const href = safeUrl(l.url);
        return `<div class="launch">
          <div class="launchtitle">${href
            ? `<a class="ext" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(l.title)}</a>`
            : esc(l.title)}
            <span class="postdate">${esc((l.published || "").slice(0, 10))}</span></div>
          <div class="launchbody">${esc((l.summary || "").slice(0, 320))}${(l.summary || "").length > 320 ? "…" : ""}</div>
        </div>`;
      }).join("")
    : "";

  const posts = yc.posts || [];
  const postBlock = posts.length
    ? `<ul class="postlist">${posts.slice(0, 8).map((p) => {
        const href = safeUrl(p.url);
        const when = (p.published || "").slice(0, 10);
        const title = href
          ? `<a class="ext" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(p.title)}</a>`
          : esc(p.title);
        return `<li>${when ? `<span class="postdate">${esc(when)}</span>` : ""}${title}</li>`;
      }).join("")}</ul>`
    : `<p class="empty">${esc(yc.posts_via || "No posts found.")}</p>`;

  const detected = fp.detected || {};
  const detBlocks = Object.keys(detected).sort().map((cat) => `
    <div style="margin-bottom:8px">
      <div style="font-size:12px;color:var(--muted);margin-bottom:4px">${esc(cat.replace(/_/g, " "))}</div>
      <div class="chips">${detected[cat].map((i) =>
        `<span class="chip ${i.confidence}" title="${esc(i.evidence)}">${esc(i.product)}</span>`).join("")}</div>
    </div>`).join("");

  currentCompany = c.name;
  jobsData = yc.jobs.slice();
  sortJobs();

  el.innerHTML = `
    <div class="company-head">
      <div style="flex:1 1 320px">
        <h3>${esc(c.name)}</h3>
        <p class="oneliner">${esc(c.one_liner || "")}</p>
        <div class="badges">${badges}</div>
        <div style="margin-top:6px">
          <a class="ext" href="${esc(safeUrl(yc.yc_url))}" target="_blank" rel="noopener noreferrer">YC profile ↗</a>
          ${safeUrl(c.website) ? ` · <a class="ext" href="${esc(safeUrl(c.website))}" target="_blank" rel="noopener noreferrer">Website ↗</a>` : ""}
        </div>
      </div>
      <div class="scorebox">
        <div class="scorelabel">Atlassian fit</div>
        <div class="scorenum" style="color:${scoreColor(s.total)}">${s.total.toFixed(0)}</div>
        <div class="conf ${esc(s.confidence)}" title="${esc((s.confidence_reasons || []).join(" · "))}">${esc(s.confidence)} confidence</div>
      </div>
    </div>

    <details class="breakdown" open>
      <summary>Why this score — ${esc(s.rules_version)}</summary>
      <div style="margin-top:8px">${renderBreakdown(s)}</div>
      ${(s.confidence_reasons || []).length ? `
        <div class="confwhy">
          <span class="confwhyhead">Why ${esc(s.confidence)} confidence</span>
          <ul>${s.confidence_reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>
        </div>` : ""}
    </details>

    <h4 class="section">Tech stack (from job posting skills)</h4>
    ${stack}

    <h4 class="section">Tooling named in job descriptions</h4>
    ${toolBlock}
    <p class="notice">The company describing its own stack in prose &mdash; the strongest
    signal here, and stronger than the website fingerprint below. Detection uses a fixed
    catalog, so the brief is also asked to spot anything it misses.</p>

    <h4 class="section">Detected on website</h4>
    ${detBlocks || `<p class="empty">${esc(fp.error || "Nothing detected.")}</p>`}
    <p class="notice">Public surface only — a solid signal that a tool is in use, but absence
    proves nothing. Hover a chip to see the matching evidence. Dashed chips are weak matches.</p>

    <h4 class="section">YC-curated news</h4>
    ${ycNewsBlock}
    ${launchBlock ? `<h4 class="section">Launch YC post</h4>${launchBlock}` : ""}

    <h4 class="section">From the company's own site
      ${posts.length ? `<span style="text-transform:none;letter-spacing:0;font-weight:400"> &middot; ${esc(yc.posts_via || "")}</span>` : ""}</h4>
    ${postBlock}

    <h4 class="section">Open roles</h4>
    <div id="jobswrap">${jobsTable()}</div>

    <div class="briefzone">
      <h4 class="section" style="margin-top:22px">So what?</h4>
      <p class="notice" id="briefnote">
        ${briefAvailable
          ? "Interprets everything above and drafts outreach. Costs one API call; the result is cached."
          : "No API key for the configured model backend. Set the key and restart the server."}
      </p>
      <button type="button" id="briefbtn" ${briefAvailable ? "" : "disabled"}>Generate brief</button>
      <button type="button" id="briefregen" class="secondary" hidden>Regenerate</button>
      <div id="briefout"></div>
    </div>
  `;
  bindSort();
  bindBrief();
}

function list(title, items) {
  if (!items || !items.length) return "";
  return `<h5 class="briefh">${esc(title)}</h5><ul class="brieflist">` +
    items.map((i) => `<li>${esc(i)}</li>`).join("") + `</ul>`;
}

function renderBrief(data) {
  const b = data.brief;
  const when = data.created_at ? new Date(data.created_at * 1000).toLocaleString() : "";
  $("briefout").innerHTML = `
    <div class="brief">
      <div class="briefhead">
        <span class="prio ${esc(b.priority.replace(/\s+/g, "-"))}">${esc(b.priority)}</span>
        <span class="briefmeta">${esc(data.model || "")}${data.cached ? " · cached" : ""}${when ? " · " + esc(when) : ""}</span>
      </div>
      <p class="headline">${esc(b.headline)}</p>

      <h5 class="briefh">News summary</h5>
      <p>${esc(b.news_summary)}</p>

      <h5 class="briefh">What the data says</h5>
      <p>${esc(b.interpretation)}</p>

      <h5 class="briefh">Recommended action</h5>
      <p>${esc(b.recommended_action)}</p>

      ${b.news_hook ? `<h5 class="briefh">Outreach hook</h5><p>${esc(b.news_hook)}</p>` : ""}
      ${list("Talking points", b.talking_points)}
      ${list("Risks", b.risks)}
      ${list("Evidence gaps", b.evidence_gaps)}

      <h5 class="briefh">Draft email <span class="draftwarn">review before sending</span></h5>
      <div class="email">
        <div class="emailsubj">Subject: ${esc(b.email_subject)}</div>
        <pre id="emailbody">${esc(b.email_body)}</pre>
        <button type="button" id="copyemail" class="secondary">Copy email</button>
      </div>
    </div>`;
  $("briefregen").hidden = false;
  const copy = $("copyemail");
  if (copy) {
    copy.addEventListener("click", async () => {
      const text = `Subject: ${b.email_subject}\n\n${b.email_body}`;
      try {
        await navigator.clipboard.writeText(text);
        copy.textContent = "Copied";
        setTimeout(() => { copy.textContent = "Copy email"; }, 1500);
      } catch {
        copy.textContent = "Copy failed - select manually";
      }
    });
  }
}

async function fetchBrief(regenerate) {
  if (!currentCompany) return;
  const btn = regenerate ? $("briefregen") : $("briefbtn");
  const original = btn.textContent;
  btn.disabled = true;
  btn.textContent = regenerate ? "Regenerating…" : "Thinking…";
  $("briefout").innerHTML = `<p class="empty">Reading the data and drafting…</p>`;
  const params = new URLSearchParams({
    company: currentCompany,
    context: $("context").value.trim(),
    source: $("source").value,
    regenerate: regenerate ? "true" : "false",
  });
  try {
    const r = await fetch(`/api/brief?${params}`, { method: "POST" });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `API returned ${r.status}`);
    renderBrief(d);
  } catch (err) {
    $("briefout").innerHTML = `<p class="empty">${esc(err.message || err)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

function bindBrief() {
  const b = $("briefbtn"), r = $("briefregen");
  if (b) b.addEventListener("click", () => fetchBrief(false));
  if (r) r.addEventListener("click", () => fetchBrief(true));
}

function setMode(mode) {
  searchMode = mode;
  document.querySelectorAll(".mode").forEach((b) =>
    b.classList.toggle("active", b.dataset.mode === mode));
  const label = { company: "Company", idea: "Idea", vc: "Investor" }[mode];
  document.querySelector('label[for="company"]').firstChild.textContent = label + " ";
  $("company").placeholder =
    { company: "Stripe", idea: "AI agents for customer support", vc: "Greylock" }[mode];
  // The company panels only make sense for a company lookup.
  ["ycpanel", "techpanel", "newspanel"].forEach((id) => {
    const el = $(id); if (el) el.hidden = mode !== "company";
  });
  $("resultspanel").hidden = mode === "company";
}
document.querySelectorAll(".mode").forEach((b) =>
  b.addEventListener("click", () => setMode(b.dataset.mode)));

function renderIdea(d) {
  $("resultstitle").textContent = `Companies matching “${d.query}”`;
  if (!d.results.length) {
    $("moderesults").innerHTML = `<p class="empty">No matches. Try different words.</p>`;
    return;
  }
  $("moderesults").innerHTML = `<table class="ptable"><thead><tr>
      <th>Match</th><th>Company</th><th>What they do</th><th>Scanned</th>
    </tr></thead><tbody>${d.results.map((r) => {
      const p = r.prospect;
      return `<tr>
        <td class="pscore">${r.match.toFixed(0)}</td>
        <td><div class="pname">${esc(r.name)}</div>
            <div class="toolsrc">${esc(r.batch || "")} · ${r.team_size ?? "?"} people${r.is_hiring ? " · hiring" : ""}</div></td>
        <td>${esc(r.one_liner || "")}</td>
        <td>${p ? `<span class="prio ${verdictClass(p.verdict)}">${esc(p.verdict)}</span>
              ${p.lead_product ? `<div class="toolsrc">lead: ${esc(p.lead_product)}</div>` : ""}`
             : "<span class='toolsrc'>not scanned</span>"}</td>
      </tr>`;
    }).join("")}</tbody></table>`;
}

function renderInvestor(d) {
  $("resultstitle").textContent = `${d.investor} — recent portfolio`;
  if (!d.portfolio.length) {
    $("moderesults").innerHTML =
      `<p class="empty">No named investments found. Refresh the what's-new feed, or try another firm.</p>`;
    return;
  }
  $("moderesults").innerHTML = `<table class="ptable"><thead><tr>
      <th>Age</th><th>Company</th><th>Announcement</th><th>Scanned</th>
    </tr></thead><tbody>${d.portfolio.map((e) => {
      const p = e.prospect;
      const age = e.age_days == null ? "?" : `${Math.round(e.age_days)}d`;
      const href = safeUrl(e.url);
      return `<tr>
        <td class="pscore" style="font-size:14px">${esc(age)}</td>
        <td><div class="pname">${esc(e.company)}</div>
            <div class="toolsrc">${esc(e.amount || "")}${e.round_stage ? " · " + esc(e.round_stage) : ""}</div></td>
        <td>${href ? `<a class="ext" href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(e.title)}</a>` : esc(e.title)}</td>
        <td>${p ? `<span class="prio ${verdictClass(p.verdict)}">${esc(p.verdict)}</span>`
                : "<span class='toolsrc'>not scanned</span>"}</td>
      </tr>`;
    }).join("")}</tbody></table>`;
}

async function runModeSearch(q) {
  const url = searchMode === "idea"
    ? `/api/search/idea?q=${encodeURIComponent(q)}`
    : `/api/search/investor?name=${encodeURIComponent(q)}`;
  $("moderesults").innerHTML = `<p class="empty">Searching…</p>`;
  try {
    const r = await fetch(url);
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || `API returned ${r.status}`);
    (searchMode === "idea" ? renderIdea : renderInvestor)(d);
    setStatus(searchMode === "idea" ? `${d.count} matches` : `${d.portfolio.length} portfolio companies`);
  } catch (err) {
    $("moderesults").innerHTML = `<p class="empty">${esc(err.message || err)}</p>`;
  }
}

$("searchform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = $("company").value.trim();
  if (!company) return;
  if (searchMode !== "company") {
    $("go").disabled = true;
    await runModeSearch(company);
    $("go").disabled = false;
    return;
  }
  const params = new URLSearchParams({
    company,
    context: $("context").value.trim(),
    source: $("source").value,
    refresh: $("refresh").checked ? "true" : "false",
  });
  $("go").disabled = true;
  setStatus(`Searching “${company}”…`);
  try {
    const r = await fetch(`/api/lookup?${params}`);
    if (!r.ok) throw new Error(`API returned ${r.status}`);
    const d = await r.json();
    renderNews(d.news);
    renderYC(d.yc);
    const cached = d.yc.cached ? ` · jobs ${d.yc.cached.jobs ? "cached" : "fetched"}, site ${d.yc.cached.fingerprint ? "cached" : "fetched"}` : "";
    setStatus(`${d.news.articles.length} articles${cached}`);
    // Technographics are their own lookup now: only scan what was typed into the domain
    // box. Falling back to the YC website quietly re-centred everything on YC, which is
    // exactly what this pivot moves away from.
    const typed = $("domain").value.trim();
    fetchTech(typed, company);
  } catch (err) {
    setStatus(String(err.message || err), true);
  } finally {
    $("go").disabled = false;
  }
});

$("scanbtn").addEventListener("click", async () => {
  const btn = $("scanbtn");
  btn.disabled = true;
  btn.textContent = "Scanning…";
  $("scanprogress").textContent = "starting…";
  const params = new URLSearchParams({
    use_yc: $("scanyc").checked ? "true" : "false",
    hn_threads: $("scanhn").value || "3",
  });
  try {
    const r = await fetch(`/api/scan?${params}`, { method: "POST" });
    if (!r.ok) {
      const d = await r.json();
      throw new Error(d.detail || `API returned ${r.status}`);
    }
    pollScan();
  } catch (err) {
    $("scanprogress").textContent = String(err.message || err);
    btn.disabled = false;
    btn.textContent = "Run scan";
  }
});
$("showall").addEventListener("change", loadProspects);
$("fundedonly").addEventListener("change", loadDigest);
$("digesttag").addEventListener("change", loadDigest);
$("digestbtn").addEventListener("click", async () => {
  const btn = $("digestbtn");
  btn.disabled = true;
  btn.textContent = "Refreshing…";
  $("digeststatus").textContent = "pulling 32 feeds…";
  try {
    const r = await fetch("/api/digest/refresh", { method: "POST" });
    if (!r.ok) {
      const d = await r.json();
      throw new Error(d.detail || `API returned ${r.status}`);
    }
    setTimeout(loadDigest, 2500);
  } catch (err) {
    $("digeststatus").textContent = String(err.message || err);
    btn.disabled = false;
    btn.textContent = "Refresh feed";
  }
});

loadSources();
loadProspects();
loadDigest();
