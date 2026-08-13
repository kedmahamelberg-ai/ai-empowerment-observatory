"use strict";

const STORAGE_KEY =
  "aieo_positive_event_enrichment_v1";

const summaryEl =
  document.getElementById("summary");

const pairsEl =
  document.getElementById("pairs");

const progressEl =
  document.getElementById("progress");

const downloadBtn =
  document.getElementById("download");

const clearBtn =
  document.getElementById("clear");

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
  const done =
    Object.values(labels)
      .filter(Boolean)
      .length;

  progressEl.textContent =
    `${done} / ${payload.pairs.length} labelled`;
}

function articleCard(article, side) {
  const translated =
    article.english_headline &&
    article.english_headline !==
      article.original_headline;

  const snippet =
    article.snippet
      ? `
        <p class="snippet">
          ${escapeHTML(article.snippet)}
        </p>
      `
      : "";

  return `
    <section class="article">
      <span class="label">
        Article ${side}
      </span>

      <h2>
        ${escapeHTML(article.original_headline)}
      </h2>

      ${
        translated
          ? `
            <div class="translation">
              <span>English normalization</span>
              ${escapeHTML(article.english_headline)}
            </div>
          `
          : ""
      }

      ${snippet}

      <div class="article-footer">
        <span>
          ${escapeHTML(article.publisher)}
          · ${escapeHTML(
              String(
                article.source_language
              ).toUpperCase()
            )}
        </span>

        ${
          article.url
            ? `
              <a
                href="${escapeHTML(article.url)}"
                target="_blank"
                rel="noopener noreferrer"
              >
                Open source ↗
              </a>
            `
            : ""
        }
      </div>
    </section>
  `;
}

function render() {
  summaryEl.innerHTML = `
    <strong>${escapeHTML(payload.meta.sample_size)} positive-enriched pairs</strong>
    selected from ${escapeHTML(payload.meta.candidate_pool)}
    candidate pairs. Model predictions are hidden.
  `;

  pairsEl.innerHTML =
    payload.pairs
      .map((pair, index) => {
        const current =
          labels[
            pair.pair_id
          ] || "";

        const choices = [
          ["same_event", "Same event"],
          ["not_same_event", "Not same event"],
          [
            "unclear_from_headlines",
            "Unclear from headlines"
          ]
        ]
          .map(
            ([value, title]) => `
              <label class="choice ${
                current === value
                  ? "selected"
                  : ""
              }">
                <input
                  type="radio"
                  name="pair-${index}"
                  value="${value}"
                  data-pair-id="${escapeHTML(pair.pair_id)}"
                  ${current === value ? "checked" : ""}
                >
                ${title}
              </label>
            `
          )
          .join("");

        return `
          <article class="pair-card">
            <div class="pair-meta">
              <span>
                Pair ${index + 1}
              </span>

              <span>
                ${escapeHTML(pair.day_gap)} days apart
              </span>
            </div>

            <div class="article-grid">
              ${articleCard(pair.article_a, "A")}
              ${articleCard(pair.article_b, "B")}
            </div>

            <div class="choices">
              ${choices}
            </div>
          </article>
        `;
      })
      .join("");

  document
    .querySelectorAll(
      "input[type='radio']"
    )
    .forEach((input) => {
      input.addEventListener(
        "change",
        (event) => {
          labels[
            event.target.dataset.pairId
          ] = event.target.value;

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
    "pair_id",
    "human_label",
    "article_a_id",
    "article_b_id",
    "headline_a_original",
    "headline_a_english",
    "publisher_a",
    "headline_b_original",
    "headline_b_english",
    "publisher_b",
    "day_gap",
    "minilm_similarity",
    "modernbert_same_probability",
    "qwen_relationship",
    "qwen_confidence",
    "story_token_match",
    "cross_language",
    "selection_type"
  ]];

  for (const pair of payload.pairs) {
    rows.push([
      pair.pair_id,
      labels[pair.pair_id] || "",
      pair.article_a.article_id,
      pair.article_b.article_id,
      pair.article_a.original_headline,
      pair.article_a.english_headline,
      pair.article_a.publisher,
      pair.article_b.original_headline,
      pair.article_b.english_headline,
      pair.article_b.publisher,
      pair.day_gap,
      pair.minilm_similarity,
      pair.modernbert_same_probability,
      pair.qwen_relationship,
      pair.qwen_confidence,
      pair.story_token_match,
      pair.cross_language,
      pair.selection_type
    ]);
  }

  const csv =
    rows
      .map(
        row =>
          row
            .map(csvCell)
            .join(",")
      )
      .join("\n");

  const blob =
    new Blob(
      [csv],
      {
        type:
          "text/csv;charset=utf-8"
      }
    );

  const url =
    URL.createObjectURL(blob);

  const anchor =
    document.createElement("a");

  anchor.href = url;

  anchor.download =
    "aieo-model-agreed-positive-event-labels.csv";

  document.body.appendChild(anchor);

  anchor.click();

  anchor.remove();

  URL.revokeObjectURL(url);
}

async function init() {
  loadLabels();

  try {
    const response =
      await fetch(
        "./latest.json",
        { cache: "no-store" }
      );

    if (!response.ok) {
      throw new Error(
        `HTTP ${response.status}`
      );
    }

    payload =
      await response.json();

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

  } catch (error) {
    console.error(error);

    summaryEl.innerHTML = `
      <strong>No positive-enrichment sample yet.</strong>
      Run “Generate Model Agreed Positive Event Pairs” in GitHub Actions.
    `;
  }
}

init();
