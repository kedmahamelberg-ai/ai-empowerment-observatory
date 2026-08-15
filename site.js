"use strict";

async function fetchJSON(path) {
  const response =
    await fetch(
      path,
      { cache: "no-store" }
    );

  if (!response.ok) {
    throw new Error(
      `HTTP ${response.status}`
    );
  }

  return response.json();
}

function formatNumber(value) {
  return Number(
    value || 0
  ).toLocaleString("en-GB");
}

function signed(value) {
  if (
    value == null
    || Number.isNaN(Number(value))
  ) {
    return "—";
  }

  const number =
    Number(value);

  return (
    `${number > 0 ? "+" : ""}`
    + number.toFixed(2)
  );
}

function signalLabel(value) {
  const number =
    Number(value || 0);

  if (number <= -20) {
    return "more human constraint";
  }

  if (number < -5) {
    return "leaning toward constraint";
  }

  if (number <= 5) {
    return "near neutral";
  }

  if (number < 20) {
    return "leaning toward empowerment";
  }

  return "more human empowerment";
}

function dateRange(eventsPayload) {
  const values = [];

  for (
    const event
    of eventsPayload?.events || []
  ) {
    if (event.event_date) {
      values.push(
        new Date(
          event.event_date
        )
      );
    }

    for (
      const source
      of event.sources || []
    ) {
      if (source.published_at) {
        values.push(
          new Date(
            source.published_at
          )
        );
      }
    }
  }

  const valid =
    values
    .filter(
      date => !Number.isNaN(
        date.getTime()
      )
    )
    .sort(
      (a, b) => a - b
    );

  if (!valid.length) {
    return null;
  }

  return {
    start: valid[0],
    end: valid[
      valid.length - 1
    ]
  };
}

function formatDate(date) {
  return new Intl.DateTimeFormat(
    "en-GB",
    {
      day: "numeric",
      month: "short",
      year: "numeric"
    }
  ).format(date);
}

async function init() {
  const [
    lenses,
    events
  ] = await Promise.all([
    fetchJSON(
      "/data/lenses/latest.json"
    ),
    fetchJSON(
      "/data/events/latest.json"
    )
  ]);

  const coverage =
    lenses.global.coverage;

  const event =
    lenses.global.event;

  const amplification =
    lenses.global.amplification;

  const coverageCount =
    Number(
      coverage
      .unit_count_ai_relevant
      || 0
    );

  const eventCount =
    Number(
      event
      .unit_count_ai_relevant
      || 0
    );

  const extra =
    Math.max(
      0,
      coverageCount
      - eventCount
    );

  document.getElementById(
    "home-coverage-count"
  ).textContent =
    formatNumber(
      coverageCount
    );

  document.getElementById(
    "home-event-count"
  ).textContent =
    formatNumber(
      eventCount
    );

  const index =
    Number(
      event.empowerment_index
      || 0
    );

  document.getElementById(
    "home-summary-text"
  ).textContent =
    `${extra} extra article instance${extra === 1 ? "" : "s"} `
    + `of repeated coverage this week. The unique-development human-power `
    + `signal is ${signalLabel(index)} (${signed(index)}).`;

  const range =
    dateRange(events);

  if (range) {
    document.getElementById(
      "scope-strip"
    ).textContent =
      `${formatDate(range.start)}–${formatDate(range.end)} · `
      + "5 discovery markets · source-backed public release";
  }

  const releaseStatus =
    String(
      lenses.meta
      ?.release_status
      || ""
    );

  document.getElementById(
    "release-badge"
  ).textContent =
    releaseStatus
    .startsWith(
      "human_audited"
    )
      ? "Human-audited public release"
      : "Current public release";
}

init().catch(
  error => {
    console.error(error);

    document.getElementById(
      "release-badge"
    ).textContent =
      "AI Empowerment Observatory";
  }
);
