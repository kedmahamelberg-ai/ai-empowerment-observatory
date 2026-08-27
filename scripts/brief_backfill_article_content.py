#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, os, re, time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser
import requests, trafilatura
from bs4 import BeautifulSoup
from supabase import create_client
from brief_content_common import article_url, domain_of, normalize_space, sha256_text

USER_AGENT = "AIEOResearchBot/1.0 (+https://observatory.hamelberg-ai.com/methodology/)"
TIMEOUT = (10, 35)
MIN_WORDS = 80

def detect_paywall(html: str) -> bool:
    low=html.casefold()
    return any(x in low for x in ['"isaccessibleforfree":false','"isaccessibleforfree": false','meteredcontent','subscriptionrequired','subscribe to continue','sign in to continue'])

def robots_allowed(url: str):
    p=urlsplit(url); robots_url=f"{p.scheme}://{p.netloc}/robots.txt"
    try:
        rp=RobotFileParser(); rp.set_url(robots_url); rp.read(); return bool(rp.can_fetch(USER_AGENT,url)), robots_url
    except Exception:
        return None, robots_url

def tdmrep_for(url: str):
    p=urlsplit(url); endpoint=f"{p.scheme}://{p.netloc}/.well-known/tdmrep.json"
    out={'url':endpoint,'reservation':'unset','policy':None}
    try:
        r=requests.get(endpoint,headers={'User-Agent':USER_AGENT},timeout=TIMEOUT)
        if r.status_code!=200: return out
        rules=r.json()
        if not isinstance(rules,list): return out
        path=p.path or '/'; matches=[]
        for rule in rules:
            if not isinstance(rule,dict): continue
            loc=str(rule.get('location') or '')
            if not loc: continue
            prefix=loc.rstrip('*')
            if path.startswith(prefix): matches.append((len(prefix),rule))
        if matches:
            _, rule=sorted(matches,key=lambda x:x[0],reverse=True)[0]
            v=rule.get('tdm-reservation'); out['reservation']=str(v) if v is not None else 'unset'; out['policy']=rule.get('tdm-policy')
        return out
    except Exception:
        return out

def html_tdm_signal(response, html):
    h={k.casefold():v for k,v in response.headers.items()}
    reservation=h.get('tdm-reservation'); policy=h.get('tdm-policy')
    if reservation is not None: return str(reservation).strip(), policy
    soup=BeautifulSoup(html,'html.parser')
    meta=soup.find('meta',attrs={'name':re.compile(r'^tdm-reservation$',re.I)})
    if meta and meta.get('content') is not None: reservation=str(meta.get('content')).strip()
    pm=soup.find('meta',attrs={'name':re.compile(r'^tdm-policy$',re.I)})
    if pm and pm.get('content'): policy=str(pm.get('content')).strip()
    return reservation or 'unset', policy

def fetch_and_extract(url):
    robots, robots_url=robots_allowed(url)
    if robots is False: return {'outcome':'blocked_robots','robots_allowed':False,'robots_url':robots_url,'tdm':{'reservation':'unset','policy':None}}
    tdm=tdmrep_for(url)
    if tdm.get('reservation')=='1': return {'outcome':'blocked_tdm_reserved','robots_allowed':robots,'robots_url':robots_url,'tdm':tdm}
    started=time.monotonic(); r=requests.get(url,headers={'User-Agent':USER_AGENT,'Accept':'text/html,application/xhtml+xml;q=0.9,*/*;q=0.5'},timeout=TIMEOUT,allow_redirects=True); elapsed=round((time.monotonic()-started)*1000)
    html=r.text if 'html' in (r.headers.get('content-type') or '').casefold() else ''
    header_tdm, header_policy=html_tdm_signal(r,html)
    if header_tdm=='1':
        tdm['reservation']='1'; tdm['policy']=header_policy or tdm.get('policy'); return {'outcome':'blocked_tdm_reserved','http_status':r.status_code,'robots_allowed':robots,'robots_url':robots_url,'tdm':tdm,'elapsed_ms':elapsed,'content_type':r.headers.get('content-type'),'response_bytes':len(r.content)}
    if r.status_code!=200: return {'outcome':'http_error','http_status':r.status_code,'robots_allowed':robots,'robots_url':robots_url,'tdm':tdm,'elapsed_ms':elapsed,'content_type':r.headers.get('content-type'),'response_bytes':len(r.content)}
    if detect_paywall(html): return {'outcome':'blocked_paywall_or_login','http_status':r.status_code,'robots_allowed':robots,'robots_url':robots_url,'tdm':tdm,'elapsed_ms':elapsed,'content_type':r.headers.get('content-type'),'response_bytes':len(r.content),'paywall_detected':True}
    text=(trafilatura.extract(html,include_comments=False,include_tables=True,include_links=False,favor_precision=True,output_format='txt') or '').strip(); words=len(text.split())
    if words<MIN_WORDS: return {'outcome':'too_little_extractable_text','http_status':r.status_code,'robots_allowed':robots,'robots_url':robots_url,'tdm':tdm,'elapsed_ms':elapsed,'content_type':r.headers.get('content-type'),'response_bytes':len(r.content),'word_count':words}
    soup=BeautifulSoup(html,'html.parser'); title=normalize_space(soup.title.get_text(' ',strip=True)) if soup.title else ''
    return {'outcome':'stored','http_status':r.status_code,'robots_allowed':robots,'robots_url':robots_url,'tdm':tdm,'elapsed_ms':elapsed,'content_type':r.headers.get('content-type'),'response_bytes':len(r.content),'paywall_detected':False,'body_text':text,'word_count':words,'title_extracted':title,'final_url':r.url}

def page_rows(client,page_size=200):
    start=0
    while True:
        resp=client.table('articles').select('*').range(start,start+page_size-1).execute(); rows=resp.data or []
        if not rows: break
        yield from rows
        if len(rows)<page_size: break
        start += page_size

def already_stored(client,article_id):
    return bool(client.table('brief_article_content_snapshots').select('snapshot_id').eq('article_id',article_id).eq('is_current',True).limit(1).execute().data)

def insert_attempt(client,article_id,url,result,workflow_run_id):
    row={'article_id':article_id,'source_url':url,'source_domain':domain_of(url),'workflow_run_id':workflow_run_id,'retrieval_method':'direct_public_web','http_status':result.get('http_status'),'robots_allowed':result.get('robots_allowed'),'tdm_reservation':(result.get('tdm') or {}).get('reservation'),'tdm_policy_url':(result.get('tdm') or {}).get('policy'),'paywall_detected':result.get('paywall_detected'),'outcome':result.get('outcome') or 'unknown','response_content_type':result.get('content_type'),'response_bytes':result.get('response_bytes'),'elapsed_ms':result.get('elapsed_ms'),'metadata':{'robots_url':result.get('robots_url'),'tdmrep_url':(result.get('tdm') or {}).get('url'),'final_url':result.get('final_url'),'word_count':result.get('word_count')}}
    client.table('brief_article_fetch_attempts').insert(row).execute()

def store_snapshot(client,row,url,result):
    article_id=str(row.get('article_id') or row.get('id') or '').strip(); text=result['body_text']; digest=sha256_text(text)
    client.table('brief_article_content_snapshots').update({'is_current':False}).eq('article_id',article_id).execute()
    payload={'article_id':article_id,'source_url':result.get('final_url') or url,'source_domain':domain_of(result.get('final_url') or url),'retrieval_method':'direct_public_web','http_status':result.get('http_status'),'mime_type':result.get('content_type'),'extracted_title':result.get('title_extracted'),'body_text':text,'word_count':result.get('word_count') or len(text.split()),'text_sha256':digest,'content_basis':'full_page_extraction','rights_status':'stored_private_unreserved_signal','rights_basis':'lawfully accessible public page; robots not denied; no detected TDM reservation; private analytical storage only','robots_allowed':result.get('robots_allowed'),'tdm_reservation':(result.get('tdm') or {}).get('reservation'),'tdm_policy_url':(result.get('tdm') or {}).get('policy'),'paywall_detected':False,'is_current':True}
    client.table('brief_article_content_snapshots').upsert(payload,on_conflict='article_id,text_sha256').execute()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--limit',type=int,default=0); ap.add_argument('--dry-run',action='store_true'); ap.add_argument('--retry-existing',action='store_true'); ap.add_argument('--sleep',type=float,default=1.0); args=ap.parse_args()
    client=create_client(os.environ['SUPABASE_URL'],os.environ['SUPABASE_SECRET_KEY']); run=os.environ.get('GITHUB_RUN_ID'); counters={}; processed=0
    for row in page_rows(client):
        article_id=str(row.get('article_id') or row.get('id') or '').strip(); url=article_url(row)
        if not article_id or not url: counters['missing_id_or_url']=counters.get('missing_id_or_url',0)+1; continue
        if not args.retry_existing and already_stored(client,article_id): counters['already_stored']=counters.get('already_stored',0)+1; continue
        if args.limit and processed>=args.limit: break
        processed += 1; print(f'[{processed}] {article_id} {url}',flush=True)
        try: result=fetch_and_extract(url)
        except Exception as exc: result={'outcome':'exception','error':f'{type(exc).__name__}: {exc}'}
        outcome=result.get('outcome') or 'unknown'; counters[outcome]=counters.get(outcome,0)+1; print(f"  -> {outcome} ({result.get('word_count','-')} words)",flush=True)
        if not args.dry_run:
            try:
                insert_attempt(client,article_id,url,result,run)
                if outcome=='stored': store_snapshot(client,row,url,result)
            except Exception as exc:
                print(f'  DB ERROR: {type(exc).__name__}: {exc}',flush=True); counters['db_error']=counters.get('db_error',0)+1
        time.sleep(max(0.0,args.sleep))
    print(json.dumps({'processed':processed,'counts':counters},indent=2)); return 0

if __name__=='__main__': raise SystemExit(main())
