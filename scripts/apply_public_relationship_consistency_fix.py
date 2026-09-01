#!/usr/bin/env python3
"""Apply the 2026-09-01 public relationship consistency fix in-place.

This patch intentionally changes only presentation/validation code. It does not
rewrite weekly data, event history, collection data, or classification outputs.
"""
from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def replace_between(text: str, start_marker: str, end_marker: str, replacement: str, label: str) -> str:
    start = text.find(start_marker)
    if start < 0:
        raise RuntimeError(f"{label}: start marker not found: {start_marker!r}")
    end = text.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{label}: end marker not found: {end_marker!r}")
    return text[:start] + replacement.rstrip() + "\n" + text[end:]


def write_if_changed(path: Path, new_text: str) -> bool:
    old = path.read_text(encoding="utf-8")
    if old == new_text:
        print(f"unchanged {path.relative_to(ROOT)}")
        return False
    path.write_text(new_text, encoding="utf-8")
    print(f"updated   {path.relative_to(ROOT)}")
    return True


EDU_RELATIONSHIP = r'''function relationshipSummaryData() {
  const event = currentSymbiosis?.event || {};
  const counts = event.display_configuration_counts || event.configuration_counts || {};
  const classified = Number(event.display_classified_units ?? event.classified_units ?? 0);
  const expected = Number(event.expected_units || classified || 0);
  const complete = Number(event.display_complete_configuration_count ?? event.complete_configuration_count ?? 0);
  const partial = Number(event.display_partial_signal_count ?? event.partial_signal_count ?? 0);
  const noClear = Number(event.display_no_clear_relational_signal_count ?? event.no_clear_relational_signal_count ?? 0);
  const ambiguous = Number(event.display_ambiguous_relational_signal_count ?? event.ambiguous_relational_signal_count ?? 0);
  const insufficient = Number(event.display_insufficient_evidence_count ?? event.insufficient_evidence_count ?? 0);
  return { event, counts, classified, expected, complete, partial, noClear, ambiguous, insufficient };
}

function joinCountPhrases(items) {
  const values = items.filter(Boolean);
  if (!values.length) return "";
  if (values.length === 1) return values[0];
  if (values.length === 2) return `${values[0]} and ${values[1]}`;
  return `${values.slice(0, -1).join(", ")}, and ${values.at(-1)}`;
}

function relationshipOutsideCopy(data) {
  const outside = Math.max(0, data.expected - data.complete);
  const phrases = [];
  if (data.partial) phrases.push(`${data.partial} one-sided ${plural(data.partial, "signal")}`);
  if (data.noClear) phrases.push(`${data.noClear} no-clear ${plural(data.noClear, "case")}`);
  if (data.ambiguous) phrases.push(`${data.ambiguous} ambiguous ${plural(data.ambiguous, "case")}`);
  if (data.insufficient) phrases.push(`${data.insufficient} insufficient-evidence ${plural(data.insufficient, "case")}`);
  const accounted = data.complete + data.partial + data.noClear + data.ambiguous + data.insufficient;
  const remainder = Math.max(0, data.expected - accounted);
  if (remainder) phrases.push(`${remainder} other unclassified ${plural(remainder, "case")}`);
  if (!outside) return "All current-week developments fall within the four two-sided relationship patterns.";
  return `${outside} ${plural(outside, "development was", "developments were")} outside the four-pattern denominator${phrases.length ? `: ${joinCountPhrases(phrases)}` : ""}.`;
}

function renderRelationship() {
  const core = document.getElementById("relationship-core");
  const data = relationshipSummaryData();
  const ready = Boolean(currentSymbiosis) && data.classified > 0 && (!data.expected || data.classified >= data.expected);
  if (!ready) {
    if (core) core.hidden = true;
    setText("relationship-scope", "Relationship signal is being prepared for this release");
    setText("relationship-other-summary", "Weekly counts and source evidence remain available.");
    return;
  }
  if (core) core.hidden = false;
  const entries = [
    ["mutualism", "relationship-mutualism", "relationship-mutualism-share"],
    ["ai_benefiting_parasitism", "relationship-ai-benefit", "relationship-ai-benefit-share"],
    ["human_benefiting_parasitism", "relationship-human-benefit", "relationship-human-benefit-share"],
    ["competition", "relationship-competition", "relationship-competition-share"],
  ];
  entries.forEach(([key, countId, shareId]) => {
    const count = Number(data.counts[key] || 0);
    setText(countId, count);
    setText(shareId, data.complete ? `${((count / data.complete) * 100).toFixed(0)}%` : "0%");
  });
  setText("relationship-scope", `${data.complete} of ${data.expected} developments had directional evidence for both sides`);
  setText("relationship-other-summary", relationshipOutsideCopy(data));
}
'''

EDU_HUMAN = r'''function renderEmpowerment() {
  const container = document.getElementById("human-bars");
  const data = relationshipSummaryData();
  const ready = Boolean(currentSymbiosis) && data.classified > 0 && (!data.expected || data.classified >= data.expected);
  if (!ready) {
    setText("human-plain-label", "Signal being prepared");
    setText("human-index", "The people-side view will appear with the relationship signal.");
    setText("human-denominator", "Relationship evidence is being prepared");
    if (container) container.innerHTML = "";
    return;
  }
  const counts = data.counts;
  const enabling = Number(counts.mutualism || 0)
    + Number(counts.human_benefiting_parasitism || 0)
    + Number(counts.human_enabling_only || 0);
  const constraining = Number(counts.ai_benefiting_parasitism || 0)
    + Number(counts.competition || 0)
    + Number(counts.human_constraining_only || 0);
  const noDirect = Number(counts.ai_enabling_only || 0)
    + Number(counts.ai_constraining_only || 0)
    + Number(counts.no_clear_relational_signal || 0);
  let uncertain = Number(counts.ambiguous_relational_signal || 0)
    + Number(counts.insufficient_evidence || 0);
  const accounted = enabling + constraining + noDirect + uncertain;
  if (data.expected > accounted) uncertain += data.expected - accounted;
  const directional = enabling + constraining;

  setText("human-denominator", `${data.expected} current-week developments`);
  setText("human-plain-label", `${enabling} gain ${plural(enabling, "signal")} · ${constraining} constraint ${plural(constraining, "signal")}`);
  setText("human-index", `${directional} had a directional people-side signal · ${noDirect} had no direct people-side signal · ${uncertain} insufficient or unclear`);

  const labels = [
    ["Gain / enabling", enabling],
    ["Constraint", constraining],
    ["No direct people-side signal", noDirect],
    ["Insufficient / unclear", uncertain],
  ];
  if (container) {
    container.innerHTML = labels.map(([label, count]) => {
      const pct = data.expected ? (Number(count) / data.expected) * 100 : 0;
      return `<div class="bar-row"><span>${label}</span><div class="bar-track"><i style="width:${pct}%"></i></div><strong>${count}</strong></div>`;
    }).join("");
  }
}
'''

ROOT_RELATIONSHIP = r'''function relationshipOutsideCopy(total, complete, partial, noClear, ambiguous, insufficient) {
  const outside = Math.max(0, total - complete);
  const phrases = [];
  if (partial) phrases.push(`${partial} one-sided ${plural(partial, "signal")}`);
  if (noClear) phrases.push(`${noClear} no-clear ${plural(noClear, "case")}`);
  if (ambiguous) phrases.push(`${ambiguous} ambiguous ${plural(ambiguous, "case")}`);
  if (insufficient) phrases.push(`${insufficient} insufficient-evidence ${plural(insufficient, "case")}`);
  const accounted = complete + partial + noClear + ambiguous + insufficient;
  const remainder = Math.max(0, total - accounted);
  if (remainder) phrases.push(`${remainder} other unclassified ${plural(remainder, "case")}`);
  let tail = "";
  if (phrases.length === 1) tail = phrases[0];
  else if (phrases.length === 2) tail = `${phrases[0]} and ${phrases[1]}`;
  else if (phrases.length > 2) tail = `${phrases.slice(0, -1).join(", ")}, and ${phrases.at(-1)}`;
  if (!outside) return "All current-week developments fall within the four two-sided relationship patterns.";
  return `${outside} ${plural(outside, "development was", "developments were")} outside the four-pattern denominator${tail ? `: ${tail}` : ""}.`;
}

function renderRelationship(symbiosis, error = null, releaseId = null) {
  const ticker = document.getElementById("relationship-ticker");
  if (!ticker) return;
  if (error) {
    ticker.innerHTML = '<p class="loading-line data-error">The relationship signal is temporarily unavailable.</p>';
    setText("relationship-denominator", "Signal unavailable");
    setText("relationship-other-summary", "Weekly counts and source evidence remain available.");
    return;
  }
  const sameRelease = !releaseId || String(symbiosis?.release_id || "") === String(releaseId);
  if (!symbiosis || !sameRelease) {
    ticker.innerHTML = '<p class="loading-line">The relationship signal is being prepared for this release.</p>';
    setText("relationship-denominator", "Relationship signal being prepared");
    setText("relationship-other-summary", "Weekly counts and source evidence remain available.");
    return;
  }
  const event = symbiosis.event || {};
  const counts = event.display_configuration_counts || event.configuration_counts || {};
  const classified = Number(event.display_classified_units ?? event.classified_units ?? 0);
  const total = Number(event.expected_units || classified || 0);
  if (classified === 0 || (total && classified < total)) {
    ticker.innerHTML = '<p class="loading-line">The relationship signal is being prepared for this release.</p>';
    setText("relationship-denominator", "Relationship signal being prepared");
    setText("relationship-other-summary", "Weekly counts and source evidence remain available.");
    return;
  }
  const complete = Number(event.display_complete_configuration_count ?? event.complete_configuration_count ?? 0);
  ticker.innerHTML = [
    "mutualism",
    "ai_benefiting_parasitism",
    "human_benefiting_parasitism",
    "competition",
  ].map((key) => relationshipCell(key, Number(counts[key] || 0), complete)).join("");
  setText("relationship-denominator", `${complete} of ${total} developments had directional evidence for both sides`);
  const partial = Number(event.display_partial_signal_count ?? event.partial_signal_count ?? 0);
  const noClear = Number(event.display_no_clear_relational_signal_count ?? event.no_clear_relational_signal_count ?? 0);
  const ambiguous = Number(event.display_ambiguous_relational_signal_count ?? event.ambiguous_relational_signal_count ?? 0);
  const insufficient = Number(event.display_insufficient_evidence_count ?? event.insufficient_evidence_count ?? 0);
  setText("relationship-other-summary", relationshipOutsideCopy(total, complete, partial, noClear, ambiguous, insufficient));
}
'''

HUMAN_SECTION = r'''    <section id="human-impact" class="empowerment-section" aria-labelledby="empowerment-title">
      <div class="section-heading compact-heading">
        <div><p class="eyebrow">People-side view</p><h2 id="empowerment-title">What changed for people?</h2></div>
        <div class="heading-info"><span id="human-denominator">Loading relationship evidence...</span><details class="info-popover align-right"><summary aria-label="Explain the people-side view">i</summary><div class="info-card"><strong>Same relationship evidence, people side only</strong><p>This view collapses the relationship categories above onto the people side. Gain or enabling combines developments where people are represented as gaining capacity, access, autonomy, or control. Constraint combines developments where people are represented as losing them.</p><p>AI-only and no-clear patterns are shown as no direct people-side signal. Insufficient or ambiguous evidence stays separate.</p></div></details></div>
      </div>
      <div class="empowerment-card">
        <div class="empowerment-summary"><span>People-side directional evidence</span><strong id="human-plain-label">Loading</strong><small id="human-index">Loading the current relationship evidence...</small></div>
        <div><div id="human-bars" class="bar-list"></div></div>
      </div>
    </section>

'''


def patch_edu_dashboard() -> None:
    path = ROOT / "edu" / "dashboard.js"
    text = path.read_text(encoding="utf-8")
    text = replace_between(text, "function renderRelationship() {", "function eventDiscoveryMarkets(event) {", EDU_RELATIONSHIP, "edu relationship")
    text = replace_between(text, "function empowermentPlain(index) {", "function setupEvidence() {", EDU_HUMAN, "edu people-side view")
    text = re.sub(
        r'<span class="relationship-badge" title="\$\{relationship\.reviewed \? "Human-reviewed" : "Model-coded provisional"\}">',
        '<span class="relationship-badge" title="Relationship pattern">',
        text,
    )
    write_if_changed(path, text)


def patch_edu_index() -> None:
    path = ROOT / "edu" / "index.html"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'^\s*<div id="relationship-review-banner"[^\n]*\n', '', text, flags=re.MULTILINE)
    text = replace_between(text, '    <section id="human-impact"', '    <section class="method-note">', HUMAN_SECTION, "edu human section")
    text = re.sub(r'/edu/dashboard\.js\?v=[0-9.]+', '/edu/dashboard.js?v=5.10.0', text)
    write_if_changed(path, text)


def patch_root_site() -> None:
    path = ROOT / "site.js"
    text = path.read_text(encoding="utf-8")
    text = replace_between(text, "function renderRelationship(symbiosis, error = null, releaseId = null) {", "function marketRows(release, iso3) {", ROOT_RELATIONSHIP, "root relationship")
    text = re.sub(r'import \{ initDiscoveryGlobe \} from "/globe\.js\?v=[0-9.]+";', 'import { initDiscoveryGlobe } from "/globe.js?v=5.10.0";', text)
    text = re.sub(r'const BUILD_ID = "[0-9.]+";', 'const BUILD_ID = "5.10.0";', text)
    write_if_changed(path, text)


def patch_root_index() -> None:
    path = ROOT / "index.html"
    text = path.read_text(encoding="utf-8")
    text = re.sub(r'/site\.js\?v=[0-9.]+', '/site.js?v=5.10.0', text)
    write_if_changed(path, text)


def patch_publish_workflow() -> None:
    path = ROOT / ".github" / "workflows" / "publish-observatory-release.yml"
    text = path.read_text(encoding="utf-8")
    marker = "      - name: Validate canonical release and every public derivative\n        run: python scripts/validate_and_publish_release.py\n"
    addition = marker + "      - name: Validate relationship arithmetic and people-side derivation\n        run: python scripts/validate_public_relationship_consistency.py\n"
    if "Validate relationship arithmetic and people-side derivation" not in text:
        if marker not in text:
            raise RuntimeError("publish workflow: canonical validation step not found")
        text = text.replace(marker, addition, 1)
    write_if_changed(path, text)


def assert_no_public_process_labels() -> None:
    forbidden = [
        "Model-coded provisional signal",
        "model-coded weekly lens",
        'title="${relationship.reviewed ? "Human-reviewed" : "Model-coded provisional"}"',
    ]
    for rel in ["site.js", "index.html", "edu/dashboard.js", "edu/index.html"]:
        text = (ROOT / rel).read_text(encoding="utf-8")
        for phrase in forbidden:
            if phrase in text:
                raise RuntimeError(f"forbidden public process label remains in {rel}: {phrase}")


def main() -> int:
    required = [ROOT / "site.js", ROOT / "index.html", ROOT / "edu" / "dashboard.js", ROOT / "edu" / "index.html"]
    missing = [str(p.relative_to(ROOT)) for p in required if not p.exists()]
    if missing:
        raise SystemExit(f"Run from the Observatory repository; missing: {', '.join(missing)}")
    patch_edu_dashboard()
    patch_edu_index()
    patch_root_site()
    patch_root_index()
    patch_publish_workflow()
    assert_no_public_process_labels()
    print("Public relationship consistency fix applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
