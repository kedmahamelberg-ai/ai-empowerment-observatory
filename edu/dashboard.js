"use strict";

import { initDiscoveryGlobe } from "/edu/map.js";

const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const COUNTRIES_URL = "/edu/countries.json";
const SYMBIOSIS_URL = "/data/symbiosis/current.json";

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

const RELATIONSHIP_LABELS = {
  mutualism: "Both people and the AI side gain",
  ai_benefiting_parasitism: "The AI or operator side gains while people are constrained",
  human_benefiting_parasitism: "People gain while the AI system is constrained",
  competition: "People and the AI side are both constrained",
  human_enabling_only: "Human-side enabling signal only",
  human_constraining_only: "Human-side constraining signal only",
  ai_enabling_only: "AI-side enabling signal only",
  ai_constraining_only: "AI-side constraining signal only",
  no_clear_relational_signal: "No clear human-AI relationship signal",
  ambiguous_relational_signal: "Relationship direction unclear",
  insufficient_evidence: "Insufficient source evidence",
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
let currentSymbiosis = null;
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
    followOn,
    rediscovered: Number(raw.rediscovered_article_records ?? currentRelease?.dynamics?.rediscovered_article_records ?? 0),
    possible: Number(raw.possible_historical_match_event_records || 0),
    unclassified: Number(raw.unclassified_novelty_event_records || 0),
    declaredExtra: Number(raw.extra_coverage || 0),
  };
}

function validatedWeeklyArithmetic(c) {
  const computedExtra = Math.max(0, c.articles - c.events);
  const underReview = Math.max(0, c.events - c.newDevelopments - c.recurring);
  const declaredUnderReview = c.possible + c.unclassified;
  const issues = [];

  if (c.declaredExtra !== computedExtra) {
    issues.push(`extra coverage was declared as ${c.declaredExtra}, but articles minus developments equals ${computedExtra}`);
  }
  if (declaredUnderReview > underReview) {
    issues.push("declared novelty review categories exceed the unresolved novelty total");
  }
  if (c.newDevelopments + c.recurring + underReview !== c.events) {
    issues.push("new, recurring and unresolved developments do not reconcile to the total");
  }
  if (issues.length) {
    console.warn("AIEO weekly arithmetic check", issues);
  }
  return {
    extra: computedExtra,
    underReview,
    valid: issues.length === 0,
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

function relationshipEventById(event) {
  if (!currentSymbiosis || currentSymbiosis.release_id !== currentRelease?.release_id) return null;
  if (!currentSymbiosis.review?.complete) return null;
  const eventId = String(event?.effective_event_id || event?.event_id || "");
  return (currentSymbiosis.evidence || []).find((row) => String(row.event_id) === eventId) || null;
}

function renderRelationship() {
  const banner = document.getElementById("relationship-review-banner");
  const core = document.getElementById("relationship-core");
  const other = document.getElementById("relationship-other");
  if (!banner || !core || !other) return;

  if (!currentSymbiosis || currentSymbiosis.release_id !== currentRelease?.release_id) {
    banner.hidden = false;
    banner.textContent = "The relationship-pattern review has not yet been published for this weekly release. Counts, novelty, the globe, and source evidence remain available.";
    core.hidden = true;
    other.hidden = true;
    setText("relationship-scope", "Relationship classifications are human-gated and are not shown until the matching review artifact exists.");
    setText("story-index", "Review pending");
    setText("story-human-copy", "AIEO will show the two-sided relationship signal after the current release has a matching human-reviewed artifact.");
    return;
  }

  const review = currentSymbiosis.review || {};
  const eventSummary = currentSymbiosis.event || {};
  if (!review.complete) {
    banner.hidden = false;
    banner.textContent = `Human review is in progress: ${Number(review.event_reviewed || 0)} of ${Number(review.event_total || 0)} developments and ${Number(review.coverage_reviewed || 0)} of ${Number(review.coverage_total || 0)} coverage items have been reviewed.`;
    core.hidden = true;
    other.hidden = true;
    setText("relationship-scope", "AIEO does not publish a partial relationship distribution because every development and coverage item must pass the same explicit human-review gate.");
    setText("story-index", `${Number(review.event_reviewed || 0)}/${Number(review.event_total || 0)}`);
    setText("story-human-copy", "The primary relationship lens remains under human review for this week.");
    return;
  }

  banner.hidden = true;
  core.hidden = false;
  other.hidden = false;
  const counts = eventSummary.configuration_counts || {};
  setText("relationship-mutualism", Number(counts.mutualism || 0));
  setText("relationship-ai-benefit", Number(counts.ai_benefiting_parasitism || 0));
  setText("relationship-human-benefit", Number(counts.human_benefiting_parasitism || 0));
  setText("relationship-competition", Number(counts.competition || 0));
  setText("relationship-partial", Number(eventSummary.partial_signal_count || 0));
  setText("relationship-none", Number(eventSummary.no_clear_relational_signal_count || 0));
  setText("relationship-insufficient", Number(eventSummary.insufficient_evidence_count || 0));
  setText(
    "relationship-scope",
    `${Number(review.event_reviewed || 0)} developments were human reviewed. The four large cards count complete two-sided configurations. One-sided, no-clear-signal, ambiguous, and insufficient-evidence cases are kept outside that denominator.`,
  );
  setText("story-index", Number(review.event_reviewed || 0));
  const coreCounts = [
    ["mutualism", Number(counts.mutualism || 0)],
    ["ai_benefiting_parasitism", Number(counts.ai_benefiting_parasitism || 0)],
    ["human_benefiting_parasitism", Number(counts.human_benefiting_parasitism || 0)],
    ["competition", Number(counts.competition || 0)],
  ].sort((a, b) => b[1] - a[1]);
  const dominant = coreCounts[0];
  setText(
    "story-human-copy",
    dominant && dominant[1] > 0
      ? `${dominant[1]} reviewed complete configurations were most often coded as: ${RELATIONSHIP_LABELS[dominant[0]]}. One-sided and no-clear-signal cases remain separate.`
      : "No complete two-sided relationship configuration was established in the reviewed event evidence. One-sided and no-clear-signal cases remain visible instead of being forced into a category.",
  );
}

function renderOpening() {
  const c = counts();
  const arithmetic = validatedWeeklyArithmetic(c);
  const period = formatRange(currentRelease.period_start, currentRelease.period_end);

  setText("week-badge", `Current weekly signal · ${period}`);
  setText("count-articles", c.articles);
  setText("count-new", c.newDevelopments);
  setText("count-recurring", c.recurring);
  setText("count-extra", arithmetic.extra);
  setText(
    "week-intro",
    arithmetic.underReview
      ? `AIEO found ${c.articles} AI-related coverage items and grouped them into ${c.events} developments represented in this weekly release. ${c.newDevelopments} were new to the disclosed historical pool, ${c.recurring} had been seen before, and ${arithmetic.underReview} remained under novelty review.`
      : `AIEO found ${c.articles} AI-related coverage items and grouped them into ${c.events} developments represented in this weekly release. ${c.newDevelopments} were new to the disclosed historical pool and ${c.recurring} had been seen before.`,
  );
  setText(
    "week-equation-coverage",
    `${c.articles} coverage items = ${c.events} distinct developments + ${arithmetic.extra} additional ${plural(arithmetic.extra, "report")} about developments already counted.`,
  );
  setText(
    "week-equation-novelty",
    arithmetic.underReview
      ? `${c.events} developments = ${c.newDevelopments} new to AIEO + ${c.recurring} already in AIEO's history + ${arithmetic.underReview} still under novelty review.`
      : `${c.events} developments = ${c.newDevelopments} new to AIEO + ${c.recurring} already in AIEO's history.`,
  );
  setText("week-definition-copy", sourceMixCopy());
  setText(
    "remember-copy",
    `${c.newDevelopments} ${plural(c.newDevelopments, "development was", "developments were")} new to AIEO in this weekly release. ${c.recurring} ${plural(c.recurring, "development had", "developments had")} appeared in AIEO's history before and received coverage again this week.`,
  );
  setText("pool-copy", poolCopy());

  setText("story-articles", c.articles);
  setText("story-new", c.newDevelopments);
  setText(
    "story-coverage-copy",
    `${arithmetic.extra} ${plural(arithmetic.extra, "coverage item was", "coverage items were")} another report about a development already counted.`,
  );
  setText(
    "story-recurrence-copy",
    `${c.recurring} prior ${plural(c.recurring, "development was", "developments were")} represented again in this week's coverage. ${c.followOn ? `${c.followOn} follow-on ${plural(c.followOn, "development was", "developments were")} kept separate and linked to an existing story.` : "Genuine later actions remain separate developments and can be linked to a continuing story."}`,
  );
  renderRelationship();
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
  const review = currentSymbiosis?.release_id === currentRelease?.release_id
    ? (currentSymbiosis.review || {})
    : {};
  const secondary = currentSymbiosis?.secondary_empowerment?.event || null;

  if (!review.complete || !secondary) {
    setText("human-index", "Review pending");
    setText(
      "human-denominator",
      `${Number(review.event_reviewed || 0)} of ${Number(review.event_total || 0)} developments and ${Number(review.coverage_reviewed || 0)} of ${Number(review.coverage_total || 0)} coverage items have completed the explicit review gate.`,
    );
    const container = document.getElementById("human-bars");
    if (container) {
      container.innerHTML = "<p>The secondary empowerment distribution will appear after the full relationship-review queue is complete. Legacy model-only empowerment scores are not shown here.</p>";
    }
    return;
  }

  setText("human-index", signed(secondary.empowerment_index));
  setText(
    "human-denominator",
    `${Number(secondary.scored_units || 0)} human-reviewed developments in the index; ${Number(secondary.excluded_unclear || 0)} unclear and excluded.`,
  );
  renderBars("human-bars", barRows(secondary.status_distribution, STATUS_LABELS), STATUS_LABELS);
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

function historicalReferences() {
  return normaliseHistory().filter((row) => row.series_kind !== "weekly");
}

function weeklyHistory() {
  return normaliseHistory().filter((row) => row.series_kind === "weekly");
}

function filteredHistory() {
  const weekly = weeklyHistory();
  if (historyMode === "4") return weekly.slice(-4);
  if (historyMode === "12") return weekly.slice(-12);
  return weekly;
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

function periodsOverlap(a, b) {
  const aStart = parseDate(a?.period_start);
  const aEnd = parseDate(a?.period_end);
  const bStart = parseDate(b?.period_start);
  const bEnd = parseDate(b?.period_end);
  if (!aStart || !aEnd || !bStart || !bEnd) return false;
  return aStart <= bEnd && bStart <= aEnd;
}

function renderHistoryBaseline() {
  const container = document.getElementById("history-baseline");
  if (!container) return;
  const baseline = historicalReferences()[0];
  const firstWeekly = weeklyHistory()[0];
  if (!baseline) {
    container.hidden = true;
    container.innerHTML = "";
    return;
  }
  const overlapCopy = firstWeekly && periodsOverlap(baseline, firstWeekly)
    ? `It overlaps the first standardized week (${formatRange(firstWeekly.period_start, firstWeekly.period_end, true)}), so connecting the points would suggest a week-to-week comparison that the data do not support.`
    : "It is shown separately because it was collected before the standardized Monday-to-Sunday series.";
  container.innerHTML = `
    <div>
      <p class="eyebrow">Launch reference</p>
      <h3>${escapeHTML(formatRange(baseline.period_start, baseline.period_end, true))}</h3>
      <p>${escapeHTML(overlapCopy)}</p>
    </div>
    <dl>
      <div><dt>Coverage items</dt><dd>${escapeHTML(baseline.articles ?? "Not available")}</dd></div>
      <div><dt>Developments</dt><dd>${escapeHTML(baseline.event_records ?? "Not available")}</dd></div>
    </dl>
  `;
  container.hidden = false;
}

function renderHistoryChart() {
  const svg = document.getElementById("history-chart");
  const note = document.getElementById("history-note");
  const explainer = document.getElementById("history-explainer");
  if (!svg || !note || !explainer) return;
  const rows = filteredHistory();
  const allWeekly = weeklyHistory();
  const historical = historicalReferences();
  renderHistoryBaseline();
  svg.querySelectorAll(":scope > :not(title):not(desc)").forEach((node) => node.remove());

  explainer.textContent = allWeekly.length < 2
    ? `${allWeekly.length} standardized weekly release is available. A launch reference is shown separately above the chart.`
    : `${allWeekly.length} standardized weeks are available. Consecutive Monday-to-Sunday weeks are connected. The overlapping launch reference is shown separately above the chart.`;

  if (!rows.length) {
    svg.appendChild(svgElement("text", { x: 500, y: 210, "text-anchor": "middle", fill: "#5f7181" }, "No standardized weekly history is available yet."));
    note.textContent = historical.length
      ? "The launch reference is preserved above. The weekly line begins with the first standardized release."
      : "The first weekly point will appear after publication.";
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

  for (let index = 1; index < rows.length; index += 1) {
    const previous = rows[index - 1];
    const current = rows[index];
    if (!current.connect_to_previous) continue;
    [["articles", "#719cf5"], ["event_records", "#087985"]].forEach(([key, color]) => {
      svg.appendChild(svgElement("line", {
        x1: x(index - 1), y1: y(previous[key]),
        x2: x(index), y2: y(current[key]),
        stroke: color, "stroke-width": 3, "stroke-linecap": "round",
      }));
    });
  }

  rows.forEach((row, index) => {
    const xx = x(index);
    [["articles", "#719cf5", -5], ["event_records", "#087985", 5]].forEach(([key, color, offset]) => {
      svg.appendChild(svgElement("circle", {
        cx: xx + offset, cy: y(row[key]), r: 7,
        fill: color, stroke: color, "stroke-width": 1,
      }));
      svg.appendChild(svgElement("text", {
        x: xx + offset, y: y(row[key]) - 12, "text-anchor": "middle", fill: color, "font-size": 11, "font-weight": 800,
      }, row[key]));
    });
    const label = formatRange(row.period_start, row.period_end, true);
    svg.appendChild(svgElement("text", { x: xx, y: height - 52, "text-anchor": "middle", fill: "#415767", "font-size": 12, "font-weight": 800 }, label));
    svg.appendChild(svgElement("text", { x: xx, y: height - 34, "text-anchor": "middle", fill: "#71808a", "font-size": 10 }, String(row.release_id || "Weekly release")));
  });

  note.textContent = "Only consecutive standardized Monday-to-Sunday releases are connected. The launch reference is preserved separately because it overlaps the first standardized week.";
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
  return true;
}

function storyLocation(event) {
  const relationship = relationshipEventById(event);
  if (!relationship?.reviewed) return "Story location review pending";
  const reviewedCodes = Array.isArray(relationship.story_country_iso3s)
    ? relationship.story_country_iso3s.filter(Boolean)
    : [];
  if (reviewedCodes.length) return reviewedCodes.map((code) => markets[code]?.name || code).join(", ");
  return "Not established by the human-reviewed source evidence";
}

function evidenceScopeCopy(total = null) {
  const period = formatRange(currentRelease.period_start, currentRelease.period_end);
  const marketName = selectedMarket && markets[selectedMarket]
    ? ` found through the ${markets[selectedMarket].name} search`
    : "";
  const viewLabel = evidenceView === "new"
    ? "developments that were new to AIEO in this weekly release"
    : evidenceView === "recurring"
      ? "developments represented this week that AIEO had seen before"
      : "all developments represented in this weekly release";
  const countCopy = total == null ? "" : `${total} ${plural(total, "development")} shown. `;
  return `${countCopy}Scope: ${period}. This view contains ${viewLabel}${marketName}. Search market and story location are shown separately.`;
}
function setupEvidenceFilters() {
  const requested = new URLSearchParams(window.location.search).get("view");
  evidenceView = ["all", "new", "recurring"].includes(requested)
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
    container.innerHTML = "<p>No development in the current weekly release matches this filter and search market. Choose another current-week view or clear the market selection.</p>";
    more.hidden = true;
    return;
  }

  container.innerHTML = visible.map((event, index) => {
    const relationship = relationshipEventById(event);
    const reviewedEmpowerment = relationship?.reviewed ? relationship.empowerment_secondary : null;
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
    const lagDays = Number(event.replication_lag_days || 0);
    const recurrenceTiming = novelty === "recurring" && Number.isFinite(lagDays) && lagDays > 0
      ? `<span class="evidence-pill">Covered again after ${lagDays} ${plural(lagDays, "day")}</span>`
      : "";
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
              ${recurrenceTiming}
              <span class="relationship-badge">${escapeHTML(relationship?.reviewed ? (RELATIONSHIP_LABELS[relationship.configuration] || relationship.plain_label) : "Relationship review pending")}</span>
              <span class="evidence-pill">Secondary empowerment: ${escapeHTML(reviewedEmpowerment?.status ? (STATUS_LABELS[reviewedEmpowerment.status] || reviewedEmpowerment.status) : "review pending")}</span>
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
          ${relationship?.reviewed ? `
            <div class="relationship-evidence-grid">
              <div><strong>People side</strong><span>${escapeHTML(String(relationship.human_experience_type || "not established").replaceAll("_", " "))}</span></div>
              <div><strong>AI or operator side</strong><span>${escapeHTML(String(relationship.ai_expressive_role || "not established").replaceAll("_", " "))}</span></div>
            </div>
            ${relationship.evidence_summary ? `<p><strong>Human-reviewed evidence summary:</strong> ${escapeHTML(relationship.evidence_summary)}</p>` : ""}
            ${relationship.reasoning ? `<p><strong>Why this relationship pattern:</strong> ${escapeHTML(relationship.reasoning)}</p>` : ""}
          ` : `<p><strong>Relationship lens:</strong> Human review is still pending for this development.</p>`}
          ${reviewedEmpowerment?.reasoning ? `<details><summary>Secondary empowerment reasoning</summary><p>${escapeHTML(reviewedEmpowerment.reasoning)}</p></details>` : ""}
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
    const [release, index, countryData, symbiosis] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(INDEX_URL),
      fetchJSON(COUNTRIES_URL),
      fetchJSON(SYMBIOSIS_URL, true),
    ]);
    currentRelease = release;
    currentSymbiosis = symbiosis;
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
