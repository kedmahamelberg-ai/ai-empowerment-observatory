"use strict";


function releaseName(value) {
  const text = String(value || "");
  if (text.startsWith("human_audited")) return "Human-audited baseline";
  if (text.includes("audited")) return "Audited public release";
  if (text.includes("provisional")) return "Provisional automated release";
  return text ? text.replaceAll("_", " ") : "—";
}

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
      · ${releaseName(data.release_status)}
      · structural gate ${data.structural_gate}
    </div>
    <div class="card">
      ${latest.coverage_units ?? "—"} article units ·
      ${latest.event_units ?? "—"} resolved event records ·
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
