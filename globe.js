import {geoOrthographic, geoPath, geoDistance} from './vendor/globe-geometry.js';

// Local geometry and a 2D canvas work without WebGL or an external tile service.
const INITIAL_CENTER = [8, 27];
const SPEED = 0.002; // One quiet revolution in three minutes.

export async function initDiscoveryGlobe({containerId, toggleId, promptId, fallbackId, markets, onSelect}) {
  const container = document.getElementById(containerId);
  const toggle = document.getElementById(toggleId);
  const prompt = document.getElementById(promptId);
  const fallback = document.getElementById(fallbackId);
  if (!container) throw new Error(`Missing globe container: ${containerId}`);
  const canvas = document.createElement('canvas');
  canvas.setAttribute('role', 'img');
  canvas.setAttribute('aria-label', 'Rotating world globe. Use the five market buttons below to explore sources.');
  const context = canvas.getContext('2d');
  let land;
  try {
    if (!context) throw new Error('Canvas is unavailable.');
    const response = await fetch('/data/geography/land.json');
    if (!response.ok) throw new Error('Land geometry is unavailable.');
    land = await response.json();
  } catch (error) {
    if (fallback) fallback.hidden = false;
    if (toggle) toggle.hidden = true;
    throw error;
  }
  container.replaceChildren(canvas);
  if (fallback) fallback.hidden = true;
  if (toggle) toggle.hidden = false;
  const projection = geoOrthographic().clipAngle(90).precision(0.35);
  const path = geoPath(projection, context);
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)');
  const listeners = new AbortController();
  let center = [...INITIAL_CENTER], selected = null, moving = !motionPreference.matches;
  let width = 0, height = 0, ratio = 1, visible = true, hovering = false, dirty = true;
  let frame = 0, previous = 0, destroyed = false, drag = null;
  const markers = new Map();

  function updateToggle() {
    if (!toggle) return;
    toggle.textContent = moving ? 'Pause globe' : 'Play globe';
    toggle.setAttribute('aria-pressed', String(moving));
  }
  function draw() {
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.clearRect(0, 0, width, height);
    projection.rotate([-center[0], -center[1], 0]);
    context.beginPath(); path({type:'Sphere'});
    context.fillStyle = '#f6f9fa'; context.fill();
    context.strokeStyle = '#b7c8d3'; context.lineWidth = 0.8; context.stroke();
    context.beginPath(); path(land);
    context.fillStyle = '#b9cbd5'; context.fill();
    context.strokeStyle = '#94adb9'; context.lineWidth = 0.45; context.stroke();
    markers.forEach(({button, position}, code) => {
      const front = geoDistance(center, position) < Math.PI / 2 - 0.035;
      button.hidden = !front;
      if (front) {
        const xy = projection(position);
        button.style.left = `${xy[0]}px`; button.style.top = `${xy[1]}px`;
      }
      button.setAttribute('aria-current', String(code === selected));
    });
    dirty = false;
  }
  function resize() {
    const bounds = container.getBoundingClientRect();
    width = bounds.width; height = bounds.height;
    ratio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    projection.translate([width / 2, (height - 32) / 2]).scale(Math.max(1, Math.min(width - 42, height - 52) / 2));
    dirty = true;
  }
  function selectMarket(code, {notify = true} = {}) {
    const market = markets?.[code];
    if (!market) return;
    selected = code; moving = false;
    center = [Number(market.longitude), Number(market.latitude)];
    if (prompt) prompt.textContent = `${market.name || code} selected`;
    updateToggle(); dirty = true;
    if (notify) onSelect?.(code);
  }
  function reset({resume = true} = {}) {
    selected = null; moving = resume; center = [...INITIAL_CENTER];
    if (prompt) prompt.textContent = 'Drag to explore';
    updateToggle(); dirty = true; onSelect?.(null);
  }
  Object.entries(markets || {}).forEach(([code, market]) => {
    const button = document.createElement('button');
    button.type = 'button'; button.className = 'market-marker';
    button.title = market.name || code;
    button.setAttribute('aria-label', `Explore ${market.name || code}`);
    // Stable keyboard controls are the labelled country buttons below the globe.
    button.tabIndex = -1;
    button.addEventListener('click', () => selectMarket(code), {signal:listeners.signal});
    container.append(button);
    markers.set(code, {button, position:[Number(market.longitude), Number(market.latitude)]});
  });
  toggle?.addEventListener('click', () => {
    if (moving) {moving = false; updateToggle();}
    else reset({resume:true});
  }, {signal:listeners.signal});
  canvas.addEventListener('pointerdown', event => {
    moving = false; updateToggle();
    drag = {id:event.pointerId, x:event.clientX, y:event.clientY, center:[...center]};
    canvas.setPointerCapture(event.pointerId);
  }, {signal:listeners.signal});
  canvas.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId) return;
    const sensitivity = 70 / projection.scale();
    center = [drag.center[0] - (event.clientX-drag.x)*sensitivity,
      Math.max(-75, Math.min(75, drag.center[1] + (event.clientY-drag.y)*sensitivity))];
    selected = null; dirty = true;
    if (prompt) prompt.textContent = 'Drag to explore';
  }, {signal:listeners.signal});
  const endDrag = () => {if (drag) {drag = null; onSelect?.(null);}};
  canvas.addEventListener('pointerup', endDrag, {signal:listeners.signal});
  canvas.addEventListener('pointercancel', endDrag, {signal:listeners.signal});
  container.addEventListener('pointerenter', () => {hovering = true;}, {signal:listeners.signal});
  container.addEventListener('pointerleave', () => {hovering = false;}, {signal:listeners.signal});
  motionPreference.addEventListener('change', () => {if (motionPreference.matches) {moving=false; updateToggle();}}, {signal:listeners.signal});
  const resizeObserver = new ResizeObserver(resize);
  resizeObserver.observe(container);
  const observer = new IntersectionObserver(([entry]) => {visible=entry.isIntersecting;}, {threshold:0.1});
  observer.observe(container);
  function tick(now) {
    if (destroyed) return;
    if (now-previous >= 32) {
      const elapsed = Math.min(80, now-previous); previous = now;
      if (visible && !document.hidden && moving && !hovering && !drag) {
        center[0] = (center[0] + elapsed*SPEED + 540) % 360 - 180; dirty = true;
      }
      if (visible && dirty) draw();
    }
    frame = requestAnimationFrame(tick);
  }
  resize(); draw(); updateToggle(); frame = requestAnimationFrame(tick);
  return {selectMarket, reset, resize, destroy() {
    destroyed=true; cancelAnimationFrame(frame); listeners.abort();
    resizeObserver.disconnect(); observer.disconnect(); container.replaceChildren();
  }};
}
