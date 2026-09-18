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
        <div class="conf ${esc(s.confidence)}">${esc(s.confidence)} confidence</div>
      </div>
    </div>

    <details class="breakdown" open>
      <summary>Why this score — ${esc(s.rules_version)}</summary>
      <div style="margin-top:8px">${renderBreakdown(s)}</div>
    </details>

    <h4 class="section">Tech stack (from job postings)</h4>
    ${stack}

    <h4 class="section">Detected on website</h4>
    ${detBlocks || `<p class="empty">${esc(fp.error || "Nothing detected.")}</p>`}
    <p class="notice">Public surface only — a solid signal that a tool is in use, but absence
    proves nothing. Hover a chip to see the matching evidence. Dashed chips are weak matches.</p>

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

$("searchform").addEventListener("submit", async (e) => {
  e.preventDefault();
  const company = $("company").value.trim();
  if (!company) return;
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
  } catch (err) {
    setStatus(String(err.message || err), true);
  } finally {
    $("go").disabled = false;
  }
});

loadSources();
