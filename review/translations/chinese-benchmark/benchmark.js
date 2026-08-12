"use strict";

const STORAGE_KEY = "aieo_chinese_three_model_benchmark_v1";

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
    labels = JSON.parse(
      localStorage.getItem(STORAGE_KEY) || "{}"
    );
  } catch {
    labels = {};
  }
}

function saveLabels() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify(labels)
  );
}

function updateProgress() {
  const done = Object.values(labels).filter(Boolean).length;

  progressEl.textContent =
    `${done} / ${payload.items.length} labelled`;
}

function candidateCard(letter, text) {
  return `
    <section>
      <span class="label">Candidate ${letter}</span>
      <h3>${escapeHTML(text)}</h3>
    </section>
  `;
}

function render() {
  summaryEl.innerHTML = `
    <strong>${escapeHTML(payload.meta.sample_size)} Chinese headlines</strong>
    · three candidates per headline · model identity hidden.
  `;

  itemsEl.innerHTML = payload.items
    .map((item, index) => {
      const current =
        labels[item.benchmark_id] || "";

      const options = [
        ["candidate_a_best", "A best"],
        ["candidate_b_best", "B best"],
        ["candidate_c_best", "C best"],
        ["tie_a_b", "Tie A + B"],
        ["tie_a_c", "Tie A + C"],
        ["tie_b_c", "Tie B + C"],
        ["all_three_accurate", "All three accurate"],
        ["none_adequate", "None adequate"],
        ["unsure", "Unsure"]
      ];

      const choices = options.map(
        ([value, title]) => `
          <label class="choice ${
            current === value ? "selected" : ""
          }">
            <input
              type="radio"
              name="benchmark-${index}"
              data-id="${escapeHTML(item.benchmark_id)}"
              value="${value}"
              ${current === value ? "checked" : ""}
            >
            ${title}
          </label>
        `
      ).join("");

      return `
        <article class="card">
          <div class="meta">
            <span>Item ${index + 1}</span>

            ${
              item.previous_qc_flag
                ? '<span class="flag">Previously QC-flagged</span>'
                : ""
            }
          </div>

          <section class="original">
            <span class="label">Original Chinese headline</span>

            <h2>
              ${escapeHTML(item.original_headline)}
            </h2>
          </section>

          <div class="candidate-grid">
            ${candidateCard("A", item.candidate_a)}
            ${candidateCard("B", item.candidate_b)}
            ${candidateCard("C", item.candidate_c)}
          </div>

          <div class="choices">
            ${choices}
          </div>
        </article>
      `;
    })
    .join("");

  document
    .querySelectorAll("input[type='radio']")
    .forEach((input) => {
      input.addEventListener(
        "change",
        (event) => {
          labels[event.target.dataset.id] =
            event.target.value;

          saveLabels();
          render();
        }
      );
    });

  updateProgress();
}

function csvCell(value) {
  return `"${String(value ?? "")
    .replaceAll('"', '""')}"`;
}

function downloadCSV() {
  const rows = [[
    "benchmark_id",
    "human_label",
    "original_headline",
    "candidate_a",
    "candidate_b",
    "candidate_c",
    "candidate_a_model",
    "candidate_b_model",
    "candidate_c_model",
    "previous_qc_flag",
    "previous_qc_reason"
  ]];

  for (const item of payload.items) {
    rows.push([
      item.benchmark_id,
      labels[item.benchmark_id] || "",
      item.original_headline,
      item.candidate_a,
      item.candidate_b,
      item.candidate_c,
      item.candidate_a_model,
      item.candidate_b_model,
      item.candidate_c_model,
      item.previous_qc_flag,
      item.previous_qc_reason
    ]);
  }

  const csv = rows
    .map(
      row => row.map(csvCell).join(",")
    )
    .join("\n");

  const blob = new Blob(
    [csv],
    { type: "text/csv;charset=utf-8" }
  );

  const url = URL.createObjectURL(blob);

  const anchor = document.createElement("a");

  anchor.href = url;

  anchor.download =
    "aieo-chinese-translation-benchmark-labels.csv";

  document.body.appendChild(anchor);

  anchor.click();

  anchor.remove();

  URL.revokeObjectURL(url);
}

async function init() {
  loadLabels();

  const response = await fetch(
    "./latest.json",
    { cache: "no-store" }
  );

  if (!response.ok) {
    summaryEl.innerHTML =
      "<strong>No benchmark yet.</strong> Run the GitHub workflow.";

    return;
  }

  payload = await response.json();

  render();

  downloadBtn.addEventListener(
    "click",
    downloadCSV
  );

  clearBtn.addEventListener(
    "click",
    () => {
      if (
        !confirm(
          "Clear all labels in this browser?"
        )
      ) {
        return;
      }

      labels = {};

      saveLabels();

      render();
    }
  );
}

init().catch(
  (error) => {
    console.error(error);

    summaryEl.textContent =
      `Benchmark error: ${error.message}`;
  }
);
