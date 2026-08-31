"use strict";

import { initDiscoveryGlobe } from "/globe.js";

const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const COUNTRIES_URL = "/edu/countries.json";
const SYMBIOSIS_URL = "/data/symbiosis/current.json";

const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });
const dateShort = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });

const RELATIONSHIP_LABELS = {
  mutualism: "Both gain",
  ai_benefiting_parasitism: "AI side gains, people are constrained",
  human_benefiting_parasitism: "People gain, AI side is constrained",
  competition: "Both are constrained",
  human_enabling_only: "People-side gain only",
  human_constraining_only: "People-side constraint only",
  ai_enabling_only: "AI-side gain only",
  ai_constraining_only: "AI-side constraint only",
  no_clear_relational_signal: "No clear relationship",
  ambiguous_relational_signal: "Direction unclear",
  insufficient_evidence: "Insufficient evidence",
};

const SIDE_LABELS = {
  extension: "↑ enabling through use",
  expansion: "↑ gaining capacity",
  restriction: "↓ losing autonomy or control",
  reduction: "↓ losing capacity",
  ai_extension: "↑ useful or reliable output",
  ai_expansion: "↑ system or operator gains",
  ai_restriction: "↓ system limits or blocks",
  ai_reduction: "↓ system degrades or fails",
  neutral: "↔ no directional signal",
  unclear: "? direction unclear",
};

let currentRelease = null;
let releaseIndex = null;
let currentSymbiosis = null;
let markets = {};
let selectedMarket = null;
let globe = null;
let evidenceLimit = 6;
let evidenceView = normalizeEvidenceView(new URLSearchParams(window.location.search).get("view"));
let coverageByArticle = new Map();

function normalizeEvidenceView(value) {
  if (value === "resurfaced") return "recurring";
  return ["all", "new", "recurring", "review"].includes(value) ? value : "all";
}

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
    const url = new URL(String(value || ""));
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch {
    return "#";
  }
}

function parseDate(value) {
  const date = new Date(`${String(value || "").slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatRange(startValue, endValue) {
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (!start || !end) return "Date unavailable";
  const sameMonth = start.getUTCMonth() === end.getUTCMonth() && start.getUTCFullYear() === end.getUTCFullYear();
  return sameMonth ? `${start.getUTCDate()}-${dateLong.format(end)}` : `${dateLong.format(start)}-${dateLong.format(end)}`;
}

function formatShortRange(startValue, endValue) {
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (!start || !end) return "Date unavailable";
  if (start.toISOString().slice(0, 10) === end.toISOString().slice(0, 10)) return dateShort.format(start);
  return `${dateShort.format(start)}-${dateShort.format(end)}`;
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "Not available");
}

async function fetchJSON(url, optional = false) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
    return response.json();
  } catch (error) {
    if (optional) {
      console.info(`${url} is not available yet`, error);
      return null;
    }
    throw error;
  }
}

function periodCounts(release) {
  const raw = release?.counts || {};
  const articles = Number(raw.ai_relevant_articles || 0);
  const events = Number(raw.ai_relevant_event_records || 0);
  const first = Number(raw.first_time_event_records ?? raw.new_event_records ?? 0);
  const followOn = Number(raw.follow_on_event_records || 0);
  const newDevelopments = Number(raw.new_event_records ?? (first + followOn));
  const possible = Number(raw.possible_historical_match_event_records || 0);
  const unclassified = Number(raw.unclassified_novelty_event_records || 0);
  const recurring = Number(raw.recurring_event_records ?? Math.max(0, events - newDevelopments - possible - unclassified));
  const extra = Number.isFinite(Number(raw.extra_coverage)) ? Number(raw.extra_coverage) : Math.max(0, articles - events);
  return { articles, events, first, followOn, newDevelopments, recurring, possible, unclassified, extra };
}

function weeklyRows(index) {
  const rows = Array.isArray(index?.weekly) ? index.weekly.slice() : [];
  return rows.sort((a, b) => String(a.period_start || "").localeCompare(String(b.period_start || "")));
}

function rowNumber(row, keys) {
  for (const key of keys) {
    if (row && row[key] != null && Number.isFinite(Number(row[key]))) return Number(row[key]);
  }
  return 0;
}

function changeMeta(current, previous) {
  if (!Number.isFinite(previous)) return { className: "flat", arrow: "", text: "current" };
  const delta = current - previous;
  if (delta === 0) return { className: "flat", arrow: "→", text: "0" };
  return { className: delta > 0 ? "up" : "down", arrow: delta > 0 ? "↑" : "↓", text: `${Math.abs(delta)}` };
}

function renderTape() {
  const container = document.getElementById("weekly-tape");
  const c = periodCounts(currentRelease);
  const rows = weeklyRows(releaseIndex);
  const currentRow = rows.find((row) => row.release_id === currentRelease.release_id) || rows.at(-1) || null;
  const idx = currentRow ? rows.indexOf(currentRow) : rows.length - 1;
  const previous = idx > 0 ? rows[idx - 1] : null;
  const values = [
    { label: "Coverage", value: c.articles, previous: previous ? rowNumber(previous, ["articles", "ai_relevant_articles"]) : NaN, note: "AI-news source pages", href: "?view=all#evidence" },
    { label: "Developments", value: c.events, previous: previous ? rowNumber(previous, ["event_records", "ai_relevant_event_records"]) : NaN, note: "distinct occurrences", href: "#history" },
    { label: "Additional coverage", value: c.extra, previous: previous ? rowNumber(previous, ["extra_coverage"]) : NaN, note: "extra source pages, not extra developments", href: "?view=all#evidence" },
    { label: "First-time developments", value: c.newDevelopments, previous: NaN, note: "not established in an earlier standardized release", href: "?view=new#evidence" },
  ];
  container.innerHTML = values.map((item) => {
    const change = changeMeta(item.value, item.previous);
    return `<a class="tape-item" href="${item.href}"><span>${escapeHTML(item.label)}</span><strong>${item.value}</strong><b class="tape-change ${change.className}">${change.arrow} ${change.text}</b><small>${escapeHTML(item.note)}</small></a>`;
  }).join("");
}

function renderOpening() {
  const c = periodCounts(currentRelease);
  const pending = c.possible + c.unclassified;
  const extraSentence = c.extra
    ? ` ${c.extra} additional ${plural(c.extra, "source page")} covered a development already counted.`
    : "";
  const noveltySentence = pending
    ? `${c.newDevelopments} were not established in an earlier standardized release, ${c.recurring} were recurring, and ${pending} ${plural(pending, "historical match is", "historical matches are")} still being validated.`
    : `${c.newDevelopments} were not established in an earlier standardized release and ${c.recurring} were recurring.`;
  setText("week-badge", `Current weekly signal | ${formatRange(currentRelease.period_start, currentRelease.period_end)}`);
  setText("week-intro", `${c.articles} AI-news source pages were grouped into ${c.events} distinct developments. ${noveltySentence}${extraSentence}`);
  setText("fact-new", `${c.newDevelopments} first recorded`);
  setText("fact-seen", `${c.recurring} recurring`);
  const reviewFact = document.getElementById("fact-review");
  if (reviewFact) {
    reviewFact.hidden = pending === 0;
    reviewFact.textContent = `${pending} ${plural(pending, "history match", "history matches")} under validation`;
  }
  setText("week-equation-coverage", `${c.articles} source pages = ${c.events} developments + ${c.extra} additional ${plural(c.extra, "source page")} about developments already counted.`);
  setText("week-equation-novelty", pending
    ? `${c.events} developments = ${c.newDevelopments} first recorded + ${c.recurring} recurring + ${pending} history-match ${plural(pending, "case", "cases")} under validation.`
    : `${c.events} developments = ${c.newDevelopments} first recorded + ${c.recurring} recurring.`);
}

function renderMarketSelection(selection) {
  setText("edu-market-selection-summary", selection?.summary || "AIEO uses a five-market pilot across English, French and Chinese news environments.");
  const sources = document.getElementById("edu-market-selection-sources");
  if (sources) {
    sources.innerHTML = (selection?.sources || []).map((row) => `<a href="${escapeHTML(safeUrl(row.url))}" target="_blank" rel="noopener noreferrer">${escapeHTML(row.name || "Source")}</a>`).join("");
  }
}

function marketCoverageRows(iso3) {
  return (currentRelease.units?.coverage_articles || []).filter((row) => row.classification?.ai_relevant && (row.search_markets || []).includes(iso3));
}

function topPublishers(rows) {
  const counts = new Map();
  rows.forEach((row) => {
    const publisher = String(row.publisher || "Unknown publication");
    counts.set(publisher, (counts.get(publisher) || 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3);
}

function renderMarketButtons() {
  const container = document.getElementById("edu-market-buttons");
  container.innerHTML = Object.entries(markets).map(([iso3, market]) => `<button type="button" data-market="${escapeHTML(iso3)}" aria-pressed="${String(selectedMarket === iso3)}">${escapeHTML(market.short_name || market.name)}</button>`).join("");
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
  if (!selectedMarket || !markets[selectedMarket]) {
    card.firstElementChild.innerHTML = "<strong>Choose a market</strong><p>See the sources found through that Google News search.</p>";
    clear.hidden = true;
    return;
  }
  const market = markets[selectedMarket];
  const rows = marketCoverageRows(selectedMarket);
  const fallback = Number(currentRelease.sources?.discovery_markets?.[selectedMarket] || 0);
  const count = rows.length || fallback;
  const leaders = topPublishers(rows).map(([name, value]) => `${name} (${value})`).join(", ");
  card.firstElementChild.innerHTML = `<strong>${count} coverage ${plural(count, "item")} found through ${escapeHTML(market.name)}</strong><p>${leaders ? `Most visible: ${escapeHTML(leaders)}.` : "Open the evidence below for source details."}</p>`;
  clear.hidden = false;
}

function selectMarket(iso3) {
  selectedMarket = iso3 && markets[iso3] ? iso3 : null;
  renderMarketButtons();
  renderMarketCard();
  renderEvidence();
}

function relationshipEventById(event) {
  const id = String(event.effective_event_id || event.event_id || "");
  return (currentSymbiosis?.evidence || []).find((row) => String(row.event_id || "") === id) || null;
}

function renderRelationship() {
  const banner = document.getElementById("relationship-review-banner");
  const core = document.getElementById("relationship-core");
  if (!currentSymbiosis) {
    banner.hidden = false;
    banner.textContent = "Relationship classification is not available for this release yet.";
    core.hidden = true;
    setText("relationship-scope", "Classification pending");
    setText("relationship-other-summary", "The core weekly counts and source evidence remain available.");
    return;
  }

  const status = String(currentSymbiosis.public_status || "classification_in_progress");
  const event = currentSymbiosis.event || {};
  const humanReviewed = status === "human_reviewed" && Boolean(currentSymbiosis.review?.event_complete);
  const classified = humanReviewed
    ? Number(event.classified_units || event.expected_units || 0)
    : Number(event.display_classified_units || event.classified_units || 0);
  if (status === "classification_in_progress" || status === "review_in_progress" || classified === 0) {
    banner.hidden = false;
    banner.textContent = "Relationship classification is still running. This layer will refresh automatically when it finishes.";
    core.hidden = true;
    setText("relationship-scope", "Classification in progress");
    setText("relationship-other-summary", "The core weekly counts are already published; no older relationship percentages are substituted.");
    return;
  }

  banner.hidden = humanReviewed;
  if (!humanReviewed) {
    banner.textContent = "Model-coded provisional signal. Accepted human corrections replace model outputs as review proceeds.";
  }
  core.hidden = false;
  const counts = humanReviewed ? (event.configuration_counts || {}) : (event.display_configuration_counts || event.configuration_counts || {});
  const complete = humanReviewed
    ? Number(event.complete_configuration_count || 0)
    : Number(event.display_complete_configuration_count ?? event.complete_configuration_count ?? 0);
  const entries = [
    ["mutualism", "relationship-mutualism", "relationship-mutualism-share"],
    ["ai_benefiting_parasitism", "relationship-ai-benefit", "relationship-ai-benefit-share"],
    ["human_benefiting_parasitism", "relationship-human-benefit", "relationship-human-benefit-share"],
    ["competition", "relationship-competition", "relationship-competition-share"],
  ];
  entries.forEach(([key, countId, shareId]) => {
    const count = Number(counts[key] || 0);
    setText(countId, count);
    setText(shareId, complete ? `${((count / complete) * 100).toFixed(0)}%` : "0%");
  });
  setText("relationship-scope", humanReviewed
    ? `${complete} human-reviewed developments had evidence for both sides`
    : `${complete} model-coded developments had evidence for both sides`);
  const partial = Number(humanReviewed ? event.partial_signal_count : (event.display_partial_signal_count ?? event.partial_signal_count) || 0);
  const none = Number(humanReviewed ? event.no_clear_relational_signal_count : (event.display_no_clear_relational_signal_count ?? event.no_clear_relational_signal_count) || 0);
  const ambiguous = Number(humanReviewed ? event.ambiguous_relational_signal_count : (event.display_ambiguous_relational_signal_count ?? event.ambiguous_relational_signal_count) || 0);
  const insufficient = Number(humanReviewed ? event.insufficient_evidence_count : (event.display_insufficient_evidence_count ?? event.insufficient_evidence_count) || 0);
  setText("relationship-other-summary", `${partial} one-sided signals, ${none} no-clear cases, ${ambiguous} ambiguous cases, and ${insufficient} insufficient-evidence cases are outside the four-pattern percentages.`);
}

function eventDiscoveryMarkets(event) {
  const values = new Set();
  for (const articleId of event.member_article_ids || []) {
    for (const iso3 of coverageByArticle.get(String(articleId))?.search_markets || []) values.add(String(iso3));
  }
  return [...values].filter((iso3) => markets[iso3]);
}

function eventMatchesMarket(event) {
  return !selectedMarket || eventDiscoveryMarkets(event).includes(selectedMarket);
}

function eventMatchesView(event) {
  const novelty = String(event.novelty_status || "");
  if (evidenceView === "new") return novelty === "first_time" || novelty === "follow_on_development" || Boolean(event.first_time_in_period) || Boolean(event.follow_on_development);
  if (evidenceView === "recurring") return novelty === "recurring" || Boolean(event.recurring_in_period);
  if (evidenceView === "review") return novelty === "possible_historical_match" || novelty === "unclassified" || Boolean(event.possible_historical_match);
  return true;
}

function relationshipArrows(row) {
  if (!row) return { people: "People ?", ai: "AI ?" };
  const human = row.human_direction === "enabling" ? "People ↑" : row.human_direction === "constraining" ? "People ↓" : row.human_direction === "neutral" ? "People ↔" : "People ?";
  const ai = row.ai_direction === "enabling" ? "AI ↑" : row.ai_direction === "constraining" ? "AI ↓" : row.ai_direction === "neutral" ? "AI ↔" : "AI ?";
  return { people: human, ai };
}

function storyLocation(event, relationship) {
  const codes = Array.isArray(relationship?.story_country_iso3s) ? relationship.story_country_iso3s.filter(Boolean) : [];
  if (codes.length) return codes.map((code) => markets[code]?.name || code).join(", ");
  const legacy = event.classification?.country_iso3s || [];
  if (legacy.length) return legacy.map((code) => markets[code]?.name || code).join(", ");
  return "Not established from the available evidence";
}

function evidenceScopeCopy(count) {
  const marketCopy = selectedMarket ? ` found through the ${markets[selectedMarket]?.name || selectedMarket} search` : "";
  if (evidenceView === "new") return `${count} first-recorded ${plural(count, "development")} shown${marketCopy}.`;
  if (evidenceView === "recurring") return `${count} recurring ${plural(count, "development")} covered this week${marketCopy}.`;
  if (evidenceView === "review") return `${count} ${plural(count, "development has", "developments have")} a cross-week history match still being validated${marketCopy}.`;
  return `${count} current-week ${plural(count, "development")} shown${marketCopy}.`;
}

function renderEvidence() {
  const container = document.getElementById("evidence-list");
  const more = document.getElementById("show-more-evidence");
  const events = (currentRelease.evidence || []).filter(eventMatchesMarket).filter(eventMatchesView).sort((a, b) => Number(b.member_article_count || 0) - Number(a.member_article_count || 0));
  setText("evidence-scope", evidenceScopeCopy(events.length));
  document.querySelectorAll("[data-evidence-view]").forEach((link) => link.setAttribute("aria-current", String(link.dataset.evidenceView === evidenceView)));
  const visible = events.slice(0, evidenceLimit);
  if (!visible.length) {
    container.innerHTML = "<p>No current-week development matches this filter. Choose another view or clear the market selection.</p>";
    more.hidden = true;
    return;
  }
  container.innerHTML = visible.map((event, index) => {
    const relationship = relationshipEventById(event);
    const hasRelationship = Boolean(relationship?.configuration);
    const arrows = relationshipArrows(relationship);
    const sources = event.sources || [];
    const sourceDates = sources.map((source) => String(source.published_date || "")).filter(Boolean).sort();
    const eventDate = sourceDates.length
      ? formatShortRange(sourceDates[0], sourceDates.at(-1))
      : formatShortRange(event.event_date, event.event_date);
    const marketsFound = eventDiscoveryMarkets(event);
    const novelty = String(event.novelty_status || "");
    const noveltyLabel = novelty === "recurring" || event.recurring_in_period
      ? "Recurring"
      : novelty === "follow_on_development" || event.follow_on_development
        ? "First recorded · follow-on"
        : novelty === "first_time" || event.first_time_in_period
          ? "First recorded"
          : novelty === "possible_historical_match" || event.possible_historical_match
            ? "History match under validation"
            : "History status under validation";
    const relationshipText = hasRelationship ? (RELATIONSHIP_LABELS[relationship.configuration] || relationship.plain_label || "Relationship pattern") : "";
    const relationshipBadge = hasRelationship
      ? `<span class="relationship-badge" title="${relationship.reviewed ? "Human-reviewed" : "Model-coded provisional"}">${escapeHTML(relationshipText)}</span>`
      : "";
    const relationshipSnapshot = hasRelationship
      ? `<div class="relationship-snapshot"><div class="relationship-side"><strong>${arrows.people}</strong><span>${escapeHTML(SIDE_LABELS[relationship?.human_experience_type] || "No directional signal")}</span></div><div class="relationship-side"><strong>${arrows.ai}</strong><span>${escapeHTML(SIDE_LABELS[relationship?.ai_expressive_role] || "No directional signal")}</span></div></div>`
      : "";
    const shouldOpen = evidenceView === "review" && events.length === 1 && index === 0;
    const marketButtons = marketsFound.length ? marketsFound.map((iso3) => `<button type="button" class="market-evidence-chip" data-globe-market="${escapeHTML(iso3)}">${escapeHTML(markets[iso3]?.name || iso3)} search</button>`).join("") : "Search market not available";
    return `
      <details class="evidence-card" ${shouldOpen ? "open" : ""}>
        <summary><div><h3>${escapeHTML(event.event_title || "Untitled development")}</h3><div class="evidence-meta"><span>${escapeHTML(eventDate)}</span><span class="evidence-pill">${sources.length} ${plural(sources.length, "source")}</span><span class="evidence-pill">${escapeHTML(noveltyLabel)}</span>${relationshipBadge}</div></div></summary>
        <div class="evidence-body">
          ${relationshipSnapshot}
          <div class="source-links">${sources.map((source) => `<a href="${escapeHTML(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer"><span><strong>${escapeHTML(source.publisher || "Publication")}</strong>: ${escapeHTML(source.headline || "Open source")}</span><small>${escapeHTML(source.published_date || "")}</small></a>`).join("")}</div>
          <details class="compact-more"><summary>Context and classification detail</summary><p><strong>Found through:</strong></p><div class="market-evidence-chips">${marketButtons}</div><p><strong>Story location:</strong> ${escapeHTML(storyLocation(event, relationship))}</p>${relationship?.evidence_summary ? `<p><strong>Evidence summary:</strong> ${escapeHTML(relationship.evidence_summary)}</p>` : ""}${relationship?.reasoning ? `<p><strong>Why this pattern:</strong> ${escapeHTML(relationship.reasoning)}</p>` : ""}</details>
        </div>
      </details>
    `;
  }).join("");
  container.querySelectorAll("button[data-globe-market]").forEach((button) => button.addEventListener("click", () => {
    selectMarket(button.dataset.globeMarket);
    globe?.selectMarket(button.dataset.globeMarket, { notify: false });
    document.getElementById("explore")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  const remaining = events.length - visible.length;
  more.hidden = remaining <= 0;
  if (remaining > 0) more.textContent = `Show next ${Math.min(6, remaining)} ${plural(Math.min(6, remaining), "development")}`;
}

function deltaText(current, previous) {
  if (!Number.isFinite(previous)) return { className: "flat", text: "First standardized week" };
  const delta = current - previous;
  const pct = previous ? (delta / previous) * 100 : 0;
  if (delta === 0) return { className: "flat", text: "→ no change" };
  return { className: delta > 0 ? "up" : "down", text: `${delta > 0 ? "↑" : "↓"} ${Math.abs(delta)} (${Math.abs(pct).toFixed(1)}%)` };
}

function renderHistoryComparison() {
  const rows = weeklyRows(releaseIndex);
  const container = document.getElementById("history-comparison");
  const current = rows.at(-1);
  const previous = rows.length > 1 ? rows.at(-2) : null;
  setText("history-explainer", `${rows.length} standardized ${plural(rows.length, "week")} available`);
  if (!current) {
    container.innerHTML = "<p>No standardized weekly history is available yet.</p>";
    return;
  }
  const metrics = [
    ["Coverage items", ["articles", "ai_relevant_articles"]],
    ["Distinct developments", ["event_records", "ai_relevant_event_records"]],
    ["Additional coverage", ["extra_coverage"]],
  ];
  container.innerHTML = metrics.map(([label, keys]) => {
    const now = rowNumber(current, keys);
    const before = previous ? rowNumber(previous, keys) : NaN;
    const delta = deltaText(now, before);
    return `<article class="history-row"><h3>${label}</h3><div class="history-values">${previous ? `<strong>${before}</strong><i>to</i>` : ""}<strong>${now}</strong></div><span class="history-change ${delta.className}">${delta.text}</span></article>`;
  }).join("");
  const baseline = (releaseIndex.historical_snapshots || releaseIndex.baselines || []).find(Boolean);
  if (baseline) setText("history-baseline-copy", `The launch reference covers ${formatRange(baseline.period_start, baseline.period_end)} and overlaps the first standardized week. It recorded ${rowNumber(baseline, ["articles", "coverage_count"])} coverage items and ${rowNumber(baseline, ["event_records", "event_count"])} developments.`);
}

function svgElement(name, attrs = {}, text = "") {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, String(value)));
  if (text) element.textContent = text;
  return element;
}

function renderHistoryChart() {
  const rows = weeklyRows(releaseIndex);
  const svg = document.getElementById("history-chart");
  const note = document.getElementById("history-note");
  svg.replaceChildren();
  if (!rows.length) return;
  const width = 940, height = 260, left = 60, right = 28, top = 26, bottom = 54;
  const values = rows.flatMap((row) => [rowNumber(row, ["articles"]), rowNumber(row, ["event_records"])]);
  const min = Math.max(0, Math.min(...values) - 8);
  const max = Math.max(...values) + 8;
  const x = (i) => rows.length === 1 ? (left + width - right) / 2 : left + (i / (rows.length - 1)) * (width - left - right);
  const y = (value) => top + ((max - value) / Math.max(1, max - min)) * (height - top - bottom);
  [0, .5, 1].forEach((fraction) => {
    const value = Math.round(max - fraction * (max - min));
    const yy = y(value);
    svg.appendChild(svgElement("line", { x1: left, x2: width - right, y1: yy, y2: yy, stroke: "#dbe3e7", "stroke-width": 1 }));
    svg.appendChild(svgElement("text", { x: left - 10, y: yy + 4, "text-anchor": "end", fill: "#607386", "font-size": 12 }, value));
  });
  const pathFor = (keys) => rows.map((row, i) => `${i ? "L" : "M"}${x(i)},${y(rowNumber(row, keys))}`).join(" ");
  svg.appendChild(svgElement("path", { d: pathFor(["articles"]), fill: "none", stroke: "#6b94f7", "stroke-width": 3 }));
  svg.appendChild(svgElement("path", { d: pathFor(["event_records"]), fill: "none", stroke: "#087d86", "stroke-width": 3 }));
  rows.forEach((row, i) => {
    [["articles", "#6b94f7"], ["event_records", "#087d86"]].forEach(([key, color]) => svg.appendChild(svgElement("circle", { cx: x(i), cy: y(rowNumber(row, [key])), r: 5, fill: color })));
    svg.appendChild(svgElement("text", { x: x(i), y: height - 23, "text-anchor": "middle", fill: "#40576d", "font-size": 12, "font-weight": 700 }, formatShortRange(row.period_start, row.period_end)));
  });
  note.textContent = "Only completed, standardized Monday-to-Sunday weeks are connected.";
}

function empowermentPlain(index) {
  if (index == null || !Number.isFinite(Number(index))) return "Not enough scored evidence";
  const value = Number(index);
  if (Math.abs(value) < 5) return "Near neutral";
  if (value >= 5 && value < 20) return "Slightly more enabling";
  if (value >= 20 && value < 45) return "More enabling";
  if (value >= 45) return "Strongly more enabling";
  if (value <= -5 && value > -20) return "Slightly more constraining";
  if (value <= -20 && value > -45) return "More constraining";
  return "Strongly more constraining";
}

function renderEmpowerment() {
  const lens = currentRelease?.lenses?.event || {};
  const index = lens.empowerment_index;
  const scored = Number(lens.unit_count_scored || 0);
  const unclear = Number(lens.unit_count_excluded_unclear || 0);
  const evidenceRows = (currentRelease?.evidence || []).filter((row) => row.classification?.ai_relevant !== false);
  const counts = { expanding: 0, contracting: 0, mixed: 0, non_empowerment: 0, unclear: 0 };
  evidenceRows.forEach((row) => {
    const key = String(row.classification?.empowerment_status || "unclear");
    if (Object.hasOwn(counts, key)) counts[key] += 1;
    else counts.unclear += 1;
  });
  setText("human-plain-label", empowermentPlain(index));
  setText("human-index", index == null ? "No scored index" : `${Number(index) >= 0 ? "+" : ""}${Number(index).toFixed(2)} on a -100 to +100 scale`);
  setText("human-denominator", `${scored} scored, ${unclear} unclear · model-coded weekly lens`);
  const marker = document.getElementById("human-gauge-marker");
  if (marker && index != null) marker.style.left = `${Math.max(0, Math.min(100, (Number(index) + 100) / 2))}%`;
  const labels = [
    ["expanding", "Enabling"],
    ["contracting", "Constraining"],
    ["mixed", "Mixed"],
    ["non_empowerment", "No direct change"],
    ["unclear", "Unclear"],
  ];
  const total = evidenceRows.length;
  document.getElementById("human-bars").innerHTML = labels.map(([key, label]) => {
    const count = Number(counts[key] || 0);
    const pct = total ? (count / total) * 100 : 0;
    return `<div class="bar-row"><span>${label}</span><div class="bar-track"><i style="width:${pct}%"></i></div><strong>${count}</strong></div>`;
  }).join("");
}

function setupEvidence() {
  if (new URLSearchParams(window.location.search).get("view") === "resurfaced") {
    const url = new URL(window.location.href);
    url.searchParams.set("view", "recurring");
    history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
  }
  document.getElementById("show-more-evidence")?.addEventListener("click", () => {
    evidenceLimit += 6;
    renderEvidence();
  });
}

function setupInfoPopovers() {
  document.addEventListener("click", (event) => {
    document.querySelectorAll("details.info-popover[open]").forEach((detail) => {
      if (!detail.contains(event.target)) detail.removeAttribute("open");
    });
  });
}

async function initialiseGlobe() {
  try {
    globe = await initDiscoveryGlobe({ containerId: "edu-globe", toggleId: "edu-globe-toggle", promptId: "edu-globe-prompt", fallbackId: "edu-globe-fallback", markets, onSelect: (iso3) => selectMarket(iso3) });
  } catch (error) {
    console.warn("Globe unavailable; market buttons remain active", error);
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
  setupEvidence();
  setupInfoPopovers();
  try {
    const [release, index, countryData, symbiosis] = await Promise.all([fetchJSON(CURRENT_URL), fetchJSON(INDEX_URL), fetchJSON(COUNTRIES_URL), fetchJSON(SYMBIOSIS_URL, true)]);
    currentRelease = release;
    releaseIndex = index;
    currentSymbiosis = symbiosis && String(symbiosis.release_id || "") === String(release.release_id || "") ? symbiosis : null;
    markets = countryData.markets || {};
    coverageByArticle = new Map((currentRelease.units?.coverage_articles || []).map((row) => [String(row.article_id), row]));
    renderMarketSelection(countryData.selection || {});
    renderTape();
    renderOpening();
    renderRelationship();
    renderEvidence();
    renderHistoryComparison();
    renderHistoryChart();
    renderEmpowerment();
    await initialiseGlobe();
  } catch (error) {
    console.error("Current-signal page could not initialise", error);
    setText("week-badge", "Current signal temporarily unavailable");
    setText("week-intro", "The current release could not be loaded. Please try again shortly.");
    document.getElementById("evidence-list").innerHTML = "<p>Source-linked evidence is temporarily unavailable.</p>";
  }
}

init();
