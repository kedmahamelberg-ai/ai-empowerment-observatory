"use strict";

const PERIOD_INDEX_URL = "/data/releases/period-index.json";
const RELEASE_INDEX_URL = "/data/releases/index.json";

const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });

function escapeHTML(value) {
  return String(value ?? "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");
}

function parseDate(value) {
  const date = new Date(`${String(value || "").slice(0, 10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatRange(startValue, endValue) {
  const start = parseDate(startValue), end = parseDate(endValue);
  if (!start || !end) return "Date unavailable";
  return `${dateLong.format(start)}–${dateLong.format(end)}`;
}

function plural(value, singular, pluralForm = `${singular}s`) { return Number(value) === 1 ? singular : pluralForm; }

async function fetchJSON(url, optional = false) {
  const response = await fetch(url, { cache: "no-store" });
  if (!response.ok) {
    if (optional) return null;
    throw new Error(`${url} returned HTTP ${response.status}`);
  }
  return response.json();
}

async function loadSummary(path) {
  if (!path) return null;
  return fetchJSON(path, true);
}

function groupTitle(type) {
  return { monthly: "Monthly Pulses", quarterly: "Quarterly syntheses", annual: "Annual evidence reviews" }[type] || type;
}

function card(row, summary, currentId) {
  const reality = summary?.reality || {};
  const attention = summary?.attention || {};
  const story = summary?.story || "This period summary is being assembled from the available standardized weekly releases.";
  const current = row.period_id === currentId;
  const lag = summary?.replication_delay?.median_days;
  return `
    <article class="period-card" data-current="${String(current)}">
      <span class="period-label">${current ? "Current · " : ""}${escapeHTML(row.status || "available")} · revision ${Number(row.revision || 1)}</span>
      <h4>${escapeHTML(row.period_id)}</h4>
      <p>${escapeHTML(formatRange(row.period_start, row.period_end))}</p>
      <div class="period-stats">
        <span>${Number(attention.published_coverage_articles ?? row.articles ?? 0)} articles</span>
        <span>${Number(reality.distinct_event_records ?? row.distinct_event_records ?? 0)} distinct event records</span>
        <span>${Number(attention.recurring_event_appearances ?? row.recurring_event_appearances ?? 0)} recurring</span>
        ${lag != null ? `<span>${Number(lag).toFixed(0)}-day median recurrence</span>` : ""}
      </div>
      <details>
        <summary>Read the period in one sentence</summary>
        <p class="period-story">${escapeHTML(story)}</p>
      </details>
    </article>
  `;
}

async function renderReports(periodIndex) {
  const container = document.getElementById("report-groups");
  const status = document.getElementById("archive-status");
  const rows = periodIndex?.summaries || [];
  if (!rows.length) {
    status.textContent = "The first standardized period summaries will appear automatically as the weekly history grows.";
    container.innerHTML = ["monthly", "quarterly", "annual"].map((type) => `
      <section class="report-group">
        <div class="group-heading"><h3>${groupTitle(type)}</h3><span>Building from weekly releases</span></div>
        <p>No ${type} summary has been published yet.</p>
      </section>
    `).join("");
    return;
  }
  status.textContent = `${rows.length} period ${plural(rows.length, "summary is", "summaries are")} available. Current accumulating periods update automatically.`;
  const summaries = new Map();
  await Promise.all(rows.map(async (row) => summaries.set(row.period_id, await loadSummary(row.path))));
  container.innerHTML = ["monthly", "quarterly", "annual"].map((type) => {
    const groupRows = rows.filter((row) => row.period_type === type).sort((a, b) => String(b.period_end).localeCompare(String(a.period_end)));
    return `
      <section class="report-group">
        <div class="group-heading"><h3>${groupTitle(type)}</h3><span>${groupRows.length} ${plural(groupRows.length, "period")}</span></div>
        <div class="period-cards">
          ${groupRows.length ? groupRows.slice(0, 6).map((row) => card(row, summaries.get(row.period_id), periodIndex.current?.[type])).join("") : `<p>No ${type} summary yet.</p>`}
        </div>
      </section>
    `;
  }).join("");
}

function renderRevision(index) {
  const currentRevision = Number(index?.current_revision || 1);
  const archives = index?.revision_archives || {};
  const archived = Object.values(archives).reduce((sum, rows) => sum + (Array.isArray(rows) ? rows.length : 0), 0);
  document.getElementById("revision-copy").textContent = archived
    ? `The current weekly series uses revision ${currentRevision}. ${archived} earlier ${plural(archived, "revision remains", "revisions remain")} archived and inspectable.`
    : `The current weekly series uses revision ${currentRevision}. No historical restatement has been required yet; the archive will appear automatically when a correction changes published counts.`;
}

async function init() {
  try {
    const [periodIndex, releaseIndex] = await Promise.all([
      fetchJSON(PERIOD_INDEX_URL, true),
      fetchJSON(RELEASE_INDEX_URL),
    ]);
    await renderReports(periodIndex);
    renderRevision(releaseIndex);
  } catch (error) {
    console.error("Report archive could not load", error);
    document.getElementById("archive-status").textContent = "The report archive is temporarily unavailable.";
  }
}

init();
