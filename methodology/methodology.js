"use strict";

const CURRENT_URL = "/data/releases/current.json";
const INDEX_URL = "/data/releases/index.json";
const dateLong = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "long", year: "numeric" });
const dateTime = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", timeZoneName: "short" });

function parseDate(value) { const d = new Date(`${String(value || "").slice(0,10)}T12:00:00Z`); return Number.isNaN(d.getTime()) ? null : d; }
function range(a,b){ const start=parseDate(a),end=parseDate(b); if(!start||!end)return "Not available"; const same=start.getUTCMonth()===end.getUTCMonth()&&start.getUTCFullYear()===end.getUTCFullYear(); return same?`${start.getUTCDate()}–${dateLong.format(end)}`:`${dateLong.format(start)}–${dateLong.format(end)}`; }
function time(value){ const d=new Date(String(value||"")); return Number.isNaN(d.getTime())?null:dateTime.format(d); }
function setText(id,value){ const el=document.getElementById(id); if(el)el.textContent=String(value??"Not available"); }
async function fetchJSON(url){ const r=await fetch(url,{cache:"no-store"}); if(!r.ok)throw new Error(`${url}: ${r.status}`); return r.json(); }

async function init(){
  try{
    const [current,index]=await Promise.all([fetchJSON(CURRENT_URL),fetchJSON(INDEX_URL)]);
    const pool=current.historical_pool||index.historical_pool||{};
    setText("method-period",range(current.period_start,current.period_end));
    setText("method-pool",pool.all_prior_events_considered&&pool.considered_through?`${time(pool.starts_at)||"5 Aug 2026"} through ${time(pool.considered_through)}`:"Pilot pool from 5 Aug 2026; longitudinal scope provisional");
    setText("method-through",time(current.data_current_through||current.generated_at)||"Not available");
    setText("method-revision",`Revision ${Number(current.revision||index.current_revision||1)}`);
  }catch(error){
    console.error("Methodology scope could not load",error);
    setText("method-period","Current release unavailable");
    setText("method-pool","Public history begins 5 August 2026");
  }
}
init();
