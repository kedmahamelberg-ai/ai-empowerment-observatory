"use strict";

const summaryEl =
  document.getElementById("summary");

const pairsEl =
  document.getElementById("pairs");

let payload = null;

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function metric(name, data) {
  if (!data) {
    return "";
  }

  return `
    <div class="metric">
      <strong>${escapeHTML(name)}</strong>
      <span>
        P ${escapeHTML(data.precision)}
        · R ${escapeHTML(data.recall)}
        · F1 ${escapeHTML(data.f1)}
        · Acc ${escapeHTML(data.accuracy)}
      </span>
    </div>
  `;
}

function humanClass(label) {
  if (label === "same_event") {
    return "same";
  }

  if (label === "related_topic") {
    return "related";
  }

  return "different";
}

function renderSummary() {
  const s = payload.summary;

  summaryEl.innerHTML = `
    <div class="summary-top">
      <strong>${escapeHTML(s.gold_pairs)} human-labelled pairs</strong>
      · ${escapeHTML(s.gold_same_event)} same-event
      · ${escapeHTML(s.gold_not_same)} not-same.
    </div>

    <div class="metric-grid">
      ${metric("ModernBERT", s.modernbert_raw)}
      ${metric("ModernBERT + date", s.modernbert_date_adjusted)}
      ${metric("Qwen3-4B", s.qwen_binary)}
      ${metric("Agreement rule", s.ensemble_auto_metrics)}
    </div>

    <div class="summary-note">
      Qwen 3-class accuracy:
      <strong>${escapeHTML(s.qwen_three_class_accuracy)}</strong>
      · Agreement rule auto-coverage:
      <strong>${escapeHTML(s.ensemble_coverage)}</strong>
      · Human-review pairs:
      <strong>${escapeHTML(s.ensemble_review_count)}</strong>
    </div>
  `;
}

function renderPairs() {
  pairsEl.innerHTML = payload.pairs
    .map((pair) => {
      const cls =
        humanClass(pair.human_label);

      return `
        <article class="pair-card">
          <div class="topline">
            <span class="pill ${cls}">
              Human: ${escapeHTML(pair.human_label)}
            </span>

            <span>
              MiniLM ${escapeHTML(pair.embedding_similarity)}
              · ${escapeHTML(pair.day_gap)} day gap
              · story token ${pair.story_token_match ? "match" : "no match"}
            </span>
          </div>

          <div class="article-grid">
            <section>
              <span class="label">Article A</span>
              <h2>${escapeHTML(pair.original_a)}</h2>

              ${
                pair.english_a !== pair.original_a
                  ? `
                    <p>
                      <strong>EN:</strong>
                      ${escapeHTML(pair.english_a)}
                    </p>
                  `
                  : ""
              }

              <small>${escapeHTML(pair.publisher_a)}</small>
            </section>

            <section>
              <span class="label">Article B</span>
              <h2>${escapeHTML(pair.original_b)}</h2>

              ${
                pair.english_b !== pair.original_b
                  ? `
                    <p>
                      <strong>EN:</strong>
                      ${escapeHTML(pair.english_b)}
                    </p>
                  `
                  : ""
              }

              <small>${escapeHTML(pair.publisher_b)}</small>
            </section>
          </div>

          <div class="model-grid">
            <section>
              <span class="label">ModernBERT + date</span>

              <strong>
                ${escapeHTML(pair.modernbert_date_label)}
              </strong>

              <p>
                same-event probability:
                ${escapeHTML(pair.modernbert_same_probability_date_adjusted)}
              </p>
            </section>

            <section>
              <span class="label">Qwen3-4B</span>

              <strong>
                ${escapeHTML(pair.qwen_relationship)}
              </strong>

              <p>
                confidence ${escapeHTML(pair.qwen_confidence)}
              </p>

              <p class="reason">
                ${escapeHTML(pair.qwen_reason)}
              </p>
            </section>

            <section class="${
              pair.ensemble_decision === "review"
                ? "needs-review"
                : ""
            }">
              <span class="label">Agreement rule</span>

              <strong>
                ${escapeHTML(pair.ensemble_decision)}
              </strong>
            </section>
          </div>
        </article>
      `;
    })
    .join("");
}

async function init() {
  try {
    const response = await fetch(
      "./latest.json",
      { cache: "no-store" }
    );

    if (!response.ok) {
      throw new Error(
        `HTTP ${response.status}`
      );
    }

    payload = await response.json();

    renderSummary();
    renderPairs();

  } catch (error) {
    console.error(error);

    summaryEl.innerHTML = `
      <strong>No benchmark result yet.</strong>
      Run “Benchmark Same Event Verifiers” in GitHub Actions.
    `;
  }
}

init();
