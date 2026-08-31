"use strict";

const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });

function parseDate(value) {
  const date = new Date(`${String(value || "").slice(0,10)}T12:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatRange(startValue, endValue) {
  const start = parseDate(startValue);
  const end = parseDate(endValue);
  if (!start || !end) return "Date unavailable";
  return `${start.getUTCDate()}-${dateLong.format(end)}`;
}

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = String(value ?? "Not available");
}

async function fetchJSON(url, optional = false) {
  try {
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  } catch (error) {
    if (optional) return null;
    throw error;
  }
}

async function init() {
  const [release, symbiosis] = await Promise.all([
    fetchJSON("/data/releases/current.json"),
    fetchJSON("/data/symbiosis/current.json", true),
  ]);
  setText("scope-period", formatRange(release.period_start, release.period_end));
  const pool = release.historical_pool || {};
  setText("scope-pool", pool.all_prior_events_considered ? `All accepted evidence since ${String(pool.starts_at || "5 August 2026").slice(0,10)}` : "Pilot history from 5 August 2026");
  setText("scope-release", `${release.release_id} revision ${Number(release.revision || 1)}`);
  const relationshipCurrent = symbiosis && String(symbiosis.release_id || "") === String(release.release_id || "");
  setText("scope-review", relationshipCurrent && symbiosis?.public_status === "human_reviewed" ? `${symbiosis.review?.event_reviewed || 0} of ${symbiosis.review?.event_total || 0} developments reviewed` : "Relationship review in progress");
}

init().catch((error) => {
  console.error(error);
  setText("scope-period", "Current scope unavailable");
});
