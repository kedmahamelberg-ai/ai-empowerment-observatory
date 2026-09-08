import {weeklyModel} from '../public-data.js?v=7.3.0';
"use strict";

const BUILD_ID = "6.4.0";

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
    const response = await fetch(`${url}?build=${BUILD_ID}&t=${Date.now()}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    return response.json();
  } catch (error) {
    if (optional) return null;
    throw error;
  }
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
  const [release, symbiosis] = await Promise.all([
    fetchJSON("/data/releases/current.json"),
    fetchJSON("/data/symbiosis/current.json", true),
  ]);
  setText("scope-period", formatRange(release.period_start, release.period_end));
  const pool = release.historical_pool || {};
  setText("scope-pool", pool.all_prior_events_considered ? `All accepted evidence since ${String(pool.starts_at || "5 August 2026").slice(0,10)}` : "Pilot history from 5 August 2026");
  setText("scope-release", `${release.release_id} revision ${Number(release.revision || 1)}`);
  const model = weeklyModel(release, symbiosis);
  setText("scope-picture", model.ready ? `${model.total} developments with complete source content; ${model.inventory.total} in the collection audit` : "Current picture being prepared");
}

init().catch((error) => {
  console.error(error);
  setText("scope-period", "Current scope unavailable");
});
