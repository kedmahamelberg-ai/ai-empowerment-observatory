"use strict";

const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";

const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });
const dateTime = new Intl.DateTimeFormat("en-GB", {
  day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short",
});

function parseDate(value) {
  const date = new Date(`${String(value || "").slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatRange(startValue, endValue) {
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (!start || !end) return "Date unavailable";
  const sameMonth = start.getUTCMonth() === end.getUTCMonth() && start.getUTCFullYear() === end.getUTCFullYear();
  return sameMonth ? `${start.getUTCDate()}–${dateLong.format(end)}` : `${dateLong.format(start)}–${dateLong.format(end)}`;
}

function formatDateTime(value) {
  const parsed = new Date(String(value || ""));
  return Number.isNaN(parsed.getTime()) ? null : dateTime.format(parsed);
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

function signed(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
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

function updateNextEdition() {
  const config = window.AIEO_NEWSLETTER_CONFIG || {};
  const label = String(config.nextEditionLabel || "the next scheduled edition");
  const date = parseDate(config.nextEditionDate);
  setText("next-edition-date", label);
  if (!date) {
    setText("monthly-pulse-countdown", "One useful signal, once a month.");
    return;
  }
  const now = new Date();
  const todayUtc = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  const targetUtc = Date.UTC(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate());
  const days = Math.ceil((targetUtc - todayUtc) / 86400000);
  if (days > 1) setText("monthly-pulse-countdown", `${days} days to go. One useful signal, once a month.`);
  else if (days === 1) setText("monthly-pulse-countdown", "Tomorrow. One useful signal, once a month.");
  else if (days === 0) setText("monthly-pulse-countdown", "Scheduled for today.");
  else setText("monthly-pulse-countdown", "The next edition schedule will be updated shortly.");
}

function periodCounts(current) {
  const raw = current.counts || {};
  const events = Number(raw.ai_relevant_event_records || 0);
  const first = Number(raw.first_time_event_records ?? raw.new_event_records ?? 0);
  const followOn = Number(raw.follow_on_event_records ?? 0);
  const newDevelopments = Number(raw.new_event_records ?? (first + followOn));
  return {
    articles: Number(raw.ai_relevant_articles || 0),
    events,
    first,
    followOn,
    newDevelopments,
    recurring: Number(raw.recurring_event_records ?? Math.max(0, events - newDevelopments)),
    resurfaced: Number(raw.resurfaced_event_records ?? current.dynamics?.resurfaced_event_appearances ?? 0),
    possible: Number(raw.possible_historical_match_event_records || 0),
    unclassified: Number(raw.unclassified_novelty_event_records || 0),
    extra: Number(raw.extra_coverage || 0),
  };
}

function renderPreview(current) {
  const c = periodCounts(current);
  const eventIndex = current.lenses?.event?.empowerment_index;
  const cards = [
    {
      label: "New reality",
      title: `${c.newDevelopments} new ${plural(c.newDevelopments, "development")}`,
      body: c.followOn ? `${c.first} entered the pool for the first time; ${c.followOn} were genuine follow-on developments in continuing stories.` : "These developments were not counted as repetitions of an earlier event in the disclosed historical pool.",
    },
    {
      label: "Recurring attention",
      title: `${c.recurring} previously seen ${plural(c.recurring, "development")}`,
      body: c.resurfaced
        ? `${c.resurfaced} resurfaced after at least four weeks without observed coverage.${c.possible + c.unclassified ? ` ${c.possible + c.unclassified} novelty ${plural(c.possible + c.unclassified, "decision remains", "decisions remain")} under review.` : ""}`
        : `Repeated coverage remains visible without being counted as another new development.${c.possible + c.unclassified ? ` ${c.possible + c.unclassified} novelty ${plural(c.possible + c.unclassified, "decision remains", "decisions remain")} under review.` : ""}`,
    },
    {
      label: "Human-empowerment signal",
      title: `${signed(eventIndex)} on the Event Lens`,
      body: "Narrative tone is measured separately, so positive framing is not automatically treated as human empowerment.",
    },
  ];
  const grid = document.getElementById("takeaway-grid");
  if (grid) {
    grid.innerHTML = cards.map((card) => `
      <article><span>${card.label}</span><h3>${card.title}</h3><p>${card.body}</p></article>
    `).join("");
  }
}

function renderScope(current, index) {
  const pool = current.historical_pool || index.historical_pool || {};
  const start = formatDateTime(pool.starts_at) || "5 August 2026";
  const through = formatDateTime(pool.considered_through || current.data_current_through);
  setText("scope-period", formatRange(current.period_start, current.period_end));
  setText(
    "scope-pool",
    pool.all_prior_events_considered && through
      ? `${start} through ${through}`
      : "Pilot pool begins 5 August 2026; longitudinal scope is provisional",
  );
  setText("scope-through", formatDateTime(current.data_current_through || current.generated_at) || "—");
  setText("scope-revision", `Revision ${Number(current.revision || index.current_revision || 1)}`);
}

async function init() {
  updateNextEdition();
  try {
    const [current, index] = await Promise.all([fetchJSON(CURRENT_URL), fetchJSON(INDEX_URL)]);
    const c = periodCounts(current);
    const period = formatRange(current.period_start, current.period_end);
    setText("report-period", `Current weekly evidence feeding the next monthly edition · ${period}`);
    setText("cover-coverage-count", c.articles);
    setText("preview-new", c.newDevelopments);
    setText("preview-recurring", c.recurring);
    renderPreview(current);
    renderScope(current, index);
  } catch (error) {
    console.error("Monthly Pulse preview could not be loaded", error);
    setText("report-period", "The current weekly evidence preview is temporarily unavailable.");
    const grid = document.getElementById("takeaway-grid");
    if (grid) grid.innerHTML = "<p>The current evidence preview could not be loaded. Please try again shortly.</p>";
  }
}

init();
