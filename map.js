"use strict";

const MAP_STYLE = "https://demotiles.maplibre.org/globe.json";
const GLOBAL_VIEW = { center: [0, 18], zoom: 1.25 };

const elements = {
  map: document.getElementById("map"),
  panel: document.getElementById("country-panel"),
  panelContent: document.getElementById("country-panel-content"),
  closePanel: document.getElementById("close-country-panel"),
  resetMap: document.getElementById("reset-map"),
  openMethodology: document.getElementById("open-methodology"),
  methodologyDialog: document.getElementById("methodology-dialog"),
  closeMethodology: document.getElementById("close-methodology")
};

let map;
let dataset;

function escapeHTML(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function signed(value) {
  const number = Number(value);
  return number > 0 ? `+${number}` : String(number);
}

function scoreClass(value) {
  const number = Number(value);
  if (number > 5) return "score-positive";
  if (number < -5) return "score-negative";
  return "score-mixed";
}

function trendSymbol(value) {
  const number = Number(value);
  if (number > 0) return "↑";
  if (number < 0) return "↓";
  return "→";
}

function toGeoJSON(countries) {
  return {
    type: "FeatureCollection",
    features: countries.map((country) => ({
      type: "Feature",
      geometry: {
        type: "Point",
        coordinates: country.coordinates
      },
      properties: {
        id: country.id,
        country: country.country,
        direction: country.direction,
        score: country.score
      }
    }))
  };
}

function renderDimension(label, value) {
  return `
    <div class="dimension-row">
      <span>${escapeHTML(label)}</span>
      <strong class="${scoreClass(value)}">${signed(value)}</strong>
    </div>
  `;
}

function renderCountry(country) {
  const storiesHTML = country.stories.length
    ? country.stories
        .map(
          (story) => `
            <li>
              <a href="${escapeHTML(story.url)}" rel="noopener noreferrer">
                ${escapeHTML(story.headline)}
              </a>
            </li>
          `
        )
        .join("")
    : `
        <li class="empty-state">
          Validated headlines will be added after the classification rules are tested.
        </li>
      `;

  elements.panelContent.innerHTML = `
    <p class="panel-kicker">${escapeHTML(country.evidence)} data</p>
    <h2>${escapeHTML(country.country)}</h2>

    <div class="headline-score">
      <strong class="${scoreClass(country.score)}">${signed(country.score)}</strong>
      <span>
        ${trendSymbol(country.change)} ${Math.abs(country.change)} from the comparison period
      </span>
    </div>

    <p class="data-warning">
      Illustrative interface data — not an empirical country assessment.
    </p>

    <h3>Four dimensions</h3>
    <div class="dimension-list">
      ${renderDimension("Operational", country.operational)}
      ${renderDimension("Creative", country.creative)}
      ${renderDimension("Agentic", country.agentic)}
      ${renderDimension("Normative", country.normative)}
    </div>

    <h3>Prototype narrative</h3>
    <p>${escapeHTML(country.summary)}</p>

    <h3>Source-backed headlines</h3>
    <ul class="story-list">${storiesHTML}</ul>
  `;

  elements.panel.hidden = false;
  elements.closePanel.focus();

  map.flyTo({
    center: country.coordinates,
    zoom: 3.2,
    essential: true,
    offset: window.innerWidth > 850 ? [-190, 0] : [0, -100]
  });
}

function closeCountryPanel({ resetView = false } = {}) {
  elements.panel.hidden = true;

  if (resetView) {
    map.flyTo({
      ...GLOBAL_VIEW,
      essential: true
    });
  }
}

function addCountryLayer() {
  map.addSource("prototype-countries", {
    type: "geojson",
    data: toGeoJSON(dataset.countries)
  });

  map.addLayer({
    id: "prototype-country-points",
    type: "circle",
    source: "prototype-countries",
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 1, 10, 4, 18],
      "circle-color": [
        "match",
        ["get", "direction"],
        "positive",
        "#16835b",
        "negative",
        "#b64c45",
        "#b47a18"
      ],
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 3,
      "circle-opacity": 0.94
    }
  });

  map.on("click", "prototype-country-points", (event) => {
    const selectedId = event.features?.[0]?.properties?.id;
    const selectedCountry = dataset.countries.find(
      (country) => country.id === selectedId
    );

    if (selectedCountry) renderCountry(selectedCountry);
  });

  map.on("mouseenter", "prototype-country-points", () => {
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mouseleave", "prototype-country-points", () => {
    map.getCanvas().style.cursor = "";
  });
}

function attachInterfaceEvents() {
  elements.closePanel.addEventListener("click", () => closeCountryPanel());

  elements.resetMap.addEventListener("click", () => {
    closeCountryPanel({ resetView: true });
  });

  elements.openMethodology.addEventListener("click", () => {
    elements.methodologyDialog.showModal();
  });

  elements.closeMethodology.addEventListener("click", () => {
    elements.methodologyDialog.close();
  });

  elements.methodologyDialog.addEventListener("click", (event) => {
    if (event.target === elements.methodologyDialog) {
      elements.methodologyDialog.close();
    }
  });
}

async function initialize() {
  try {
    const response = await fetch("./data/countries.json", {
      cache: "no-store"
    });

    if (!response.ok) {
      throw new Error(`Data request failed with status ${response.status}`);
    }

    dataset = await response.json();

    map = new maplibregl.Map({
      container: "map",
      style: MAP_STYLE,
      ...GLOBAL_VIEW,
      minZoom: 1
    });

    map.addControl(
      new maplibregl.NavigationControl({ visualizePitch: true }),
      "bottom-right"
    );

    map.on("load", addCountryLayer);
    map.on("error", (event) => console.error("Map error:", event.error));

    attachInterfaceEvents();
  } catch (error) {
    console.error(error);
    elements.map.innerHTML = `
      <div class="load-error">
        <strong>The observatory could not load.</strong>
        <span>Please refresh the page or check the data file path.</span>
      </div>
    `;
  }
}

initialize();
