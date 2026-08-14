"use strict";

const paths = {
  lenses: "/data/lenses/latest.json",
  events: "/data/events/latest.json",
  status: "/data/status/latest.json",
  config: "/data/site-config.json",
  insights: "/data/insights/latest.json",
  history: "/data/history/releases.json"
};

const discoveryMarkets = {
  "United States": { coordinates: [-98.5, 39.5], code: "USA" },
  "China": { coordinates: [104.2, 35.9], code: "CHN" },
  "United Kingdom": { coordinates: [-2.5, 54.2], code: "GBR" },
  "France": { coordinates: [2.3, 46.4], code: "FRA" },
  "Canada": { coordinates: [-106.3, 56.1], code: "CAN" }
};

const topicLabels = {
  work_employment: "Work & jobs",
  business_productivity: "Business & productivity",
  consumer_services: "Consumer services",
  creativity_ip: "Creativity & IP",
  education_research: "Education & research",
  healthcare: "Healthcare",
  government_regulation: "Government & rules",
  privacy_security: "Privacy & security",
  infrastructure_investment: "Infrastructure & investment",
  other: "Other"
};

let state = {
  lenses: null,
  events: null,
  status: null,
  config: null,
  insights: null,
  history: null,
  breakdownLens: "event",
  trendLimit: 12
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

function formatNumber(value) {
  return Number(value || 0).toLocaleString("en-GB");
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
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

  return {
    start: dates[0],
    end: dates[dates.length - 1]
  };
}

function formatDate(date) {
  return new Intl.DateTimeFormat("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric"
  }).format(date);
}

function releaseText(lenses, status) {
  const value = String(
    lenses?.meta?.release_status
    || status?.release_status
    || "provisional_automated"
  );

  if (value.startsWith("human_audited")) {
    return "Live public release · Human-audited baseline";
  }

  if (value.includes("audited")) {
    return "Live public release · Audited";
  }

  return "Live public release · Provisional automated signal";
}

function gapInterpretation(gap, coverageCount, eventCount) {
  const extra = Math.max(0, coverageCount - eventCount);

  if (gap == null) {
    return "The current media amplification gap is unavailable.";
  }

  const number = Number(gap);

  if (extra <= 0) {
    return "This release contains essentially no repeated coverage after event resolution.";
  }

  if (Math.abs(number) < 1) {
    return (
      `${extra} extra article instance${extra === 1 ? "" : "s"} appear above the `
      + `unique-development count. Repetition is low this week, so the two `
      + `empowerment indices are almost identical.`
    );
  }

  if (number > 0) {
    return (
      `${extra} extra article instance${extra === 1 ? "" : "s"} are repeated `
      + `coverage. That repetition makes the news picture `
      + `${Math.abs(number).toFixed(2)} points more expansion-oriented.`
    );
  }

  return (
    `${extra} extra article instance${extra === 1 ? "" : "s"} are repeated `
    + `coverage. That repetition makes the news picture `
    + `${Math.abs(number).toFixed(2)} points more contraction-oriented.`
  );
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
            <span>News volume ${percent(c)}</span>
          </div>
          <div class="metric-bar event">
            <i style="width:${e * 100}%"></i>
            <span>Unique developments ${percent(e)}</span>
          </div>
        </div>
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
        evidence threshold is not yet met. This is a feature, not missing data:
        the Observatory does not turn search-market coverage into unsupported
        country scores.
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
        <span>${escapeHTML(row.country_iso3)}</span>
        <strong>Unique-development index ${signed(event.empowerment_index)}</strong>
        <small>
          News-volume index ${signed(coverage.empowerment_index)} ·
          Gap ${signed(amp.directional_amplification_gap)} ·
          ${event.unit_count_ai_relevant || 0} developments
        </small>
      </article>
    `;
  }).join("");
}

function renderEvents(container, eventsPayload) {
  const events = [...(eventsPayload?.events || [])]
    .sort((a, b) => {
      const countDifference =
        Number(b.article_count || 0)
        - Number(a.article_count || 0);

      if (countDifference) return countDifference;

      return String(b.event_date || "")
        .localeCompare(String(a.event_date || ""));
    })
    .slice(0, 10);

  if (!events.length) {
    container.innerHTML =
      '<div class="country-empty">No event evidence is available in the current public artifact.</div>';
    return;
  }

  container.innerHTML = events.map(event => {
    const links = (event.sources || [])
      .filter(source => source.url)
      .slice(0, 5)
      .map(source => `
        <a
          href="${escapeHTML(source.url)}"
          target="_blank"
          rel="noopener noreferrer"
        >
          ${escapeHTML(source.publisher || "Source")} ↗
        </a>
      `)
      .join("");

    return `
      <article class="event-card">
        <div class="meta">
          <span>${escapeHTML(event.event_date || "Date unavailable")}</span>
          <span>
            ${event.article_count || (event.sources || []).length || 1}
            source article(s)
          </span>
        </div>

        <h3>${escapeHTML(event.event_title || "Untitled event")}</h3>
        <div class="sources">${links || "Source links unavailable"}</div>
      </article>
    `;
  }).join("");
}

function renderNonEmpBreakdown() {
  const data = state.insights?.non_empowerment?.[state.breakdownLens];

  const container =
    document.getElementById("non-emp-topic-chart");

  if (!data || !Array.isArray(data.by_topic)) {
    container.innerHTML = `
      <div class="country-empty">
        Topic breakdown will appear after
        <strong>Generate Public Observatory Insights</strong> runs.
      </div>
    `;
    return;
  }

  const rows = data.by_topic.slice(0, 10);

  container.innerHTML = rows.map(row => `
    <div class="topic-row">
      <span>${escapeHTML(row.label || topicLabels[row.topic] || row.topic)}</span>
      <div class="topic-track">
        <i style="width:${Number(row.share || 0) * 100}%"></i>
      </div>
      <strong>${percent(row.share)}</strong>
    </div>
  `).join("");
}

function renderSources(filter = "") {
  const container = document.getElementById("source-list");
  const rows = state.insights?.sources?.rows || [];
  const term = String(filter || "").trim().toLowerCase();

  const visible = rows.filter(row =>
    !term
    || String(row.publisher || "").toLowerCase().includes(term)
  );

  if (!rows.length) {
    container.innerHTML = `
      <div class="country-empty">
        The current source inventory has not been generated yet.
        Run <strong>Generate Public Observatory Insights</strong>.
      </div>
    `;
    return;
  }

  container.innerHTML = `
    <div class="source-table-header">
      <span>Publication / organisation</span>
      <span>Articles</span>
      <span>Unique developments</span>
    </div>

    ${visible.map(row => `
      <div class="source-table-row">
        <strong>${escapeHTML(row.publisher)}</strong>
        <span>${formatNumber(row.article_count)}</span>
        <span>${formatNumber(row.unique_event_count)}</span>
      </div>
    `).join("")}
  `;
}

function linePath(points, key, x, y) {
  return points
    .map((point, index) =>
      `${index ? "L" : "M"} ${x(index).toFixed(1)} ${y(point[key]).toFixed(1)}`
    )
    .join(" ");
}

function renderTrendChart() {
  const container = document.getElementById("trend-chart");
  const note = document.getElementById("trend-note");

  const all = state.history?.points || [];

  if (!all.length) {
    container.innerHTML = `
      <div class="country-empty">
        Weekly history starts when
        <strong>Generate Public Observatory Insights</strong> runs.
      </div>
    `;
    note.textContent = "";
    return;
  }

  let points = all;

  if (state.trendLimit !== "all") {
    points = all.slice(-Number(state.trendLimit));
  }

  const width = 920;
  const height = 330;
  const left = 58;
  const right = 20;
  const top = 28;
  const bottom = 62;

  const maxValue = Math.max(
    1,
    ...points.flatMap(point => [
      Number(point.coverage_count || 0),
      Number(point.event_count || 0)
    ])
  );

  const x = index => {
    if (points.length === 1) return width / 2;
    return left + index * ((width - left - right) / (points.length - 1));
  };

  const y = value =>
    top
    + (height - top - bottom)
    * (1 - Number(value || 0) / (maxValue * 1.08));

  const grid = [0, .25, .5, .75, 1]
    .map(ratio => {
      const value = Math.round(maxValue * ratio);
      const yy = y(value);

      return `
        <line x1="${left}" y1="${yy}" x2="${width - right}" y2="${yy}" class="trend-grid-line"/>
        <text x="${left - 10}" y="${yy + 4}" text-anchor="end" class="trend-axis-text">${value}</text>
      `;
    })
    .join("");

  const coveragePath =
    points.length > 1
      ? `<path d="${linePath(points, "coverage_count", x, y)}" class="trend-line coverage-line"/>`
      : "";

  const eventPath =
    points.length > 1
      ? `<path d="${linePath(points, "event_count", x, y)}" class="trend-line event-line"/>`
      : "";

  const dots = points.map((point, index) => {
    const label = point.window_end || `Release ${index + 1}`;

    return `
      <circle
        cx="${x(index)}"
        cy="${y(point.coverage_count)}"
        r="5"
        class="trend-dot coverage-dot"
      >
        <title>${label}: ${point.coverage_count} news articles</title>
      </circle>

      <circle
        cx="${x(index)}"
        cy="${y(point.event_count)}"
        r="5"
        class="trend-dot event-dot"
      >
        <title>${label}: ${point.event_count} unique developments</title>
      </circle>

      <text
        x="${x(index)}"
        y="${height - 25}"
        text-anchor="middle"
        class="trend-axis-text"
      >
        ${escapeHTML(label.slice(5))}
      </text>
    `;
  }).join("");

  container.innerHTML = `
    <div class="trend-legend">
      <span><i class="coverage-swatch"></i> News volume</span>
      <span><i class="event-swatch"></i> Unique developments</span>
    </div>

    <svg
      viewBox="0 0 ${width} ${height}"
      role="img"
      aria-label="Weekly trend of news volume and unique AI developments"
    >
      ${grid}
      ${coveragePath}
      ${eventPath}
      ${dots}
    </svg>
  `;

  if (all.length === 1) {
    note.textContent =
      "One weekly release is available so far. The line will grow automatically with each future weekly release.";
  } else {
    const latest = all[all.length - 1];

    note.textContent =
      `History contains ${all.length} weekly releases. Latest difference: `
      + `${Math.max(0, Number(latest.coverage_count) - Number(latest.event_count))} `
      + `extra article instance(s) above the unique-development count.`;
  }
}

function initGlobe(markets, countryRows) {
  const element = document.getElementById("globe-map");

  if (!window.maplibregl) {
    element.innerHTML =
      '<div class="map-fallback">The interactive globe library could not load.</div>';
    return;
  }

  const map = new maplibregl.Map({
    container: "globe-map",
    style: "https://demotiles.maplibre.org/globe.json",
    center: [8, 24],
    zoom: 1.25,
    attributionControl: true
  });

  map.addControl(
    new maplibregl.NavigationControl({
      visualizePitch: true
    }),
    "bottom-right"
  );

  const features = markets
    .map(name => {
      const item = discoveryMarkets[name];
      if (!item) return null;

      return {
        type: "Feature",
        geometry: {
          type: "Point",
          coordinates: item.coordinates
        },
        properties: {
          name,
          type: "discovery_market"
        }
      };
    })
    .filter(Boolean);

  map.on("load", () => {
    map.addSource("discovery-markets", {
      type: "geojson",
      data: {
        type: "FeatureCollection",
        features
      }
    });

    map.addLayer({
      id: "discovery-market-rings",
      type: "circle",
      source: "discovery-markets",
      paint: {
        "circle-radius": 10,
        "circle-color": "rgba(255,255,255,0.15)",
        "circle-stroke-color": "#176f78",
        "circle-stroke-width": 3
      }
    });

    map.addLayer({
      id: "discovery-market-labels",
      type: "symbol",
      source: "discovery-markets",
      layout: {
        "text-field": ["get", "name"],
        "text-size": 12,
        "text-offset": [0, 1.5],
        "text-anchor": "top"
      },
      paint: {
        "text-color": "#0d223d",
        "text-halo-color": "#ffffff",
        "text-halo-width": 1.5
      }
    });
  });
}

async function init() {
  const [
    lenses,
    events,
    status,
    config,
    insights,
    history
  ] = await Promise.all([
    fetchJSON(paths.lenses),
    fetchJSON(paths.events),
    fetchJSON(paths.status).catch(() => null),
    fetchJSON(paths.config).catch(() => null),
    fetchJSON(paths.insights).catch(() => null),
    fetchJSON(paths.history).catch(() => null)
  ]);

  state = {
    ...state,
    lenses,
    events,
    status,
    config,
    insights,
    history
  };

  const coverage = lenses.global.coverage;
  const event = lenses.global.event;
  const amplification = lenses.global.amplification;

  const coverageCount = Number(
    coverage.unit_count_ai_relevant || 0
  );

  const eventCount = Number(
    event.unit_count_ai_relevant || 0
  );

  const extraCoverage = Math.max(
    0,
    coverageCount - eventCount
  );

  const maximum = Math.max(
    coverageCount,
    eventCount,
    1
  );

  const range = dateRange(events);

  const windowText =
    range
      ? `${formatDate(range.start)}–${formatDate(range.end)}`
      : "Current release";

  const markets =
    config?.search_markets
    || [
      "United States",
      "China",
      "United Kingdom",
      "France",
      "Canada"
    ];

  document.getElementById("release-label").textContent =
    releaseText(lenses, status);

  document.getElementById("data-window").textContent =
    windowText;

  document.getElementById("data-scope").textContent =
    `${markets.length} search markets · ${markets.join(", ")}`;

  for (const id of ["coverage-count", "coverage-count-2"]) {
    document.getElementById(id).textContent =
      formatNumber(coverageCount);
  }

  for (const id of ["event-count", "event-count-2"]) {
    document.getElementById(id).textContent =
      formatNumber(eventCount);
  }

  document.getElementById("extra-coverage-count").textContent =
    formatNumber(extraCoverage);

  document.getElementById("amplification-gap").textContent =
    signed(amplification.directional_amplification_gap);

  document.getElementById("coverage-count-bar").style.width =
    `${coverageCount / maximum * 100}%`;

  document.getElementById("event-count-bar").style.width =
    `${eventCount / maximum * 100}%`;

  document.getElementById("gap-interpretation").textContent =
    gapInterpretation(
      amplification.directional_amplification_gap,
      coverageCount,
      eventCount
    );

  renderGroupedChart(
    document.getElementById("narrative-chart"),
    coverage.narrative_distribution,
    event.narrative_distribution
  );

  const nonEmpShare =
    Number(event.status_distribution?.non_empowerment || 0);

  document.getElementById("non-emp-share").textContent =
    percent(nonEmpShare);

  document.getElementById("non-emp-count-note").textContent =
    `${Math.round(nonEmpShare * eventCount)} of ${eventCount} unique `
    + `developments are coded this way in the current release.`;

  document.getElementById("dim-operational").textContent =
    percent(event.dimension_distribution?.operational);

  document.getElementById("dim-creative").textContent =
    percent(event.dimension_distribution?.creative);

  document.getElementById("dim-agentic").textContent =
    percent(event.dimension_distribution?.agentic);

  document.getElementById("dim-normative").textContent =
    percent(event.dimension_distribution?.normative);

  document.getElementById("unique-source-count").textContent =
    formatNumber(insights?.sources?.unique_publishers || 0);

  renderNonEmpBreakdown();
  renderSources();
  renderTrendChart();

  renderCountries(
    document.getElementById("country-signals"),
    lenses.countries
  );

  renderEvents(
    document.getElementById("event-list"),
    events
  );

  initGlobe(
    markets,
    lenses.countries
  );

  document
    .querySelectorAll("[data-breakdown-lens]")
    .forEach(button => {
      button.addEventListener("click", () => {
        state.breakdownLens =
          button.dataset.breakdownLens;

        document
          .querySelectorAll("[data-breakdown-lens]")
          .forEach(item =>
            item.classList.toggle(
              "active",
              item === button
            )
          );

        renderNonEmpBreakdown();
      });
    });

  document
    .querySelectorAll("[data-trend-limit]")
    .forEach(button => {
      button.addEventListener("click", () => {
        state.trendLimit =
          button.dataset.trendLimit === "all"
            ? "all"
            : Number(button.dataset.trendLimit);

        document
          .querySelectorAll("[data-trend-limit]")
          .forEach(item =>
            item.classList.toggle(
              "active",
              item === button
            )
          );

        renderTrendChart();
      });
    });

  document
    .getElementById("source-filter")
    .addEventListener(
      "input",
      event => renderSources(event.target.value)
    );
}

init().catch(error => {
  console.error(error);

  document.getElementById("release-label").textContent =
    "Current data could not be loaded";

  document.getElementById("data-scope").textContent =
    "Please check the latest public data artifacts.";
});
