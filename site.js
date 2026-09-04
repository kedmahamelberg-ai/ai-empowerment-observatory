"use strict";

import { initDiscoveryGlobe } from "/globe.js?v=6.1.0";

const BUILD_ID = "6.1.1";
const CURRENT_URL = "/data/releases/current.json";
const SYMBIOSIS_URL = "/data/symbiosis/current.json";
const COUNTRIES_URL = "/edu/countries.json";
const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });

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
  return sameMonth ? `${start.getUTCDate()}–${dateLong.format(end)}` : `${dateLong.format(start)}–${dateLong.format(end)}`;
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "—");
}

function formatPercent(value, total) {
  const denominator = Number(total || 0);
  if (!denominator) return "—";
  return `${((Number(value || 0) / denominator) * 100).toFixed(1)}%`;
}

async function fetchJSON(url, optional = false) {
  try {
    const separator = url.includes("?") ? "&" : "?";
    const response = await fetch(`${url}${separator}build=${BUILD_ID}&t=${Date.now()}`, {
      cache: "no-store",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
    return response.json();
  } catch (error) {
    if (optional) return null;
    throw error;
  }
}

function releaseCounts(release) {
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

function plainSignalData(symbiosis, release) {
  const total = releaseCounts(release).events;
  const sameRelease = symbiosis && String(symbiosis.release_id || "") === String(release?.release_id || "");
  if (!sameRelease) return null;
  if (symbiosis.people_signals) return symbiosis.people_signals;

  const event = symbiosis.event || {};
  const counts = event.display_configuration_counts || event.configuration_counts || {};
  const classified = Number(event.display_classified_units ?? event.classified_units ?? 0);
  const complete = total > 0 && classified === total;
  const gaining = Number(counts.mutualism || 0) + Number(counts.human_benefiting_parasitism || 0) + Number(counts.human_enabling_only || 0);
  const losing = Number(counts.ai_benefiting_parasitism || 0) + Number(counts.competition || 0) + Number(counts.human_constraining_only || 0);
  return {
    expected_units: total,
    classified_units: classified,
    people_signal_counts: {
      people_gaining: gaining,
      people_losing_ground: losing,
      mixed_picture: 0,
      not_everyone_benefits: 0,
      not_clear_yet: complete ? Math.max(0, total - gaining - losing) : 0,
    },
    relationship_pattern_counts: {
      mutualism: Number(counts.mutualism || 0),
      ai_benefiting_parasitism: Number(counts.ai_benefiting_parasitism || 0),
      human_benefiting_parasitism: Number(counts.human_benefiting_parasitism || 0),
      competition: Number(counts.competition || 0),
    },
    availability: {
      people_gaining: complete,
      people_losing_ground: complete,
      mixed_picture: false,
      not_everyone_benefits: false,
      not_clear_yet: complete,
    },
  };
}

function renderRelease(release) {
  const counts = releaseCounts(release);
  setText("release-badge", `This week · ${formatRange(release.period_start, release.period_end)}`);
  setText("count-source-pages", counts.articles);
  setText("count-developments", counts.events);
  setText("count-first-time", counts.firstRecorded);
  setText("count-recurring", counts.recurring);
  setText("count-review", counts.review);
  const reviewCard = document.getElementById("count-review-card");
  if (reviewCard) reviewCard.hidden = counts.review === 0;
  setText(
    "count-equation",
    `${counts.articles} source ${plural(counts.articles, "page")} = ${counts.events} developments + ${counts.extra} extra ${plural(counts.extra, "page")} about something already counted.`,
  );
}

function takeawayCopy(counts, total) {
  const gaining = Number(counts.people_gaining || 0);
  const losing = Number(counts.people_losing_ground || 0);
  const unclear = Number(counts.not_clear_yet || 0);
  if (unclear > total / 2) {
    return `Most AI news still did not show a clear change for people. Among the clearer developments, ${gaining} pointed to gains and ${losing} to people losing ground.`;
  }
  if (gaining > losing) return `Gains appeared more often than losses, but the picture was not the same for every person or every use of AI.`;
  if (losing > gaining) return `More developments showed people losing ground than gaining, with ${unclear} still too unclear to call.`;
  return `The week showed a balanced picture of gains and losses, with ${unclear} developments still not clear enough to call.`;
}

function renderSignals(signalData, release) {
  const total = Number(signalData?.expected_units || releaseCounts(release).events || 0);
  const classified = Number(signalData?.classified_units || 0);
  const complete = total > 0 && classified === total;
  const counts = signalData?.people_signal_counts || {};
  const available = signalData?.availability || {};
  const cards = [
    ["people_gaining", "signal-people-gaining", "percent-people-gaining", "status-people-gaining"],
    ["people_losing_ground", "signal-people-losing", "percent-people-losing", "status-people-losing"],
    ["mixed_picture", "signal-mixed", "percent-mixed", "status-mixed"],
    ["not_everyone_benefits", "signal-unequal", "percent-unequal", "status-unequal"],
    ["not_clear_yet", "signal-unclear", "percent-unclear", "status-unclear"],
  ];
  cards.forEach(([key, countId, percentId, statusId]) => {
    const card = document.querySelector(`[data-signal="${key}"]`);
    const ready = complete && available[key] === true;
    const value = Number(counts[key] || 0);
    setText(countId, ready ? value : "—");
    setText(percentId, ready ? formatPercent(value, total) : "—");
    setText(statusId, ready ? `of ${total} developments` : "New count coming after review");
    card?.classList.toggle("is-pending", !ready);
  });
  setText(
    "signal-denominator",
    complete
      ? `${total} developments checked this week`
      : `${classified} of ${total} relationship classifications published`,
  );
  setText(
    "week-takeaway",
    complete
      ? takeawayCopy(counts, total)
      : "The people-first picture is still being prepared. Missing classifications are not counted as unclear results.",
  );

  const patterns = signalData?.relationship_pattern_counts || {};
  setText("pattern-together", complete ? Number(patterns.mutualism || 0) : "—");
  setText("pattern-people-down", complete ? Number(patterns.ai_benefiting_parasitism || 0) : "—");
  setText("pattern-ai-held", complete ? Number(patterns.human_benefiting_parasitism || 0) : "—");
  setText("pattern-both-down", complete ? Number(patterns.competition || 0) : "—");
  setText("movement-scope", complete ? `${total} developments checked` : "Picture being prepared");
}

function marketRows(release, iso3) {
  return (release?.units?.coverage_articles || []).filter((row) => row.classification?.ai_relevant && (row.search_markets || []).includes(iso3));
}

function topPublishers(rows) {
  const counts = new Map();
  rows.forEach((row) => {
    const publisher = String(row.publisher || "Unknown publication");
    counts.set(publisher, (counts.get(publisher) || 0) + 1);
  });
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 3);
}

function renderMarketCard(release, markets, iso3) {
  const card = document.getElementById("market-card");
  if (!card) return;
  if (!iso3 || !markets?.[iso3]) {
    card.innerHTML = '<div><strong>Choose a market</strong><p>See the coverage found through that search.</p></div><a class="text-link" href="/edu/#evidence">All evidence</a>';
    return;
  }
  const rows = marketRows(release, iso3);
  const fallbackCount = Number(release?.sources?.discovery_markets?.[iso3] || 0);
  const count = rows.length || fallbackCount;
  const leaders = topPublishers(rows).map(([name, value]) => `${name} (${value})`).join(", ");
  card.innerHTML = `<div><strong>${count} coverage ${plural(count, "item")} found through ${escapeHTML(markets[iso3].name)}</strong><p>${leaders ? `Most visible: ${escapeHTML(leaders)}.` : "Open the evidence list for the sources."}</p></div><a class="text-link" href="/edu/?market=${encodeURIComponent(iso3)}&view=all#evidence">Open evidence</a>`;
}

function renderMarketButtons(release, markets, globe) {
  const container = document.getElementById("market-buttons");
  if (!container) return null;
  const select = (iso3) => {
    container.querySelectorAll("button[data-market]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.market === iso3)));
    renderMarketCard(release, markets, iso3);
  };
  container.innerHTML = Object.entries(markets || {}).map(([iso3, market]) => `<button type="button" data-market="${escapeHTML(iso3)}" aria-pressed="false">${escapeHTML(market.short_name || market.name)}</button>`).join("");
  container.querySelectorAll("button[data-market]").forEach((button) => button.addEventListener("click", () => {
    select(button.dataset.market);
    globe?.selectMarket(button.dataset.market, { notify: false });
  }));
  return select;
}

async function loadGlobe(release, countryData) {
  const markets = countryData?.markets || {};
  let selectMarket = null;
  let globe = null;
  try {
    globe = await initDiscoveryGlobe({
      containerId: "home-globe",
      toggleId: "home-globe-toggle",
      promptId: "home-globe-prompt",
      fallbackId: "home-globe-fallback",
      markets,
      onSelect: (iso3) => selectMarket?.(iso3),
    });
  } catch (error) {
    console.warn("Map unavailable; market buttons remain active", error);
  }
  selectMarket = renderMarketButtons(release, markets, globe);
  renderMarketCard(release, markets, null);
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
  console.info(`AIEO public interface build ${BUILD_ID}`);
  setupNavigation();
  try {
    const [release, symbiosis, countryData] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(SYMBIOSIS_URL, true),
      fetchJSON(COUNTRIES_URL, true),
    ]);
    renderRelease(release);
    renderSignals(plainSignalData(symbiosis, release), release);
    await loadGlobe(release, countryData);
  } catch (error) {
    console.error("The current Observatory picture could not load", error);
    setText("release-badge", "This week's picture is temporarily unavailable");
    setText("hero-summary", "Please try again shortly or open the source list.");
    setText("week-takeaway", "The data could not be loaded right now.");
  }
}

init();
