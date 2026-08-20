"use strict";

const paths = {
  lenses: "/data/lenses/latest.json",
  events: "/data/events/latest.json"
};

async function safeJSON(path) {
  try {
    const response = await fetch(path, { cache: "no-store" });
    if (!response.ok) return null;
    return response.json();
  } catch {
    return null;
  }
}

function signed(value) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}

function dateRange(eventsPayload) {
  const values = [];
  for (const event of eventsPayload?.events || []) {
    if (event.event_date) values.push(event.event_date);
    for (const source of event.sources || []) {
      if (source.published_at) values.push(source.published_at);
    }
  }

  const dates = values
    .map(value => new Date(value))
    .filter(date => !Number.isNaN(date.getTime()))
    .sort((a, b) => a - b);

  if (!dates.length) return null;
  return { start: dates[0], end: dates[dates.length - 1] };
}

function formatDate(date, options = {}) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "Europe/Amsterdam",
    ...options
  }).format(date);
}

function shortDate(date) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    timeZone: "Europe/Amsterdam"
  }).format(date);
}

function dominantKey(distribution) {
  return Object.entries(distribution || {})
    .sort((a, b) => Number(b[1]) - Number(a[1]))[0]?.[0] || "unclear";
}

function readable(value) {
  return String(value || "")
    .replaceAll("_", " ")
    .replace(/^./, char => char.toUpperCase());
}

function nextPulseRelease(now = new Date()) {
  const currentCandidate = new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth(),
    3,
    8,
    0,
    0
  ));

  if (now.getTime() < currentCandidate.getTime()) {
    return currentCandidate;
  }

  return new Date(Date.UTC(
    now.getUTCFullYear(),
    now.getUTCMonth() + 1,
    3,
    8,
    0,
    0
  ));
}

function updateCountdown() {
  const release = nextPulseRelease();
  const now = new Date();
  const days = Math.max(
    0,
    Math.ceil((release.getTime() - now.getTime()) / 86400000)
  );

  const dateNode = document.getElementById("next-edition-date");
  const countdownNode = document.getElementById("monthly-pulse-countdown");

  if (dateNode) {
    dateNode.textContent = formatDate(release);
  }

  if (countdownNode) {
    countdownNode.textContent =
      days === 0
        ? "Scheduled for release today."
        : `${days} day${days === 1 ? "" : "s"} to go. One useful signal, once a month.`;
  }
}

function takeaways(lenses) {
  const coverage = lenses.global.coverage;
  const event = lenses.global.event;
  const amp = lenses.global.amplification;
  const gap = Number(amp.directional_amplification_gap || 0);
  const ratio = Number(amp.coverage_event_ratio || 0);
  const narrative = dominantKey(coverage.narrative_distribution);
  const eventIndex = Number(event.empowerment_index || 0);

  return [
    {
      label: "Overall direction",
      title:
        Math.abs(eventIndex) < 5
          ? "The unique-development signal is close to neutral"
          : `The Event Lens is ${
              eventIndex > 0 ? "expansion-oriented" : "contraction-oriented"
            }`,
      text: `The latest Event Empowerment Index is ${signed(
        event.empowerment_index
      )} on a -100 to +100 scale.`
    },
    {
      label: "Attention versus developments",
      title:
        Math.abs(gap) < 1
          ? "Media volume barely shifts the direction"
          : `Media volume shifts the signal ${
              gap > 0 ? "toward expansion" : "toward contraction"
            }`,
      text: `The latest Directional Amplification Gap is ${signed(
        gap
      )} points.`
    },
    {
      label: "Repeated coverage",
      title:
        ratio < 1.1
          ? "Most observed articles currently map to distinct developments"
          : "Repeated coverage materially exceeds unique-development volume",
      text: `The latest Coverage/Event ratio is ${ratio.toFixed(2)}.`
    },
    {
      label: "Narrative climate",
      title: `${readable(narrative)} is the largest article-weighted frame`,
      text:
        "Narrative framing is measured separately from substantive human empowerment."
    }
  ];
}

async function init() {
  updateCountdown();

  const [lenses, events] = await Promise.all([
    safeJSON(paths.lenses),
    safeJSON(paths.events)
  ]);

  if (!lenses?.global) {
    const period = document.getElementById("report-period");
    if (period) period.textContent = "Current evidence preview unavailable.";
    return;
  }

  const coverage = lenses.global.coverage;
  const event = lenses.global.event;
  const range = dateRange(events);
  const period = range
    ? `${shortDate(range.start)}–${shortDate(range.end)}`
    : "Latest weekly release";

  document.getElementById("report-period").textContent =
    `Current weekly evidence feeding the next monthly edition · ${period}`;
  document.getElementById("cover-coverage-count").textContent =
    Number(coverage.unit_count_ai_relevant || 0).toLocaleString("en-GB");
  document.getElementById("cover-event-count").textContent =
    Number(event.unit_count_ai_relevant || 0).toLocaleString("en-GB");
  document.getElementById("fact-window").textContent = period;
  document.getElementById("fact-coverage").textContent =
    Number(coverage.unit_count_ai_relevant || 0).toLocaleString("en-GB");
  document.getElementById("fact-events").textContent =
    Number(event.unit_count_ai_relevant || 0).toLocaleString("en-GB");

  document.getElementById("takeaway-grid").innerHTML =
    takeaways(lenses)
      .map(item => `
        <article>
          <span>${item.label}</span>
          <h3>${item.title}</h3>
          <p>${item.text}</p>
        </article>
      `)
      .join("");
}

init();
