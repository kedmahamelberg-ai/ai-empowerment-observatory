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

function relationshipCard(symbiosis, releaseId) {
  const sameRelease = String(symbiosis?.release_id || "") === String(releaseId || "");
  if (!sameRelease) {
    return {
      label: "Human-AI relationship lens",
      title: "Relationship classification is being prepared",
      body: "The core weekly evidence is already available. This relationship layer is added automatically when the same-release classification finishes.",
    };
  }
  const status = String(symbiosis?.public_status || "classification_in_progress");
  const humanReviewed = status === "human_reviewed" && Boolean(symbiosis?.review?.event_complete);
  const event = symbiosis?.event || {};
  const classified = humanReviewed ? Number(event.classified_units || event.expected_units || 0) : Number(event.display_classified_units || event.classified_units || 0);
  if (status === "classification_in_progress" || status === "review_in_progress" || classified === 0) {
    return {
      label: "Human-AI relationship lens",
      title: "Relationship classification is still running",
      body: "No older relationship percentages are substituted. The same-release model-coded signal will appear automatically when classification completes.",
    };
  }

  const counts = humanReviewed ? (event.configuration_counts || {}) : (event.display_configuration_counts || event.configuration_counts || {});
  const candidates = [
    ["mutualism", Number(counts.mutualism || 0)],
    ["ai_benefiting_parasitism", Number(counts.ai_benefiting_parasitism || 0)],
    ["human_benefiting_parasitism", Number(counts.human_benefiting_parasitism || 0)],
    ["competition", Number(counts.competition || 0)],
  ];
  candidates.sort((a, b) => b[1] - a[1]);
  const [key, count] = candidates[0];
  const labels = {
    mutualism: "Both people and the AI side gain",
    ai_benefiting_parasitism: "The AI or operator side gains while people are constrained",
    human_benefiting_parasitism: "People gain while the AI system is constrained",
    competition: "People and the AI side are both constrained",
  };
  const completeCount = humanReviewed ? Number(event.complete_configuration_count || 0) : Number(event.display_complete_configuration_count ?? event.complete_configuration_count ?? 0);
  const noClear = humanReviewed ? Number(event.no_clear_relational_signal_count || 0) : Number(event.display_no_clear_relational_signal_count ?? event.no_clear_relational_signal_count ?? 0);
  const partial = humanReviewed ? Number(event.partial_signal_count || 0) : Number(event.display_partial_signal_count ?? event.partial_signal_count ?? 0);
  return {
    label: humanReviewed ? "Human-AI relationship lens · human reviewed" : "Human-AI relationship lens · model-coded provisional",
    title: count ? `${count} ${plural(count, "development")} showed: ${labels[key]}` : "No complete two-sided pattern dominated",
    body: `${completeCount} developments had a complete two-sided relationship signal. ${partial} had a one-sided signal and ${noClear} described no clear relationship. Accepted human corrections replace model outputs as review proceeds.`,
  };
}

function renderPreview(current, symbiosis) {
  const c = periodCounts(current);
  const relationship = relationshipCard(symbiosis, current?.release_id);
  const cards = [
    {
      label: "First recorded",
      title: `${c.newDevelopments} first-time ${plural(c.newDevelopments, "development")}`,
      body: c.followOn ? `${c.first} were first recorded in AIEO weekly history with this release; ${c.followOn} were distinct follow-on developments in continuing stories.` : `These developments were first recorded in AIEO weekly history with this release.${c.possible + c.unclassified ? ` ${c.possible + c.unclassified} additional history-match ${plural(c.possible + c.unclassified, "case is", "cases are")} kept separately while validation is unresolved.` : ""}`,
    },
    {
      label: "Recurring attention",
      title: `${c.recurring} previously seen ${plural(c.recurring, "development")}`,
      body: `${c.recurring} ${plural(c.recurring, "development was", "developments were")} established before this weekly period and received coverage again. Collection retries and rediscovery alone do not make a development recurring.`,
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
      : "Pilot pool begins 5 August 2026; longitudinal scope is provisional",
  );
  setText("scope-through", formatDateTime(current.data_current_through || current.generated_at) || "Not available");
  setText("scope-revision", `Revision ${Number(current.revision || index.current_revision || 1)}`);
}

async function init() {
  updateNextEdition();
  try {
    const [current, index, symbiosis] = await Promise.all([
      fetchJSON(CURRENT_URL),
      fetchJSON(INDEX_URL),
      fetchOptionalJSON(SYMBIOSIS_URL),
    ]);
    const c = periodCounts(current);
    const period = formatRange(current.period_start, current.period_end);
    setText("report-period", `Current weekly evidence feeding the next monthly edition · ${period}`);
    setText("cover-coverage-count", c.articles);
    setText("preview-new", c.newDevelopments);
    setText("preview-recurring", c.recurring);
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
