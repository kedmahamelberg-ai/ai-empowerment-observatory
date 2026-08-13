"use strict";

const STORAGE_KEY = "aieo_event_assignment_reviews_v1";

const summaryEl = document.getElementById("summary");
const itemsEl = document.getElementById("items");
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

function visibleItems() {
  const mode = filterEl.value;

  if (mode === "review") {
    return payload.review_queue;
  }

  if (mode === "all") {
    return payload.decisions;
  }

  return payload.decisions.filter(
    item => item.decision === mode
  );
}

function updateProgress() {
  const queue = payload.review_queue || [];

  const done = queue.filter(
    item => reviews[item.assignment_decision_id]?.final_decision
  ).length;

  progressEl.textContent =
    `${done} / ${queue.length} review cases decided`;
}

function evidenceHTML(candidate) {
  if (!candidate?.evidence?.length) {
    return '<p class="muted">No evidence headlines available.</p>';
  }

  return candidate.evidence.map((item, index) => `
    <div class="evidence">
      <span>Evidence ${index + 1}</span>
      <strong>${escapeHTML(item.headline_english)}</strong>
      <small>
        ${escapeHTML(item.publisher)}
        · ${escapeHTML(item.date)}
      </small>
    </div>
  `).join("");
}

function signalHTML(signals) {
  if (!signals) return "";

  return `
    <div class="signals">
      <span>Event sim ${escapeHTML(signals.event_similarity)}</span>
      <span>ModernBERT ${escapeHTML(signals.modernbert_max)}</span>
      <span>
        Qwen ${escapeHTML(signals.qwen_relationship ?? "not called")}
        ${
          signals.qwen_confidence != null
            ? `(${escapeHTML(signals.qwen_confidence)})`
            : ""
        }
      </span>
      ${
        signals.story_token_match
          ? "<span>story-token match</span>"
          : ""
      }
      ${
        signals.competing_candidate
          ? "<span>competing event candidate</span>"
          : ""
      }
    </div>
  `;
}

function reviewControls(item) {
  if (!item.requires_review) {
    return "";
  }

  const current =
    reviews[item.assignment_decision_id] || {};

  const options = [
    ["merge_candidate", "Merge with candidate event"],
    ["keep_separate", "Keep as separate event"],
    ["defer", "Defer"]
  ];

  return `
    <div class="review-controls">
      ${options.map(([value, label]) => `
        <label class="choice ${
          current.final_decision === value ? "selected" : ""
        }">
          <input
            type="radio"
            name="review-${escapeHTML(item.assignment_decision_id)}"
            data-id="${escapeHTML(item.assignment_decision_id)}"
            value="${value}"
            ${
              current.final_decision === value
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
        data-notes-id="${escapeHTML(item.assignment_decision_id)}"
        placeholder="Optional note"
        value="${escapeHTML(current.notes || "")}"
      >
    </div>
  `;
}

function render() {
  const items = visibleItems();

  itemsEl.innerHTML = items.length
    ? items.map(item => `
      <article class="card ${
        item.requires_review ? "needs-review" : ""
      }">
        <div class="topline">
          <span class="decision">${escapeHTML(item.decision)}</span>
          ${
            item.review_reason
              ? `<span class="reason">${escapeHTML(item.review_reason)}</span>`
              : ""
          }
        </div>

        <div class="comparison">
          <section>
            <span class="label">New article</span>
            <h2>${escapeHTML(
              item.article?.headline_english
              || item.article_headline
              || ""
            )}</h2>

            ${
              item.article?.headline_original
              && item.article.headline_original
                !== item.article.headline_english
                ? `
                  <p class="original">
                    ${escapeHTML(item.article.headline_original)}
                  </p>
                `
                : ""
            }

            <small>
              ${escapeHTML(item.article?.publisher || "")}
              ${item.article?.date ? ` · ${escapeHTML(item.article.date)}` : ""}
            </small>
          </section>

          <section>
            <span class="label">Candidate event</span>
            <h2>${escapeHTML(item.candidate_event?.event_title || "—")}</h2>
            <small>
              ${escapeHTML(item.candidate_event?.event_date || "")}
            </small>

            <div class="evidence-list">
              ${evidenceHTML(item.candidate_event)}
            </div>
          </section>
        </div>

        ${signalHTML(item.signals)}
        ${reviewControls(item)}
      </article>
    `).join("")
    : '<div class="empty">No decisions match this filter.</div>';

  document.querySelectorAll(
    "input[type='radio'][data-id]"
  ).forEach(input => {
    input.addEventListener("change", event => {
      const id = event.target.dataset.id;

      reviews[id] = {
        ...(reviews[id] || {}),
        final_decision: event.target.value
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

function csvCell(value) {
  return `"${String(value ?? "").replaceAll('"', '""')}"`;
}

function downloadCSV() {
  const rows = [[
    "assignment_decision_id",
    "final_decision",
    "notes"
  ]];

  for (const item of payload.review_queue) {
    const review =
      reviews[item.assignment_decision_id] || {};

    rows.push([
      item.assignment_decision_id,
      review.final_decision || "",
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
  const anchor = document.createElement("a");

  anchor.href = url;
  anchor.download = "event_assignment_reviews.csv";

  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();

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
    const m = payload.meta;

    summaryEl.innerHTML = `
      <strong>${escapeHTML(m.article_count)} coverage articles</strong>
      → <strong>${escapeHTML(m.active_event_count)} active events</strong>
      · ${escapeHTML(m.auto_merge_count)} auto-merged
      · ${escapeHTML(m.new_event_count)} new events
      · ${escapeHTML(m.review_count)} review cases
      · ${escapeHTML(m.pending_event_count)} pending events.
      <br>
      <span>${escapeHTML(m.principle)}</span>
    `;

    render();

    filterEl.addEventListener("change", render);
    downloadBtn.addEventListener("click", downloadCSV);

    clearBtn.addEventListener("click", () => {
      if (!confirm("Clear all event-review choices in this browser?")) {
        return;
      }

      reviews = {};
      saveReviews();
      render();
    });

  } catch (error) {
    console.error(error);

    summaryEl.innerHTML = `
      <strong>No Stage 7B.3 resolution run yet.</strong>
      Run “Resolve AI News Into Events” in GitHub Actions.
    `;
  }
}

init();
