"use strict";

const STORAGE_KEY = "aieo_stage7c_audit_v1";

const indicesEl = document.getElementById("indices");
const queueEl = document.getElementById("queue");
const filterEl = document.getElementById("filter");
const progressEl = document.getElementById("progress");
const downloadBtn = document.getElementById("download");
const clearBtn = document.getElementById("clear");

let payload = null;
let reviews = {};

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function loadReviews() {
  try {
    reviews = JSON.parse(
      localStorage.getItem(STORAGE_KEY) || "{}"
    );
  } catch {
    reviews = {};
  }
}

function saveReviews() {
  localStorage.setItem(
    STORAGE_KEY,
    JSON.stringify(reviews)
  );
}

function signed(value) {
  if (value == null) return "—";

  const n = Number(value);
  return `${n > 0 ? "+" : ""}${n.toFixed(2)}`;
}

function percent(value) {
  return `${(Number(value || 0) * 100).toFixed(1)}%`;
}

function renderIndices() {
  const g = payload.global;
  const c = g.coverage;
  const e = g.event;
  const a = g.amplification;

  indicesEl.innerHTML = `
    <div class="index-card">
      <span>Coverage Empowerment Index</span>
      <strong>${signed(c.empowerment_index)}</strong>
      <small>
        ${escapeHTML(c.unit_count_ai_relevant)}
        AI-relevant articles ·
        mean confidence ${escapeHTML(c.mean_confidence ?? "—")}
      </small>
    </div>

    <div class="index-card">
      <span>Event Empowerment Index</span>
      <strong>${signed(e.empowerment_index)}</strong>
      <small>
        ${escapeHTML(e.unit_count_ai_relevant)}
        unique AI events ·
        mean confidence ${escapeHTML(e.mean_confidence ?? "—")}
      </small>
    </div>

    <div class="index-card gap">
      <span>Directional Amplification Gap</span>
      <strong>${signed(a.directional_amplification_gap)}</strong>
      <small>
        Coverage − Event ·
        article/event ratio ${escapeHTML(a.coverage_event_ratio ?? "—")}
      </small>
    </div>

    <div class="index-card">
      <span>Coverage narrative</span>
      <strong>${percent(c.narrative_distribution.opportunity)} opportunity</strong>
      <small>
        ${percent(c.narrative_distribution.threat)} threat ·
        ${percent(c.narrative_distribution.contested)} contested
      </small>
    </div>
  `;
}

function visibleQueue() {
  const mode = filterEl.value;

  if (mode === "all") {
    return payload.review_queue;
  }

  if (mode === "model") {
    return payload.review_queue.filter(
      row => row.requires_review
    );
  }

  if (mode === "audit") {
    return payload.review_queue.filter(
      row => row.audit_selected
    );
  }

  return payload.review_queue.filter(
    row => row.lens === mode
  );
}

function dimensionsHTML(dimensions) {
  return Object.entries(dimensions)
    .filter(([, value]) => value.present)
    .map(([name, value]) => `
      <span class="dimension">
        ${escapeHTML(name)}
        · ${escapeHTML(value.direction)}
        · ${escapeHTML(value.degree)}
      </span>
    `)
    .join("") || '<span class="dimension absent">No empowerment dimension</span>';
}

function reviewControls(item) {
  const current =
    reviews[item.lens_classification_id] || {};

  const values = [
    ["accepted", "Looks correct"],
    ["needs_correction", "Needs correction"],
    ["uncertain", "I’m unsure"]
  ];

  return `
    <div class="review-controls">
      ${values.map(([value, label]) => `
        <label class="choice ${
          current.review_status === value ? "selected" : ""
        }">
          <input
            type="radio"
            name="r-${escapeHTML(item.lens_classification_id)}"
            data-id="${escapeHTML(item.lens_classification_id)}"
            value="${value}"
            ${
              current.review_status === value
                ? "checked"
                : ""
            }
          >
          ${label}
        </label>
      `).join("")}

      <input
        class="notes"
        type="text"
        data-notes-id="${escapeHTML(item.lens_classification_id)}"
        value="${escapeHTML(current.notes || "")}"
        placeholder="Optional note / correction"
      >
    </div>
  `;
}

function render() {
  const rows = visibleQueue();

  queueEl.innerHTML = rows.length
    ? rows.map(item => `
      <article class="card">
        <div class="topline">
          <span class="lens">${escapeHTML(item.lens)} lens</span>

          ${
            item.requires_review
              ? `<span class="flag">model review: ${escapeHTML(item.review_reason)}</span>`
              : ""
          }

          ${
            item.audit_selected
              ? `<span class="audit">${escapeHTML(item.audit_reason)}</span>`
              : ""
          }
        </div>

        <h2>${escapeHTML(item.title)}</h2>

        <p class="source">
          ${escapeHTML(item.publisher_or_sources)}
          ${item.date ? ` · ${escapeHTML(item.date)}` : ""}
        </p>

        <div class="labels">
          <div>
            <span>Empowerment</span>
            <strong>
              ${escapeHTML(item.empowerment_status)}
              · degree ${escapeHTML(item.empowerment_degree)}
              · score ${escapeHTML(item.unit_score ?? "excluded")}
            </strong>
          </div>

          <div>
            <span>Narrative</span>
            <strong>${escapeHTML(item.narrative_frame)}</strong>
          </div>

          <div>
            <span>Breadth</span>
            <strong>${escapeHTML(item.distribution_breadth)}</strong>
          </div>

          <div>
            <span>Country</span>
            <strong>
              ${escapeHTML(
                item.country_iso3s?.length
                  ? item.country_iso3s.join(", ")
                  : item.geographic_scope
              )}
            </strong>
          </div>
        </div>

        <div class="dimensions">
          ${dimensionsHTML(item.dimensions)}
        </div>

        <div class="reasoning">
          <strong>Model reasoning</strong>
          <p>${escapeHTML(item.reasoning)}</p>
          <small>
            Confidence ${escapeHTML(item.confidence)}
            · Topic ${escapeHTML(item.topic)}
            · AI authority ${escapeHTML(item.ai_authority_shift)}
          </small>
        </div>

        <details>
          <summary>Evidence supplied to classifier</summary>
          <pre>${escapeHTML(item.evidence)}</pre>
        </details>

        ${reviewControls(item)}
      </article>
    `).join("")
    : '<div class="empty">No classifications match this filter.</div>';

  document.querySelectorAll(
    "input[type='radio'][data-id]"
  ).forEach(input => {
    input.addEventListener("change", event => {
      const id = event.target.dataset.id;

      reviews[id] = {
        ...(reviews[id] || {}),
        review_status: event.target.value
      };

      saveReviews();
      render();
    });
  });

  document.querySelectorAll(
    "input[data-notes-id]"
  ).forEach(input => {
    input.addEventListener("input", event => {
      const id = event.target.dataset.notesId;

      reviews[id] = {
        ...(reviews[id] || {}),
        notes: event.target.value
      };

      saveReviews();
      updateProgress();
    });
  });

  updateProgress();
}

function updateProgress() {
  const done = payload.review_queue.filter(
    item => reviews[item.lens_classification_id]?.review_status
  ).length;

  progressEl.textContent =
    `${done} / ${payload.review_queue.length} audit/review cases decided`;
}

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function downloadCSV() {
  const rows = [[
    "lens_classification_id",
    "lens",
    "unit_id",
    "model_empowerment_status",
    "model_empowerment_degree",
    "model_narrative_frame",
    "model_distribution_breadth",
    "model_dominant_dimension",
    "model_confidence",
    "review_status",
    "notes"
  ]];

  for (const item of payload.review_queue) {
    const review =
      reviews[item.lens_classification_id] || {};

    rows.push([
      item.lens_classification_id,
      item.lens,
      item.unit_id,
      item.empowerment_status,
      item.empowerment_degree,
      item.narrative_frame,
      item.distribution_breadth,
      item.dominant_dimension || "",
      item.confidence,
      review.review_status || "",
      review.notes || ""
    ]);
  }

  const csv = rows
    .map(row => row.map(csvCell).join(","))
    .join("\n");

  const blob = new Blob(
    [csv],
    { type: "text/csv;charset=utf-8" }
  );

  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");

  a.href = url;
  a.download = "aieo-stage7c-classification-audit.csv";

  document.body.appendChild(a);
  a.click();
  a.remove();

  URL.revokeObjectURL(url);
}

async function init() {
  loadReviews();

  try {
    const response = await fetch(
      "./latest.json",
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    payload = await response.json();

    renderIndices();
    render();

    filterEl.addEventListener("change", render);
    downloadBtn.addEventListener("click", downloadCSV);

    clearBtn.addEventListener("click", () => {
      if (!confirm("Clear all Stage 7C review choices in this browser?")) {
        return;
      }

      reviews = {};
      saveReviews();
      render();
    });

  } catch (error) {
    console.error(error);

    indicesEl.innerHTML = `
      <strong>No Stage 7C classification output yet.</strong>
      Run “Classify Coverage and Event Lenses” in GitHub Actions.
    `;
  }
}

init();
