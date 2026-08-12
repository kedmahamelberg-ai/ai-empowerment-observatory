"use strict";

const summaryEl = document.getElementById("summary");
const eventsEl = document.getElementById("events");
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

function renderSummary() {
  const m = payload.meta;
  summaryEl.innerHTML = `
    <strong>${escapeHTML(m.event_count)} provisional events</strong>
    from ${escapeHTML(m.article_count)} articles.
    ${escapeHTML(m.multi_article_event_count)} events contain multiple articles;
    ${escapeHTML(m.review_required_count)} are flagged for review.
    <br>
    <span>
      Model: ${escapeHTML(m.model_name)} · threshold
      ${escapeHTML(m.similarity_threshold)} · max date span
      ${escapeHTML(m.max_day_gap)} days.
    </span>
  `;
}

function passesFilter(event) {
  const mode = filterEl.value;
  if (mode === "multi") return event.article_count > 1;
  if (mode === "review") return event.requires_review;
  if (mode === "single") return event.article_count === 1;
  return true;
}

function passesSearch(event) {
  const q = searchEl.value.trim().toLocaleLowerCase();
  if (!q) return true;
  const text = [
    event.event_title,
    ...event.articles.flatMap((article) => [
      article.headline,
      article.publisher
    ])
  ].join(" ").toLocaleLowerCase();
  return text.includes(q);
}

function render() {
  const events = payload.events.filter(
    (event) => passesFilter(event) && passesSearch(event)
  );

  if (!events.length) {
    eventsEl.innerHTML =
      '<div class="empty">No clusters match the current filters.</div>';
    return;
  }

  eventsEl.innerHTML = events.map((event) => {
    const review = event.requires_review
      ? `<span class="pill pill-review">Review suggested</span>`
      : `<span class="pill">Provisional</span>`;

    const stats = event.article_count > 1
      ? `
        <span>avg sim ${escapeHTML(event.average_similarity)}</span>
        <span>min sim ${escapeHTML(event.minimum_similarity)}</span>
      `
      : `<span>singleton</span>`;

    const articles = event.articles.map((article) => `
      <li class="${article.canonical ? "canonical" : ""}">
        <div>
          <strong>${escapeHTML(article.headline)}</strong>
          <span>
            ${escapeHTML(article.publisher)}
            · rank ${escapeHTML(article.search_rank)}
            ${article.similarity_to_canonical !== null
              ? `· sim ${escapeHTML(article.similarity_to_canonical)}`
              : ""}
          </span>
        </div>
        ${article.canonical ? '<em>canonical</em>' : ""}
      </li>
    `).join("");

    return `
      <article class="event-card">
        <div class="event-topline">
          <div class="pills">
            ${review}
            <span class="pill">${escapeHTML(event.article_count)} article${event.article_count === 1 ? "" : "s"}</span>
          </div>
          <div class="stats">${stats}</div>
        </div>

        <h2>${escapeHTML(event.event_title)}</h2>

        ${event.review_reason
          ? `<p class="review-reason">${escapeHTML(event.review_reason)}</p>`
          : ""}

        <ol>${articles}</ol>
      </article>
    `;
  }).join("");
}

async function init() {
  try {
    const response = await fetch("./latest.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = await response.json();
    renderSummary();
    render();
    searchEl.addEventListener("input", render);
    filterEl.addEventListener("change", render);
  } catch (error) {
    console.error(error);
    summaryEl.innerHTML = `
      <strong>No event-clustering review exists yet.</strong>
      Run the “Cluster AI News Events” workflow in GitHub Actions.
    `;
    eventsEl.innerHTML = `<div class="empty">${escapeHTML(error.message)}</div>`;
  }
}

init();
