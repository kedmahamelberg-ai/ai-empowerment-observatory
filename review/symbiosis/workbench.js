"use strict";

const DATA = window.AIEO_SYMBIOSIS_PAYLOAD;
const STORAGE_KEY = `aieo_symbiosis_reviews_${DATA.codebook_version}`;
const HUMAN_TYPES = ["extension", "expansion", "restriction", "reduction", "neutral", "unclear"];
const AI_ROLES = ["ai_extension", "ai_expansion", "ai_restriction", "ai_reduction", "neutral", "unclear"];
const EVIDENCE = ["sufficient", "partial", "insufficient"];
const EMPOWERMENT = ["expanding", "contracting", "mixed", "non_empowerment", "unclear"];
const HUMAN_DIR = {extension:"enabling", expansion:"enabling", restriction:"constraining", reduction:"constraining", neutral:"neutral", unclear:"unclear"};
const AI_DIR = {ai_extension:"enabling", ai_expansion:"enabling", ai_restriction:"constraining", ai_reduction:"constraining", neutral:"neutral", unclear:"unclear"};
const LABELS = {
  mutualism:"Both people and the AI side gain (mutualism)",
  ai_benefiting_parasitism:"The AI or operator side gains while people are constrained (AI-benefiting parasitism)",
  human_benefiting_parasitism:"People gain while the AI system is constrained (human-benefiting parasitism)",
  competition:"Both people and the AI side are constrained (competition or co-constraint)",
  human_enabling_only:"Human-side enabling signal only",
  human_constraining_only:"Human-side constraining signal only",
  ai_enabling_only:"AI-side enabling signal only",
  ai_constraining_only:"AI-side constraining signal only",
  no_clear_relational_signal:"No clear human-AI relationship signal",
  ambiguous_relational_signal:"Ambiguous relationship signal",
  insufficient_evidence:"Insufficient evidence"
};

let reviews = {};
try { reviews = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}"); } catch { reviews = {}; }
for (const item of DATA.items) {
  if (item.existing_review && !reviews[item.unit_key]) reviews[item.unit_key] = item.existing_review;
}

const esc = value => String(value ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;");
const options = (values, selected) => values.map(value => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(value.replaceAll("_"," "))}</option>`).join("");

function derive(humanType, aiRole, evidenceStatus) {
  const h = HUMAN_DIR[humanType];
  const a = AI_DIR[aiRole];
  if (evidenceStatus === "insufficient") return "insufficient_evidence";
  if (h === "unclear" || a === "unclear") return "ambiguous_relational_signal";
  if (h === "enabling" && a === "enabling") return "mutualism";
  if (h === "constraining" && a === "enabling") return "ai_benefiting_parasitism";
  if (h === "enabling" && a === "constraining") return "human_benefiting_parasitism";
  if (h === "constraining" && a === "constraining") return "competition";
  if (h === "enabling" && a === "neutral") return "human_enabling_only";
  if (h === "constraining" && a === "neutral") return "human_constraining_only";
  if (h === "neutral" && a === "enabling") return "ai_enabling_only";
  if (h === "neutral" && a === "constraining") return "ai_constraining_only";
  return "no_clear_relational_signal";
}

function save() {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(reviews));
  renderSummary();
}

function finalFor(item) {
  const saved = reviews[item.unit_key];
  if (saved) return saved.final;
  return {
    human_experience_type: item.model.human_experience_type,
    ai_expressive_role: item.model.ai_expressive_role,
    evidence_status: item.model.evidence_status,
    story_country_iso3s: item.model.country_iso3s || [],
    evidence_summary: item.model.summary || "",
    reasoning: item.model.summary || "",
    empowerment_status: item.empowerment_model?.empowerment_status || "unclear",
    empowerment_degree: item.empowerment_model?.empowerment_degree ?? 0,
    empowerment_reasoning: item.empowerment_model?.reasoning || ""
  };
}

function visibleItems() {
  const lens = document.getElementById("filter-lens").value;
  const status = document.getElementById("filter-status").value;
  const config = document.getElementById("filter-config").value;
  const query = document.getElementById("filter-text").value.trim().toLowerCase();
  return DATA.items.filter(item => {
    const review = reviews[item.unit_key];
    const final = finalFor(item);
    const derived = derive(final.human_experience_type, final.ai_expressive_role, final.evidence_status);
    if (lens !== "all" && item.lens !== lens) return false;
    if (status === "pending" && review) return false;
    if (status === "reviewed" && !review) return false;
    if (config !== "all" && derived !== config) return false;
    if (query && !`${item.title} ${item.sources.map(s => s.publisher).join(" ")}`.toLowerCase().includes(query)) return false;
    return true;
  });
}

function renderSummary() {
  const reviewed = Object.keys(reviews).filter(key => DATA.items.some(item => item.unit_key === key)).length;
  const eventTotal = DATA.items.filter(item => item.lens === "event").length;
  const eventReviewed = DATA.items.filter(item => item.lens === "event" && reviews[item.unit_key]).length;
  const coverageTotal = DATA.items.filter(item => item.lens === "coverage").length;
  const coverageReviewed = DATA.items.filter(item => item.lens === "coverage" && reviews[item.unit_key]).length;
  document.getElementById("summary").innerHTML = `
    <article><strong>${DATA.items.length}</strong><span>classifications in queue</span></article>
    <article><strong>${reviewed}</strong><span>explicitly reviewed</span></article>
    <article><strong>${eventReviewed}/${eventTotal}</strong><span>development review</span></article>
    <article><strong>${coverageReviewed}/${coverageTotal}</strong><span>coverage-item review</span></article>
    <article><strong>${DATA.items.length - reviewed}</strong><span>still pending</span></article>`;
  document.getElementById("progress").textContent = `${reviewed} of ${DATA.items.length} reviewed`;
}

function sourceLinks(item) {
  return item.sources.map(source => `<a href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(source.publisher || "Open source")}</a>`).join("");
}

function card(item) {
  const review = reviews[item.unit_key];
  const final = finalFor(item);
  const derived = derive(final.human_experience_type, final.ai_expressive_role, final.evidence_status);
  return `<article class="card" data-unit="${esc(item.unit_key)}">
    <div class="card-header">
      <div class="badges">
        <span class="badge">${esc(item.release_id || "release")}</span>
        <span class="badge">${esc(item.lens)} lens</span>
        <span class="badge">${esc(item.content_basis.replaceAll("_"," "))}</span>
        <span class="badge ${review ? "reviewed" : "pending"}">${review ? esc(review.review_status) : "pending human review"}</span>
        <span class="badge">${esc(LABELS[derived])}</span>
      </div>
      <h2>${esc(item.title)}</h2>
      <p class="release-scope">Weekly scope: ${esc(item.period_start || "?")} to ${esc(item.period_end || "?")}</p>
      <div class="sources">${sourceLinks(item)}</div>
    </div>
    <div class="card-body">
      <div class="model-grid">
        <section class="panel">
          <h3>Model: human side</h3>
          <p><strong>${esc(item.model.human_experience_type)}</strong></p>
          <p>${esc(item.model.human_reasoning)}</p>
        </section>
        <section class="panel">
          <h3>Model: AI or operator side</h3>
          <p><strong>${esc(item.model.ai_expressive_role)}</strong></p>
          <p>${esc(item.model.ai_reasoning)}</p>
        </section>
      </div>
      <section class="panel">
        <h3>Evidence supplied to the model</h3>
        <div class="evidence">${esc(item.evidence)}</div>
      </section>
      <div class="edit-grid">
        <label>Human-side type
          <select data-field="human_experience_type">${options(HUMAN_TYPES, final.human_experience_type)}</select>
          <small>Extension or expansion enables people. Restriction or reduction constrains people.</small>
        </label>
        <label>AI-side expressive role
          <select data-field="ai_expressive_role">${options(AI_ROLES, final.ai_expressive_role)}</select>
          <small>This is how the source represents the system or operator side, not a claim of AI consciousness.</small>
        </label>
        <label>Evidence status
          <select data-field="evidence_status">${options(EVIDENCE, final.evidence_status)}</select>
          <small>Do not use a core configuration when the source evidence is insufficient.</small>
        </label>
        <label>Derived relationship pattern
          <input type="text" data-derived value="${esc(LABELS[derived])}" readonly>
        </label>
        <label>Secondary empowerment status
          <select data-field="empowerment_status">${options(EMPOWERMENT, final.empowerment_status)}</select>
        </label>
        <label>Secondary empowerment degree
          <input type="number" min="0" max="3" data-field="empowerment_degree" value="${esc(final.empowerment_degree)}">
        </label>
        <label>Story country ISO3 codes
          <input type="text" data-field="story_country_iso3s" value="${esc((final.story_country_iso3s || []).join(", "))}" placeholder="USA, GBR">
          <small>Leave blank unless the source evidence establishes the story location.</small>
        </label>
        <label>Reviewer note
          <input type="text" data-field="notes" value="${esc(review?.notes || "")}" placeholder="Optional governance note">
        </label>
      </div>
      <label>Evidence summary
        <textarea data-field="evidence_summary">${esc(final.evidence_summary || "")}</textarea>
        <small>Summarise only what the source supports. Do not copy long source passages.</small>
      </label>
      <label>Why this relationship pattern
        <textarea data-field="reasoning">${esc(final.reasoning || "")}</textarea>
      </label>
      <label>Why this empowerment status, if retained
        <textarea data-field="empowerment_reasoning">${esc(final.empowerment_reasoning || "")}</textarea>
      </label>
      <div class="review-actions">
        <button type="button" data-action="accept">Accept model</button>
        <button type="button" data-action="correct">Save correction</button>
        <button type="button" data-action="insufficient">Mark insufficient evidence</button>
        <button type="button" data-action="clear">Clear review</button>
      </div>
    </div>
  </article>`;
}

function collectFinal(cardEl) {
  const value = field => cardEl.querySelector(`[data-field="${field}"]`).value;
  return {
    human_experience_type: value("human_experience_type"),
    ai_expressive_role: value("ai_expressive_role"),
    evidence_status: value("evidence_status"),
    story_country_iso3s: value("story_country_iso3s").split(",").map(v => v.trim().toUpperCase()).filter(Boolean),
    evidence_summary: value("evidence_summary").trim(),
    reasoning: value("reasoning").trim(),
    empowerment_status: value("empowerment_status"),
    empowerment_degree: Number(value("empowerment_degree") || 0),
    empowerment_reasoning: value("empowerment_reasoning").trim()
  };
}

function renderQueue() {
  const items = visibleItems();
  const queue = document.getElementById("queue");
  queue.innerHTML = items.length ? items.map(card).join("") : '<div class="empty">No items match these filters.</div>';
  queue.querySelectorAll("article[data-unit]").forEach(cardEl => {
    const unitKey = cardEl.dataset.unit;
    const item = DATA.items.find(row => row.unit_key === unitKey);
    cardEl.querySelectorAll("select[data-field]").forEach(select => select.addEventListener("change", () => {
      const final = collectFinal(cardEl);
      const config = derive(final.human_experience_type, final.ai_expressive_role, final.evidence_status);
      cardEl.querySelector("[data-derived]").value = LABELS[config];
    }));
    cardEl.querySelectorAll("button[data-action]").forEach(button => button.addEventListener("click", () => {
      const action = button.dataset.action;
      if (action === "clear") {
        delete reviews[unitKey];
      } else {
        let final;
        let reviewStatus;
        if (action === "accept") {
          final = {
            human_experience_type: item.model.human_experience_type,
            ai_expressive_role: item.model.ai_expressive_role,
            evidence_status: item.model.evidence_status,
            story_country_iso3s: item.model.country_iso3s || [],
            evidence_summary: item.model.summary || "",
            reasoning: item.model.summary || "",
            empowerment_status: item.empowerment_model?.empowerment_status || "unclear",
            empowerment_degree: item.empowerment_model?.empowerment_degree ?? 0,
            empowerment_reasoning: item.empowerment_model?.reasoning || ""
          };
          reviewStatus = "accepted";
        } else {
          final = collectFinal(cardEl);
          if (action === "insufficient") {
            final.evidence_status = "insufficient";
            final.human_experience_type = "unclear";
            final.ai_expressive_role = "unclear";
            reviewStatus = "insufficient_evidence";
          } else {
            reviewStatus = "corrected";
          }
        }
        reviews[unitKey] = {
          decision_id: `symbiosis-${unitKey.replaceAll(":", "-")}-v1`,
          release_id: item.release_id,
          lens: item.lens,
          unit_key: unitKey,
          article_id: item.article_id || null,
          event_id: item.event_id || null,
          review_status: reviewStatus,
          reviewer_name: DATA.default_reviewer || "Kedma Hamelberg",
          notes: cardEl.querySelector('[data-field="notes"]').value.trim(),
          source_urls: item.sources.map(source => source.url).filter(Boolean),
          final
        };
      }
      save();
      renderQueue();
    }));
  });
  renderSummary();
}


function importDecisions(file) {
  const reader = new FileReader();
  reader.addEventListener("load", () => {
    try {
      const payload = JSON.parse(String(reader.result || ""));
      if (payload.codebook_version !== DATA.codebook_version) {
        throw new Error(`Decision codebook ${payload.codebook_version || "missing"} does not match ${DATA.codebook_version}.`);
      }
      if (!Array.isArray(payload.decisions)) {
        throw new Error("The imported file does not contain a decisions array.");
      }
      let imported = 0;
      for (const decision of payload.decisions) {
        const unitKey = String(decision?.unit_key || "");
        if (!unitKey || !DATA.items.some(item => item.unit_key === unitKey)) continue;
        reviews[unitKey] = decision;
        imported += 1;
      }
      save();
      renderQueue();
      alert(`${imported} reviewed decisions were imported.`);
    } catch (error) {
      alert(`The decision file could not be imported: ${error.message}`);
    }
  });
  reader.readAsText(file);
}

function downloadDecisions() {
  const decisions = DATA.items.map(item => reviews[item.unit_key]).filter(Boolean);
  const payload = {
    schema_version: "aieo_symbiosis_reviews_v1",
    codebook_version: DATA.codebook_version,
    generated_at: new Date().toISOString(),
    reviewer_name: DATA.default_reviewer || "Kedma Hamelberg",
    expected_unit_count: DATA.items.length,
    reviewed_unit_count: decisions.length,
    decisions
  };
  const blob = new Blob([JSON.stringify(payload, null, 2) + "\n"], {type:"application/json"});
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "symbiosis-reviewed-decisions.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function init() {
  document.getElementById("download").addEventListener("click", downloadDecisions);
  const importInput = document.getElementById("import-file");
  document.getElementById("import").addEventListener("click", () => importInput.click());
  importInput.addEventListener("change", () => {
    const [file] = importInput.files || [];
    if (file) importDecisions(file);
    importInput.value = "";
  });
  document.getElementById("clear-all").addEventListener("click", () => {
    if (!confirm("Clear all locally stored review decisions for this codebook?")) return;
    reviews = {};
    save();
    renderQueue();
  });
  ["filter-lens","filter-status","filter-config","filter-text"].forEach(id => {
    document.getElementById(id).addEventListener(id === "filter-text" ? "input" : "change", renderQueue);
  });
  renderQueue();
}

init();
