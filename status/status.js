"use strict";
const escapeText = value => String(value ?? '—').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
async function init() {
  const response = await fetch('/data/status/latest.json', {cache:'no-store'});
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const data = await response.json();
  const latest = data.latest || {};
  document.getElementById('status').innerHTML = `
    <div class="card"><strong>${escapeText(data.system_status)}</strong> · Release checks ${escapeText(data.structural_gate)}</div>
    <div class="card">${escapeText(latest.coverage_units)} source pages · ${escapeText(latest.event_units)} developments</div>
    <div class="card">${escapeText(data.period_start)} to ${escapeText(data.period_end)} · <a href="/edu/">Explore the source readings</a></div>
    <div class="card">Updated ${escapeText(data.generated_at)}</div>`;
}
init().catch(error => {console.error(error);document.getElementById('status').textContent='Status data are currently unavailable.';});
