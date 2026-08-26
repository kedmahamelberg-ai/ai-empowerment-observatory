"use strict";

import { initDiscoveryGlobe } from "/globe.js";

const CURRENT_URL = "/data/releases/current.json";
const COUNTRIES_URL = "/edu/countries.json";

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
const dateTime = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  timeZoneName: "short",
});

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
  const sameMonth = start.getUTCMonth() === end.getUTCMonth()
    && start.getUTCFullYear() === end.getUTCFullYear();
  return sameMonth
    ? `${start.getUTCDate()}–${dateLong.format(end)}`
    : `${dateLong.format(start)}–${dateLong.format(end)}`;
}

function formatDateTime(value) {
  const parsed = new Date(String(value || ""));
  return Number.isNaN(parsed.getTime()) ? null : dateTime.format(parsed);
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "Not available");
}

async function fetchJSON(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
}

function marketRows(release, iso3) {
  return (release.units?.coverage_articles || []).filter((row) => (
    row.classification?.ai_relevant
    && (row.search_markets || []).includes(iso3)
  ));
}

function topPublishers(rows) {
  const counts = new Map();
  rows.forEach((row) => {
    const publisher = String(row.publisher || "Unknown publication");
    counts.set(publisher, (counts.get(publisher) || 0) + 1);
  });
  return [...counts.entries()]
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 3);
}

function sourceMixCopy(release) {
  const strata = release.sources?.strata || {};
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
  const summary = document.getElementById("market-selection-summary");
  const context = document.getElementById("market-selection-context");
  const sources = document.getElementById("market-selection-sources");
  if (summary) summary.textContent = selection?.summary || "AIEO uses a five-market pilot to compare several leading AI ecosystems and news environments.";
  if (context) context.textContent = selection?.ranking_context || "The market set is a research sample, not a definitive global top-five ranking.";
  if (sources) {
    const rows = Array.isArray(selection?.sources) ? selection.sources : [];
    sources.innerHTML = rows.map((row) => `
      <a href="${escapeHTML(safeExternalUrl(row.url))}" target="_blank" rel="noopener noreferrer">
        ${escapeHTML(row.name || "Open external source")}
      </a>
    `).join("");
  }
}

function renderMarketCard(release, markets, iso3) {
  const card = document.getElementById("market-card");
  const cta = document.getElementById("market-evidence-cta");
  if (!card || !cta) return;

  if (!iso3 || !markets[iso3]) {
    const discovered = Object.keys(release.sources?.discovery_markets || markets).length;
    card.firstElementChild.innerHTML = `
      <p class="eyebrow">Choose a search market</p>
      <h3>Start anywhere on the globe</h3>
      <p>AIEO currently runs Google News searches in ${discovered} ${plural(discovered, "market")}. Choose one to see the AI-related coverage found through that search.</p>
    `;
    cta.href = "/edu/?view=all#evidence";
    cta.textContent = "See all source-linked evidence →";
    return;
  }

  const market = markets[iso3];
  const rows = marketRows(release, iso3);
  const fallbackCount = Number(release.sources?.discovery_markets?.[iso3] || 0);
  const itemCount = rows.length || fallbackCount;
  const publications = new Set(rows.map((row) => row.publisher).filter(Boolean));
  const leaders = topPublishers(rows);
  const leaderCopy = leaders.length
    ? leaders.map(([name, count]) => `${escapeHTML(name)} (${count})`).join(", ")
    : "Source detail will appear with the next standardized release.";

  card.firstElementChild.innerHTML = `
    <p class="eyebrow">${escapeHTML(market.name)} search</p>
    <h3>${itemCount} AI-related coverage ${plural(itemCount, "item")} found</h3>
    <div class="market-statline">
      <span><strong>${publications.size || "Not available"}</strong> represented ${plural(publications.size, "publication")}</span>
    </div>
    <p>Most visible: ${leaderCopy}</p>
    <p class="market-caveat">This identifies where AIEO searched, not necessarily where the reported development happened.</p>
  `;
  cta.href = `/edu/?market=${encodeURIComponent(iso3)}&view=all#evidence`;
  cta.textContent = `View evidence found through ${market.name} →`;
}

function renderMarketButtons(markets, globe, select) {
  const container = document.getElementById("market-buttons");
  if (!container) return;
  container.innerHTML = Object.entries(markets).map(([iso3, market]) => `
    <button type="button" data-market="${escapeHTML(iso3)}" aria-pressed="false">
      ${escapeHTML(market.short_name || market.name)}
    </button>
  `).join("");
  container.querySelectorAll("button[data-market]").forEach((button) => {
    button.addEventListener("click", () => {
      const iso3 = button.dataset.market;
      select(iso3);
      globe?.selectMarket(iso3, { notify: false });
    });
  });
}

function selectMarket(release, markets, iso3) {
  document.querySelectorAll("#market-buttons button[data-market]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.market === iso3));
  });
  renderMarketCard(release, markets, iso3);
}

function poolDisclosure(release) {
  const pool = release.historical_pool || {};
  const start = formatDateTime(pool.starts_at) || "5 August 2026";
  const through = formatDateTime(pool.considered_through || release.data_current_through);
  if (pool.all_prior_events_considered && through) {
    return `New means not previously matched among events collected from ${start} through ${through}. Later reconciliation can revise a release, and earlier revisions remain archived.`;
  }
  return "This pilot began collecting evidence on 5 August 2026. Until the longitudinal matching pool is activated, new and recurring labels remain provisional and are disclosed as such.";
}

function renderRelease(release) {
  const counts = release.counts || {};
  const items = Number(counts.ai_relevant_articles || 0);
  const developments = Number(counts.ai_relevant_event_records || 0);
  const firstTime = Number(counts.first_time_event_records ?? counts.new_event_records ?? 0);
  const followOn = Number(counts.follow_on_event_records ?? 0);
  const newDevelopments = Number(counts.new_event_records ?? (firstTime + followOn));
  const recurring = Number(counts.recurring_event_records ?? Math.max(0, developments - newDevelopments));
  const declaredExtra = Number(counts.extra_coverage || 0);
  const computedExtra = Math.max(0, items - developments);
  const extra = declaredExtra === computedExtra ? declaredExtra : computedExtra;
  const pending = Math.max(0, developments - newDevelopments - recurring);
  const period = formatRange(release.period_start, release.period_end);

  setText("release-badge", `Current weekly signal · ${period}`);
  setText("metric-articles", items);
  setText("metric-new", newDevelopments);
  setText("metric-recurring", recurring);
  setText(
    "metric-new-action",
    newDevelopments === 1 ? "Open the new development" : `Open ${newDevelopments} new developments`,
  );
  setText(
    "equation-coverage",
    `${items} coverage items = ${developments} distinct developments + ${extra} additional ${plural(extra, "report")} about developments already counted.`,
  );
  setText(
    "equation-novelty",
    pending
      ? `${developments} developments = ${newDevelopments} new to AIEO + ${recurring} already in AIEO's history + ${pending} still under novelty review.`
      : `${developments} developments = ${newDevelopments} new to AIEO + ${recurring} already in AIEO's history.`,
  );
  setText("hero-takeaway", sourceMixCopy(release));
  setText(
    "remember-copy",
    pending
      ? `AIEO found ${items} AI-related coverage items and grouped them into ${developments} developments. ${newDevelopments} were new to the disclosed historical pool, ${recurring} had been seen before, and ${pending} remained under novelty review.`
      : `AIEO found ${items} AI-related coverage items and grouped them into ${developments} developments. ${newDevelopments} were new to the disclosed historical pool and ${recurring} had been seen before.`,
  );
  setText("historical-pool-copy", poolDisclosure(release));

  const articleLink = document.getElementById("metric-articles-link");
  const newLink = document.getElementById("metric-new-link");
  const recurringLink = document.getElementById("metric-recurring-link");
  if (articleLink) articleLink.href = "/edu/?view=all#evidence";
  if (newLink) newLink.href = "/edu/?view=new#evidence";
  if (recurringLink) recurringLink.href = "/edu/?view=recurring#evidence";
}

function setupNavigation() {
  const toggle = document.querySelector(".nav-toggle");
  const nav = document.getElementById("main-nav");
  toggle?.addEventListener("click", () => {
    const open = toggle.getAttribute("aria-expanded") !== "true";
    toggle.setAttribute("aria-expanded", String(open));
    nav?.setAttribute("data-open", String(open));
  });
  nav?.querySelectorAll("a").forEach((link) => link.addEventListener("click", () => {
    toggle?.setAttribute("aria-expanded", "false");
    nav.removeAttribute("data-open");
  }));

  const globeSection = document.getElementById("globe");
  const sticky = document.getElementById("mobile-globe-cta");
  if (globeSection && sticky) {
    new IntersectionObserver(([entry]) => {
      sticky.setAttribute("data-hidden", String(Boolean(entry?.isIntersecting)));
    }, { threshold: 0.15 }).observe(globeSection);
  }
}

async function init() {
  setupNavigation();
  try {
    const [release, countryData] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(COUNTRIES_URL),
    ]);
    const markets = countryData.markets || {};
    renderRelease(release);
    renderMarketSelection(countryData.selection || {});

    let globe = null;
    const notify = (iso3) => selectMarket(release, markets, iso3);
    try {
      globe = await initDiscoveryGlobe({
        containerId: "home-globe",
        toggleId: "home-globe-toggle",
        promptId: "home-globe-prompt",
        fallbackId: "home-globe-fallback",
        markets,
        onSelect: notify,
      });
    } catch (mapError) {
      console.warn(
        "AIEO globe could not initialise; using market-button fallback",
        mapError,
      );
    }
    renderMarketButtons(markets, globe, notify);
    renderMarketCard(release, markets, null);
  } catch (error) {
    console.error("AIEO homepage could not initialise", error);
    setText("release-badge", "Current signal temporarily unavailable");
    setText("hero-takeaway", "The current release could not be loaded. Open the current-signal page to try again.");
  }
}

init();
