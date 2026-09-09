import {initDiscoveryGlobe} from './globe.js?v=7.3.0';
import {OUTCOMES, AXIS_LABELS, PATTERNS, escapeHTML as e, safeUrl, formatRange, percent, fetchJSON, weeklyModel, weeklyTakeaway, outcomeFor, releaseCounts} from './public-data.js?v=7.3.0';

export function setupNavigation() {
  const button = document.querySelector('.nav-toggle');
  const nav = document.getElementById('main-nav');
  button?.addEventListener('click', () => {
    const open = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(open)); nav?.setAttribute('data-open', String(open));
  });
  document.querySelectorAll('#main-nav a').forEach(a => {
    if (a.getAttribute('href') === location.pathname) a.setAttribute('aria-current','page');
  });
}
export function outcomeTable(model) {
  if (!model.ready) return '<p>The weekly assessment is being prepared. Source articles remain available below.</p>';
  return `<table class="audit-table"><caption>${model.total} developments with complete source content, counted once</caption><thead><tr><th scope="col">Source reading</th><th scope="col">Developments</th><th scope="col">Share</th></tr></thead><tbody>${Object.entries(OUTCOMES).filter(([key])=>key!=='too_little_evidence').map(([key,label]) => `<tr><th scope="row">${label}</th><td>${model.counts[key]}</td><td>${percent(model.counts[key],model.total)}</td></tr>`).join('')}</tbody><tfoot><tr><th scope="row">Analysis total</th><td>${model.total}</td><td>${model.total ? '100%' : '—'}</td></tr></tfoot></table>`;
}
export function renderEvidenceContext(model, container = document.getElementById('evidence-context')) {
  if (!container) return;
  if (!model.ready) {container.innerHTML='<p class="coverage-note">The weekly assessment is being prepared. Missing assessments are not assigned an outcome.</p>'; return;}
  const c = model.counts;
  container.innerHTML = `<p class="evidence-summary"><a href="/edu/?signal=no_clear_people_change#evidence">${c.no_clear_people_change} with no human direction stated</a></p><p class="coverage-note">${model.total} of ${model.inventory.total} collected developments meet the complete-content rule. <a href="/edu/?scope=audit&signal=too_little_evidence#evidence">${model.cohort.counts.excluded_developments} excluded from analysis</a>.</p><details class="quiet-details"><summary>What these numbers mean</summary><div>${outcomeTable(model)}<p>Each included development has a completed reading supported by at least one complete source with matching content records. Missing companion sources are excluded. Audio and video summaries require a complete transcript.</p><p>“No direction stated” remains in the denominator. Missing evidence does not. Percentages are rounded and may not add to exactly 100%. These are automated source-availability checks, not a certification of classification accuracy. Unavailable sources can bias the sample; these figures do not measure verified real-world impact.</p><a href="/methodology/#outcomes">How the sources are read</a></div></details>`;
}
export function countingDetail(model) {
  return `<p>${model.articles} source pages cover ${model.total} distinct developments, including ${model.extra} additional ${model.extra === 1 ? 'page' : 'pages'} about developments already counted.</p><table class="audit-table"><thead><tr><th>Earlier coverage</th><th>Developments</th></tr></thead><tbody><tr><th>First recorded here</th><td>${model.first}</td></tr><tr><th>Seen in an earlier week</th><td>${model.recurring}</td></tr><tr><th>History link being checked</th><td>${model.review}</td></tr></tbody><tfoot><tr><th>Total</th><td>${model.total}</td></tr></tfoot></table>`;
}
export function eventMarkets(event, coverage) {
  return [...new Set((event.member_article_ids || event.sources?.map(s => s.article_id) || []).flatMap(id => coverage.get(String(id))?.search_markets || []))];
}
function eventDate(event) { return (event.sources || []).map(s => s.published_date).filter(Boolean).sort().at(-1) || event.event_date || ''; }
export function sortEvents(events) {return [...events].sort((a,b) => String(eventDate(b)).localeCompare(String(eventDate(a))) || String(a.event_title).localeCompare(String(b.event_title)));}
export function renderStories(container, events, model, markets = {}, coverage = new Map()) {
  if (!container) return;
  if (!events.length) {container.innerHTML='<p class="empty-state">No developments match these filters.</p>';return;}
  container.innerHTML = events.map(event => {
    const id = String(event.effective_event_id || event.event_id);
    const excluded = model.ready && model.auditRecords?.get(id)?.eligible === false;
    const row = model.ready ? (model.rows.get(id) || model.allRows?.get(id)) : null;
    const outcome = outcomeFor(row);
    const sourceRows = row?.sources || event.sources || [];
    const publications = [...new Set(sourceRows.map(s => s.publisher).filter(Boolean))];
    const discovered = eventMarkets(event,coverage).map(code => markets[code]?.name || code);
    const date = formatRange(eventDate(event),eventDate(event),true).replace(/^(\d+)–\1 /,'$1 ');
    const full = Number(row?.evidence_basis_summary?.full_text_sources || 0);
    const noRun = excluded || row?.classification_audit?.classification_not_run === true;
    const evidence = String(row?.classification_audit?.people_evidence || '').trim();
    const explanation = String(row?.public_takeaway || row?.evidence_summary || '').trim();
    const aiExplanation = excluded ? '' : row?.axes?.ai?.evidence || '';
    const scopeNote = row?.display_scope || '';
    const title = row?.event_title || event.event_title || 'Untitled development';
    const novelty = event.novelty_status === 'recurring' ? 'Seen in an earlier week' : ['first_time','follow_on_development'].includes(event.novelty_status) ? 'First recorded here' : 'History link being checked';
    return `<details class="story-row" data-event-id="${e(event.effective_event_id || event.event_id)}" data-outcome="${e(outcome || 'pending')}"><summary><span class="outcome-tag ${e(outcome)}">${e(excluded ? 'Excluded from analysis' : OUTCOMES[outcome] || 'Assessment pending')}</span><div class="story-title-wrap"><h3>${e(title)}</h3><div class="story-meta"><span>${e(publications.slice(0,2).join(' · '))}</span><span>${e(date)}</span>${sourceRows.length>1 ? `<span>${sourceRows.length} sources</span>`:''}</div></div><span class="expand-icon" aria-hidden="true">+</span></summary><div class="story-body">${scopeNote ? `<p class="story-footnote">${e(scopeNote)}</p>`:''}${explanation && !noRun ? `<p><strong>For people</strong><br>${e(explanation)}</p>`:''}${aiExplanation ? `<p><strong>For AI and its operators · ${e(AXIS_LABELS[row.axes.ai.direction])}</strong><br>${e(aiExplanation)}</p>`:''}<div class="source-links">${sourceRows.map(s => `<a href="${e(safeUrl(s.url))}" target="_blank" rel="noopener noreferrer">Read ${e(s.publisher || 'the original source')} ↗ <small>${e(s.headline || '')}</small></a>`).join('')}</div><p class="story-footnote">${excluded ? e((model.auditRecords.get(id).reason_codes || []).map(code=>model.cohort.reason_definitions[code] || code).join(' ')) : row ? 'Complete source content used' : 'Assessment pending'} · ${e(novelty)}${discovered.length ? `<br>Found through ${e(discovered.join(', '))} searches`:''}</p></div></details>`;
  }).join('');
}
export function renderPatterns(model, scope = document.getElementById('movement-scope'), grid = document.getElementById('movement-grid')) {
  if (!grid) return;
  if (!model.ready || !model.axesReady) {grid.innerHTML='<p>The independent AI reading is being prepared.</p>';return;}
  if (scope) scope.textContent=`A separate reading of the same ${model.total} developments. A source can describe a change for people, for AI, or for both.`;
  const order = ['gain','mixed','loss','none'];
  grid.innerHTML = order.map(key=>`<div class="pattern-row ${['none','unresolved'].includes(key)?'is-zero':''}"><span>${e(AXIS_LABELS[key])}</span><strong>${model.aiCounts[key]}</strong></div>`).join('') + `<details class="quiet-details"><summary>What counts as a change for AI?</summary><div><p>Capabilities, use, resources, reach and operating limits of AI systems and their operators. These are assessed separately from gains and losses for people.</p><p>Each column counts the same ${model.total} developments once. The human and AI columns are not added together.</p><a href="/methodology/#relationships">Read the definitions →</a></div></details>`;
}

export function renderHistory(index, release) {
  const rows = [...(index?.weekly || [])].filter(r => r.period_end <= release.period_end).sort((a,b) => a.period_start.localeCompare(b.period_start));
  const current = rows.find(r=>r.release_id===release.release_id);
  const previous = rows.filter(r=>r.period_end<release.period_start).at(-1);
  const container = document.getElementById('history-comparison');
  if (!container) return;
  const values = row => ({articles:Number(row.ai_relevant_articles ?? row.articles ?? 0),total:Number(row.ai_relevant_event_records ?? row.event_records ?? row.events ?? 0),extra:Number(row.extra_coverage ?? 0)});
  const now = releaseCounts(release);
  if (previous) {
    const before = values(previous);
    document.getElementById('history-dates').textContent=`${formatRange(previous.period_start,previous.period_end,true)} → ${formatRange(release.period_start,release.period_end,true)}`;
    container.innerHTML = [['articles','Source pages'],['total','Distinct developments'],['extra','Additional pages on the same developments']].map(([key,label])=>{
      const delta=now[key]-before[key];
      return `<div><span>${label} collected</span><strong>${before[key]} → ${now[key]}</strong><small>${Math.abs(delta)} ${delta<0?'fewer':delta>0?'more':'change'} in the collection inventory</small></div>`;
    }).join('');
  } else {container.innerHTML='<p>This is the first completed week available for comparison.</p>';}
  document.getElementById('history-detail').innerHTML=`<table class="audit-table"><caption>${rows.length} completed weeks</caption><thead><tr><th>Week</th><th>Source pages</th><th>Developments</th><th>Additional pages</th></tr></thead><tbody>${rows.map(r=>{const v=values(r);return `<tr><th>${formatRange(r.period_start,r.period_end,true)}</th><td>${v.articles}</td><td>${v.total}</td><td>${v.extra}</td></tr>`;}).join('')}</tbody></table>${countingDetail(now)}<p>This collection audit includes unavailable sources. It is separate from the complete-content findings and the Brief export; it does not measure real-world AI activity or outcomes.</p>`;
}
async function renderGlobe(release, countryData) {
  const markets = countryData?.markets || {};
  const buttons = document.getElementById('market-overview');
  let globe;
  const select = code => {
    buttons.querySelectorAll('[data-market]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.market===code)));
    const card=document.getElementById('market-card');
    if (!code) {card.textContent='English, French and Chinese reporting. Choose a market to explore.';return;}
    const articles=(release.units?.coverage_articles || []).filter(a=>a.classification?.ai_relevant && a.search_markets?.includes(code));
    card.innerHTML=`${articles.length} source pages found through ${e(markets[code]?.name || code)}. <a href="/edu/?market=${encodeURIComponent(code)}#evidence">Explore sources →</a>`;
  };
  buttons.innerHTML=Object.entries(markets).map(([code,m])=>`<button type="button" data-market="${e(code)}" aria-pressed="false">${e(m.short_name || m.name)}</button>`).join('');
  buttons.querySelectorAll('button').forEach(b=>b.addEventListener('click',()=>{select(b.dataset.market);globe?.selectMarket(b.dataset.market,{notify:false});}));
  try {globe=await initDiscoveryGlobe({containerId:'home-globe',toggleId:'home-globe-toggle',promptId:'home-globe-prompt',fallbackId:'home-globe-fallback',markets,onSelect:select});}
  catch {document.getElementById('home-globe-fallback').hidden=false;}
}
async function init() {
  setupNavigation();
  try {
    const [release,relationships,countries,index]=await Promise.all([fetchJSON('/data/releases/current.json'),fetchJSON('/data/symbiosis/current.json',true),fetchJSON('/edu/countries.json',true),fetchJSON('/data/releases/index.json',true)]);
    const model=weeklyModel(release,relationships);
    document.getElementById('release-badge').textContent=formatRange(release.period_start,release.period_end);
    document.getElementById('lead-meta').innerHTML=model.ready ? `<strong>${model.total} developments with complete source content</strong> · ${model.articles} supporting source pages` : 'Complete-content findings are being prepared.';
    document.getElementById('hero-summary').textContent='What sources report, claim and anticipate about people and AI.';
    const primary=document.getElementById('primary-findings');
    primary.innerHTML=model.ready ? `<div class="outcome-pair${model.counts.benefit_and_downside ? ' has-mixed' : ''}"><a class="outcome-link gain" href="/edu/?signal=benefit_shown#evidence"><strong>${model.counts.benefit_shown}</strong><span>Benefits only</span><small>${percent(model.counts.benefit_shown,model.total)} of ${model.total} included developments</small></a><a class="outcome-link loss" href="/edu/?signal=downside_shown#evidence"><strong>${model.counts.downside_shown}</strong><span>Downsides only</span><small>${percent(model.counts.downside_shown,model.total)} of ${model.total} included developments</small></a>${model.counts.benefit_and_downside ? `<a class="outcome-link mixed" href="/edu/?signal=benefit_and_downside#evidence"><strong>${model.counts.benefit_and_downside}</strong><span>Benefits and downsides</span><small>${percent(model.counts.benefit_and_downside,model.total)} of ${model.total} included developments</small></a>` : ''}</div>${model.counts.benefit_and_downside ? '' : '<p class="mixed-note">0 reported both benefits and downsides</p>'}` : '<p class="error-state">The weekly assessment is being prepared. <a href="/edu/">Explore available source articles.</a></p>';
    renderEvidenceContext(model);
    const coverage=new Map((release.units?.coverage_articles || []).map(a=>[String(a.article_id),a]));
    const eligible=sortEvents(release.evidence || []).filter(event=>{
      const row=model.rows.get(String(event.effective_event_id || event.event_id));
      return model.ready && ['benefit_shown','downside_shown','benefit_and_downside'].includes(outcomeFor(row));
    });
    const chosen=eligible.slice(0,4);
    renderStories(document.getElementById('home-stories'),chosen,model,countries?.markets,coverage);
    renderPatterns(model);renderHistory(index,release);
    if (model.ready) void renderGlobe(model.analysisRelease,countries);
  } catch(error) {
    console.error(error);
    document.getElementById('release-badge').textContent='Weekly evidence unavailable';
    document.getElementById('primary-findings').innerHTML='<p class="error-state">The latest data could not load. Please try again shortly.</p>';
    ['home-stories','movement-grid','history-comparison'].forEach(id=>{document.getElementById(id).innerHTML='<p class="empty-state">Data could not load.</p>';});
  }
}
if (typeof document !== 'undefined' && document.body?.dataset.page==='home') init();
