"use strict";

const BUILD_ID = "5.9.1";
const PERIOD_INDEX_URL = "/data/releases/period-index.json";
const RELEASE_INDEX_URL = "/data/releases/index.json";

const dateLong = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "long",
  year: "numeric",
});
const monthLong = new Intl.DateTimeFormat("en-GB", {
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
  return `${dateLong.format(start)} to ${dateLong.format(end)}`;
}

function plural(value, singular, pluralForm = `${singular}s`) {
  return Number(value) === 1 ? singular : pluralForm;
}

async function fetchJSON(url, optional = false) {
  try {
    const separator = url.includes("?") ? "&" : "?";
    const response = await fetch(`${url}${separator}build=${encodeURIComponent(BUILD_ID)}&t=${Date.now()}`, {
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

function typeMeta(type) {
  return {
    monthly: { label: "Monthly Pulse", short: "Month", cls: "monthly" },
    quarterly: { label: "Quarterly synthesis", short: "Quarter", cls: "quarterly" },
    annual: { label: "Annual evidence review", short: "Year", cls: "annual" },
  }[type] || { label: type || "Period summary", short: "Period", cls: "period" };
}

function periodDisplay(row) {
  if (row.period_type === "monthly") {
    const date = parseDate(`${row.period_id}-01`);
    return date ? monthLong.format(date) : row.period_id;
  }
  if (row.period_type === "quarterly") {
    const match = String(row.period_id || "").match(/^(\d{4})-Q([1-4])$/);
    return match ? `Q${match[2]} ${match[1]}` : row.period_id;
  }
  return String(row.period_id || "Period");
}

function summaryValues(row, summary) {
  const attention = summary?.attention || {};
  const reality = summary?.reality || {};
  const coverage = Number(attention.published_coverage_articles ?? row.articles ?? 0);
  const developments = Number(reality.distinct_event_records ?? row.distinct_event_records ?? 0);
  const recurring = Number(attention.recurring_event_appearances ?? row.recurring_event_appearances ?? 0);
  const lag = Number(summary?.replication_delay?.median_days);
  return {
    coverage,
    developments,
    recurring,
    lag: Number.isFinite(lag) && lag > 0 ? lag : null,
    story: String(summary?.story || "This summary is being assembled from the available standardized weekly releases."),
  };
}

function currentId(periodIndex, type, rows) {
  const direct = periodIndex?.current?.[type];
  if (typeof direct === "string") return direct;
  if (direct && typeof direct === "object") return direct.period_id || direct.id || null;
  const flagged = rows.find((row) => row.period_type === type && (row.current === true || row.status === "accumulating"));
  if (flagged) return flagged.period_id;
  return rows
    .filter((row) => row.period_type === type)
    .sort((a, b) => String(b.period_end || "").localeCompare(String(a.period_end || "")))[0]?.period_id || null;
}

function statusLabel(row, isCurrent) {
  const status = String(row.status || "available").toLowerCase();
  if (isCurrent && status === "accumulating") return "Live and updating";
  if (status === "complete") return "Complete";
  if (status === "accumulating") return "Still building";
  return status.replaceAll("_", " ");
}

function metric(label, value) {
  return `<div class="period-metric"><strong>${Number(value).toLocaleString("en-GB")}</strong><span>${escapeHTML(label)}</span></div>`;
}

function currentCard(row, summary, isCurrent) {
  const meta = typeMeta(row.period_type);
  const values = summaryValues(row, summary);
  return `
    <article class="period-card current-card ${meta.cls}" data-current="${String(isCurrent)}">
      <header class="period-card-head">
        <div>
          <span class="period-type">${escapeHTML(meta.label)}</span>
          <h3>${escapeHTML(periodDisplay(row))}</h3>
        </div>
        <span class="status-pill">${escapeHTML(statusLabel(row, isCurrent))}</span>
      </header>
      <p class="period-range">${escapeHTML(formatRange(row.period_start, row.period_end))}</p>
      <div class="period-metrics">
        ${metric("coverage items", values.coverage)}
        ${metric("distinct developments", values.developments)}
        ${metric("seen-before appearances", values.recurring)}
      </div>
      <details class="period-story">
        <summary>Read the period takeaway</summary>
        <p>${escapeHTML(values.story)}</p>
        ${values.lag != null ? `<small>Median return delay: ${Math.round(values.lag)} ${plural(Math.round(values.lag), "day")}.</small>` : ""}
      </details>
      <footer class="period-card-foot">
        <span>Revision ${Number(row.revision || 1)}</span>
        <span>${escapeHTML(row.period_id)}</span>
      </footer>
    </article>
  `;
}

function archiveCard(row, summary) {
  const meta = typeMeta(row.period_type);
  const values = summaryValues(row, summary);
  return `
    <article class="archive-card ${meta.cls}">
      <div>
        <span class="period-type">${escapeHTML(meta.short)}</span>
        <h3>${escapeHTML(periodDisplay(row))}</h3>
        <p>${escapeHTML(formatRange(row.period_start, row.period_end))}</p>
      </div>
      <div class="archive-stats">
        <span><strong>${values.coverage}</strong> coverage</span>
        <span><strong>${values.developments}</strong> developments</span>
      </div>
      <details>
        <summary>Takeaway</summary>
        <p>${escapeHTML(values.story)}</p>
      </details>
    </article>
  `;
}

async function loadSummaries(rows) {
  const entries = await Promise.all(rows.map(async (row) => {
    const summary = row.path ? await fetchJSON(row.path, true) : null;
    return [row.period_id, summary];
  }));
  return new Map(entries);
}

function renderOverview(rows, releaseIndex) {
  const currentRows = rows.filter((row) => row.status === "accumulating" || row.current === true);
  const summaryCount = currentRows.length || Math.min(3, rows.length);
  const weeklyCount = Array.isArray(releaseIndex?.weekly) ? releaseIndex.weekly.length : 0;
  document.getElementById("summary-count").textContent = `${summaryCount} current ${plural(summaryCount, "summary", "summaries")}`;
  document.getElementById("weekly-basis").textContent = weeklyCount
    ? `Built from ${weeklyCount} standardized weekly ${plural(weeklyCount, "release")}.`
    : "Built from the available standardized weekly releases.";
}

async function renderReports(periodIndex, releaseIndex) {
  const rows = Array.isArray(periodIndex?.summaries) ? periodIndex.summaries.slice() : [];
  const status = document.getElementById("archive-status");
  const currentContainer = document.getElementById("current-periods");
  const archiveSection = document.getElementById("archive-section");
  const archiveContainer = document.getElementById("archive-periods");

  if (!rows.length) {
    status.textContent = "The first period summaries will appear as the standardized weekly history grows.";
    currentContainer.innerHTML = '<div class="empty-state"><strong>No period summary yet</strong><p>The weekly evidence remains available on the current-week page.</p><a href="/edu/">Open this week</a></div>';
    renderOverview([], releaseIndex);
    return;
  }

  const summaries = await loadSummaries(rows);
  const types = ["monthly", "quarterly", "annual"];
  const currentRows = [];
  const currentIds = new Set();

  types.forEach((type) => {
    const id = currentId(periodIndex, type, rows);
    const row = rows.find((item) => item.period_type === type && item.period_id === id);
    if (row) {
      currentRows.push(row);
      currentIds.add(row.period_id);
    }
  });

  status.textContent = `${currentRows.length} reporting ${plural(currentRows.length, "window is", "windows are")} open. Each updates when a new standardized week is accepted.`;
  currentContainer.innerHTML = currentRows.map((row) => currentCard(row, summaries.get(row.period_id), true)).join("");

  const archiveRows = rows
    .filter((row) => !currentIds.has(row.period_id))
    .sort((a, b) => String(b.period_end || "").localeCompare(String(a.period_end || "")));

  if (archiveRows.length) {
    archiveSection.hidden = false;
    archiveContainer.innerHTML = archiveRows.map((row) => archiveCard(row, summaries.get(row.period_id))).join("");
  } else {
    archiveSection.hidden = true;
    archiveContainer.innerHTML = "";
  }

  renderOverview(rows, releaseIndex);
}

function renderRevision(index) {
  const weekly = Array.isArray(index?.weekly) ? index.weekly : [];
  const currentIdValue = index?.current_release_id;
  const current = weekly.find((row) => row.release_id === currentIdValue) || weekly.at(-1) || null;
  const revision = Number(current?.revision || index?.current_revision || 1);
  const archives = index?.revision_archives || {};
  const archived = Object.values(archives).reduce((sum, rows) => sum + (Array.isArray(rows) ? rows.length : 0), 0);
  const releaseLabel = current?.release_id ? `${current.release_id}, revision ${revision}` : `revision ${revision}`;
  document.getElementById("revision-copy").textContent = archived
    ? `The current weekly series uses ${releaseLabel}. ${archived} earlier ${plural(archived, "revision remains", "revisions remain")} archived and inspectable.`
    : `The current weekly series uses ${releaseLabel}. Earlier versions are archived automatically when an accepted correction changes published counts.`;
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
  setupNavigation();
  try {
    const [periodIndex, releaseIndex] = await Promise.all([
      fetchJSON(PERIOD_INDEX_URL, true),
      fetchJSON(RELEASE_INDEX_URL, true),
    ]);
    await renderReports(periodIndex, releaseIndex);
    renderRevision(releaseIndex);
  } catch (error) {
    console.error("Report archive could not load", error);
    document.getElementById("archive-status").textContent = "The report archive is temporarily unavailable.";
    document.getElementById("current-periods").innerHTML = '<div class="empty-state"><strong>Reports could not be loaded</strong><p>Please try again shortly or open the current weekly evidence.</p><a href="/edu/">Open this week</a></div>';
    document.getElementById("summary-count").textContent = "Archive unavailable";
    document.getElementById("weekly-basis").textContent = "The weekly evidence remains available.";
  }
}

init();
