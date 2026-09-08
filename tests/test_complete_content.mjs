import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {weeklyModel, percent} from '../public-data.js';
import {outcomeTable} from '../site.js';
const read = p=>JSON.parse(fs.readFileSync(new URL('../_site/'+p,import.meta.url),'utf8'));
const release = read('data/releases/current.json');
const relationship = read('data/symbiosis/current.json');
const exported = read('data/brief/current.json');

test('homepage and Brief use exactly the same eligible developments and percentages',()=>{
  const model=weeklyModel(release,relationship);
  assert.equal(model.ready,true);
  assert.equal(model.total,exported.release.evidence.length);
  assert.equal(model.articles,exported.release.units.coverage_articles.length);
  assert.deepEqual([...model.rows.keys()].sort(),exported.release.complete_content.eligible_event_ids);
  for (const side of ['human','ai']) assert.deepEqual(model[`${side}Counts`],exported.relationship.directional_summary[side]);
  assert.equal(model.counts.too_little_evidence,0);
  assert.equal(Object.values(model.counts).reduce((a,b)=>a+b),model.total);
  assert.equal(model.total+model.cohort.counts.excluded_developments,model.inventory.total);
  assert.match(outcomeTable(model),/complete source content/);
  assert.doesNotMatch(outcomeTable(model),/Incomplete evidence/);
  assert.equal(percent(15,81),'18.5%');
});
test('missing, stale and manipulated cohorts cannot fall back to the collection denominator',()=>{
  for (const mutate of [s=>delete s.complete_content,s=>s.complete_content.source_relationship_sha256='stale',s=>s.complete_content.records.pop(),s=>s.complete_content.directional_summary.human.gain++,s=>s.complete_content.counts.eligible_sources++]) {
    const s=structuredClone(relationship);mutate(s);
    const model=weeklyModel(release,s);assert.equal(model.ready,false);assert.equal(model.total,0);
  }
});
test('an empty eligible cohort displays no percentage and no fabricated 100 percent',()=>{
  const s=structuredClone(relationship), c=s.complete_content;
  c.records.forEach(r=>{r.eligible=false;r.eligible_article_ids=[];r.reason_codes=['reading_incomplete'];});
  Object.assign(c.counts,{eligible_developments:0,eligible_sources:0,excluded_developments:c.counts.collected_developments,excluded_sources:c.counts.collected_sources});
  c.denominator.value=0;c.directional_summary.total=0;c.directional_summary.evidence_complete=0;
  for (const side of ['human','ai']) for (const key in c.directional_summary[side]) c.directional_summary[side][key]=0;
  const model=weeklyModel(release,s);assert.equal(model.ready,true);assert.equal(model.total,0);assert.equal(percent(0,0),'—');assert.doesNotMatch(outcomeTable(model),/100%/);
});
