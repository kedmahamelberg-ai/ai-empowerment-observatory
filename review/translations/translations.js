"use strict";

const summaryEl = document.getElementById("summary");
const listEl = document.getElementById("translations");
const searchEl = document.getElementById("search");
const filterEl = document.getElementById("filter");

let payload = null;

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function matches(row) {
  const mode = filterEl.value;
  if (mode === "review" && !row.requires_review) return false;
  if (mode === "fr" && row.source_language !== "fr") return false;
  if (mode === "zh" && row.source_language !== "zh") return false;
  if (mode === "unsupported" && row.status !== "unsupported") return false;

  const q = searchEl.value.trim().toLocaleLowerCase();
  if (!q) return true;

  return [
    row.original_headline,
    row.english_headline,
    row.publisher
  ].join(" ").toLocaleLowerCase().includes(q);
}

function render() {
  const rows = payload.translations.filter(matches);

  if (!rows.length) {
    listEl.innerHTML = '<div class="empty">No translations match this filter.</div>';
    return;
  }

  listEl.innerHTML = rows.map((row) => `
    <article class="translation-card">
      <div class="meta">
        <span>${escapeHTML(row.publisher)}</span>
        <span>${escapeHTML(row.source_language.toUpperCase())} → EN</span>
        <span>language confidence ${escapeHTML(row.detection_confidence)}</span>
        ${row.requires_review
          ? '<span class="review-pill">Review suggested</span>'
          : '<span class="ok-pill">Translated</span>'}
      </div>

      <div class="headline-grid">
        <section>
          <span class="label">Original</span>
          <h2>${escapeHTML(row.original_headline)}</h2>
        </section>

        <section>
          <span class="label">English normalization</span>
          <h2>${escapeHTML(row.english_headline)}</h2>
        </section>
      </div>

      <div class="footer">
        <span>
          ${escapeHTML(row.model_name || "No model")}
          ${row.model_revision ? ` · ${escapeHTML(row.model_revision.slice(0, 10))}` : ""}
        </span>
        ${row.review_reason
          ? `<span class="reason">${escapeHTML(row.review_reason)}</span>`
          : ""}
        ${row.url
          ? `<a href="${escapeHTML(row.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>`
          : ""}
      </div>
    </article>
  `).join("");
}

async function init() {
  try {
    const response = await fetch("./latest.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = await response.json();

    const m = payload.meta;
    summaryEl.innerHTML = `
      <strong>${escapeHTML(m.translated)} translated headlines</strong>
      · ${escapeHTML(m.passthrough)} English passthrough
      · ${escapeHTML(m.unsupported)} unsupported
      · ${escapeHTML(m.review)} review suggested.
      <br>
      <span>${escapeHTML(m.principle)}</span>
    `;

    render();
    searchEl.addEventListener("input", render);
    filterEl.addEventListener("change", render);
  } catch (error) {
    console.error(error);
    summaryEl.innerHTML = `
      <strong>No translation run exists yet.</strong>
      Run “Translate AI News to English” in GitHub Actions.
    `;
  }
}

init();
