"use strict";

const summaryEl = document.getElementById("summary");
const listEl = document.getElementById("translations");
const searchEl = document.getElementById("search");
const filterEl = document.getElementById("filter");
let payload = null;

function esc(v) {
  return String(v ?? "")
    .replaceAll("&","&amp;").replaceAll("<","&lt;")
    .replaceAll(">","&gt;").replaceAll('"',"&quot;")
    .replaceAll("'","&#039;");
}

function keep(row) {
  const mode = filterEl.value;
  if (mode === "review" && !row.requires_review) return false;
  if (mode === "fr" && row.source_language !== "fr") return false;
  if (mode === "zh" && row.source_language !== "zh") return false;
  if (mode === "unsupported" && row.status !== "unsupported") return false;
  const q = searchEl.value.trim().toLocaleLowerCase();
  if (!q) return true;
  return [
    row.original_headline, row.english_headline,
    row.auditor_translation, row.publisher
  ].join(" ").toLocaleLowerCase().includes(q);
}

function render() {
  const rows = payload.translations.filter(keep);
  if (!rows.length) {
    listEl.innerHTML = '<div class="empty">No translations match this filter.</div>';
    return;
  }

  listEl.innerHTML = rows.map(row => {
    const audit = row.auditor_translation ? `
      <section>
        <span class="label">Chinese audit translation</span>
        <h2>${esc(row.auditor_translation)}</h2>
        <div class="audit-meta">
          ${esc(row.auditor_model || "")}
          · agreement ${esc(row.audit_agreement_score)}
          · ${esc(row.audit_status || "")}
        </div>
      </section>` : "";

    return `
      <article class="translation-card">
        <div class="meta">
          <span>${esc(row.publisher)}</span>
          <span>${esc(row.source_language.toUpperCase())} → EN</span>
          ${row.requires_review
            ? '<span class="review-pill">Review suggested</span>'
            : '<span class="ok-pill">Ready</span>'}
        </div>

        <div class="headline-grid ${row.auditor_translation ? "three-column" : ""}">
          <section>
            <span class="label">Original</span>
            <h2>${esc(row.original_headline)}</h2>
          </section>
          <section>
            <span class="label">Primary English normalization</span>
            <h2>${esc(row.english_headline)}</h2>
            <div class="audit-meta">${esc(row.primary_model || "passthrough")}</div>
          </section>
          ${audit}
        </div>

        <div class="footer">
          ${row.audit_reason ? `<span class="reason">${esc(row.audit_reason)}</span>` : ""}
          ${row.review_reason ? `<span class="reason">${esc(row.review_reason)}</span>` : ""}
          ${row.url ? `<a href="${esc(row.url)}" target="_blank" rel="noopener noreferrer">Open source ↗</a>` : ""}
        </div>
      </article>`;
  }).join("");
}

async function init() {
  try {
    const response = await fetch("./latest.json", {cache:"no-store"});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    payload = await response.json();
    const m = payload.meta;
    summaryEl.innerHTML = `
      <strong>${esc(m.translated)} translated headlines</strong>
      · ${esc(m.passthrough)} English passthrough
      · ${esc(m.unsupported)} unsupported
      · ${esc(m.audited)} Chinese audits
      · ${esc(m.audit_disagreement)} audit disagreements
      · ${esc(m.review)} review suggested.
      <br><span>${esc(m.principle)}</span>`;
    render();
    searchEl.addEventListener("input", render);
    filterEl.addEventListener("change", render);
  } catch (error) {
    console.error(error);
    summaryEl.innerHTML = "<strong>No validated translation run yet.</strong>";
  }
}
init();
