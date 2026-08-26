"use strict";

import { initDiscoveryGlobe } from "/edu/map.js";

const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const COUNTRIES_URL = "/edu/countries.json";

const STATUS_LABELS = {
  expanding: "Expanding capability or control",
  contracting: "Contracting capability or control",
  mixed: "Mixed effects",
  non_empowerment: "No direct empowerment change",
  unclear: "Unclear from available evidence",
};
const TONE_LABELS = {
  opportunity: "Opportunity",
  threat: "Threat",
  contested: "Contested",
  descriptive_neutral: "Descriptive / neutral",
  unclear: "Unclear",
};
const TOPIC_LABELS = {
  work_employment: "Work and employment",
  business_productivity: "Business and productivity",
  consumer_services: "Consumer services",
  creativity_ip: "Creativity and intellectual property",
  education_research: "Education and research",
  healthcare: "Healthcare",
  government_regulation: "Government and regulation",
  privacy_security: "Privacy and security",
  infrastructure_investment: "Infrastructure and investment",
  other: "Other",
};
const SOURCE_TYPE_LABELS = {
  general_news: "newspapers and broadcasters",
  specialist: "specialist publications",
  primary_official: "government, university and other official pages",
  research_policy: "research and policy outlets",
  regional_local: "local and regional news",
  independent_newsletter: "independent newsletters",
  unclassified: "other sources still being categorized",
};

const dateLong = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "long",
  year: "numeric",
});
const dateShort = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
});
const dateTime = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short",
});

let currentRelease = null;
let releaseIndex = null;
let markets = {};
let globe = null;
let selectedMarket = null;
let evidenceLimit = 6;
let historyMode = "all";
let evidenceView = "all";

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeUrl(value) {
  try {
    const url = new URL(String(value || ""), window.location.origin);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
}

function parseDate(value) {
  const date = new Date(`${String(value || "").slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatRange(startValue, endValue, short = false) {
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (!start || !end) return "Date unavailable";
  const formatter = short ? dateShort : dateLong;
  const sameMonth = start.getUTCMonth() === end.getUTCMonth()
    && start.getUTCFullYear() === end.getUTCFullYear();
  if (short) return `${dateShort.format(start)}–${dateShort.format(end)}`;
  return sameMonth
    ? `${start.getUTCDate()}–${formatter.format(end)}`
    : `${formatter.format(start)}–${formatter.format(end)}`;
}

function formatDateTime(value) {
  const parsed = new Date(String(value || ""));
  return Number.isNaN(parsed.getTime()) ? null : dateTime.format(parsed);
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

function signed(value) {
  if (value == null || Number.isNaN(Number(value))) return "Not available";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}

function percent(value) {
  const number = Number(value || 0) * 100;
  return `${number.toFixed(number >= 10 ? 0 : 1)}%`;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "Not available");
}

async function fetchJSON(url, optional = false) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    if (optional) return null;
    throw new Error(`${url} returned HTTP ${response.status}`);
  }
  return response.json();
}

function counts() {
  const raw = currentRelease?.counts || {};
  const events = Number(raw.ai_relevant_event_records || 0);
  const first = Number(raw.first_time_event_records ?? raw.new_event_records ?? 0);
  const followOn = Number(raw.follow_on_event_records ?? 0);
  const newDevelopments = Number(raw.new_event_records ?? (first + followOn));
  return {
    articles: Number(raw.ai_relevant_articles || 0),
    events,
    first,
    newDevelopments,
    recurring: Number(raw.recurring_event_records ?? Math.max(0, events - newDevelopments)),
    resurfaced: Number(raw.resurfaced_event_records ?? currentRelease?.dynamics?.resurfaced_event_appearances ?? 0),
    followOn,
    rediscovered: Number(raw.rediscovered_article_records ?? currentRelease?.dynamics?.rediscovered_article_records ?? 0),
    possible: Number(raw.possible_historical_match_event_records || 0),
    unclassified: Number(raw.unclassified_novelty_event_records || 0),
    extra: Number(raw.extra_coverage || 0),
  };
}

function sourceMixCopy() {
  const strata = currentRelease?.sources?.strata || {};
  const represented = Object.entries(strata)
    .filter(([, row]) => Number(row?.articles || 0) > 0)
    .map(([key]) => SOURCE_TYPE_LABELS[key])
    .filter(Boolean);
  const unique = [...new Set(represented)];
  const mix = unique.length
    ? unique.join(", ")
    : "newsrooms, official pages, specialist publications, research and policy outlets";
  return `A coverage item is an AI-related page returned by AIEO's Google News searches and dated within the weekly period. The source mix includes ${mix}. It is not limited to academic journal articles. A development is one real-world occurrence after pages about the same thing are grouped together. The database stores each development as an event record.`;
}

function renderMarketSelection(selection) {
  setText("edu-market-selection-summary", selection?.summary || "AIEO uses a five-market pilot to compare several leading AI ecosystems and news environments.");
  setText("edu-market-selection-context", selection?.ranking_context || "The market set is a research sample, not a definitive global top-five ranking.");
  const container = document.getElementById("edu-market-selection-sources");
  if (!container) return;
  const rows = Array.isArray(selection?.sources) ? selection.sources : [];
  container.innerHTML = rows.map((row) => `
    <a href="${escapeHTML(safeUrl(row.url))}" target="_blank" rel="noopener noreferrer">
      ${escapeHTML(row.name || "Open external source")}
    </a>
  `).join("");
}

function poolCopy() {
  const pool = currentRelease?.historical_pool || {};
  const start = formatDateTime(pool.starts_at) || "5 August 2026";
  const through = formatDateTime(pool.considered_through || currentRelease?.data_current_through);
  if (pool.all_prior_events_considered && through) {
    return `The current release was matched against all prior Observatory events collected from ${start} through ${through}. “New” means new relative to that disclosed pool. Later reconciliation may restate a release without deleting its earlier revision.`;
  }
  return "The pilot history begins on 5 August 2026. Longitudinal matching is being activated, so the current new/recurring distinction should be read as provisional until the historical pool is disclosed in the next release.";
}

function renderOpening() {
  const c = counts();
  const period = formatRange(currentRelease.period_start, currentRelease.period_end);
  const pending = Math.max(0, c.events - c.newDevelopments - c.recurring);
  const computedExtra = Math.max(0, c.articles - c.events);
  const extra = c.extra === computedExtra ? c.extra : computedExtra;

  setText("week-badge", `Current weekly signal · ${period}`);
  setText("count-articles", c.articles);
  setText("count-new", c.newDevelopments);
  setText("count-recurring", c.recurring);
  setText("count-resurfaced", c.resurfaced);
  setText(
    "week-intro",
    pending
      ? `AIEO found ${c.articles} AI-related coverage items and grouped them into ${c.events} developments. ${c.newDevelopments} were new to the disclosed historical pool, ${c.recurring} had been seen before, and ${pending} remained under novelty review.`
      : `AIEO found ${c.articles} AI-related coverage items and grouped them into ${c.events} developments. ${c.newDevelopments} were new to the disclosed historical pool and ${c.recurring} had been seen before.`,
  );
  setText(
    "week-equation-coverage",
    `${c.articles} coverage items = ${c.events} distinct developments + ${extra} additional ${plural(extra, "report")} about developments already counted.`,
  );
  setText(
    "week-equation-novelty",
    pending
      ? `${c.events} developments = ${c.newDevelopments} new to AIEO + ${c.recurring} already in AIEO's history + ${pending} still under novelty review.`
      : `${c.events} developments = ${c.newDevelopments} new to AIEO + ${c.recurring} already in AIEO's history.`,
  );
  setText("week-definition-copy", sourceMixCopy());
  setText(
    "remember-copy",
    c.resurfaced
      ? `${c.newDevelopments} developments were new to AIEO. ${c.recurring} previously seen developments received new attention, including ${c.resurfaced} that returned after at least four weeks.`
      : `${c.newDevelopments} developments were new to AIEO, while ${c.recurring} previously seen developments received new coverage. Repetition adds attention, not another development.`,
  );
  setText("pool-copy", poolCopy());

  setText("story-articles", c.articles);
  setText("story-new", c.newDevelopments);
  setText("story-index", signed(currentRelease.lenses?.event?.empowerment_index));
  setText(
    "story-coverage-copy",
    `${extra} ${plural(extra, "coverage item was", "coverage items were")} another report about a development already counted.`,
  );
  setText(
    "story-recurrence-copy",
    `${c.recurring} prior developments returned to the coverage. ${c.followOn ? `${c.followOn} follow-on ${plural(c.followOn, "development was", "developments were")} kept separate and linked to an existing story.` : "Genuine later actions remain separate developments and can be linked to the continuing story."}`,
  );
  setText(
    "story-human-copy",
    `The scored Event Lens was ${signed(currentRelease.lenses?.event?.empowerment_index)}. Narrative framing is measured separately, so hopeful language is not automatically treated as human empowerment.`,
  );
}

function coverageRowsForMarket(iso3) {
  return (currentRelease.units?.coverage_articles || []).filter((row) => (
    row.classification?.ai_relevant && (row.search_markets || []).includes(iso3)
  ));
}

function publisherRanking(rows) {
  const countsMap = new Map();
  rows.forEach((row) => {
    const name = String(row.publisher || "Unknown publication");
    countsMap.set(name, (countsMap.get(name) || 0) + 1);
  });
  return [...countsMap.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

function renderMarketButtons() {
  const container = document.getElementById("edu-market-buttons");
  if (!container) return;
  container.innerHTML = Object.entries(markets).map(([iso3, market]) => `
    <button type="button" data-market="${escapeHTML(iso3)}" aria-pressed="false">
      ${escapeHTML(market.short_name || market.name)}
    </button>
  `).join("");
  container.querySelectorAll("button[data-market]").forEach((button) => {
    button.addEventListener("click", () => {
      selectMarket(button.dataset.market);
      globe?.selectMarket(button.dataset.market, { notify: false });
    });
  });
}

function renderMarketCard() {
  const card = document.getElementById("edu-market-card");
  const clear = document.getElementById("clear-market");
  if (!card || !clear) return;

  document.querySelectorAll("#edu-market-buttons button[data-market]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.market === selectedMarket));
  });

  if (!selectedMarket || !markets[selectedMarket]) {
    card.innerHTML = `
      <h3>Choose a search market</h3>
      <p>See how much AI-related coverage the market search found and which publications were most visible.</p>
      <p class="market-caveat">A search market is where AIEO looked for coverage. It is not automatically the location of the reported development.</p>
    `;
    clear.hidden = true;
    setText("evidence-scope", evidenceScopeCopy());
    return;
  }

  const market = markets[selectedMarket];
  const rows = coverageRowsForMarket(selectedMarket);
  const fallback = Number(currentRelease.sources?.discovery_markets?.[selectedMarket] || 0);
  const items = rows.length || fallback;
  const ranking = publisherRanking(rows);
  const publications = new Set(rows.map((row) => row.publisher).filter(Boolean)).size;
  const visible = ranking.slice(0, 3).map(([name, count]) => `${escapeHTML(name)} (${count})`).join(", ");
  card.innerHTML = `
    <p class="eyebrow">${escapeHTML(market.name)} search</p>
    <h3>${items} AI-related coverage ${plural(items, "item")} found</h3>
    <dl>
      <div><dt>Publications</dt><dd>${publications || "Not available"}</dd></div>
      <div><dt>Share of week</dt><dd>${counts().articles ? Math.round((items / counts().articles) * 100) : 0}%</dd></div>
    </dl>
    <p>Most visible: ${visible || "Source detail will appear with the next standardized release."}</p>
    <p class="market-caveat">These items were found through the ${escapeHTML(market.name)} Google News search. Their stories may concern another country or a global issue.</p>
  `;
  clear.hidden = false;
  setText("evidence-scope", evidenceScopeCopy());
}

function selectMarket(iso3) {
  selectedMarket = iso3 && markets[iso3] ? iso3 : null;
  evidenceLimit = 6;
  renderMarketCard();
  renderEvidence();
  const url = new URL(window.location.href);
  if (selectedMarket) url.searchParams.set("market", selectedMarket);
  else url.searchParams.delete("market");
  history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function barRows(distribution, labels, limit = null) {
  const rows = Object.entries(distribution || {})
    .filter(([, value]) => Number(value) > 0)
    .sort((a, b) => Number(b[1]) - Number(a[1]));
  return limit ? rows.slice(0, limit) : rows;
}

function renderBars(containerId, rows, labels) {
  const container = document.getElementById(containerId);
  if (!container) return;
  if (!rows.length) {
    container.innerHTML = "<p>No scored distribution is available for this view.</p>";
    return;
  }
  container.innerHTML = rows.map(([key, value]) => `
    <div class="bar-row">
      <span class="bar-label">${escapeHTML(labels[key] || key.replaceAll("_", " "))}</span>
      <span class="bar-track"><span class="bar-fill" style="width:${Math.max(1, Number(value) * 100).toFixed(2)}%"></span></span>
      <span class="bar-value">${percent(value)}</span>
    </div>
  `).join("");
}

function renderLenses() {
  const eventLens = currentRelease.lenses?.event || {};
  setText("human-index", signed(eventLens.empowerment_index));
  setText(
    "human-denominator",
    `${Number(eventLens.unit_count_scored || 0)} scored event ${plural(eventLens.unit_count_scored || 0, "record")} · ${Number(eventLens.unit_count_excluded_unclear || 0)} unclear and excluded from the index`,
  );
  renderBars("human-bars", barRows(eventLens.status_distribution, STATUS_LABELS), STATUS_LABELS);
  renderBars("tone-bars", barRows(eventLens.narrative_distribution, TONE_LABELS), TONE_LABELS);
  renderBars("topic-bars", barRows(eventLens.topic_distribution, TOPIC_LABELS, 7), TOPIC_LABELS);
}

function setupTabs() {
  const tabs = [...document.querySelectorAll('[role="tab"][data-panel]')];
  function activate(tab, focus = false) {
    tabs.forEach((candidate) => {
      const active = candidate === tab;
      candidate.setAttribute("aria-selected", String(active));
      candidate.tabIndex = active ? 0 : -1;
      const panel = document.getElementById(candidate.dataset.panel);
      if (panel) panel.hidden = !active;
    });
    if (focus) tab.focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (event) => {
      let target = null;
      if (event.key === "ArrowRight") target = tabs[(index + 1) % tabs.length];
      if (event.key === "ArrowLeft") target = tabs[(index - 1 + tabs.length) % tabs.length];
      if (event.key === "Home") target = tabs[0];
      if (event.key === "End") target = tabs[tabs.length - 1];
      if (!target) return;
      event.preventDefault();
      activate(target, true);
    });
  });
}

function normaliseHistory() {
  if (Array.isArray(releaseIndex.display_series) && releaseIndex.display_series.length) {
    return releaseIndex.display_series.map((row) => ({ ...row }));
  }
  const historical = (releaseIndex.historical_snapshots || []).map((row) => ({
    ...row,
    series_kind: "historical_reference",
    comparable_to_weekly_series: false,
    connect_to_previous: false,
  }));
  const weekly = (releaseIndex.weekly || []).map((row, index) => ({
    ...row,
    series_kind: "weekly",
    comparable_to_weekly_series: true,
    connect_to_previous: index > 0,
  }));
  return [...historical, ...weekly].sort((a, b) => String(a.period_end).localeCompare(String(b.period_end)));
}

function filteredHistory() {
  const all = normaliseHistory();
  const historical = all.filter((row) => row.series_kind !== "weekly");
  const weekly = all.filter((row) => row.series_kind === "weekly");
  if (historyMode === "4") return [...historical, ...weekly.slice(-4)];
  if (historyMode === "12") return [...historical, ...weekly.slice(-12)];
  return all;
}

function renderHistoryControls() {
  const controls = document.getElementById("history-controls");
  const weeklyCount = normaliseHistory().filter((row) => row.series_kind === "weekly").length;
  if (!controls) return;
  let options = [];
  if (weeklyCount >= 13) options = [["4", "Last 4 weeks"], ["12", "Last 12 weeks"], ["all", "All weeks"]];
  else if (weeklyCount >= 5) options = [["4", "Last 4 weeks"], ["all", "All weeks"]];
  if (!options.length) {
    controls.hidden = true;
    return;
  }
  if (!options.some(([value]) => value === historyMode)) historyMode = "4";
  controls.innerHTML = options.map(([value, label]) => `
    <button type="button" data-history="${value}" aria-pressed="${String(value === historyMode)}">${label}</button>
  `).join("");
  controls.hidden = false;
  controls.querySelectorAll("button[data-history]").forEach((button) => {
    button.addEventListener("click", () => {
      historyMode = button.dataset.history;
      renderHistoryControls();
      renderHistoryChart();
    });
  });
}

function svgElement(name, attributes = {}, text = null) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attributes).forEach(([key, value]) => element.setAttribute(key, String(value)));
  if (text != null) element.textContent = String(text);
  return element;
}

function renderHistoryChart() {
  const svg = document.getElementById("history-chart");
  const note = document.getElementById("history-note");
  const explainer = document.getElementById("history-explainer");
  if (!svg || !note || !explainer) return;
  const rows = filteredHistory();
  svg.querySelectorAll(":scope > :not(title):not(desc)").forEach((node) => node.remove());

  const weekly = normaliseHistory().filter((row) => row.series_kind === "weekly");
  const historical = normaliseHistory().filter((row) => row.series_kind !== "weekly");
  explainer.textContent = weekly.length < 2
    ? `${historical.length + weekly.length} published snapshots are visible. The launch reference overlaps the first standardized week, so they are shown separately rather than joined as a trend.`
    : `${weekly.length} standardized weeks are available. Only consecutive Monday–Sunday weeks are connected; historical references remain separate.`;

  if (!rows.length) {
    svg.appendChild(svgElement("text", { x: 500, y: 210, "text-anchor": "middle", fill: "#5f7181" }, "No publication history is available yet."));
    note.textContent = "The first history point will appear after publication.";
    return;
  }

  const width = 1000;
  const height = 420;
  const pad = { left: 66, right: 35, top: 35, bottom: 90 };
  const values = rows.flatMap((row) => [Number(row.articles || 0), Number(row.event_records || 0)]);
  const maximum = Math.max(10, ...values);
  const yMax = Math.ceil(maximum / 10) * 10;
  const x = (index) => rows.length === 1
    ? width / 2
    : pad.left + (index * (width - pad.left - pad.right)) / (rows.length - 1);
  const y = (value) => pad.top + (1 - Number(value || 0) / yMax) * (height - pad.top - pad.bottom);

  for (let step = 0; step <= 4; step += 1) {
    const value = (yMax * step) / 4;
    const yy = y(value);
    svg.appendChild(svgElement("line", { x1: pad.left, y1: yy, x2: width - pad.right, y2: yy, stroke: "#dde5e7", "stroke-width": 1 }));
    svg.appendChild(svgElement("text", { x: pad.left - 12, y: yy + 4, "text-anchor": "end", fill: "#667987", "font-size": 12 }, Math.round(value)));
  }

  const weeklyIndices = rows.map((row, index) => ({ row, index })).filter(({ row }) => row.series_kind === "weekly");
  for (let position = 1; position < weeklyIndices.length; position += 1) {
    const previous = weeklyIndices[position - 1];
    const current = weeklyIndices[position];
    if (!current.row.connect_to_previous) continue;
    [["articles", "#719cf5"], ["event_records", "#087985"]].forEach(([key, color]) => {
      svg.appendChild(svgElement("line", {
        x1: x(previous.index), y1: y(previous.row[key]),
        x2: x(current.index), y2: y(current.row[key]),
        stroke: color, "stroke-width": 3, "stroke-linecap": "round",
      }));
    });
  }

  rows.forEach((row, index) => {
    const baseline = row.series_kind !== "weekly";
    const xx = x(index);
    [["articles", "#719cf5", -5], ["event_records", "#087985", 5]].forEach(([key, color, offset]) => {
      svg.appendChild(svgElement("circle", {
        cx: xx + offset, cy: y(row[key]), r: baseline ? 6 : 7,
        fill: baseline ? "#fff" : color,
        stroke: color, "stroke-width": baseline ? 3 : 1,
        "stroke-dasharray": baseline ? "3 2" : "none",
      }));
      svg.appendChild(svgElement("text", {
        x: xx + offset, y: y(row[key]) - 12, "text-anchor": "middle", fill: color, "font-size": 11, "font-weight": 800,
      }, row[key]));
    });
    const label = formatRange(row.period_start, row.period_end, true);
    svg.appendChild(svgElement("text", { x: xx, y: height - 52, "text-anchor": "middle", fill: "#415767", "font-size": 12, "font-weight": 800 }, label));
    svg.appendChild(svgElement("text", { x: xx, y: height - 34, "text-anchor": "middle", fill: "#71808a", "font-size": 10 }, baseline ? "Launch reference" : String(row.release_id || "Weekly release")));
  });

  note.textContent = historical.length
    ? "The launch reference and first standardized week overlap in time and are not connected. Future non-overlapping weekly releases connect automatically."
    : "Consecutive Monday–Sunday releases connect automatically; gaps remain visible.";
}

function articleMarketMap() {
  return new Map(
    (currentRelease.units?.coverage_articles || []).map((row) => [
      String(row.article_id),
      Array.isArray(row.search_markets) ? row.search_markets : [],
    ]),
  );
}

function eventDiscoveryMarkets(event) {
  const byArticle = articleMarketMap();
  const found = new Set();
  (event.member_article_ids || []).forEach((articleId) => {
    (byArticle.get(String(articleId)) || []).forEach((iso3) => found.add(iso3));
  });
  return [...found].filter((iso3) => markets[iso3]);
}

function eventMatchesView(event) {
  const novelty = String(event.novelty_status || "unclassified");
  if (evidenceView === "new") {
    return novelty === "first_time" || novelty === "follow_on_development";
  }
  if (evidenceView === "recurring") return novelty === "recurring";
  if (evidenceView === "resurfaced") {
    return Boolean(event.resurfaced_in_period)
      || Number(event.replication_lag_days || 0) >= 28;
  }
  return true;
}

function storyLocation(event) {
  const classification = event.classification || {};
  if (classification.geographic_scope === "global") return "Global";
  const codes = Array.isArray(classification.country_iso3s)
    ? classification.country_iso3s.filter(Boolean)
    : [];
  if (codes.length) {
    return codes.map((code) => markets[code]?.name || code).join(", ");
  }
  return "Not established from the available evidence";
}

function evidenceScopeCopy(total = null) {
  const marketName = selectedMarket && markets[selectedMarket]
    ? ` found through the ${markets[selectedMarket].name} search`
    : "";
  const viewLabel = evidenceView === "new"
    ? "new developments"
    : evidenceView === "recurring"
      ? "previously seen developments"
      : evidenceView === "resurfaced"
        ? "developments that resurfaced after at least four weeks"
        : "all developments";
  const countCopy = total == null ? "" : `${total} ${plural(total, "development")} shown: `;
  return `${countCopy}${viewLabel}${marketName}. Search market and story location are shown separately.`;
}

function setupEvidenceFilters() {
  const requested = new URLSearchParams(window.location.search).get("view");
  evidenceView = ["all", "new", "recurring", "resurfaced"].includes(requested)
    ? requested
    : "all";
  document.querySelectorAll("[data-evidence-view]").forEach((link) => {
    const active = link.dataset.evidenceView === evidenceView;
    link.setAttribute("aria-current", active ? "true" : "false");
  });
}

function eventMatchesMarket(event) {
  if (!selectedMarket) return true;
  return eventDiscoveryMarkets(event).includes(selectedMarket);
}

function evidenceStatus(classification) {
  return STATUS_LABELS[classification?.empowerment_status]
    || String(classification?.empowerment_status || "Unclassified").replaceAll("_", " ");
}

function renderEvidence() {
  const container = document.getElementById("evidence-list");
  const more = document.getElementById("show-more-evidence");
  if (!container || !more) return;
  const events = (currentRelease.evidence || [])
    .filter(eventMatchesMarket)
    .filter(eventMatchesView)
    .sort((a, b) => Number(b.member_article_count || 0) - Number(a.member_article_count || 0));
  const visible = events.slice(0, evidenceLimit);
  setText("evidence-scope", evidenceScopeCopy(events.length));

  document.querySelectorAll("[data-evidence-view]").forEach((link) => {
    link.setAttribute("aria-current", String(link.dataset.evidenceView === evidenceView));
  });

  if (!visible.length) {
    container.innerHTML = "<p>No source-linked development matches this evidence view and search market in the current release.</p>";
    more.hidden = true;
    return;
  }

  container.innerHTML = visible.map((event, index) => {
    const classification = event.classification || {};
    const sources = event.sources || [];
    const discoveryMarkets = eventDiscoveryMarkets(event);
    const novelty = String(event.novelty_status || "unclassified");
    const recurrence = novelty === "recurring"
      ? '<span class="evidence-pill">Seen before</span>'
      : novelty === "follow_on_development"
        ? '<span class="evidence-pill">New follow-on development</span>'
        : novelty === "possible_historical_match"
          ? '<span class="evidence-pill">Possible historical match</span>'
          : novelty === "first_time"
            ? '<span class="evidence-pill">New to AIEO</span>'
            : '<span class="evidence-pill">Novelty under review</span>';
    const duplicateNote = event.possible_duplicate_record
      ? `<p><strong>Resolver note:</strong> ${escapeHTML(event.possible_duplicate_reason || "This record may duplicate another unresolved development.")}</p>`
      : "";
    const discoveryButtons = discoveryMarkets.length
      ? discoveryMarkets.map((iso3) => `
          <button type="button" class="market-evidence-chip" data-globe-market="${escapeHTML(iso3)}">
            ${escapeHTML(markets[iso3]?.name || iso3)} search
          </button>
        `).join("")
      : '<span class="evidence-pill">Search market not available</span>';
    const shouldOpen = evidenceView === "new" && events.length === 1 && index === 0;

    return `
      <details class="evidence-card" ${shouldOpen ? "open" : ""}>
        <summary>
          <div>
            <h3>${escapeHTML(event.event_title || "Untitled development")}</h3>
            <div class="evidence-meta">
              <span>${escapeHTML(formatRange(event.event_date, event.event_date))}</span>
              <span class="evidence-pill">${sources.length} source ${plural(sources.length, "item")}</span>
              ${recurrence}
              <span class="evidence-pill">${escapeHTML(evidenceStatus(classification))}</span>
            </div>
          </div>
        </summary>
        <div class="evidence-body">
          <div class="location-clarity">
            <div>
              <strong>Found through AIEO search in</strong>
              <div class="market-evidence-chips">${discoveryButtons}</div>
              <small>Click a search market to move the globe. This shows where AIEO found the coverage.</small>
            </div>
            <div>
              <strong>Story location</strong>
              <p>${escapeHTML(storyLocation(event))}</p>
              <small>This is what the available evidence says the development concerns.</small>
            </div>
          </div>
          ${classification.reasoning ? `<p>${escapeHTML(classification.reasoning)}</p>` : ""}
          ${duplicateNote}
          <div class="source-links">
            ${sources.map((source) => `
              <a href="${escapeHTML(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer">
                <span><strong>${escapeHTML(source.publisher || "Publication")}</strong>: ${escapeHTML(source.headline || "Open source")}</span>
                <small>${escapeHTML(source.published_date || "")}</small>
              </a>
            `).join("")}
          </div>
        </div>
      </details>
    `;
  }).join("");

  container.querySelectorAll("button[data-globe-market]").forEach((button) => {
    button.addEventListener("click", () => {
      const iso3 = button.dataset.globeMarket;
      selectMarket(iso3);
      globe?.selectMarket(iso3, { notify: false });
      document.getElementById("explore")?.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  });

  more.hidden = visible.length >= events.length;
  more.textContent = `Show ${Math.min(6, events.length - visible.length)} more`;
}

function setupEvidenceMore() {
  const button = document.getElementById("show-more-evidence");
  button?.addEventListener("click", () => {
    evidenceLimit += 6;
    renderEvidence();
  });
}

async function initialiseGlobe() {
  try {
    globe = await initDiscoveryGlobe({
      containerId: "edu-globe",
      toggleId: "edu-globe-toggle",
      promptId: "edu-globe-prompt",
      fallbackId: "edu-globe-fallback",
      markets,
      onSelect: (iso3) => selectMarket(iso3),
    });
  } catch (mapError) {
    globe = null;
    // Do not suppress the weekly story, history or evidence when the external
    // map module, WebGL or tile service is unavailable. Country buttons remain active.
    console.warn(
      "AIEO globe could not initialise; using market-button fallback",
      mapError,
    );
  }
  renderMarketButtons();
  const requested = new URLSearchParams(window.location.search).get("market");
  if (requested && markets[requested]) {
    selectMarket(requested);
    globe?.selectMarket(requested, { notify: false });
  } else {
    renderMarketCard();
  }
  document.getElementById("clear-market")?.addEventListener("click", () => {
    selectMarket(null);
    globe?.reset({ resume: true });
  });
}

async function init() {
  setupTabs();
  setupEvidenceFilters();
  setupEvidenceMore();
  try {
    const [release, index, countryData] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(INDEX_URL),
      fetchJSON(COUNTRIES_URL),
    ]);
    currentRelease = release;
    releaseIndex = index;
    markets = countryData.markets || {};

    renderOpening();
    renderMarketSelection(countryData.selection || {});
    renderLenses();
    renderHistoryControls();
    renderHistoryChart();
    renderEvidence();
    await initialiseGlobe();
  } catch (error) {
    console.error("Current-signal page could not initialise", error);
    setText("week-badge", "Current signal temporarily unavailable");
    setText("week-intro", "The current release could not be loaded. Please try again shortly.");
    document.getElementById("evidence-list").innerHTML = "<p>Source-linked evidence is temporarily unavailable.</p>";
  }
}

init();
