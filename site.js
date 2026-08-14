"use strict";

const paths = {
  lenses: "/data/lenses/latest.json",
  events: "/data/events/latest.json",
  status: "/data/status/latest.json",
  config: "/data/site-config.json"
};

async function safeJSON(path) {
  try {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
    return await response.json();
  } catch (error) {
    console.error(error);
    return null;
  }
}

function signed(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function isoDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function dateRange(eventsPayload) {
  const values = [];
  for (const event of eventsPayload?.events || []) {
    if (event.event_date) values.push(event.event_date);
    for (const source of event.sources || []) {
      if (source.published_at) values.push(source.published_at);
    }
  }
  const dates = values.map(isoDate).filter(Boolean).sort((a, b) => a - b);
  if (!dates.length) return null;
  return { start: dates[0], end: dates[dates.length - 1] };
}

function formatDate(date) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric"
  }).format(date);
}

function releaseLabel(lenses, status) {
  const value = lenses?.meta?.release_status || status?.release_status || "provisional_automated";
  if (String(value).startsWith("human_audited")) return "Live now · Human-audited public release";
  if (String(value).includes("audited")) return "Live now · Audited public release";
  return "Live now · Provisional automated release";
}

async function init() {
  const [lenses, events, status, config] = await Promise.all([
    safeJSON(paths.lenses),
    safeJSON(paths.events),
    safeJSON(paths.status),
    safeJSON(paths.config)
  ]);

  if (!lenses?.global) {
    document.getElementById("release-badge").textContent = "Current data temporarily unavailable";
    return;
  }

  const coverage = lenses.global.coverage || {};
  const event = lenses.global.event || {};
  const amplification = lenses.global.amplification || {};
  const coverageCount = Number(coverage.unit_count_ai_relevant || coverage.unit_count_total || 0);
  const eventCount = Number(event.unit_count_ai_relevant || event.unit_count_total || 0);
  const maximum = Math.max(coverageCount, eventCount, 1);
  const range = dateRange(events);

  document.getElementById("release-badge").textContent = releaseLabel(lenses, status);
  document.getElementById("home-coverage-count").textContent = coverageCount.toLocaleString("en-GB");
  document.getElementById("home-event-count").textContent = eventCount.toLocaleString("en-GB");
  document.getElementById("home-coverage-index").textContent = signed(coverage.empowerment_index);
  document.getElementById("home-event-index").textContent = signed(event.empowerment_index);
  document.getElementById("home-gap").textContent = signed(amplification.directional_amplification_gap);
  document.getElementById("home-coverage-bar").style.width = `${coverageCount / maximum * 100}%`;
  document.getElementById("home-event-bar").style.width = `${eventCount / maximum * 100}%`;

  const markets = config?.search_markets || [];
  const windowText = range
    ? `${formatDate(range.start)}–${formatDate(range.end)}`
    : "current observation window";

  document.getElementById("scope-strip").innerHTML = `
    <strong>${windowText}</strong>
    <span>${markets.length || 5} search markets · ${coverageCount} AI-relevant articles · ${eventCount} unique events</span>
  `;
  document.getElementById("observation-window").textContent = windowText;
  document.getElementById("snapshot-period").textContent = `Current release covers ${windowText}; last updated ${status?.generated_at ? formatDate(new Date(status.generated_at)) : "with the latest successful pipeline run"}.`;
}

init();
