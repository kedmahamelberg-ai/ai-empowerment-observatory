import {OUTCOMES, escapeHTML as e, formatRange, fetchJSON, weeklyModel, weeklyTakeaway, outcomeFor} from '../public-data.js?v=7.2.0';
import {setupNavigation, renderEvidenceContext, renderStories, sortEvents, eventMarkets, countingDetail} from '../site.js?v=7.2.0';

const params=new URLSearchParams(location.search);
let signal=Object.hasOwn(OUTCOMES,params.get('signal')) ? params.get('signal') : 'all';
let market=params.get('market') || '';
let novelty=['new','recurring','review'].includes(params.get('view')) ? params.get('view') : 'all';
let limit=12;
let release, model, markets={}, coverage=new Map();
const el=id=>document.getElementById(id);
function matchesNovelty(event) {
  if (novelty==='all') return true;
  if (novelty==='new') return ['first_time','follow_on_development'].includes(event.novelty_status);
  if (novelty==='recurring') return event.novelty_status==='recurring';
  return ['possible_historical_match','unclassified'].includes(event.novelty_status) || event.possible_historical_match;
}
function render() {
  const events=sortEvents(release.evidence || []).filter(event=>(!market || eventMarkets(event,coverage).includes(market)) && matchesNovelty(event) && (signal==='all' || (model.ready && outcomeFor(model.rows.get(String(event.effective_event_id || event.event_id)))===signal)));
  renderStories(el('evidence-list'),events.slice(0,limit),model,markets,coverage);
  el('evidence-scope').textContent=`${events.length} of ${model.total} developments${signal!=='all' ? ` · ${OUTCOMES[signal]}`:''}${market ? ` · ${markets[market]?.name || market} search`:''} · Showing ${Math.min(limit,events.length)}`;
  el('show-more-evidence').hidden=events.length<=limit;
  el('show-more-evidence').textContent=`Show ${Math.min(12,Math.max(0,events.length-limit))} more developments`;
  document.querySelectorAll('button[data-signal]').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.signal===signal));b.disabled=b.dataset.signal!=='all'&&!model.ready;});
  el('outcome-filter').value=signal;el('outcome-filter').disabled=!model.ready;
  el('market-filter').value=market;el('novelty-filter').value=novelty;
  const url=new URL(location.href);
  for(const [key,value] of [['signal',signal==='all'?'':signal],['market',market],['view',novelty==='all'?'':novelty]]){if(value)url.searchParams.set(key,value);else url.searchParams.delete(key);}
  history.replaceState(null,'',url.pathname+url.search+url.hash);
}
async function init() {
  setupNavigation();
  try {
    const data=await Promise.all([fetchJSON('/data/releases/current.json'),fetchJSON('/data/symbiosis/current.json',true),fetchJSON('/edu/countries.json',true)]);
    release=data[0];model=weeklyModel(release,data[1]);markets=data[2]?.markets || {};
    coverage=new Map((release.units?.coverage_articles || []).map(a=>[String(a.article_id),a]));
    if (!markets[market]) market='';
    // Keep unavailable classifications out of all substantive outcome filters.
    if (!model.ready) signal='all';
    el('release-badge').textContent=formatRange(release.period_start,release.period_end);
    el('news-context').innerHTML=`${e(weeklyTakeaway(model))} <a href="/">See the weekly overview →</a>`;
    el('market-filter').insertAdjacentHTML('beforeend',Object.entries(markets).map(([code,m])=>`<option value="${e(code)}">${e(m.name)}</option>`).join(''));
    renderEvidenceContext(model);el('count-detail').innerHTML=countingDetail(model);
    document.querySelectorAll('button[data-signal]').forEach(b=>b.addEventListener('click',()=>{signal=b.dataset.signal;limit=12;render();}));
    el('more-filters').addEventListener('click',()=>{const open=el('filter-options').hidden;el('filter-options').hidden=!open;el('more-filters').setAttribute('aria-expanded',String(open));});
    el('outcome-filter').addEventListener('change',event=>{signal=event.target.value;limit=12;render();});
    el('market-filter').addEventListener('change',event=>{market=event.target.value;limit=12;render();});
    el('novelty-filter').addEventListener('change',event=>{novelty=event.target.value;limit=12;render();});
    el('show-more-evidence').addEventListener('click',()=>{limit+=12;render();});
    if (market || novelty!=='all' || ['no_clear_people_change','too_little_evidence'].includes(signal)) {el('filter-options').hidden=false;el('more-filters').setAttribute('aria-expanded','true');}
    render();
    if (['#history','#explore','#movement'].includes(location.hash)) {
      el('news-context').insertAdjacentHTML('beforeend',` <a href="/${e(location.hash==='#explore'?'#main-content':location.hash)}">Open ${location.hash==='#history'?'weekly comparisons':location.hash==='#movement'?'relationship patterns':'the globe'} →</a>`);
    }
  } catch(error) {console.error(error);el('release-badge').textContent='Weekly evidence unavailable';el('evidence-list').innerHTML='<p class="error-state">The latest evidence could not load. Please try again shortly.</p>';}
}
init();
