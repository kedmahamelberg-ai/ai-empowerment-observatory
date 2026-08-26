"use strict";

import { initDiscoveryGlobe } from "/globe.js";

const CURRENT_URL = "/data/releases/current.json";
const COUNTRIES_URL = "/edu/countries.json";

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
  if (element) element.textContent = String(value ?? "—");
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

function renderMarketCard(release, markets, iso3) {
  const card = document.getElementById("market-card");
  const cta = document.getElementById("market-evidence-cta");
  if (!card || !cta) return;

  if (!iso3 || !markets[iso3]) {
    const discovered = Object.keys(release.sources?.discovery_markets || markets).length;
    card.firstElementChild.innerHTML = `
      <p class="eyebrow">Choose a market</p>
      <h3>Start anywhere on the globe</h3>
      <p>AIEO currently follows ${discovered} discovery ${plural(discovered, "market")}. Select one to see what appeared there.</p>
    `;
    cta.href = "/edu/#evidence";
    cta.textContent = "See all source-linked evidence →";
    return;
  }

  const market = markets[iso3];
  const rows = marketRows(release, iso3);
  const fallbackCount = Number(release.sources?.discovery_markets?.[iso3] || 0);
  const articleCount = rows.length || fallbackCount;
  const publishers = new Set(rows.map((row) => row.publisher).filter(Boolean));
  const leaders = topPublishers(rows);
  const leaderCopy = leaders.length
    ? leaders.map(([name, count]) => `${escapeHTML(name)} (${count})`).join(" · ")
    : "Source detail will appear with the next standardized release.";

  card.firstElementChild.innerHTML = `
    <p class="eyebrow">${escapeHTML(market.name)}</p>
    <h3>${articleCount} observed ${plural(articleCount, "article")}</h3>
    <div class="market-statline">
      <span><strong>${publishers.size || "—"}</strong> represented ${plural(publishers.size, "publication")}</span>
    </div>
    <p>Most visible: ${leaderCopy}</p>
  `;
  cta.href = `/edu/?market=${encodeURIComponent(iso3)}#evidence`;
  cta.textContent = `View ${market.name} evidence →`;
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
    return `“New” means not previously matched among events collected from ${start} through ${through}. Later reconciliation can revise a release, and earlier revisions remain archived.`;
  }
  return "This pilot began collecting evidence on 5 August 2026. Until the longitudinal matching pool is activated, new/recurring labels remain provisional and are disclosed as such.";
}

function renderRelease(release) {
  const counts = release.counts || {};
  const articles = Number(counts.ai_relevant_articles || 0);
  const events = Number(counts.ai_relevant_event_records || 0);
  const firstTime = Number(counts.first_time_event_records ?? counts.new_event_records ?? 0);
  const followOn = Number(counts.follow_on_event_records ?? 0);
  const newDevelopments = Number(counts.new_event_records ?? (firstTime + followOn));
  const recurring = Number(counts.recurring_event_records ?? Math.max(0, events - newDevelopments));
  const extra = Number(counts.extra_coverage || 0);
  const resurfaced = Number(counts.resurfaced_event_records ?? release.dynamics?.resurfaced_event_appearances ?? 0);
  const possible = Number(counts.possible_historical_match_event_records ?? 0);
  const unclassified = Number(counts.unclassified_novelty_event_records ?? 0);
  const reviewNote = possible + unclassified > 0
    ? ` ${possible + unclassified} ${plural(possible + unclassified, "record remains", "records remain")} under novelty review.`
    : "";
  const period = formatRange(release.period_start, release.period_end);

  setText("release-badge", `Current weekly signal · ${period}`);
  setText("metric-articles", articles);
  setText("metric-new", newDevelopments);
  setText("metric-recurring", recurring);
  setText(
    "hero-takeaway",
    `${articles} published ${plural(articles, "article")} represented ${events} resolved event ${plural(events, "record")}. ${extra} ${plural(extra, "article was", "articles were")} additional coverage rather than additional reality.${reviewNote}`,
  );
  setText(
    "remember-copy",
    resurfaced > 0
      ? `${newDevelopments} new developments entered the week’s reality view. ${recurring} previously observed developments received new coverage, including ${resurfaced} that resurfaced after at least four weeks.`
      : `${newDevelopments} new developments entered the week’s reality view, while ${recurring} previously observed developments received new coverage. Repetition adds attention, not another development.`,
  );
  setText("historical-pool-copy", poolDisclosure(release));
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
      // Keep the weekly signal and text-based market controls usable when the
      // external map module, WebGL or tile service is unavailable.
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
