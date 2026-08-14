"use strict";

const paths = {
  lenses: "/data/lenses/latest.json",
  events: "/data/events/latest.json",
  status: "/data/status/latest.json",
  config: "/data/site-config.json"
};

async function fetchJSON(path) {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

function signed(value, digits = 2) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
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

function formatDate(date) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric"
  }).format(date);
}

function releaseText(lenses, status) {
  const value = String(lenses?.meta?.release_status || status?.release_status || "provisional_automated");
  if (value.startsWith("human_audited")) return "Live public release · Human-audited baseline";
  if (value.includes("audited")) return "Live public release · Audited";
  return "Live public release · Provisional automated signal";
}

function gapInterpretation(gap) {
  if (gap == null) return "The current amplification gap is unavailable.";
  const number = Number(gap);
  if (Math.abs(number) < 1) {
    return "Coverage and Event Lens are directionally very similar in this release. Article repetition changes the aggregate picture only slightly.";
  }
  if (number > 0) {
    return `Coverage is ${Math.abs(number).toFixed(2)} points more expansion-oriented than the unique-event mix. Media repetition is amplifying the expansion side of the signal.`;
  }
  return `Coverage is ${Math.abs(number).toFixed(2)} points more contraction-oriented than the unique-event mix. Media repetition is amplifying the contraction side of the signal.`;
}

function renderGroupedChart(container, coverage, event) {
  const order = [
    ["opportunity", "Opportunity"],
    ["threat", "Threat"],
    ["contested", "Contested"],
    ["descriptive_neutral", "Descriptive / neutral"],
    ["unclear", "Unclear"]
  ];

  container.innerHTML = order.map(([key, label]) => {
    const c = Number(coverage?.[key] || 0);
    const e = Number(event?.[key] || 0);
    return `
      <div class="chart-row">
        <strong>${label}</strong>
        <div class="bar-pair">
          <div class="metric-bar coverage">
            <i style="width:${c * 100}%"></i>
            <span>Coverage ${percent(c)}</span>
          </div>
          <div class="metric-bar event">
            <i style="width:${e * 100}%"></i>
            <span>Event ${percent(e)}</span>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

function renderStack(container, values, labels) {
  container.innerHTML = labels.map(([key, label]) => {
    const value = Number(values?.[key] || 0);
    return `
      <div class="stack-item">
        <strong>${label}</strong>
        <div class="stack-track"><div class="stack-fill" style="width:${value * 100}%"></div></div>
        <span>${percent(value)}</span>
      </div>
    `;
  }).join("");
}

function renderCountries(container, rows) {
  const supported = (rows || []).filter(row =>
    row?.coverage?.signal_ready || row?.event?.signal_ready
  );

  if (!supported.length) {
    container.innerHTML = `
      <div class="country-empty">
        No country signal is published in this release because the minimum
        evidence threshold is not yet met. The global signal remains available.
      </div>
    `;
    return;
  }

  container.innerHTML = supported.map(row => {
    const event = row.event || {};
    const coverage = row.coverage || {};
    const amp = row.amplification || {};
    return `
      <article class="country-card">
        <span>${row.country_iso3}</span>
        <strong>Event ${signed(event.empowerment_index)}</strong>
        <small>
          Coverage ${signed(coverage.empowerment_index)} · Gap ${signed(amp.directional_amplification_gap)} ·
          ${event.unit_count_ai_relevant || 0} events
        </small>
      </article>
    `;
  }).join("");
}

function renderEvents(container, eventsPayload) {
  const events = [...(eventsPayload?.events || [])]
    .sort((a, b) => {
      const countDifference = Number(b.article_count || 0) - Number(a.article_count || 0);
      if (countDifference) return countDifference;
      return String(b.event_date || "").localeCompare(String(a.event_date || ""));
    })
    .slice(0, 8);

  if (!events.length) {
    container.innerHTML = '<div class="country-empty">No event evidence is available in the current public artifact.</div>';
    return;
  }

  container.innerHTML = events.map(event => {
    const links = (event.sources || [])
      .filter(source => source.url)
      .slice(0, 4)
      .map(source => `<a href="${source.url}" target="_blank" rel="noopener noreferrer">${source.publisher || "Source"} ↗</a>`)
      .join("");

    return `
      <article class="event-card">
        <div class="meta">
          <span>${event.event_date || "Date unavailable"}</span>
          <span>${event.article_count || (event.sources || []).length || 1} source article(s)</span>
        </div>
        <h3>${event.event_title || "Untitled event"}</h3>
        <div class="sources">${links || "Source links unavailable"}</div>
      </article>
    `;
  }).join("");
}

async function init() {
  const [lenses, events, status, config] = await Promise.all([
    fetchJSON(paths.lenses),
    fetchJSON(paths.events),
    fetchJSON(paths.status).catch(() => null),
    fetchJSON(paths.config).catch(() => null)
  ]);

  const coverage = lenses.global.coverage;
  const event = lenses.global.event;
  const amplification = lenses.global.amplification;
  const coverageCount = Number(coverage.unit_count_ai_relevant || 0);
  const eventCount = Number(event.unit_count_ai_relevant || 0);
  const maximum = Math.max(coverageCount, eventCount, 1);
  const range = dateRange(events);
  const windowText = range ? `${formatDate(range.start)}–${formatDate(range.end)}` : "Current release";
  const markets = config?.search_markets || ["United States", "China", "United Kingdom", "France", "Canada"];

  document.getElementById("release-label").textContent = releaseText(lenses, status);
  document.getElementById("data-window").textContent = windowText;
  document.getElementById("data-scope").textContent = `${markets.length} search markets: ${markets.join(", ")}. Search market does not equal event country.`;

  document.getElementById("coverage-index").textContent = signed(coverage.empowerment_index);
  document.getElementById("event-index").textContent = signed(event.empowerment_index);
  document.getElementById("amplification-gap").textContent = signed(amplification.directional_amplification_gap);
  document.getElementById("coverage-event-ratio").textContent = Number(amplification.coverage_event_ratio || 0).toFixed(2);
  document.getElementById("coverage-count").textContent = coverageCount.toLocaleString("en-GB");
  document.getElementById("event-count").textContent = eventCount.toLocaleString("en-GB");
  document.getElementById("coverage-count-bar").style.width = `${coverageCount / maximum * 100}%`;
  document.getElementById("event-count-bar").style.width = `${eventCount / maximum * 100}%`;
  document.getElementById("gap-interpretation").textContent = gapInterpretation(amplification.directional_amplification_gap);

  renderGroupedChart(
    document.getElementById("narrative-chart"),
    coverage.narrative_distribution,
    event.narrative_distribution
  );

  renderStack(
    document.getElementById("status-chart"),
    event.status_distribution,
    [
      ["expanding", "Expanding"],
      ["contracting", "Contracting"],
      ["mixed", "Mixed"],
      ["non_empowerment", "Non-empowerment"],
      ["unclear", "Unclear"]
    ]
  );

  renderStack(
    document.getElementById("dimension-chart"),
    event.dimension_distribution,
    [
      ["operational", "Operational"],
      ["creative", "Creative"],
      ["agentic", "Agentic"],
      ["normative", "Normative"]
    ]
  );

  renderCountries(document.getElementById("country-signals"), lenses.countries);
  renderEvents(document.getElementById("event-list"), events);
}

init().catch(error => {
  console.error(error);
  document.getElementById("release-label").textContent = "Current data could not be loaded";
  document.getElementById("data-scope").textContent = "Please check the latest public data artifacts.";
});
