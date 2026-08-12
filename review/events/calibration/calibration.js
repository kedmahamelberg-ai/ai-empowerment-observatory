"use strict";

const STORAGE_KEY = "aieo_event_calibration_labels_v1";
const summaryEl = document.getElementById("summary");
const pairsEl = document.getElementById("pairs");
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
  progressEl.textContent = `${done} / ${payload.pairs.length} labelled`;
}

function articleCard(article, side) {
  return `
    <article class="article">
      <span class="side">Article ${side}</span>
      <h3>${escapeHTML(article.headline)}</h3>
      <p>
        ${escapeHTML(article.publisher)}
        ${article.markets?.length ? ` · ${escapeHTML(article.markets.join(", "))}` : ""}
        ${article.languages?.length ? ` · ${escapeHTML(article.languages.join(", "))}` : ""}
      </p>
      ${article.url
        ? `<a href="${escapeHTML(article.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>`
        : ""}
    </article>
  `;
}

function render() {
  summaryEl.innerHTML = `
    <strong>${escapeHTML(payload.meta.sample_size)} article pairs</strong>
    sampled across similarity bands.
    Judge the same specific occurrence, not whether they discuss the same theme.
  `;

  pairsEl.innerHTML = payload.pairs.map((pair, index) => {
    const current = labels[pair.pair_id] || "";
    const options = [
      ["same_event", "Same event"],
      ["related_topic", "Related topic, different event"],
      ["different_event", "Different event"],
      ["unsure", "Unsure"]
    ].map(([value, title]) => `
      <label class="choice ${current === value ? "selected" : ""}">
        <input
          type="radio"
          name="pair-${index}"
          value="${value}"
          data-pair-id="${escapeHTML(pair.pair_id)}"
          ${current === value ? "checked" : ""}
        >
        ${title}
      </label>
    `).join("");

    return `
      <section class="pair-card">
        <div class="pair-meta">
          <span>Pair ${index + 1}</span>
          <span>embedding similarity ${escapeHTML(pair.similarity)}</span>
          <span>${escapeHTML(pair.day_gap)} days apart</span>
          <span>${escapeHTML(pair.sampling_stratum)}</span>
        </div>

        <div class="article-grid">
          ${articleCard(pair.article_a, "A")}
          ${articleCard(pair.article_b, "B")}
        </div>

        <div class="choices">${options}</div>
      </section>
    `;
  }).join("");

  document.querySelectorAll("input[type='radio']").forEach((input) => {
    input.addEventListener("change", (event) => {
      labels[event.target.dataset.pairId] = event.target.value;
      saveLabels();
      updateProgress();
      render();
    });
  });

  updateProgress();
}

function csvCell(value) {
  const text = String(value ?? "");
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadCSV() {
  const rows = [[
    "pair_id",
    "human_label",
    "similarity",
    "day_gap",
    "sampling_stratum",
    "headline_a",
    "publisher_a",
    "headline_b",
    "publisher_b"
  ]];

  for (const pair of payload.pairs) {
    rows.push([
      pair.pair_id,
      labels[pair.pair_id] || "",
      pair.similarity,
      pair.day_gap,
      pair.sampling_stratum,
      pair.article_a.headline,
      pair.article_a.publisher,
      pair.article_b.headline,
      pair.article_b.publisher
    ]);
  }

  const csv = rows.map(row => row.map(csvCell).join(",")).join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = "aieo-event-cluster-calibration-labels.csv";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

async function init() {
  loadLabels();

  const response = await fetch("./pairs.json", { cache: "no-store" });
  if (!response.ok) {
    summaryEl.innerHTML =
      "<strong>No calibration sample yet.</strong> Run the GitHub calibration workflow.";
    return;
  }

  payload = await response.json();
  render();

  downloadBtn.addEventListener("click", downloadCSV);
  clearBtn.addEventListener("click", () => {
    if (!window.confirm("Clear all calibration labels in this browser?")) return;
    labels = {};
    saveLabels();
    render();
  });
}

init().catch((error) => {
  console.error(error);
  summaryEl.textContent = `Calibration page error: ${error.message}`;
});
