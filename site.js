"use strict";

import { initDiscoveryGlobe } from "/globe.js?v=5.10.0";

const BUILD_ID = "5.10.0";
const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const COUNTRIES_URL = "/edu/countries.json";
const SYMBIOSIS_URL = "/data/symbiosis/current.json";

const dateLong = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "long",
  year: "numeric",
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
    ? `${start.getUTCDate()}-${dateLong.format(end)}`
    : `${dateLong.format(start)}-${dateLong.format(end)}`;
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "Not available");
}

async function fetchJSON(url) {
  const separator = url.includes("?") ? "&" : "?";
  const response = await fetch(`${url}${separator}build=${encodeURIComponent(BUILD_ID)}&t=${Date.now()}`, {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
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
  const recurring = Number(raw.recurring_event_records
    ?? Math.max(0, events - newDevelopments - possible - unclassified));
  const declaredExtra = Number(raw.extra_coverage);
  const computedExtra = Math.max(0, articles - events);
  const extra = Number.isFinite(declaredExtra) && declaredExtra >= 0
    ? declaredExtra
    : computedExtra;
  return {
    articles,
    events,
    first,
    followOn,
    newDevelopments,
    recurring,
    possible,
    unclassified,
    extra,
  };
}

function weeklyRows(index) {
  const rows = Array.isArray(index?.weekly) ? index.weekly.slice() : [];
  return rows.sort((a, b) => String(a.period_start || "").localeCompare(String(b.period_start || "")));
}

function rowNumber(row, keys) {
  for (const key of keys) {
    if (row && row[key] != null && Number.isFinite(Number(row[key]))) return Number(row[key]);
  }
  return null;
}

function changeMeta(current, previous) {
  if (!Number.isFinite(previous)) return { className: "flat", arrow: "", text: "current" };
  const delta = current - previous;
  if (delta === 0) return { className: "flat", arrow: "→", text: "0" };
  return {
    className: delta > 0 ? "up" : "down",
    arrow: delta > 0 ? "↑" : "↓",
    text: String(Math.abs(delta)),
  };
}

function renderSignalTape(release, index) {
  const container = document.getElementById("home-signal-tape");
  if (!container) return;
  if (!release) {
    container.innerHTML = '<p class="loading-line data-error">Weekly comparison is temporarily unavailable. <a href="/edu/">Open this week</a></p>';
    return;
  }

  const counts = releaseCounts(release);
  const rows = weeklyRows(index);
  const currentRow = rows.find((row) => row.release_id === release.release_id) || rows.at(-1) || null;
  const currentIndex = currentRow ? rows.indexOf(currentRow) : -1;
  const previous = currentIndex > 0 ? rows[currentIndex - 1] : null;

  const values = [
    {
      label: "Coverage",
      value: counts.articles,
      previous: previous ? rowNumber(previous, ["articles", "ai_relevant_articles"]) : null,
      note: "AI-news source pages this week",
      href: "/edu/?view=all#evidence",
    },
    {
      label: "Developments",
      value: counts.events,
      previous: previous ? rowNumber(previous, ["event_records", "ai_relevant_event_records"]) : null,
      note: "distinct occurrences",
      href: "/edu/#history",
    },
    {
      label: "Additional coverage",
      value: counts.extra,
      previous: previous ? rowNumber(previous, ["extra_coverage"]) : null,
      note: "extra source pages about developments already counted",
      href: "/edu/?view=all#evidence",
    },
    {
      label: "First-time developments",
      value: counts.newDevelopments,
      previous: null,
      note: "not established in an earlier standardized release",
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
  if (!release) {
    setText("release-badge", "Current signal temporarily unavailable");
    setText("hero-summary", "The latest weekly data could not be loaded. Open the current-week page to try again.");
    setText("hero-fact-new", "First-time count unavailable");
    setText("hero-fact-repeat", "Recurring count unavailable");
    const reviewFact = document.getElementById("hero-fact-review");
    if (reviewFact) reviewFact.hidden = true;
    return;
  }

  const counts = releaseCounts(release);
  const period = formatRange(release.period_start, release.period_end);
  const pending = counts.possible + counts.unclassified;
  const extraSentence = counts.extra
    ? ` ${counts.extra} additional ${plural(counts.extra, "source page")} covered a development already counted.`
    : "";
  const noveltySentence = pending
    ? `${counts.newDevelopments} were not established in an earlier standardized release, ${counts.recurring} were recurring, and ${pending} ${plural(pending, "historical match was", "historical matches were")} still being validated.`
    : `${counts.newDevelopments} were not established in an earlier standardized release and ${counts.recurring} were recurring.`;

  setText("release-badge", `Current weekly signal | ${period}`);
  setText(
    "hero-summary",
    `${counts.articles} AI-news source pages were grouped into ${counts.events} distinct developments. ${noveltySentence}${extraSentence}`,
  );
  setText("hero-fact-new", `${counts.newDevelopments} first recorded`);
  setText("hero-fact-repeat", `${counts.recurring} recurring`);
  const reviewFact = document.getElementById("hero-fact-review");
  if (reviewFact) {
    reviewFact.hidden = pending === 0;
    reviewFact.textContent = `${pending} ${plural(pending, "history match", "history matches")} under validation`;
  }
  setText(
    "hero-equation-coverage",
    `${counts.articles} source pages = ${counts.events} developments + ${counts.extra} additional ${plural(counts.extra, "source page")} about developments already counted.`,
  );
  setText(
    "hero-equation-novelty",
    pending
      ? `${counts.events} developments = ${counts.newDevelopments} first recorded + ${counts.recurring} recurring + ${pending} history-match ${plural(pending, "case", "cases")} under validation.`
      : `${counts.events} developments = ${counts.newDevelopments} first recorded + ${counts.recurring} recurring.`,
  );
}

function relationshipCell(key, count, complete) {
  const definitions = {
    mutualism: {
      cls: "mutualism",
      people: "People ↑",
      ai: "AI ↑",
      label: "Both gain",
      technical: "Mutualism",
    },
    ai_benefiting_parasitism: {
      cls: "ai-benefit",
      people: "People ↓",
      ai: "AI ↑",
      label: "AI side gains, people are constrained",
      technical: "AI-benefiting parasitism",
    },
    human_benefiting_parasitism: {
      cls: "human-benefit",
      people: "People ↑",
      ai: "AI ↓",
      label: "People gain, AI side is constrained",
      technical: "Human-benefiting parasitism",
    },
    competition: {
      cls: "competition",
      people: "People ↓",
      ai: "AI ↓",
      label: "Both are constrained",
      technical: "Competition or co-constraint",
    },
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

function relationshipOutsideCopy(total, complete, partial, noClear, ambiguous, insufficient) {
  const outside = Math.max(0, total - complete);
  const phrases = [];
  if (partial) phrases.push(`${partial} one-sided ${plural(partial, "signal")}`);
  if (noClear) phrases.push(`${noClear} no-clear ${plural(noClear, "case")}`);
  if (ambiguous) phrases.push(`${ambiguous} ambiguous ${plural(ambiguous, "case")}`);
  if (insufficient) phrases.push(`${insufficient} insufficient-evidence ${plural(insufficient, "case")}`);
  const accounted = complete + partial + noClear + ambiguous + insufficient;
  const remainder = Math.max(0, total - accounted);
  if (remainder) phrases.push(`${remainder} other unclassified ${plural(remainder, "case")}`);
  let tail = "";
  if (phrases.length === 1) tail = phrases[0];
  else if (phrases.length === 2) tail = `${phrases[0]} and ${phrases[1]}`;
  else if (phrases.length > 2) tail = `${phrases.slice(0, -1).join(", ")}, and ${phrases.at(-1)}`;
  if (!outside) return "All current-week developments fall within the four two-sided relationship patterns.";
  return `${outside} ${plural(outside, "development was", "developments were")} outside the four-pattern denominator${tail ? `: ${tail}` : ""}.`;
}

function renderRelationship(symbiosis, error = null, releaseId = null) {
  const ticker = document.getElementById("relationship-ticker");
  if (!ticker) return;
  if (error) {
    ticker.innerHTML = '<p class="loading-line data-error">The relationship signal is temporarily unavailable.</p>';
    setText("relationship-denominator", "Signal unavailable");
    setText("relationship-other-summary", "Weekly counts and source evidence remain available.");
    return;
  }
  const sameRelease = !releaseId || String(symbiosis?.release_id || "") === String(releaseId);
  if (!symbiosis || !sameRelease) {
    ticker.innerHTML = '<p class="loading-line">The relationship signal is being prepared for this release.</p>';
    setText("relationship-denominator", "Relationship signal being prepared");
    setText("relationship-other-summary", "Weekly counts and source evidence remain available.");
    return;
  }
  const event = symbiosis.event || {};
  const counts = event.display_configuration_counts || event.configuration_counts || {};
  const classified = Number(event.display_classified_units ?? event.classified_units ?? 0);
  const total = Number(event.expected_units || classified || 0);
  if (classified === 0 || (total && classified < total)) {
    ticker.innerHTML = '<p class="loading-line">The relationship signal is being prepared for this release.</p>';
    setText("relationship-denominator", "Relationship signal being prepared");
    setText("relationship-other-summary", "Weekly counts and source evidence remain available.");
    return;
  }
  const complete = Number(event.display_complete_configuration_count ?? event.complete_configuration_count ?? 0);
  ticker.innerHTML = [
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
  ].map((key) => relationshipCell(key, Number(counts[key] || 0), complete)).join("");
  setText("relationship-denominator", `${complete} of ${total} developments had directional evidence for both sides`);
  const partial = Number(event.display_partial_signal_count ?? event.partial_signal_count ?? 0);
  const noClear = Number(event.display_no_clear_relational_signal_count ?? event.no_clear_relational_signal_count ?? 0);
  const ambiguous = Number(event.display_ambiguous_relational_signal_count ?? event.ambiguous_relational_signal_count ?? 0);
  const insufficient = Number(event.display_insufficient_evidence_count ?? event.insufficient_evidence_count ?? 0);
  setText("relationship-other-summary", relationshipOutsideCopy(total, complete, partial, noClear, ambiguous, insufficient));
}
function marketRows(release, iso3) {
  return (release?.units?.coverage_articles || []).filter((row) => (
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
  if (!card) return;
  if (!iso3 || !markets?.[iso3]) {
    card.innerHTML = '<div><strong>Choose a market</strong><p>See the coverage found through that Google News search.</p></div><a class="text-link" href="/edu/#evidence">All evidence</a>';
    return;
  }
  const market = markets[iso3];
  const rows = marketRows(release, iso3);
  const fallbackCount = Number(release?.sources?.discovery_markets?.[iso3] || 0);
  const count = rows.length || fallbackCount;
  const leaders = topPublishers(rows).map(([name, value]) => `${name} (${value})`).join(", ");
  card.innerHTML = `
    <div>
      <strong>${count} coverage ${plural(count, "item")} found through ${escapeHTML(market.name)}</strong>
      <p>${leaders ? `Most visible: ${escapeHTML(leaders)}.` : "Open the evidence list for source details."}</p>
    </div>
    <a class="text-link" href="/edu/?market=${encodeURIComponent(iso3)}&view=all#evidence">Open evidence</a>
  `;
}

function renderMarketButtons(release, markets, globe) {
  const container = document.getElementById("market-buttons");
  if (!container) return null;
  const select = (iso3) => {
    container.querySelectorAll("button[data-market]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.market === iso3));
    });
    renderMarketCard(release, markets, iso3);
  };
  container.innerHTML = Object.entries(markets || {}).map(([iso3, market]) => `
    <button type="button" data-market="${escapeHTML(iso3)}" aria-pressed="false">
      ${escapeHTML(market.short_name || market.name)}
    </button>
  `).join("");
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

async function loadRelease() {
  try {
    const release = await fetchJSON(CURRENT_URL);
    renderRelease(release);
    return release;
  } catch (error) {
    console.error("Current weekly release could not be loaded", error);
    renderRelease(null);
    return null;
  }
}

async function loadSignalTape(releasePromise) {
  const release = await releasePromise;
  if (!release) {
    renderSignalTape(null, null);
    return;
  }
  try {
    const index = await fetchJSON(INDEX_URL);
    renderSignalTape(release, index);
  } catch (error) {
    console.warn("Weekly comparison index could not be loaded", error);
    renderSignalTape(release, null);
  }
}

async function loadRelationship(releasePromise) {
  const release = await releasePromise;
  try {
    const symbiosis = await fetchJSON(SYMBIOSIS_URL);
    renderRelationship(symbiosis, null, release?.release_id || null);
  } catch (error) {
    console.warn("Relationship artifact could not be loaded", error);
    renderRelationship(null, error, release?.release_id || null);
  }
}

async function loadGlobe(releasePromise) {
  const release = await releasePromise;
  try {
    const countries = await fetchJSON(COUNTRIES_URL);
    const markets = countries?.markets || {};
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
    console.error("Discovery-market data could not be loaded", error);
    const fallback = document.getElementById("home-globe-fallback");
    if (fallback) {
      fallback.hidden = false;
      fallback.textContent = "The discovery map is temporarily unavailable. Open the source evidence to continue.";
    }
    renderMarketCard(release, {}, null);
  }
}

async function init() {
  console.info(`AIEO public interface build ${BUILD_ID}`);
  setupNavigation();
  setupInfoPopovers();
  const releasePromise = loadRelease();
  await Promise.allSettled([
    loadSignalTape(releasePromise),
    loadRelationship(releasePromise),
    loadGlobe(releasePromise),
  ]);
}

init();
