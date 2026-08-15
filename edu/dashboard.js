"use strict";

const paths = {
  lenses: "/data/lenses/latest.json",
  events: "/data/events/latest.json",
  status: "/data/status/latest.json",
  config: "/data/site-config.json",
  insights: "/data/insights/latest.json",
  history: "/data/history/releases.json"
};

const marketCoordinates = {
  USA: [-98.5, 39.5],
  CHN: [104.2, 35.9],
  GBR: [-2.5, 54.2],
  FRA: [2.3, 46.4],
  CAN: [-106.3, 56.1]
};

let state = {
  lenses: null,
  events: null,
  insights: null,
  history: null,
  config: null,
  status: null,
  themeLens: "event",
  nonEmpLens: "event",
  trendLimit: 12,
  showAllEvents: false,
  map: null
};

async function fetchJSON(path) {
  const response = await fetch(
    path,
    { cache: "no-store" }
  );

  if (!response.ok) {
    throw new Error(
      `${path}: HTTP ${response.status}`
    );
  }

  return response.json();
}

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function signed(value, digits = 2) {
  if (
    value == null
    || Number.isNaN(Number(value))
  ) {
    return "—";
  }

  const number = Number(value);

  return (
    `${number > 0 ? "+" : ""}`
    + number.toFixed(digits)
  );
}

function percent(value) {
  return (
    Number(value || 0)
    * 100
  ).toFixed(1) + "%";
}

function formatNumber(value) {
  return Number(
    value || 0
  ).toLocaleString("en-GB");
}

function dateRange(eventsPayload) {
  const values = [];

  for (
    const event
    of eventsPayload?.events || []
  ) {
    if (event.event_date) {
      values.push(event.event_date);
    }

    for (
      const source
      of event.sources || []
    ) {
      if (source.published_at) {
        values.push(
          source.published_at
        );
      }
    }
  }

  const dates = values
    .map(value => new Date(value))
    .filter(
      date => !Number.isNaN(
        date.getTime()
      )
    )
    .sort(
      (a, b) => a - b
    );

  if (!dates.length) {
    return null;
  }

  return {
    start: dates[0],
    end: dates[
      dates.length - 1
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

function releaseLabel() {
  const value = String(
    state.lenses?.meta?.release_status
    || state.status?.release_status
    || "provisional_automated"
  );

  if (
    value.startsWith(
      "human_audited"
    )
  ) {
    return "Human-audited public release";
  }

  if (
    value.includes("audited")
  ) {
    return "Audited public release";
  }

  return "Provisional public release";
}

function signalLabel(value) {
  const number = Number(value || 0);

  if (number <= -20) {
    return "More human constraint";
  }

  if (number < -5) {
    return "Leaning toward constraint";
  }

  if (number <= 5) {
    return "Near neutral";
  }

  if (number < 20) {
    return "Leaning toward empowerment";
  }

  return "More human empowerment";
}

function repetitionText(
  coverageCount,
  eventCount
) {
  const extra = Math.max(
    0,
    coverageCount - eventCount
  );

  if (!extra) {
    return (
      "Almost every article represented "
      + "a different development this week."
    );
  }

  if (extra <= 5) {
    return (
      `${extra} article${extra === 1 ? "" : "s"} `
      + "were additional coverage of developments "
      + "already counted. Repetition was low."
    );
  }

  const share = (
    extra / coverageCount
  ) * 100;

  return (
    `${extra} articles (${share.toFixed(1)}%) `
    + "were additional coverage of developments "
    + "already counted."
  );
}

function oneSentenceSummary(
  coverageCount,
  eventCount,
  eventIndex,
  gap
) {
  const extra = Math.max(
    0,
    coverageCount - eventCount
  );

  const direction = signalLabel(
    eventIndex
  ).toLowerCase();

  const gapText =
    Math.abs(Number(gap || 0)) < 1
      ? "Repeated coverage barely changed that picture."
      : (
        Number(gap) > 0
          ? "Repeated coverage made the news picture more empowerment-oriented."
          : "Repeated coverage made the news picture more constraint-oriented."
      );

  return (
    `${coverageCount} AI news articles resolved to `
    + `${eventCount} unique developments`
    + `${extra ? `, with ${extra} extra article instance${extra === 1 ? "" : "s"} of repeated coverage` : ""}. `
    + `The overall human-power signal was ${direction}. `
    + gapText
  );
}

function openDialog(html) {
  const dialog =
    document.getElementById(
      "info-dialog"
    );

  document.getElementById(
    "dialog-content"
  ).innerHTML = html;

  dialog.showModal();
}

function closeDialog() {
  document.getElementById(
    "info-dialog"
  ).close();
}

function renderStatusChart() {
  const event =
    state.lenses.global.event;

  const distribution =
    event.status_distribution || {};

  const rows = [
    [
      "expanding",
      "Expansion",
      "People gain capability or control"
    ],
    [
      "contracting",
      "Contraction",
      "People lose capability or control"
    ],
    [
      "mixed",
      "Mixed",
      "Expansion and contraction coexist"
    ],
    [
      "non_empowerment",
      "No direct shift identified",
      "No direct human-power change established"
    ]
  ];

  const container =
    document.getElementById(
      "impact-status-chart"
    );

  container.innerHTML =
    rows.map(
      ([key, label, note]) => {
        const value = Number(
          distribution[key]
          || 0
        );

        return `
          <div class="status-row">
            <div>
              <strong>${label}</strong>
              <small>${note}</small>
            </div>

            <div class="status-track">
              <i
                class="${key}"
                style="width:${value * 100}%"
              ></i>
            </div>

            <span>${percent(value)}</span>
          </div>
        `;
      }
    ).join("");
}

function renderNarrative() {
  const coverage =
    state.lenses.global.coverage
    .narrative_distribution
    || {};

  const event =
    state.lenses.global.event
    .narrative_distribution
    || {};

  const rows = [
    ["opportunity", "Opportunity"],
    ["threat", "Threat"],
    ["contested", "Contested"],
    [
      "descriptive_neutral",
      "Mostly descriptive"
    ]
  ];

  document.getElementById(
    "narrative-chart"
  ).innerHTML =
    rows.map(
      ([key, label]) => {
        const c = Number(
          coverage[key] || 0
        );

        const e = Number(
          event[key] || 0
        );

        return `
          <div class="narrative-row">
            <strong>${label}</strong>

            <div class="narrative-bars">
              <div>
                <span>
                  News volume
                  <b>${percent(c)}</b>
                </span>
                <div class="small-track">
                  <i
                    class="coverage"
                    style="width:${c * 100}%"
                  ></i>
                </div>
              </div>

              <div>
                <span>
                  Unique developments
                  <b>${percent(e)}</b>
                </span>
                <div class="small-track">
                  <i
                    class="event"
                    style="width:${e * 100}%"
                  ></i>
                </div>
              </div>
            </div>
          </div>
        `;
      }
    ).join("");

  const gap =
    state.lenses.global
    .amplification
    .directional_amplification_gap;

  document.getElementById(
    "tone-gap-detail"
  ).textContent =
    Math.abs(Number(gap || 0)) < 1
      ? (
        "The article-weighted and unique-development "
        + "signals are almost identical this week. "
        + "Repeated coverage did not materially change "
        + "the overall direction."
      )
      : (
        `The current amplification gap is ${signed(gap)}. `
        + "This means repeated coverage changes the "
        + "direction of the article-weighted signal."
      );
}

function renderThemeChart(
  containerId,
  rows
) {
  const container =
    document.getElementById(
      containerId
    );

  if (
    !Array.isArray(rows)
    || !rows.length
  ) {
    container.innerHTML =
      '<p class="empty-state">Theme data are not available yet.</p>';
    return;
  }

  container.innerHTML =
    rows.slice(0, 9).map(
      row => `
        <div class="theme-row">
          <span>${escapeHTML(row.label)}</span>

          <div class="theme-track">
            <i
              style="width:${Number(row.share || 0) * 100}%"
            ></i>
          </div>

          <strong>${percent(row.share)}</strong>
        </div>
      `
    ).join("");
}

function renderThemes() {
  renderThemeChart(
    "theme-chart",
    state.insights?.[
      state.themeLens
    ]?.themes
  );
}

function renderNonEmpThemes() {
  renderThemeChart(
    "non-emp-theme-chart",
    state.insights
      ?.non_empowerment
      ?.[state.nonEmpLens]
      ?.themes
  );
}

function linePath(
  points,
  key,
  x,
  y
) {
  return points
    .map(
      (point, index) =>
        `${index ? "L" : "M"} `
        + `${x(index).toFixed(1)} `
        + `${y(point[key]).toFixed(1)}`
    )
    .join(" ");
}

function renderTrend() {
  const container =
    document.getElementById(
      "trend-chart"
    );

  const note =
    document.getElementById(
      "trend-note"
    );

  const all =
    state.history?.points
    || [];

  if (!all.length) {
    container.innerHTML =
      '<div class="empty-state">Weekly history begins with the first generated public release.</div>';

    note.textContent = "";
    return;
  }

  let points = all;

  if (
    state.trendLimit !== "all"
  ) {
    points = all.slice(
      -Number(
        state.trendLimit
      )
    );
  }

  const width = 980;
  const height = 320;
  const left = 55;
  const right = 22;
  const top = 28;
  const bottom = 52;

  const maxValue = Math.max(
    1,
    ...points.flatMap(
      point => [
        Number(
          point.coverage_count || 0
        ),
        Number(
          point.event_count || 0
        )
      ]
    )
  );

  const x = index => (
    points.length === 1
      ? width / 2
      : (
        left
        + index
        * (
          (width - left - right)
          / (points.length - 1)
        )
      )
  );

  const y = value => (
    top
    + (
      height - top - bottom
    )
    * (
      1
      - Number(value || 0)
      / (maxValue * 1.08)
    )
  );

  const grids = [
    0,
    .25,
    .5,
    .75,
    1
  ].map(
    ratio => {
      const value =
        Math.round(
          maxValue * ratio
        );

      const yy = y(value);

      return `
        <line
          x1="${left}"
          y1="${yy}"
          x2="${width - right}"
          y2="${yy}"
          class="trend-grid-line"
        />

        <text
          x="${left - 9}"
          y="${yy + 4}"
          text-anchor="end"
          class="trend-axis-text"
        >
          ${value}
        </text>
      `;
    }
  ).join("");

  const coveragePath =
    points.length > 1
      ? `
        <path
          d="${linePath(
            points,
            "coverage_count",
            x,
            y
          )}"
          class="trend-line coverage-line"
        />
      `
      : "";

  const eventPath =
    points.length > 1
      ? `
        <path
          d="${linePath(
            points,
            "event_count",
            x,
            y
          )}"
          class="trend-line event-line"
        />
      `
      : "";

  const dots = points.map(
    (point, index) => {
      const label =
        point.window_end
        || `Release ${index + 1}`;

      const shortLabel =
        label.length >= 10
          ? label.slice(5)
          : label;

      return `
        <circle
          cx="${x(index)}"
          cy="${y(point.coverage_count)}"
          r="5"
          class="trend-dot coverage-dot"
        >
          <title>
            ${label}: ${point.coverage_count} news articles
          </title>
        </circle>

        <circle
          cx="${x(index)}"
          cy="${y(point.event_count)}"
          r="5"
          class="trend-dot event-dot"
        >
          <title>
            ${label}: ${point.event_count} unique developments
          </title>
        </circle>

        <text
          x="${x(index)}"
          y="${height - 18}"
          text-anchor="middle"
          class="trend-axis-text"
        >
          ${escapeHTML(shortLabel)}
        </text>
      `;
    }
  ).join("");

  container.innerHTML = `
    <div class="trend-legend">
      <span>
        <i class="coverage-swatch"></i>
        News volume
      </span>

      <span>
        <i class="event-swatch"></i>
        Unique developments
      </span>
    </div>

    <svg
      viewBox="0 0 ${width} ${height}"
      role="img"
      aria-label="Weekly trend of AI news volume and unique developments"
    >
      ${grids}
      ${coveragePath}
      ${eventPath}
      ${dots}
    </svg>
  `;

  if (all.length === 1) {
    note.textContent =
      "History starts here. A second point will appear automatically after the next weekly release.";
  } else {
    const latest =
      all[all.length - 1];

    const extra = Math.max(
      0,
      Number(
        latest.coverage_count
      )
      - Number(
        latest.event_count
      )
    );

    note.textContent =
      `${all.length} weekly releases are stored. `
      + `Latest repeated coverage: ${extra} extra article instance${extra === 1 ? "" : "s"}.`;
  }
}

function eventListData() {
  return [
    ...(state.events?.events || [])
  ].sort(
    (a, b) => {
      const countDiff =
        Number(b.article_count || 0)
        - Number(a.article_count || 0);

      if (countDiff) {
        return countDiff;
      }

      return String(
        b.event_date || ""
      ).localeCompare(
        String(
          a.event_date || ""
        )
      );
    }
  );
}

function renderEvents() {
  const all =
    eventListData();

  const limit =
    state.showAllEvents
      ? Math.min(20, all.length)
      : 6;

  const events =
    all.slice(0, limit);

  const container =
    document.getElementById(
      "event-list"
    );

  if (!events.length) {
    container.innerHTML =
      '<p class="empty-state">No public event evidence is available.</p>';
    return;
  }

  container.innerHTML =
    events.map(
      event => {
        const sources =
          (event.sources || [])
          .filter(
            source => source.url
          )
          .slice(0, 4)
          .map(
            source => `
              <a
                href="${escapeHTML(source.url)}"
                target="_blank"
                rel="noopener noreferrer"
              >
                ${escapeHTML(source.publisher || "Source")} ↗
              </a>
            `
          )
          .join("");

        return `
          <article class="event-card">
            <div class="event-meta">
              <span>${escapeHTML(event.event_date || "Date unavailable")}</span>
              <span>
                ${event.article_count || (event.sources || []).length || 1}
                source article(s)
              </span>
            </div>

            <h3>
              ${escapeHTML(event.event_title || "Untitled development")}
            </h3>

            <div class="event-sources">
              ${sources || "Source links unavailable"}
            </div>
          </article>
        `;
      }
    ).join("");

  const toggle =
    document.getElementById(
      "toggle-events"
    );

  toggle.hidden =
    all.length <= 6;

  toggle.textContent =
    state.showAllEvents
      ? "Show fewer developments"
      : `Show more developments (${Math.min(20, all.length)})`;
}

function sourceRows() {
  return (
    state.insights
    ?.sources
    ?.rows
    || []
  );
}

function renderTopSources() {
  const rows =
    sourceRows();

  const container =
    document.getElementById(
      "top-source-list"
    );

  if (!rows.length) {
    container.innerHTML =
      '<p class="empty-state">The source inventory is not available yet.</p>';
    return;
  }

  container.innerHTML =
    rows.slice(0, 8).map(
      row => `
        <article>
          <strong>${escapeHTML(row.publisher)}</strong>
          <span>
            ${row.article_count}
            article${row.article_count === 1 ? "" : "s"}
            ·
            ${row.unique_event_count}
            unique development${row.unique_event_count === 1 ? "" : "s"}
          </span>
        </article>
      `
    ).join("");
}

function renderAllSources(filter = "") {
  const rows = sourceRows();

  const term =
    String(filter || "")
    .trim()
    .casefold?.()
    || String(filter || "")
      .trim()
      .toLowerCase();

  const visible =
    rows.filter(
      row =>
        !term
        || String(
          row.publisher || ""
        )
        .toLowerCase()
        .includes(term)
    );

  const container =
    document.getElementById(
      "source-list"
    );

  container.innerHTML = `
    <div class="source-table-header">
      <span>Source</span>
      <span>Articles</span>
      <span>Unique developments</span>
    </div>

    ${visible.map(
      row => `
        <div class="source-table-row">
          <strong>${escapeHTML(row.publisher)}</strong>
          <span>${row.article_count}</span>
          <span>${row.unique_event_count}</span>
        </div>
      `
    ).join("")}
  `;
}

function marketLookup() {
  const rows =
    state.insights
    ?.discovery_markets
    || [];

  return Object.fromEntries(
    rows.map(
      row => [
        row.country_iso3,
        row
      ]
    )
  );
}

function renderMarketPanel(iso3) {
  const market =
    marketLookup()[iso3];

  if (!market) {
    return;
  }

  const topSources =
    (market.top_publishers || [])
    .map(
      row => `
        <li>
          ${escapeHTML(row.publisher)}
          <span>${row.article_count}</span>
        </li>
      `
    )
    .join("");

  document.getElementById(
    "market-panel"
  ).innerHTML = `
    <span class="panel-kicker">
      Discovery market
    </span>

    <h3>${escapeHTML(market.name)}</h3>

    <div class="market-stats">
      <article>
        <strong>${market.article_count}</strong>
        <span>AI-relevant articles observed</span>
      </article>

      <article>
        <strong>${market.unique_publishers}</strong>
        <span>publications / organisations</span>
      </article>
    </div>

    ${
      market.languages?.length
        ? `
          <p class="market-languages">
            Search language:
            <strong>${escapeHTML(market.languages.join(", "))}</strong>
          </p>
        `
        : ""
    }

    <h4>Most visible sources</h4>

    <ol class="market-source-list">
      ${topSources || "<li>No source breakdown available.</li>"}
    </ol>

    <details>
      <summary>What does this mean?</summary>
      <p>
        These are discovery statistics, not a country empowerment score.
        The same article can appear in more than one search market.
      </p>
    </details>
  `;
}

function initGlobe() {
  const element =
    document.getElementById(
      "globe-map"
    );

  if (!window.maplibregl) {
    element.innerHTML =
      '<p class="empty-state">The interactive globe could not load.</p>';
    return;
  }

  const markets =
    state.insights
    ?.discovery_markets
    || [];

  const features =
    markets
    .map(
      market => {
        const coordinates =
          marketCoordinates[
            market.country_iso3
          ];

        if (!coordinates) {
          return null;
        }

        return {
          type: "Feature",
          geometry: {
            type: "Point",
            coordinates
          },
          properties: {
            iso3:
              market.country_iso3,
            name:
              market.name
          }
        };
      }
    )
    .filter(Boolean);

  const map =
    new maplibregl.Map({
      container: "globe-map",
      style:
        "https://demotiles.maplibre.org/globe.json",
      center: [8, 25],
      zoom: 1.1,
      attributionControl: true
    });

  state.map = map;

  map.addControl(
    new maplibregl.NavigationControl({
      visualizePitch: true
    }),
    "bottom-right"
  );

  map.on("load", () => {
    map.addSource(
      "discovery-markets",
      {
        type: "geojson",
        data: {
          type:
            "FeatureCollection",
          features
        }
      }
    );

    map.addLayer({
      id:
        "discovery-market-rings",
      type: "circle",
      source:
        "discovery-markets",
      paint: {
        "circle-radius": 10,
        "circle-color":
          "rgba(255,255,255,.2)",
        "circle-stroke-color":
          "#176f78",
        "circle-stroke-width": 3
      }
    });

    map.addLayer({
      id:
        "discovery-market-labels",
      type: "symbol",
      source:
        "discovery-markets",
      layout: {
        "text-field":
          ["get", "name"],
        "text-size": 12,
        "text-offset": [0, 1.5],
        "text-anchor": "top"
      },
      paint: {
        "text-color": "#0d223d",
        "text-halo-color": "#fff",
        "text-halo-width": 1.5
      }
    });

    map.on(
      "mouseenter",
      "discovery-market-rings",
      () => {
        map.getCanvas().style.cursor =
          "pointer";
      }
    );

    map.on(
      "mouseleave",
      "discovery-market-rings",
      () => {
        map.getCanvas().style.cursor =
          "";
      }
    );

    map.on(
      "click",
      "discovery-market-rings",
      event => {
        const feature =
          event.features?.[0];

        if (!feature) {
          return;
        }

        const coordinates =
          feature.geometry.coordinates;

        const iso3 =
          feature.properties.iso3;

        map.easeTo({
          center: coordinates,
          zoom: 3.1,
          duration: 900
        });

        renderMarketPanel(
          iso3
        );
      }
    );
  });
}

function renderCountrySignalNote() {
  const supported =
    (
      state.lenses
      ?.countries
      || []
    )
    .filter(
      row =>
        row?.coverage?.signal_ready
        || row?.event?.signal_ready
    );

  const container =
    document.getElementById(
      "country-signal-note"
    );

  if (!supported.length) {
    container.innerHTML = `
      <p>
        No country empowerment score is published this week because the
        evidence threshold is not yet met. AIEO keeps discovery-market
        statistics separate from country-level human-impact claims.
      </p>
    `;
    return;
  }

  container.innerHTML = `
    <div class="country-ready-list">
      ${supported.map(
        row => `
          <article>
            <strong>${escapeHTML(row.country_iso3)}</strong>
            <span>
              Unique-development index
              ${signed(row.event?.empowerment_index)}
            </span>
          </article>
        `
      ).join("")}
    </div>
  `;
}

function setExploreTab(tab) {
  document
    .querySelectorAll(
      "[data-explore-tab]"
    )
    .forEach(
      button => {
        const active =
          button.dataset.exploreTab
          === tab;

        button.classList.toggle(
          "active",
          active
        );

        button.setAttribute(
          "aria-selected",
          String(active)
        );
      }
    );

  document
    .querySelectorAll(
      "[data-panel]"
    )
    .forEach(
      panel => {
        const active =
          panel.dataset.panel
          === tab;

        panel.classList.toggle(
          "active",
          active
        );

        panel.hidden =
          !active;
      }
    );

  if (
    tab === "globe"
    && state.map
  ) {
    setTimeout(
      () => state.map.resize(),
      50
    );
  }
}

async function init() {
  const [
    lenses,
    events,
    insights,
    history,
    config,
    status
  ] = await Promise.all([
    fetchJSON(paths.lenses),
    fetchJSON(paths.events),
    fetchJSON(paths.insights)
      .catch(() => null),
    fetchJSON(paths.history)
      .catch(() => null),
    fetchJSON(paths.config)
      .catch(() => null),
    fetchJSON(paths.status)
      .catch(() => null)
  ]);

  state = {
    ...state,
    lenses,
    events,
    insights,
    history,
    config,
    status
  };

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

  const eventIndex =
    Number(
      event.empowerment_index
      || 0
    );

  const gap =
    Number(
      amplification
      .directional_amplification_gap
      || 0
    );

  const range =
    dateRange(events);

  document.getElementById(
    "release-label"
  ).textContent =
    releaseLabel();

  document.getElementById(
    "data-window"
  ).textContent =
    range
      ? (
        `${formatDate(range.start)}–`
        + formatDate(range.end)
      )
      : "Current release";

  const marketCount =
    insights
    ?.discovery_markets
    ?.length
    || 5;

  document.getElementById(
    "market-count"
  ).textContent =
    `${marketCount} discovery markets`;

  document.getElementById(
    "coverage-count"
  ).textContent =
    formatNumber(coverageCount);

  document.getElementById(
    "event-count"
  ).textContent =
    formatNumber(eventCount);

  document.getElementById(
    "repetition-summary"
  ).textContent =
    repetitionText(
      coverageCount,
      eventCount
    );

  document.getElementById(
    "signal-label"
  ).textContent =
    signalLabel(
      eventIndex
    );

  document.getElementById(
    "signal-score"
  ).textContent =
    `Event Index ${signed(eventIndex)} on a −100 to +100 scale`;

  document.getElementById(
    "one-sentence-summary"
  ).textContent =
    oneSentenceSummary(
      coverageCount,
      eventCount,
      eventIndex,
      gap
    );

  const directShare =
    Number(
      event.status_distribution
      ?.expanding
      || 0
    )
    + Number(
      event.status_distribution
      ?.contracting
      || 0
    )
    + Number(
      event.status_distribution
      ?.mixed
      || 0
    );

  const nonEmpShare =
    Number(
      event.status_distribution
      ?.non_empowerment
      || 0
    );

  document.getElementById(
    "clear-shift-share"
  ).textContent =
    percent(directShare);

  document.getElementById(
    "non-emp-share"
  ).textContent =
    percent(nonEmpShare);

  document.getElementById(
    "dim-operational"
  ).textContent =
    percent(
      event.dimension_distribution
      ?.operational
    );

  document.getElementById(
    "dim-creative"
  ).textContent =
    percent(
      event.dimension_distribution
      ?.creative
    );

  document.getElementById(
    "dim-agentic"
  ).textContent =
    percent(
      event.dimension_distribution
      ?.agentic
    );

  document.getElementById(
    "dim-normative"
  ).textContent =
    percent(
      event.dimension_distribution
      ?.normative
    );

  renderStatusChart();
  renderNarrative();
  renderThemes();
  renderNonEmpThemes();
  renderTrend();
  renderEvents();
  renderTopSources();
  renderAllSources();
  renderCountrySignalNote();

  const sourceCount =
    insights
    ?.sources
    ?.unique_publishers
    || 0;

  document.getElementById(
    "source-summary-text"
  ).textContent =
    sourceCount
      ? (
        `${sourceCount} publications and organisations appeared `
        + "in this release. The eight most visible are shown first."
      )
      : (
        "The source inventory will appear after public insights are generated."
      );

  document.getElementById(
    "all-sources-summary"
  ).textContent =
    sourceCount
      ? `Search all ${sourceCount} sources`
      : "Search all sources";

  initGlobe();

  document
    .querySelectorAll(
      "[data-explore-tab]"
    )
    .forEach(
      button => {
        button.addEventListener(
          "click",
          () => setExploreTab(
            button.dataset.exploreTab
          )
        );
      }
    );

  document
    .querySelectorAll(
      "[data-theme-lens]"
    )
    .forEach(
      button => {
        button.addEventListener(
          "click",
          () => {
            state.themeLens =
              button.dataset.themeLens;

            document
              .querySelectorAll(
                "[data-theme-lens]"
              )
              .forEach(
                item => item
                  .classList
                  .toggle(
                    "active",
                    item === button
                  )
              );

            renderThemes();
          }
        );
      }
    );

  document
    .querySelectorAll(
      "[data-nonemp-lens]"
    )
    .forEach(
      button => {
        button.addEventListener(
          "click",
          () => {
            state.nonEmpLens =
              button.dataset.nonempLens;

            document
              .querySelectorAll(
                "[data-nonemp-lens]"
              )
              .forEach(
                item => item
                  .classList
                  .toggle(
                    "active",
                    item === button
                  )
              );

            renderNonEmpThemes();
          }
        );
      }
    );

  document
    .querySelectorAll(
      "[data-trend-limit]"
    )
    .forEach(
      button => {
        button.addEventListener(
          "click",
          () => {
            state.trendLimit =
              button.dataset.trendLimit
              === "all"
                ? "all"
                : Number(
                  button.dataset.trendLimit
                );

            document
              .querySelectorAll(
                "[data-trend-limit]"
              )
              .forEach(
                item => item
                  .classList
                  .toggle(
                    "active",
                    item === button
                  )
              );

            renderTrend();
          }
        );
      }
    );

  document.getElementById(
    "toggle-events"
  ).addEventListener(
    "click",
    () => {
      state.showAllEvents =
        !state.showAllEvents;

      renderEvents();
    }
  );

  document.getElementById(
    "source-filter"
  ).addEventListener(
    "input",
    event => {
      renderAllSources(
        event.target.value
      );
    }
  );

  document.getElementById(
    "reset-globe"
  ).addEventListener(
    "click",
    () => {
      state.map?.easeTo({
        center: [8, 25],
        zoom: 1.1,
        duration: 800
      });

      document.getElementById(
        "market-panel"
      ).innerHTML = `
        <span class="panel-kicker">Choose a market</span>
        <h3>Click a marker on the globe</h3>
        <p>
          Discovery-market statistics describe where news was found.
          They are not country empowerment scores.
        </p>
      `;
    }
  );

  document.getElementById(
    "open-tour"
  ).addEventListener(
    "click",
    () => {
      openDialog(`
        <p class="dialog-kicker">AIEO in 60 seconds</p>
        <h2>Three ideas are enough to get started</h2>

        <ol class="dialog-list">
          <li>
            <strong>News volume</strong> counts every AI article found.
          </li>
          <li>
            <strong>Unique developments</strong> count repeated coverage of the
            same event only once.
          </li>
          <li>
            <strong>Human impact</strong> asks whether those developments
            changed people’s capability, autonomy, rights, or control.
          </li>
        </ol>

        <p>
          Everything else on the page is optional detail.
        </p>
      `);
    }
  );

  document.getElementById(
    "open-scope"
  ).addEventListener(
    "click",
    () => {
      const markets =
        insights
        ?.discovery_markets
        ?.map(row => row.name)
        || [
          "United States",
          "China",
          "United Kingdom",
          "France",
          "Canada"
        ];

      openDialog(`
        <p class="dialog-kicker">Discovery scope</p>
        <h2>Where AIEO looked for AI news</h2>

        <p>
          This release searches Google News in:
          <strong>${escapeHTML(markets.join(", "))}</strong>.
        </p>

        <p>
          A search market is where news was discovered. It is not automatically
          the country where an event happened and it is not a country
          empowerment score.
        </p>
      `);
    }
  );

  document.getElementById(
    "open-technical"
  ).addEventListener(
    "click",
    () => {
      openDialog(`
        <p class="dialog-kicker">Technical detail</p>
        <h2>The current lens comparison</h2>

        <div class="dialog-metrics">
          <article>
            <span>News volume</span>
            <strong>${coverageCount}</strong>
          </article>

          <article>
            <span>Unique developments</span>
            <strong>${eventCount}</strong>
          </article>

          <article>
            <span>Extra coverage</span>
            <strong>${extra}</strong>
          </article>

          <article>
            <span>Coverage Index</span>
            <strong>${signed(coverage.empowerment_index)}</strong>
          </article>

          <article>
            <span>Event Index</span>
            <strong>${signed(event.empowerment_index)}</strong>
          </article>

          <article>
            <span>Amplification Gap</span>
            <strong>${signed(gap)}</strong>
          </article>
        </div>

        <p>
          The formal index ranges from −100 (strong contraction) to +100
          (strong expansion). Values near zero indicate a broadly neutral
          aggregate signal.
        </p>
      `);
    }
  );

  document.getElementById(
    "close-dialog"
  ).addEventListener(
    "click",
    closeDialog
  );

  document.getElementById(
    "info-dialog"
  ).addEventListener(
    "click",
    event => {
      if (
        event.target
        === event.currentTarget
      ) {
        closeDialog();
      }
    }
  );
}

init().catch(
  error => {
    console.error(error);

    document.getElementById(
      "release-label"
    ).textContent =
      "Current signal could not be loaded";

    document.getElementById(
      "one-sentence-summary"
    ).textContent =
      "Please check the latest public data artifacts.";
  }
);
