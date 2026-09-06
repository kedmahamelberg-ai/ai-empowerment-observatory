import {weeklyModel, weeklyTakeaway} from '../public-data.js?v=7.2.0';
import {setupNavigation} from '../site.js?v=7.2.0';
"use strict";

const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const SYMBIOSIS_URL = "/data/symbiosis/current.json";

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

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "Not available");
}

async function fetchJSON(url) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) throw new Error(`${url} returned HTTP ${response.status}`);
  return response.json();
}

async function fetchOptionalJSON(url) {
  try {
    return await fetchJSON(url);
  } catch (error) {
    console.info(`${url} is not available yet`, error);
    return null;
  }
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
    recurring: Number(raw.recurring_event_records ?? Math.max(0, events - newDevelopments - Number(raw.possible_historical_match_event_records || 0) - Number(raw.unclassified_novelty_event_records || 0))),
    possible: Number(raw.possible_historical_match_event_records || 0),
    unclassified: Number(raw.unclassified_novelty_event_records || 0),
    extra: Number(raw.extra_coverage || 0),
  };
}

function relationshipCard(symbiosis, current) {
  const model = weeklyModel(current, symbiosis);
  if (!model.ready) return {label:'People and AI', title:'Assessment being prepared', body:'The source-linked news remains available.'};
  return {
    label:'People and AI',
    title:model.axesReady ? `${model.aiCounts.gain} describe AI gains only; ${model.aiCounts.mixed} describe gains and limitations` : "AI reading being prepared",
    body:model.axesReady ? `${model.aiCounts.loss} describe AI limitations only. The same ${model.total} developments are assessed independently for people and AI.` : "",
  };
}

function renderPreview(current, symbiosis) {
  const c = periodCounts(current);
  const relationship = relationshipCard(symbiosis, current);
  const cards = [
    {
      label: "First recorded",
      title: `${c.newDevelopments} first-time ${plural(c.newDevelopments, "development")}`,
      body: c.followOn ? `${c.first} were first recorded in AIEO weekly history with this release; ${c.followOn} were distinct follow-on developments in continuing stories.` : `These developments were first recorded in AIEO weekly history with this release.${c.possible + c.unclassified ? ` ${c.possible + c.unclassified} additional history-match ${plural(c.possible + c.unclassified, "case is", "cases are")} kept separately while validation is unresolved.` : ""}`,
    },
    {
      label: 'What the sources say about people',
      title: weeklyTakeaway(weeklyModel(current, symbiosis)),
      body: 'Open the weekly evidence for source articles and the complete breakdown.',
    },
    relationship,
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
      : "Pilot pool begins 5 August 2026; the longitudinal scope is still growing",
  );
  setText("scope-through", formatDateTime(current.data_current_through || current.generated_at) || "Not available");
  setText("scope-revision", `Revision ${Number(current.revision || index.current_revision || 1)}`);
}

async function init() {
  setupNavigation();
  updateNextEdition();
  try {
    const [current, index, symbiosis] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(INDEX_URL),
      fetchOptionalJSON(SYMBIOSIS_URL),
    ]);
    const c = periodCounts(current);
    const period = formatRange(current.period_start, current.period_end);
    setText("report-period", `Latest completed week · ${period}`);
    setText("cover-coverage-count", c.articles);
    setText("preview-new", c.newDevelopments);
    setText("preview-events", c.events);
    renderPreview(current, symbiosis);
    renderScope(current, index);
  } catch (error) {
    console.error("Monthly Pulse preview could not be loaded", error);
    setText("report-period", "The current weekly evidence preview is temporarily unavailable.");
    const grid = document.getElementById("takeaway-grid");
    if (grid) grid.innerHTML = "<p>The current evidence preview could not be loaded. Please try again shortly.</p>";
  }
}

init();
