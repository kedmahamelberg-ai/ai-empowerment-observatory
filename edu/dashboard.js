"use strict";

import { initDiscoveryGlobe } from "/globe.js?v=6.1.0";

const BUILD_ID = "6.3.0";
const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const COUNTRIES_URL = "/edu/countries.json";
const SYMBIOSIS_URL = "/data/symbiosis/current.json";
const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });
const dateShort = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });

const PRIMARY_OUTCOME_LABELS = {
  benefit_shown: "A benefit was reported",
  downside_shown: "A downside was reported",
  benefit_and_downside: "Both were reported",
  no_clear_people_change: "No clear change was reported",
  too_little_evidence: "Too little evidence",
};

let currentRelease = null;
let releaseIndex = null;
let currentSymbiosis = null;
let signalSummary = null;
let markets = {};
let selectedMarket = null;
let globe = null;
let evidenceLimit = 8;
let coverageByArticle = new Map();
const initialParams = new URLSearchParams(window.location.search);
let evidenceView = normalizeEvidenceView(initialParams.get("view"));
let signalView = normalizeSignalView(initialParams.get("signal"));

function normalizeEvidenceView(value) {
  if (value === "resurfaced") return "recurring";
  return ["all", "new", "recurring", "review"].includes(value) ? value : "all";
}

function normalizeSignalView(value) {
  return ["all", ...Object.keys(PRIMARY_OUTCOME_LABELS)].includes(value) ? value : "all";
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
  if (!start || !end) return "Current week";
  const sameMonth = start.getUTCMonth() === end.getUTCMonth() && start.getUTCFullYear() === end.getUTCFullYear();
  return sameMonth ? `${start.getUTCDate()} to ${dateLong.format(end)}` : `${dateLong.format(start)} to ${dateLong.format(end)}`;
}

function formatShortRange(startValue, endValue) {
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (!start || !end) return "Date unavailable";
  if (start.toISOString().slice(0, 10) === end.toISOString().slice(0, 10)) return dateShort.format(start);
  return `${dateShort.format(start)} to ${dateShort.format(end)}`;
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "Not available");
}

function formatPercent(value, total) {
  const denominator = Number(total || 0);
  if (!denominator) return "Not available";
  return `${((Number(value || 0) / denominator) * 100).toFixed(1)}%`;
}

async function fetchJSON(url, optional = false) {
  try {
    const separator = url.includes("?") ? "&" : "?";
    const response = await fetch(`${url}${separator}build=${BUILD_ID}&t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
    return response.json();
  } catch (error) {
    if (optional) return null;
    throw error;
  }
}

function periodCounts(release) {
  const raw = release?.counts || {};
  const articles = Number(raw.ai_relevant_articles || 0);
  const events = Number(raw.ai_relevant_event_records || 0);
  const first = Number(raw.first_time_event_records ?? raw.new_event_records ?? 0);
  const followOn = Number(raw.follow_on_event_records || 0);
  const firstRecorded = Number(raw.new_event_records ?? (first + followOn));
  const possible = Number(raw.possible_historical_match_event_records || 0);
  const unclassified = Number(raw.unclassified_novelty_event_records || 0);
  const recurring = Number(raw.recurring_event_records ?? Math.max(0, events - firstRecorded - possible - unclassified));
  const extra = Number.isFinite(Number(raw.extra_coverage)) ? Number(raw.extra_coverage) : Math.max(0, articles - events);
  return { articles, events, firstRecorded, recurring, review: possible + unclassified, extra };
}

function weeklyRows(index) {
  return (Array.isArray(index?.weekly) ? index.weekly.slice() : []).sort((a, b) => String(a.period_start || "").localeCompare(String(b.period_start || "")));
}

function rowNumber(row, keys) {
  for (const key of keys) {
    if (row && row[key] != null && Number.isFinite(Number(row[key]))) return Number(row[key]);
  }
  return 0;
}

function plainSignalData(symbiosis) {
  const total = periodCounts(currentRelease).events;
  if (!symbiosis || String(symbiosis.release_id || "") !== String(currentRelease.release_id || "")) return null;
  if (symbiosis.people_signals) return symbiosis.people_signals;
  const event = symbiosis.event || {};
  const counts = event.display_configuration_counts || event.configuration_counts || {};
  const classified = Number(event.display_classified_units ?? event.classified_units ?? 0);
  const gaining = Number(counts.mutualism || 0) + Number(counts.human_benefiting_parasitism || 0) + Number(counts.human_enabling_only || 0);
  const losing = Number(counts.ai_benefiting_parasitism || 0) + Number(counts.competition || 0) + Number(counts.human_constraining_only || 0);
  return {
    expected_units: total,
    classified_units: classified,
    people_signal_counts: { people_gaining: gaining, people_losing_ground: losing, mixed_picture: 0, not_everyone_benefits: 0, not_clear_yet: Math.max(0, total - gaining - losing) },
    relationship_pattern_counts: { mutualism: Number(counts.mutualism || 0), ai_benefiting_parasitism: Number(counts.ai_benefiting_parasitism || 0), human_benefiting_parasitism: Number(counts.human_benefiting_parasitism || 0), competition: Number(counts.competition || 0) },
    availability: { people_gaining: true, people_losing_ground: true, mixed_picture: false, not_everyone_benefits: false, not_clear_yet: true },
  };
}

function fullBodyEvidenceCount(signalData) {
  const counts = signalData?.body_coverage_counts || {};
  return Number(counts.all_sources || 0)
    + Number(counts.some_sources || 0)
    + Number(counts.owner_supplied_full_body || 0);
}

function primaryOutcomeFor(relationship) {
  const signals = relationship?.public_signals || {};
  const gaining = Boolean(signals.people_gaining);
  const losing = Boolean(signals.people_losing_ground);
  if (gaining && losing) return "benefit_and_downside";
  if (gaining) return "benefit_shown";
  if (losing) return "downside_shown";
  if (String(relationship?.evidence_status || "") === "insufficient") return "too_little_evidence";
  return "no_clear_people_change";
}

function primaryOutcomeSummary() {
  const total = Number(signalSummary?.expected_units || periodCounts(currentRelease).events || 0);
  const counts = Object.fromEntries(Object.keys(PRIMARY_OUTCOME_LABELS).map((key) => [key, 0]));
  const basis = Object.fromEntries(Object.keys(PRIMARY_OUTCOME_LABELS).map((key) => [key, { fullBody: 0, other: 0 }]));
  const rows = Array.isArray(currentSymbiosis?.evidence) ? currentSymbiosis.evidence : [];
  let uneven = 0;
  if (rows.length === total && total > 0) {
    rows.forEach((row) => {
      const key = primaryOutcomeFor(row);
      counts[key] += 1;
      if (Number(row?.evidence_basis_summary?.full_text_sources || 0) > 0) basis[key].fullBody += 1;
      else basis[key].other += 1;
      if (row?.public_signals?.not_everyone_benefits) uneven += 1;
    });
    return { counts, basis, uneven };
  }
  const legacy = signalSummary?.people_signal_counts || {};
  const breakdown = signalSummary?.not_clear_breakdown || {};
  counts.benefit_shown = Number(legacy.people_gaining || 0);
  counts.downside_shown = Number(legacy.people_losing_ground || 0);
  counts.benefit_and_downside = Number(legacy.mixed_picture || 0);
  counts.too_little_evidence = Number(breakdown.not_enough_evidence || 0);
  counts.no_clear_people_change = Math.max(0, total - counts.benefit_shown - counts.downside_shown - counts.benefit_and_downside - counts.too_little_evidence);
  uneven = Number(legacy.not_everyone_benefits || 0);
  return { counts, basis, uneven };
}

function outcomeStatus(key, count, basis, total) {
  const detail = basis?.[key] || { fullBody: 0, other: 0 };
  if (count > 0 && detail.fullBody + detail.other === count && (detail.fullBody || detail.other)) {
    const fullText = `${detail.fullBody} with a full article`;
    const other = `${detail.other} without a full article`;
    return detail.fullBody && detail.other ? `${fullText}; ${other}` : detail.fullBody ? fullText : other;
  }
  return `of ${total} developments`;
}

function assessmentStatus(total) {
  const covered = fullBodyEvidenceCount(signalSummary);
  if (!total) return "The weekly picture is being prepared.";
  if (covered >= total) return "A full source article was available for every development.";
  return `A full source article was available for ${covered} of ${total} developments. The rest are kept separate as not enough evidence.`;
}

function takeawayCopy(counts) {
  const benefit = Number(counts.benefit_shown || 0);
  const downside = Number(counts.downside_shown || 0);
  const noChange = Number(counts.no_clear_people_change || 0);
  const insufficient = Number(counts.too_little_evidence || 0);
  return `This week, ${insufficient} developments did not have enough source evidence and ${noChange} did not show a clear change for people. ${benefit} reported a benefit and ${downside} reported a downside.`;
}

function renderOpening() {
  const counts = periodCounts(currentRelease);
  setText("week-badge", `This week, ${formatRange(currentRelease.period_start, currentRelease.period_end)}`);
  setText("week-intro", `${counts.articles} source pages were grouped into ${counts.events} distinct developments. Start with what the sources show for people, then open the news behind them.`);
}

function renderSignals() {
  const total = Number(signalSummary?.expected_units || periodCounts(currentRelease).events || 0);
  const classified = Number(signalSummary?.classified_units || 0);
  const complete = total > 0 && classified === total;
  const primary = primaryOutcomeSummary();
  const counts = primary.counts;
  const ready = complete && Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0) === total;
  const cards = [
    ["benefit_shown", "week-signal-benefit", "week-percent-benefit", "week-status-benefit"],
    ["downside_shown", "week-signal-downside", "week-percent-downside", "week-status-downside"],
    ["benefit_and_downside", "week-signal-both", "week-percent-both", "week-status-both"],
    ["no_clear_people_change", "week-signal-no-change", "week-percent-no-change", "week-status-no-change"],
    ["too_little_evidence", "week-signal-insufficient", "week-percent-insufficient", "week-status-insufficient"],
  ];
  cards.forEach(([key, countId, percentId, statusId]) => {
    const value = Number(counts[key] || 0);
    setText(countId, ready ? value : "Not available");
    setText(percentId, ready ? formatPercent(value, total) : "Not available");
    setText(statusId, ready ? outcomeStatus(key, value, primary.basis, total) : "Count being prepared");
    document.querySelector(`[data-signal-card="${key}"]`)?.classList.toggle("is-pending", !ready);
    const filter = document.querySelector(`[data-signal-view="${key}"]`);
    if (filter) {
      filter.disabled = !ready;
      filter.title = ready ? "" : "This count is being prepared from the available source evidence.";
    }
  });
  if (signalView !== "all" && !ready) signalView = "all";
  setText("weekly-takeaway-copy", ready ? takeawayCopy(counts) : "The people-first picture is still being prepared for this week.");
  setText("weekly-assessment-status", assessmentStatus(total));

  const patterns = signalSummary?.relationship_pattern_counts || {};
  const grid = document.getElementById("movement-grid");
  if (grid) grid.hidden = !signalSummary;
  setText("movement-together", ready ? Number(patterns.mutualism || 0) : "Not available");
  setText("movement-people-down", ready ? Number(patterns.ai_benefiting_parasitism || 0) : "Not available");
  setText("movement-ai-held", ready ? Number(patterns.human_benefiting_parasitism || 0) : "Not available");
  setText("movement-both-down", ready ? Number(patterns.competition || 0) : "Not available");
  const fullBodyCount = fullBodyEvidenceCount(signalSummary);
  setText("movement-scope", ready ? `${total} developments checked. Full article evidence was used for ${fullBodyCount}.` : "Picture being prepared");
}

function renderTape() {
  const container = document.getElementById("weekly-tape");
  const counts = periodCounts(currentRelease);
  const values = [
    ["Source pages", counts.articles, "news pages checked"],
    ["Developments", counts.events, "distinct things that happened"],
    ["First recorded", counts.firstRecorded, "not seen in an earlier weekly release"],
    ["Seen before", counts.recurring, "covered again this week"],
    ["History links to check", counts.review, "possible links to an earlier week"],
  ];
  container.innerHTML = values.map(([label, value, note]) => `<article><span>${escapeHTML(label)}</span><strong>${value}</strong><small>${escapeHTML(note)}</small></article>`).join("");
  setText("week-equation", `${counts.articles} source ${plural(counts.articles, "page")} = ${counts.events} developments + ${counts.extra} extra ${plural(counts.extra, "page")} about something already counted.`);
}

function relationshipEventById(event) {
  const id = String(event.effective_event_id || event.event_id || "");
  return (currentSymbiosis?.evidence || []).find((row) => String(row.event_id || "") === id) || null;
}

function fallbackSignals(row) {
  if (!row) return { people_gaining: false, people_losing_ground: false, mixed_picture: false, not_everyone_benefits: false, not_clear_yet: true };
  if (row.public_signals) return row.public_signals;
  const configuration = String(row.configuration || "");
  const gaining = ["mutualism", "human_benefiting_parasitism", "human_enabling_only"].includes(configuration) || row.human_direction === "enabling";
  const losing = ["ai_benefiting_parasitism", "competition", "human_constraining_only"].includes(configuration) || row.human_direction === "constraining";
  return { people_gaining: gaining, people_losing_ground: losing, mixed_picture: gaining && losing, not_everyone_benefits: row.distribution_signal === "unequal", not_clear_yet: !gaining && !losing };
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
  if (evidenceView === "new") return ["first_time", "follow_on_development"].includes(novelty) || Boolean(event.first_time_in_period) || Boolean(event.follow_on_development);
  if (evidenceView === "recurring") return novelty === "recurring" || Boolean(event.recurring_in_period);
  if (evidenceView === "review") return ["possible_historical_match", "unclassified"].includes(novelty) || Boolean(event.possible_historical_match);
  return true;
}

function eventMatchesSignal(event) {
  if (signalView === "all") return true;
  return primaryOutcomeFor(relationshipEventById(event)) === signalView;
}

function storyLocation(event, relationship) {
  const codes = Array.isArray(relationship?.story_country_iso3s) ? relationship.story_country_iso3s.filter(Boolean) : [];
  if (codes.length) return codes.map((code) => markets[code]?.name || code).join(", ");
  const legacy = event.classification?.country_iso3s || [];
  if (legacy.length) return legacy.map((code) => markets[code]?.name || code).join(", ");
  return "Not established from the available news";
}

function evidenceScopeCopy(count) {
  const meaning = signalView === "all" ? "" : ` marked ${PRIMARY_OUTCOME_LABELS[signalView]}`;
  const market = selectedMarket ? ` found through the ${markets[selectedMarket]?.name || selectedMarket} search` : "";
  return `${count} current-week ${plural(count, "development")}${meaning}${market}.`;
}

function noveltyLabel(event) {
  const novelty = String(event.novelty_status || "");
  if (novelty === "recurring" || event.recurring_in_period) return "Seen before";
  if (novelty === "follow_on_development" || event.follow_on_development) return "First recorded, follow-on";
  if (novelty === "first_time" || event.first_time_in_period) return "First recorded";
  if (novelty === "possible_historical_match" || event.possible_historical_match) return "History link being checked";
  return "History link being checked";
}

function signalBadges(relationship) {
  const signals = fallbackSignals(relationship);
  const primary = primaryOutcomeFor(relationship);
  const badges = [`<span class="meaning-badge ${escapeHTML(primary)}">${escapeHTML(PRIMARY_OUTCOME_LABELS[primary])}</span>`];
  if (signals.not_everyone_benefits) badges.push('<span class="meaning-badge uneven-effect">Different effects for different groups</span>');
  return badges.join("");
}

function sourceMarketLabel(marketsFound) {
  const names = marketsFound.map((iso3) => markets[iso3]?.name || iso3).filter(Boolean);
  if (!names.length) return "Source market not recorded";
  return `${names.length === 1 ? "Source market" : "Source markets"}: ${names.join(", ")}`;
}

function sourceMarketLabelForSource(source) {
  const articleId = String(source?.article_id || "");
  const row = coverageByArticle.get(articleId);
  const names = (row?.search_markets || [])
    .map((iso3) => markets[iso3]?.name || iso3)
    .filter(Boolean);
  if (!names.length) return "Source market not recorded";
  return `Source market: ${names.join(", ")}`;
}

function evidenceBasisLabel(relationship) {
  const basis = relationship?.evidence_basis_summary || {};
  const full = Number(basis.full_text_sources || 0);
  const summaries = Number(basis.article_summary_sources || 0);
  const total = Number(basis.source_count || 0);
  if (full > 0 && full === total) return "Full article used";
  if (full > 0) return "Full article used with other sources";
  if (summaries > 0) return "Summary or excerpt used";
  return "Full article not available";
}

function renderEvidence() {
  const container = document.getElementById("evidence-list");
  const more = document.getElementById("show-more-evidence");
  const events = (currentRelease.evidence || [])
    .filter(eventMatchesMarket)
    .filter(eventMatchesView)
    .filter(eventMatchesSignal)
    .sort((a, b) => Number(b.member_article_count || 0) - Number(a.member_article_count || 0));
  setText("evidence-scope", evidenceScopeCopy(events.length));
  document.querySelectorAll("[data-evidence-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.evidenceView === evidenceView)));
  document.querySelectorAll("[data-signal-view]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.signalView === signalView)));
  const visible = events.slice(0, evidenceLimit);
  if (!visible.length) {
    container.innerHTML = "<p class=\"empty-state\">No development matches these choices. Try a different card or clear a filter.</p>";
    more.hidden = true;
    return;
  }

  container.innerHTML = visible.map((event) => {
    const relationship = relationshipEventById(event);
    const sources = event.sources || [];
    const sourceDates = sources.map((source) => String(source.published_date || "")).filter(Boolean).sort();
    const eventDate = sourceDates.length ? formatShortRange(sourceDates[0], sourceDates.at(-1)) : formatShortRange(event.event_date, event.event_date);
    const marketsFound = eventDiscoveryMarkets(event);
    const marketButtons = marketsFound.length
      ? marketsFound.map((iso3) => `<button type="button" class="market-evidence-chip" data-globe-market="${escapeHTML(iso3)}">Found through ${escapeHTML(markets[iso3]?.name || iso3)} search</button>`).join("")
      : "Search market not available";
    const takeaway = String(relationship?.public_takeaway || "").trim();
    const evidenceBasis = evidenceBasisLabel(relationship);
    const sourceMarket = sourceMarketLabel(marketsFound);
    return `
      <details class="evidence-card">
        <summary><div class="evidence-summary-main"><div class="evidence-badges">${signalBadges(relationship)}</div><h3>${escapeHTML(event.event_title || "Untitled development")}</h3><div class="evidence-meta"><span>${escapeHTML(eventDate)}</span><span>${sources.length} ${plural(sources.length, "source")}</span><span class="source-market-label">${escapeHTML(sourceMarket)}</span><span class="evidence-basis-label">${escapeHTML(evidenceBasis)}</span><span>${escapeHTML(noveltyLabel(event))}</span></div></div><span class="open-cue">Open</span></summary>
        <div class="evidence-body">
          ${takeaway ? `<p class="plain-takeaway"><strong>What this source reports:</strong> ${escapeHTML(takeaway)}</p>` : ""}
          <div class="source-links">${sources.map((source) => `<a href="${escapeHTML(safeUrl(source.url))}" target="_blank" rel="noopener noreferrer"><span><strong>${escapeHTML(source.publisher || "Publication")}</strong>: ${escapeHTML(source.headline || "Open source")}</span><small>${escapeHTML(source.published_date || "")} | ${escapeHTML(sourceMarketLabelForSource(source))}</small></a>`).join("")}</div>
          <details class="compact-more"><summary>Where it was found</summary><div class="market-evidence-chips">${marketButtons}</div><p><strong>Story location:</strong> ${escapeHTML(storyLocation(event, relationship))}</p></details>
        </div>
      </details>`;
  }).join("");

  container.querySelectorAll("button[data-globe-market]").forEach((button) => button.addEventListener("click", () => {
    selectMarket(button.dataset.globeMarket);
    globe?.selectMarket(button.dataset.globeMarket, { notify: false });
    document.getElementById("explore")?.scrollIntoView({ behavior: "smooth", block: "start" });
  }));
  const remaining = events.length - visible.length;
  more.hidden = remaining <= 0;
  if (remaining > 0) more.textContent = `Show next ${Math.min(8, remaining)} ${plural(Math.min(8, remaining), "development")}`;
}

function updateFilterUrl() {
  const url = new URL(window.location.href);
  evidenceView === "all" ? url.searchParams.delete("view") : url.searchParams.set("view", evidenceView);
  signalView === "all" ? url.searchParams.delete("signal") : url.searchParams.set("signal", signalView);
  window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function setupFilters() {
  document.querySelectorAll("[data-evidence-view]").forEach((button) => button.addEventListener("click", () => {
    evidenceView = normalizeEvidenceView(button.dataset.evidenceView);
    evidenceLimit = 8;
    updateFilterUrl();
    renderEvidence();
  }));
  document.querySelectorAll("[data-signal-view]").forEach((button) => button.addEventListener("click", () => {
    if (button.disabled) return;
    signalView = normalizeSignalView(button.dataset.signalView);
    evidenceLimit = 8;
    updateFilterUrl();
    renderEvidence();
  }));
  document.getElementById("show-more-evidence")?.addEventListener("click", () => {
    evidenceLimit += 8;
    renderEvidence();
  });
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

function renderMarketSelection(selection) {
  setText("edu-market-selection-summary", selection?.summary || "AIEO searches across several English, French and Chinese news environments.");
  const sources = document.getElementById("edu-market-selection-sources");
  if (sources) sources.innerHTML = (selection?.sources || []).map((row) => `<a href="${escapeHTML(safeUrl(row.url))}" target="_blank" rel="noopener noreferrer">${escapeHTML(row.name || "Source")}</a>`).join("");
}

function renderMarketButtons() {
  const container = document.getElementById("edu-market-buttons");
  container.innerHTML = Object.entries(markets).map(([iso3, market]) => `<button type="button" data-market="${escapeHTML(iso3)}" aria-pressed="${String(selectedMarket === iso3)}">${escapeHTML(market.short_name || market.name)}</button>`).join("");
  container.querySelectorAll("button[data-market]").forEach((button) => button.addEventListener("click", () => {
    selectMarket(button.dataset.market);
    globe?.selectMarket(button.dataset.market, { notify: false });
  }));
}

function renderMarketCard() {
  const card = document.getElementById("edu-market-card");
  const clear = document.getElementById("clear-market");
  const copy = card?.querySelector("div");
  if (!card || !clear || !copy) return;
  if (!selectedMarket || !markets[selectedMarket]) {
    copy.innerHTML = "<strong>Choose a market</strong><p>See the sources found through that search.</p>";
    clear.hidden = true;
    return;
  }
  const rows = marketCoverageRows(selectedMarket);
  const fallback = Number(currentRelease.sources?.discovery_markets?.[selectedMarket] || 0);
  const count = rows.length || fallback;
  const leaders = topPublishers(rows).map(([name, value]) => `${name} (${value})`).join(", ");
  copy.innerHTML = `<strong>${count} coverage ${plural(count, "item")} found through ${escapeHTML(markets[selectedMarket].name)}</strong><p>${leaders ? `Most visible: ${escapeHTML(leaders)}.` : "Open the news above for source details."}</p>`;
  clear.hidden = false;
}

function selectMarket(iso3) {
  selectedMarket = iso3 && markets[iso3] ? iso3 : null;
  evidenceLimit = 8;
  renderMarketButtons();
  renderMarketCard();
  renderEvidence();
}

async function initialiseGlobe() {
  try {
    globe = await initDiscoveryGlobe({ containerId: "edu-globe", toggleId: "edu-globe-toggle", promptId: "edu-globe-prompt", fallbackId: "edu-globe-fallback", markets, onSelect: (iso3) => selectMarket(iso3) });
  } catch (error) {
    console.warn("Map unavailable; market buttons remain active", error);
  }
  renderMarketButtons();
  const requested = initialParams.get("market");
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

function deltaText(current, previous) {
  if (!Number.isFinite(previous)) return { className: "flat", text: "First available week" };
  const delta = current - previous;
  const pct = previous ? (delta / previous) * 100 : 0;
  if (delta === 0) return { className: "flat", text: "→ no change" };
  return { className: delta > 0 ? "up" : "down", text: `${delta > 0 ? "↑" : "↓"} ${Math.abs(delta)} (${Math.abs(pct).toFixed(1)}%)` };
}

function renderHistoryComparison() {
  const rows = weeklyRows(releaseIndex);
  const container = document.getElementById("history-comparison");
  const current = rows.find((row) => row.release_id === currentRelease.release_id) || rows.at(-1);
  const index = rows.indexOf(current);
  const previous = index > 0 ? rows[index - 1] : null;
  setText("history-explainer", `${rows.length} completed ${plural(rows.length, "week")} available`);
  if (!current) {
    container.innerHTML = "<p>No weekly history is available yet.</p>";
    return;
  }
  const metrics = [["Source pages", ["articles", "ai_relevant_articles"]], ["Distinct developments", ["event_records", "ai_relevant_event_records"]], ["Extra pages about the same developments", ["extra_coverage"]]];
  container.innerHTML = metrics.map(([label, keys]) => {
    const now = rowNumber(current, keys);
    const before = previous ? rowNumber(previous, keys) : NaN;
    const delta = deltaText(now, before);
    return `<article class="history-row"><h3>${label}</h3><div class="history-values">${previous ? `<strong>${before}</strong><i>to</i>` : ""}<strong>${now}</strong></div><span class="history-change ${delta.className}">${delta.text}</span></article>`;
  }).join("");
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
  if (!svg || !rows.length) return;
  svg.replaceChildren();
  const width = 940, height = 260, left = 60, right = 28, top = 26, bottom = 54;
  const values = rows.flatMap((row) => [rowNumber(row, ["articles", "ai_relevant_articles"]), rowNumber(row, ["event_records", "ai_relevant_event_records"])]);
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
  const pathFor = (keys) => rows.map((row, index) => `${index ? "L" : "M"}${x(index)},${y(rowNumber(row, keys))}`).join(" ");
  svg.appendChild(svgElement("path", { d: pathFor(["articles", "ai_relevant_articles"]), fill: "none", stroke: "#315eae", "stroke-width": 3 }));
  svg.appendChild(svgElement("path", { d: pathFor(["event_records", "ai_relevant_event_records"]), fill: "none", stroke: "#087f87", "stroke-width": 3 }));
  rows.forEach((row, index) => {
    [["articles", "#315eae"], ["event_records", "#087f87"]].forEach(([key, color]) => svg.appendChild(svgElement("circle", { cx: x(index), cy: y(rowNumber(row, [key])), r: 5, fill: color })));
    svg.appendChild(svgElement("text", { x: x(index), y: height - 23, "text-anchor": "middle", fill: "#40576d", "font-size": 12, "font-weight": 700 }, formatShortRange(row.period_start, row.period_end)));
  });
  setText("history-note", "Only completed Monday-to-Sunday weeks are connected.");
}

function setupNavigation() {
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.getElementById("main-nav");
  toggle?.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    nav?.setAttribute("data-open", String(open));
  });
}

async function init() {
  console.info(`AIEO weekly interface build ${BUILD_ID}`);
  setupNavigation();
  setupFilters();
  try {
    const [release, index, countryData, symbiosis] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(INDEX_URL),
      fetchJSON(COUNTRIES_URL),
      fetchJSON(SYMBIOSIS_URL, true),
    ]);
    currentRelease = release;
    releaseIndex = index;
    currentSymbiosis = symbiosis && String(symbiosis.release_id || "") === String(release.release_id || "") ? symbiosis : null;
    signalSummary = plainSignalData(currentSymbiosis);
    markets = countryData.markets || {};
    coverageByArticle = new Map((currentRelease.units?.coverage_articles || []).map((row) => [String(row.article_id), row]));
    renderMarketSelection(countryData.selection || {});
    renderOpening();
    renderSignals();
    renderTape();
    renderEvidence();
    renderHistoryComparison();
    renderHistoryChart();
    await initialiseGlobe();
  } catch (error) {
    console.error("This week's page could not initialise", error);
    setText("week-badge", "This week's picture is temporarily unavailable");
    setText("week-intro", "The current release could not be loaded. Please try again shortly.");
    const list = document.getElementById("evidence-list");
    if (list) list.innerHTML = "<p class=\"empty-state\">The source list is temporarily unavailable.</p>";
  }
}

init();
