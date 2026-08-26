"use strict";

const MAPLIBRE_MODULE = "https://unpkg.com/maplibre-gl@6.5.0/dist/maplibre-gl.mjs";
const MAP_STYLE = "https://tiles.openfreemap.org/styles/positron";
const INITIAL_CENTER = [8, 27];
const ROTATION_DEGREES_PER_MS = 0.00135;

function reducedMotion() {
  return window.matchMedia?.("(prefers-reduced-motion: reduce)").matches === true;
}

function coarsePointer() {
  return window.matchMedia?.("(pointer: coarse)").matches === true;
}

function startZoom() {
  if (window.innerWidth < 480) return 0.3;
  if (window.innerWidth < 760) return 0.6;
  if (window.innerWidth < 1080) return 0.95;
  return 1.2;
}

function marketName(market) {
  return String(market?.name || market?.short_name || "market");
}

export async function initDiscoveryGlobe({
  containerId,
  toggleId,
  promptId,
  fallbackId,
  markets,
  onSelect,
}) {
  const container = document.getElementById(containerId);
  const toggle = document.getElementById(toggleId);
  const prompt = document.getElementById(promptId);
  const fallback = document.getElementById(fallbackId);
  if (!container) throw new Error(`Globe container #${containerId} was not found.`);

  let maplibregl;
  try {
    maplibregl = await import(MAPLIBRE_MODULE);
  } catch (error) {
    console.error("MapLibre could not be loaded", error);
    if (fallback) fallback.hidden = false;
    if (toggle) toggle.hidden = true;
    throw error;
  }

  let map;
  try {
    map = new maplibregl.Map({
      container,
      style: MAP_STYLE,
      center: INITIAL_CENTER,
      zoom: startZoom(),
      minZoom: 0.15,
      maxZoom: 7,
      pitch: 0,
      bearing: 0,
      attributionControl: true,
      antialias: true,
      cooperativeGestures: true,
    });
  } catch (error) {
    console.error("MapLibre could not create a map", error);
    if (fallback) fallback.hidden = false;
    if (toggle) toggle.hidden = true;
    throw error;
  }

  map.addControl(
    new maplibregl.NavigationControl({ showCompass: false, visualizePitch: false }),
    "bottom-right",
  );

  const markers = new Map();
  let selectedIso = null;
  let requestedMotion = !reducedMotion() && !coarsePointer();
  let interactionPaused = false;
  let pointerInside = false;
  let visible = true;
  let programmaticMove = false;
  let destroyed = false;
  let previousFrame = performance.now();
  let frameId = null;
  let mapReady = false;
  const fallbackTimer = window.setTimeout(() => {
    if (!mapReady) {
      if (fallback) fallback.hidden = false;
      if (toggle) toggle.hidden = true;
    }
  }, 12000);

  function rotating() {
    return requestedMotion && visible && !interactionPaused && !pointerInside && !selectedIso;
  }

  function updateToggle() {
    if (!toggle) return;
    if (requestedMotion && (interactionPaused || selectedIso)) {
      toggle.textContent = "Resume globe";
      toggle.setAttribute("aria-pressed", "false");
    } else if (requestedMotion) {
      toggle.textContent = "Pause globe";
      toggle.setAttribute("aria-pressed", "true");
    } else {
      toggle.textContent = "Play globe";
      toggle.setAttribute("aria-pressed", "false");
    }
  }

  function markSelected(iso3) {
    markers.forEach((button, key) => {
      button.setAttribute("aria-current", key === iso3 ? "true" : "false");
    });
  }

  function move(options) {
    programmaticMove = true;
    map.easeTo({ duration: 850, essential: false, ...options });
    map.once("moveend", () => { programmaticMove = false; });
  }

  function selectMarket(iso3, { notify = true } = {}) {
    const market = markets?.[iso3];
    if (!market) return;
    selectedIso = iso3;
    interactionPaused = true;
    markSelected(iso3);
    if (prompt) prompt.textContent = `${marketName(market)} selected`;
    move({
      center: [Number(market.longitude), Number(market.latitude)],
      zoom: Number(market.zoom || 2.6),
    });
    updateToggle();
    if (notify && typeof onSelect === "function") onSelect(iso3);
  }

  function reset({ resume = true } = {}) {
    selectedIso = null;
    interactionPaused = false;
    requestedMotion = resume;
    markSelected(null);
    if (prompt) prompt.textContent = "Drag the globe or choose a market";
    move({ center: INITIAL_CENTER, zoom: startZoom() });
    updateToggle();
    if (typeof onSelect === "function") onSelect(null);
  }

  function pauseForInteraction() {
    if (programmaticMove) return;
    interactionPaused = true;
    updateToggle();
  }

  function rotate(now) {
    if (destroyed) return;
    const elapsed = Math.min(80, Math.max(0, now - previousFrame));
    previousFrame = now;
    if (rotating() && map.loaded() && !map.isMoving()) {
      const center = map.getCenter();
      map.setCenter([center.lng + elapsed * ROTATION_DEGREES_PER_MS, center.lat]);
    }
    frameId = requestAnimationFrame(rotate);
  }

  map.on("style.load", () => {
    try {
      map.setProjection({ type: "globe" });
    } catch (error) {
      console.warn("Globe projection unavailable; using map projection", error);
    }
  });

  map.on("load", () => {
    mapReady = true;
    window.clearTimeout(fallbackTimer);
    Object.entries(markets || {}).forEach(([iso3, market]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "market-marker";
      button.title = marketName(market);
      button.setAttribute("aria-label", `Explore ${marketName(market)}`);
      button.setAttribute("aria-current", "false");
      button.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        selectMarket(iso3);
      });
      markers.set(iso3, button);
      new maplibregl.Marker({ element: button, anchor: "center" })
        .setLngLat([Number(market.longitude), Number(market.latitude)])
        .addTo(map);
    });
    if (fallback) fallback.hidden = true;
    updateToggle();
  });

  ["dragstart", "zoomstart", "rotatestart", "pitchstart"].forEach((eventName) => {
    map.on(eventName, pauseForInteraction);
  });
  map.on("error", (event) => console.warn("MapLibre error", event?.error || event));

  container.addEventListener("pointerenter", () => { pointerInside = true; });
  container.addEventListener("pointerleave", () => { pointerInside = false; });
  container.addEventListener("touchstart", pauseForInteraction, { passive: true });
  container.addEventListener("wheel", pauseForInteraction, { passive: true });

  toggle?.addEventListener("click", () => {
    if (requestedMotion && !interactionPaused && !selectedIso) {
      requestedMotion = false;
      updateToggle();
    } else {
      reset({ resume: true });
    }
  });

  const observer = new IntersectionObserver(
    ([entry]) => {
      visible = Boolean(entry?.isIntersecting);
      if (visible) map.resize();
    },
    { threshold: 0.1 },
  );
  observer.observe(container);

  updateToggle();
  frameId = requestAnimationFrame(rotate);

  return {
    selectMarket,
    reset,
    resize: () => map.resize(),
    destroy() {
      destroyed = true;
      observer.disconnect();
      window.clearTimeout(fallbackTimer);
      if (frameId) cancelAnimationFrame(frameId);
      map.remove();
    },
  };
}
