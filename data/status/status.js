"use strict";

async function init() {
  const response = await fetch(
    "/data/status/latest.json",
    { cache: "no-store" }
  );

  if (!response.ok) throw new Error(`HTTP ${response.status}`);

  const data = await response.json();
  const latest = data.latest || {};

  document.getElementById("status").innerHTML = `
    <div class="card">
      <strong>${data.system_status}</strong>
      · ${data.release_status}
      · structural gate ${data.structural_gate}
    </div>
    <div class="card">
      ${latest.coverage_units ?? "—"} coverage units ·
      ${latest.event_units ?? "—"} event units ·
      ${latest.review_queue_count ?? "—"} asynchronous review cases
    </div>
    <div class="card">
      Updated ${data.generated_at}
    </div>
  `;
}

init().catch(error => {
  console.error(error);
  document.getElementById("status").textContent =
    "Status data are currently unavailable.";
});
