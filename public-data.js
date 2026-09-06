/* One interpretation of the published weekly evidence for every public page. */
export const BUILD_ID = '7.2.0';
export const OUTCOMES = {
  benefit_shown: 'Benefits only',
  downside_shown: 'Downsides only',
  benefit_and_downside: 'Benefits and downsides',
  no_clear_people_change: 'No direction stated',
  too_little_evidence: 'Incomplete evidence',
};
export const AXIS_SCHEMA = 'aieo_independent_directions_v2';
export const AXIS_LABELS = {gain:'Gains only', loss:'Limitations only', mixed:'Gains and limitations', none:'No direction stated', unresolved:'Incomplete evidence'};
export const DIRECTION_KEYS = Object.keys(AXIS_LABELS);
const AXIS_OUTCOMES = {gain:'benefit_shown', loss:'downside_shown', mixed:'benefit_and_downside', none:'no_clear_people_change', unresolved:'too_little_evidence'};
export const PATTERNS = {
  mutualism: 'People benefit as AI expands',
  ai_benefiting_parasitism: 'People lose ground as AI expands',
  human_benefiting_parasitism: 'People benefit as AI is limited',
  competition: 'People and AI both face setbacks',
};
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export function safeUrl(value) {
  try { const u = new URL(value); return ['https:', 'http:'].includes(u.protocol) ? u.href : '#'; } catch { return '#'; }
}
export function formatRange(start, end, short = false) {
  const a = new Date(`${String(start).slice(0,10)}T12:00:00Z`);
  const b = new Date(`${String(end).slice(0,10)}T12:00:00Z`);
  if (!Number.isFinite(+a) || !Number.isFinite(+b)) return 'Dates unavailable';
  const f = new Intl.DateTimeFormat('en-GB', {day:'numeric', month:short ? 'short':'long', year:short ? undefined:'numeric', timeZone:'UTC'});
  return a.getUTCMonth() === b.getUTCMonth() && a.getUTCFullYear() === b.getUTCFullYear()
    ? `${a.getUTCDate()}–${f.format(b)}` : `${f.format(a)} – ${f.format(b)}`;
}
export const percent = (value, total) => total > 0 ? `${(100 * value / total).toFixed(1)}%` : '—';
export async function fetchJSON(path, optional = false) {
  try {
    const response = await fetch(`${path}${path.includes('?') ? '&' : '?'}v=${BUILD_ID}`, {cache:'no-store'});
    if (!response.ok) throw new Error(`Could not load ${path} (${response.status})`);
    return await response.json();
  } catch (error) { if (optional) return null; throw error; }
}
export function releaseCounts(release) {
  const c = release?.counts || {};
  return {
    articles:Number(c.ai_relevant_articles || 0), total:Number(c.ai_relevant_event_records || 0),
    first:Number(c.new_event_records ?? (Number(c.first_time_event_records || 0) + Number(c.follow_on_event_records || 0))),
    recurring:Number(c.recurring_event_records || 0), review:Number(c.possible_historical_match_event_records || 0) + Number(c.unclassified_novelty_event_records || 0),
    extra:Number(c.extra_coverage ?? Math.max(0, Number(c.ai_relevant_articles || 0) - Number(c.ai_relevant_event_records || 0))),
  };
}
export function outcomeFor(row) {
  if (row?.axes?.schema_version === AXIS_SCHEMA) return AXIS_OUTCOMES[row.axes.human?.direction] || null;
  if (!row || !row.public_signals || !['sufficient','partial','insufficient'].includes(row.evidence_status)) return null;
  const s = row.public_signals;
  if (typeof s.people_gaining !== 'boolean' || typeof s.people_losing_ground !== 'boolean') return null;
  if (row.evidence_status === 'insufficient' && (s.people_gaining || s.people_losing_ground)) return null;
  if (s.people_gaining && s.people_losing_ground) return 'benefit_and_downside';
  if (s.people_gaining) return 'benefit_shown';
  if (s.people_losing_ground) return 'downside_shown';
  return row.evidence_status === 'insufficient' ? 'too_little_evidence' : 'no_clear_people_change';
}
export function weeklyModel(release, relationships) {
  const c = releaseCounts(release);
  const model = {...c, ready:false, release, relationships, counts:Object.fromEntries(Object.keys(OUTCOMES).map(k => [k,0])), patterns:Object.fromEntries(Object.keys(PATTERNS).map(k => [k,0])), rows:new Map(), fullBody:0, noBody:0, insufficientWithBody:0, twoSided:0, uneven:0, axesReady:false, aiCounts:Object.fromEntries(DIRECTION_KEYS.map(k=>[k,0])), humanCounts:Object.fromEntries(DIRECTION_KEYS.map(k=>[k,0]))};
  if (!relationships || !c.total || relationships.release_id !== release?.release_id) return model;
  if (!release.content_sha256 || relationships.source_release_sha256 !== release.content_sha256) return model;
  const signals = relationships.people_signals;
  const rows = relationships.evidence;
  const expectedIds = new Set((release.evidence || []).map(r => String(r.effective_event_id || r.event_id)));
  if (!Array.isArray(rows) || rows.length !== c.total || expectedIds.size !== c.total || Number(signals?.classified_units) !== c.total || Number(signals?.expected_units) !== c.total) return model;
  if (relationships.period_start !== release.period_start || relationships.period_end !== release.period_end) return model;
  for (const row of rows) {
    const id = String(row.event_id || '');
    const outcome = outcomeFor(row);
    if (!outcome || !expectedIds.has(id) || model.rows.has(id)) return {...model, ready:false};
    if (relationships.directional_summary) {
      const axes = row.axes;
      if (typeof axes?.evidence_complete !== "boolean" || axes.policy_version !== "source_representations_independent_axes_2026_09") return {...model,ready:false};
      if (axes?.schema_version !== AXIS_SCHEMA || !DIRECTION_KEYS.includes(axes.human?.direction) || !DIRECTION_KEYS.includes(axes.ai?.direction)) return {...model,ready:false};
      const human = axes.human.direction;
      if (row.public_signals?.people_gaining !== ['gain','mixed'].includes(human) || row.public_signals?.people_losing_ground !== ['loss','mixed'].includes(human)) return {...model,ready:false};
      for (const side of ['human','ai']) {
        if (['gain','loss','mixed'].includes(axes[side].direction) && !axes[side].evidence?.trim()) return {...model,ready:false};
        model[`${side}Counts`][axes[side].direction]++;
      }
    }
    model.rows.set(id,row);
    model.counts[outcome]++;
    const hasBody = row.axes ? row.axes.evidence_complete === true : Number(row.evidence_basis_summary?.full_text_sources || 0) > 0;
    if (hasBody) model.fullBody++; else model.noBody++;
    if (hasBody && outcome === 'too_little_evidence') model.insufficientWithBody++;
    const patterns = Object.keys(PATTERNS).filter(k => row.relationship_patterns?.[k] === true);
    patterns.forEach(k => model.patterns[k]++);
    if (patterns.length) model.twoSided++;
    if (row.public_signals.not_everyone_benefits) model.uneven++;
  }
  if (relationships.directional_summary) {
    const declared = relationships.directional_summary;
    if (declared.total !== c.total || declared.evidence_complete !== model.fullBody) return {...model,ready:false};
    for (const side of ['human','ai']) for (const key of DIRECTION_KEYS) {
      if (declared[side]?.[key] !== model[`${side}Counts`][key]) return {...model,ready:false};
    }
    model.axesReady = true;
  }
  model.ready = Object.values(model.counts).reduce((a,b) => a+b,0) === c.total;
  return model;
}
export function weeklyTakeaway(model) {
  if (!model.ready) return 'The latest weekly assessment is being prepared.';
  const c = model.counts;
  const parts = [];
  if (c.benefit_shown) parts.push(`${c.benefit_shown} ${c.benefit_shown === 1 ? "development describes" : "developments describe"} benefits only`);
  if (c.downside_shown) parts.push(`${c.downside_shown} describe downsides only`);
  if (c.benefit_and_downside) parts.push(`${c.benefit_and_downside} describe both`);
  return parts.length ? `${parts.join('; ')}.` : 'No directional change for people was established in this week’s reporting.';
}
