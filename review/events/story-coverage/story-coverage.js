"use strict";

const STORAGE_KEY =
  "aieo_story_coverage_labels_v1";

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
        ${escapeHTML(article.title)}
      </h2>

      ${snippet}

      <div class="article-footer">
        <span>
          ${escapeHTML(article.source)}
          ${
            article.iso_date
              ? ` · ${escapeHTML(article.iso_date)}`
              : ""
          }
        </span>

        <a
          href="${escapeHTML(article.link)}"
          target="_blank"
          rel="noopener noreferrer"
        >
          Open source ↗
        </a>
      </div>
    </section>
  `;
}

function render() {
  summaryEl.innerHTML = `
    <strong>${escapeHTML(payload.meta.pair_count)} candidate pairs</strong>
    from ${escapeHTML(payload.meta.usable_story_groups)}
    multi-source Google News story groups.
    The grouping signal is hidden during annotation.
  `;

  pairsEl.innerHTML =
    payload.pairs
      .map((pair, index) => {
        const current =
          labels[pair.pair_id] || "";

        const options = [
          ["same_event", "Same event"],
          ["not_same_event", "Not same event"],
          [
            "unclear_from_headlines",
            "Unclear from headlines"
          ]
        ];

        const choices =
          options
            .map(([value, title]) => `
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
            `)
            .join("");

        return `
          <article class="pair-card">
            <div class="pair-meta">
              Pair ${index + 1}
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
    "headline_a",
    "publisher_a",
    "url_a",
    "headline_b",
    "publisher_b",
    "url_b",
    "story_token",
    "search_country",
    "search_iso3",
    "search_language",
    "coverage_size",
    "seed_title"
  ]];

  for (const pair of payload.pairs) {
    rows.push([
      pair.pair_id,
      labels[pair.pair_id] || "",
      pair.article_a.title,
      pair.article_a.source,
      pair.article_a.link,
      pair.article_b.title,
      pair.article_b.source,
      pair.article_b.link,
      pair.story_token,
      pair.search_country,
      pair.search_iso3,
      pair.search_language,
      pair.coverage_size,
      pair.seed_title
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
    "aieo-story-coverage-event-labels.csv";

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
      <strong>No story-coverage sample yet.</strong>
      Run “Generate Story Coverage Event Pairs” in GitHub Actions.
    `;
  }
}

init();
