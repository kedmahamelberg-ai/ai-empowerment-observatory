"use strict";

function signed(value) {
  if (value == null) return "—";
  const number = Number(value);
  return `${number > 0 ? "+" : ""}${number.toFixed(2)}`;
}


function releaseName(value) {
  const text = String(value || "");
  if (text.startsWith("human_audited")) return "Human-audited baseline";
  if (text.includes("audited")) return "Audited public release";
  if (text.includes("provisional")) return "Provisional automated release";
  return text ? text.replaceAll("_", " ") : "—";
}

function list(items) {
  return (items || [])
    .map(item => `<li>${String(item)}</li>`)
    .join("");
}

async function init() {
  const response = await fetch(
    "/data/methodology/latest.json",
    { cache: "no-store" }
  );

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }

  const data = await response.json();
  const signal = data.current_signal || {};
  const audit = data.audit || {};

  document.getElementById("signal").innerHTML = `
    <article class="card">
      <span>Release</span>
      <strong>${releaseName(data.release_status)}</strong>
    </article>
    <article class="card">
      <span>Coverage Index</span>
      <strong>${signed(signal.coverage_empowerment_index)}</strong>
    </article>
    <article class="card">
      <span>Event Index</span>
      <strong>${signed(signal.event_empowerment_index)}</strong>
    </article>
    <article class="card">
      <span>Amplification Gap</span>
      <strong>${signed(signal.directional_amplification_gap)}</strong>
    </article>
  `;

  document.getElementById("public-list").innerHTML =
    list(data.public_disclosure?.published);

  document.getElementById("private-list").innerHTML =
    list(data.public_disclosure?.kept_private);

  document.getElementById("limitations").innerHTML =
    list(data.limitations);

  document.getElementById("audit").innerHTML = `
    <p>
      <strong>${audit.sample_size ?? 0}</strong> audited units:
      <strong>${audit.accepted_count ?? 0}</strong> accepted and
      <strong>${audit.corrected_count ?? 0}</strong> corrected.
    </p>
    <p>${audit.representativeness_note || ""}</p>
  `;
}

init().catch(error => {
  console.error(error);
  document.getElementById("signal").textContent =
    "Methodology data are not available yet.";
});
