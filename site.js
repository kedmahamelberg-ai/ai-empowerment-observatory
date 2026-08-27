"use strict";

import { initDiscoveryGlobe } from "/globe.js";

const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const COUNTRIES_URL = "/edu/countries.json";
const SYMBIOSIS_URL = "/data/symbiosis/current.json";

const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function safeExternalUrl(value) {
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

function releaseCounts(release) {
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
  return {
    className: delta > 0 ? "up" : "down",
    arrow: delta > 0 ? "↑" : "↓",
    text: `${Math.abs(delta)}`,
  };
}

function renderSignalTape(release, index) {
  const container = document.getElementById("home-signal-tape");
  if (!container) return;
  const counts = releaseCounts(release);
  const rows = weeklyRows(index);
  const currentRow = rows.find((row) => row.release_id === release.release_id) || rows.at(-1) || null;
  const currentIndex = currentRow ? rows.indexOf(currentRow) : rows.length - 1;
  const previous = currentIndex > 0 ? rows[currentIndex - 1] : null;
  const values = [
    {
      label: "Coverage",
      value: counts.articles,
      previous: previous ? rowNumber(previous, ["articles", "ai_relevant_articles"]) : NaN,
      note: "source items this week",
      href: "/edu/?view=all#evidence",
    },
    {
      label: "Developments",
      value: counts.events,
      previous: previous ? rowNumber(previous, ["event_records", "ai_relevant_event_records"]) : NaN,
      note: "distinct occurrences",
      href: "/edu/#history",
    },
    {
      label: "Additional reports",
      value: counts.extra,
      previous: previous ? rowNumber(previous, ["extra_coverage"]) : NaN,
      note: "attention, not another event",
      href: "/edu/?view=recurring#evidence",
    },
    {
      label: "New to AIEO",
      value: counts.newDevelopments,
      previous: NaN,
      note: "not in the disclosed history",
      href: "/edu/?view=new#evidence",
    },
  ];
  container.innerHTML = values.map((item) => {
    const change = changeMeta(item.value, item.previous);
    return `
      <a class="tape-item" href="${item.href}">
        <span>${escapeHTML(item.label)}</span>
        <strong>${item.value}</strong>
        <b class="tape-change ${change.className}">${change.arrow} ${change.text}</b>
        <small>${escapeHTML(item.note)}</small>
      </a>
    `;
  }).join("");
}

function renderRelease(release) {
  const counts = releaseCounts(release);
  const period = formatRange(release.period_start, release.period_end);
  setText("release-badge", `Current weekly signal | ${period}`);
  setText(
    "hero-summary",
    `${counts.articles} coverage items were grouped into ${counts.events} distinct developments. ${counts.newDevelopments} ${plural(counts.newDevelopments, "was", "were")} new to AIEO, while ${counts.recurring} had been seen before.`,
  );
  setText("hero-fact-new", `${counts.newDevelopments} new to AIEO`);
  setText("hero-fact-repeat", `${counts.recurring} seen before`);
  setText(
    "hero-equation-coverage",
    `${counts.articles} coverage items = ${counts.events} developments + ${counts.extra} additional ${plural(counts.extra, "report")} about developments already counted.`,
  );
  const pending = counts.possible + counts.unclassified;
  setText(
    "hero-equation-novelty",
    pending
      ? `${counts.events} developments = ${counts.newDevelopments} new + ${counts.recurring} seen before + ${pending} still under novelty review.`
      : `${counts.events} developments = ${counts.newDevelopments} new + ${counts.recurring} seen before.`,
  );
}

function relationshipCell(key, count, complete) {
  const definitions = {
    mutualism: { cls: "mutualism", people: "People ↑", ai: "AI ↑", label: "Both gain", technical: "Mutualism" },
    ai_benefiting_parasitism: { cls: "ai-benefit", people: "People ↓", ai: "AI ↑", label: "AI side gains, people are constrained", technical: "AI-benefiting parasitism" },
    human_benefiting_parasitism: { cls: "human-benefit", people: "People ↑", ai: "AI ↓", label: "People gain, AI side is constrained", technical: "Human-benefiting parasitism" },
    competition: { cls: "competition", people: "People ↓", ai: "AI ↓", label: "Both are constrained", technical: "Competition or co-constraint" },
  };
  const row = definitions[key];
  const share = complete ? (count / complete) * 100 : 0;
  return `
    <article class="relationship-cell ${row.cls}">
      <div class="arrows"><span>${row.people}</span><span>${row.ai}</span></div>
      <strong>${count} <small>${share.toFixed(0)}%</small></strong>
      <h3>${row.label}</h3>
      <small>${row.technical}</small>
    </article>
  `;
}

function renderRelationship(symbiosis) {
  const ticker = document.getElementById("relationship-ticker");
  if (!ticker) return;
  if (!symbiosis || symbiosis.public_status !== "human_reviewed" || !symbiosis.review?.event_complete) {
    ticker.innerHTML = '<p class="loading-line">The relationship signal is still under review for this release.</p>';
    setText("relationship-denominator", "Review in progress");
    setText("relationship-other-summary", "Weekly counts and sources remain available while the relationship review is completed.");
    return;
  }
  const event = symbiosis.event || {};
  const counts = event.configuration_counts || {};
  const complete = Number(event.complete_configuration_count || 0);
  ticker.innerHTML = [
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
  ].map((key) => relationshipCell(key, Number(counts[key] || 0), complete)).join("");
  setText("relationship-denominator", `${complete} developments had evidence for both sides`);
  const partial = Number(event.partial_signal_count || 0);
  const noClear = Number(event.no_clear_relational_signal_count || 0);
  const ambiguous = Number(event.ambiguous_relational_signal_count || 0);
  const insufficient = Number(event.insufficient_evidence_count || 0);
  setText(
    "relationship-other-summary",
    `${partial} one-sided signals, ${noClear} no-clear cases, ${ambiguous} ambiguous cases, and ${insufficient} insufficient-evidence cases are kept outside the four-pattern percentages.`,
  );
}

function marketRows(release, iso3) {
  return (release.units?.coverage_articles || []).filter((row) => row.classification?.ai_relevant && (row.search_markets || []).includes(iso3));
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
  const cta = document.getElementById("market-evidence-cta");
  if (!card || !cta) return;
  if (!iso3 || !markets[iso3]) {
    card.innerHTML = '<div><strong>Choose a market</strong><p>See the coverage found through that Google News search.</p></div><a id="market-evidence-cta" class="text-link" href="/edu/#evidence">All evidence</a>';
    return;
  }
  const market = markets[iso3];
  const rows = marketRows(release, iso3);
  const fallbackCount = Number(release.sources?.discovery_markets?.[iso3] || 0);
  const count = rows.length || fallbackCount;
  const leaders = topPublishers(rows).map(([name, value]) => `${name} (${value})`).join(", ");
  card.innerHTML = `
    <div><strong>${count} coverage ${plural(count, "item")} found through ${escapeHTML(market.name)}</strong><p>${leaders ? `Most visible: ${escapeHTML(leaders)}.` : "Open the evidence list for source details."}</p></div>
    <a id="market-evidence-cta" class="text-link" href="/edu/?market=${encodeURIComponent(iso3)}&view=all#evidence">Open evidence</a>
  `;
}

function renderMarketButtons(release, markets, globe) {
  const container = document.getElementById("market-buttons");
  if (!container) return;
  let selected = null;
  const select = (iso3) => {
    selected = iso3;
    container.querySelectorAll("button[data-market]").forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.market === iso3)));
    renderMarketCard(release, markets, iso3);
  };
  container.innerHTML = Object.entries(markets).map(([iso3, market]) => `<button type="button" data-market="${escapeHTML(iso3)}" aria-pressed="false">${escapeHTML(market.short_name || market.name)}</button>`).join("");
  container.querySelectorAll("button[data-market]").forEach((button) => {
    button.addEventListener("click", () => {
      select(button.dataset.market);
      globe?.selectMarket(button.dataset.market, { notify: false });
    });
  });
  return select;
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

function setupInfoPopovers() {
  document.addEventListener("click", (event) => {
    document.querySelectorAll("details.info-popover[open]").forEach((detail) => {
      if (!detail.contains(event.target)) detail.removeAttribute("open");
    });
  });
}

async function init() {
  setupNavigation();
  setupInfoPopovers();
  try {
    const [release, index, countries, symbiosis] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(INDEX_URL),
      fetchJSON(COUNTRIES_URL),
      fetchJSON(SYMBIOSIS_URL, true),
    ]);
    renderSignalTape(release, index);
    renderRelease(release);
    renderRelationship(symbiosis);
    const markets = countries.markets || {};
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
      console.warn("Globe unavailable; market buttons remain active", error);
    }
    selectMarket = renderMarketButtons(release, markets, globe);
    renderMarketCard(release, markets, null);
  } catch (error) {
    console.error("Homepage data could not be loaded", error);
    setText("release-badge", "Current signal temporarily unavailable");
    setText("hero-summary", "The latest release could not be loaded. Please try again shortly.");
  }
}

init();
