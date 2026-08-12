"use strict";

const STORAGE_KEY = "aieo_qwen4_translation_challenge_v1";
const summaryEl = document.getElementById("summary");
const itemsEl = document.getElementById("items");
const progressEl = document.getElementById("progress");
const downloadBtn = document.getElementById("download");
const clearBtn = document.getElementById("clear");

let payload = null;
let labels = {};

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function loadLabels() {
  try {
    labels = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
  } catch {
    labels = {};
  }
}

function saveLabels() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(labels));
}

function updateProgress() {
  const done = Object.values(labels).filter(Boolean).length;
  progressEl.textContent = `${done} / ${payload.items.length} labelled`;
}

function render() {
  summaryEl.innerHTML = `
    <strong>${escapeHTML(payload.meta.sample_size)} difficult headlines</strong>
    selected from QC flags and known semantic/entity/idiom traps.
    Model identity is hidden.
  `;

  itemsEl.innerHTML = payload.items.map((item, index) => {
    const current = labels[item.challenge_id] || "";

    const choices = [
      ["candidate_a_better", "A better"],
      ["candidate_b_better", "B better"],
      ["tie_both_accurate", "Tie — both accurate"],
      ["both_inaccurate", "Both inaccurate"],
      ["unsure", "Unsure"]
    ].map(([value, label]) => `
      <label class="choice ${current === value ? "selected" : ""}">
        <input
          type="radio"
          name="challenge-${index}"
          data-id="${escapeHTML(item.challenge_id)}"
          value="${value}"
          ${current === value ? "checked" : ""}
        >
        ${label}
      </label>
    `).join("");

    return `
      <article class="card">
        <div class="meta">
          <span>Item ${index + 1}</span>
          <span>${escapeHTML(item.source_language.toUpperCase())} → EN</span>
          ${item.previous_qc_flag ? '<span class="flag">Previously QC-flagged</span>' : ""}
        </div>

        <section class="original">
          <span class="label">Original headline</span>
          <h2>${escapeHTML(item.original_headline)}</h2>
        </section>

        <div class="candidate-grid">
          <section>
            <span class="label">Candidate A</span>
            <h3>${escapeHTML(item.candidate_a)}</h3>
          </section>
          <section>
            <span class="label">Candidate B</span>
            <h3>${escapeHTML(item.candidate_b)}</h3>
          </section>
        </div>

        <div class="choices">${choices}</div>
      </article>
    `;
  }).join("");

  document.querySelectorAll("input[type='radio']").forEach((input) => {
    input.addEventListener("change", (event) => {
      labels[event.target.dataset.id] = event.target.value;
      saveLabels();
      render();
    });
  });

  updateProgress();
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function downloadCSV() {
  const rows = [[
    "challenge_id",
    "human_label",
    "source_language",
    "original_headline",
    "candidate_a",
    "candidate_b",
    "candidate_a_model",
    "candidate_b_model",
    "previous_qc_flag",
    "previous_qc_reason"
  ]];

  for (const item of payload.items) {
    rows.push([
      item.challenge_id,
      labels[item.challenge_id] || "",
      item.source_language,
      item.original_headline,
      item.candidate_a,
      item.candidate_b,
      item.candidate_a_model,
      item.candidate_b_model,
      item.previous_qc_flag,
      item.previous_qc_reason
    ]);
  }

  const csv = rows.map(row => row.map(csvCell).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "aieo-qwen4-translation-challenge-labels.csv";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function init() {
  loadLabels();

  const response = await fetch("./latest.json", { cache: "no-store" });
  if (!response.ok) {
    summaryEl.innerHTML =
      "<strong>No challenge benchmark yet.</strong> Run the GitHub workflow.";
    return;
  }

  payload = await response.json();
  render();

  downloadBtn.addEventListener("click", downloadCSV);
  clearBtn.addEventListener("click", () => {
    if (!confirm("Clear all challenge labels in this browser?")) return;
    labels = {};
    saveLabels();
    render();
  });
}

init().catch((error) => {
  console.error(error);
  summaryEl.textContent = `Challenge error: ${error.message}`;
});
