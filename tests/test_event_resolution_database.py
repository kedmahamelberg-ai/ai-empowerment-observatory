"""Offline failure injection. Never import model/extraction packages or use API keys."""
from __future__ import annotations
import ast
import copy
import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from event_resolution_database import ResolverDatabase, ResolverDatabaseError, ResolverWriteConflict, PRIMARY_KEYS, resolver_collection
from classification_database_reads import DatabaseReadError

class APIError(RuntimeError):
    def __init__(self, code=504):
        self.code = code
        super().__init__({"message":"JSON could not be generated", "code":code,
                         "hint":"Refer to full message for details", "details":'b\'{"message":"Gateway Timeout"}\''})

class MemoryClient:
    def __init__(self):
        self.rows={}; self.read_calls=0; self.write_calls=0
        self.read_faults=[]; self.write_faults=[]; self.payloads=[]; self.builders=[]
        self.page_cap=None; self.ignore_range=False
        self.empty_write=False; self.no_commit=False; self.invalid_read=False
    def table(self,name):
        q=Query(self,name); self.builders.append(q); return q

class Query:
    def __init__(self,client,name):
        self.client=client; self.name=name; self.filters=[]; self.orders=[]
        self.columns='*'; self.offset=0; self.end=None; self.maximum=None; self.count=None
        self.method='select'; self.payload=None; self.kwargs={}
    def select(self,columns='*',**kwargs): self.columns=columns; self.count=kwargs.get('count'); return self
    def eq(self,key,value): self.filters.append((key,'eq',value)); return self
    def in_(self,key,value): self.filters.append((key,'in',value)); return self
    def order(self,key,**kwargs): self.orders.append((key,kwargs.get('desc',False))); return self
    def limit(self,maximum): self.maximum=maximum; return self
    def range(self,start,end): self.offset=start; self.end=end; return self
    def insert(self,payload,**kwargs): self.method='insert'; self.payload=payload; self.kwargs=kwargs; return self
    def upsert(self,payload,**kwargs): self.method='upsert'; self.payload=payload; self.kwargs=kwargs; return self
    def update(self,payload,**kwargs): self.method='update'; self.payload=payload; self.kwargs=kwargs; return self
    def filtered(self):
        result=[r for r in self.client.rows.setdefault(self.name,[]) if all(
            r.get(k)==v if op=='eq' else r.get(k) in v for k,op,v in self.filters)]
        for k,desc in reversed(self.orders): result.sort(key=lambda r:str(r.get(k,'')),reverse=desc)
        return result
    def project(self,rows):
        if self.columns=='*': return copy.deepcopy(rows)
        return [{k:copy.deepcopy(r[k]) for k in self.columns.split(',') if k in r} for r in rows]
    def write(self):
        rows=self.client.rows.setdefault(self.name,[]); pk=PRIMARY_KEYS[self.name]
        if self.method=='update': hits=self.filtered()
        elif self.method=='upsert':
            keys=self.kwargs['on_conflict'].split(',')
            hits=[r for r in rows if all(r.get(k)==self.payload[k] for k in keys)]
        else:
            if any(r.get(pk)==self.payload.get(pk) for r in rows): raise APIError('23505')
            hits=[]
        if not hits and self.method!='update':
            hit={pk:self.payload.get(pk,'id-'+str(len(rows)+1))}; rows.append(hit); hits=[hit]
        for r in hits: r.update(copy.deepcopy(self.payload))
        return self.project(hits)
    def execute(self):
        c=self.client
        if self.method=='select':
            c.read_calls+=1
            if c.read_faults:
                fault=c.read_faults.pop(0)
                if callable(fault): fault(self)
                elif fault: raise fault
            if c.invalid_read: return SimpleNamespace(data=None)
            allrows=self.filtered(); rows=allrows
            if not c.ignore_range: rows=rows[self.offset:self.end+1 if self.end is not None else None]
            if self.maximum is not None: rows=rows[:self.maximum]
            if c.page_cap is not None: rows=rows[:c.page_cap]
            return SimpleNamespace(data=self.project(rows),count=len(allrows) if self.count else None)
        c.write_calls+=1; c.payloads.append(copy.deepcopy(self.payload))
        fault=c.write_faults.pop(0) if c.write_faults else None
        if callable(fault): return fault(self)
        if fault=='before': raise APIError()
        if isinstance(fault,Exception): raise fault
        rows=[] if c.no_commit else self.write()
        if fault=='after': raise APIError()
        return SimpleNamespace(data=[] if c.empty_write else rows,count=None)

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.c=MemoryClient(); self.db=ResolverDatabase(self.c,sleep=lambda n:None)
        self.p=patch('classification_database_reads.time.sleep');self.p.start();self.addCleanup(self.p.stop)
    def decision(self,**extra):
        payload=dict(resolution_run_id='r',article_id='a',decision='new_event',assigned_event_id='e',updated_at='2026-09-14T05:00:00Z')
        payload.update(extra)
        return self.db.table('event_assignment_decisions').upsert(payload,on_conflict='resolution_run_id,article_id').select('assignment_decision_id')
    def test_integer_504_read_retried(self):
        self.c.read_faults=[APIError(504)]; self.db.table('articles').select('*').execute(); self.assertEqual(self.c.read_calls,2)
    def test_string_504_read_retried(self):
        self.c.read_faults=[APIError('504')];self.db.table('articles').select('*').execute();self.assertEqual(self.c.read_calls,2)
    def test_transport_timeout_read_retried(self):
        self.c.read_faults=[TimeoutError()];self.db.table('articles').select('*').execute();self.assertEqual(self.c.read_calls,2)
    def test_403_fails_without_retry(self):
        self.c.read_faults=[APIError(403)]
        with self.assertRaises(APIError):self.db.table('articles').select('*').execute()
        self.assertEqual(self.c.read_calls,1)
    def test_schema_error_fails_without_retry(self):
        self.c.read_faults=[APIError('42703')]
        with self.assertRaises(APIError):self.db.table('articles').select('*').execute()
        self.assertEqual(self.c.read_calls,1)
    def test_persistent_read_failure_never_becomes_empty_evidence(self):
        self.c.read_faults=[APIError()]*8
        with self.assertRaises(DatabaseReadError): self.db.table('articles').select('*').execute()
        self.assertEqual(self.c.read_calls,5)
    def test_non_database_validation_error_not_retried(self):
        self.c.read_faults=[ValueError('Gateway Timeout')]
        with self.assertRaises(ValueError):self.db.table('articles').select('*').execute()
        self.assertEqual(self.c.read_calls,1)
    def test_committed_decision_504_confirmed_without_duplicate(self):
        self.c.write_faults=['after'];r=self.decision().execute()
        self.assertEqual(r.data[0]['assignment_decision_id'],'id-1');self.assertEqual(self.c.write_calls,1)
        self.assertEqual(len(self.c.rows['event_assignment_decisions']),1)
    def test_not_committed_decision_retried_with_identical_payload(self):
        self.c.write_faults=['before'];self.decision().execute()
        self.assertEqual(self.c.write_calls,2);self.assertEqual(self.c.payloads[0],self.c.payloads[1])
        self.assertEqual(len(self.c.rows['event_assignment_decisions']),1)
    def test_timestamp_format_change_can_confirm_saved_row(self):
        def fault(q):
            q.write();self.c.rows[q.name][0]['updated_at']='2026-09-14T05:00:00+00:00';raise APIError()
        self.c.write_faults=[fault];self.decision().execute();self.assertEqual(self.c.write_calls,1)
    def test_independent_change_stops_without_overwrite(self):
        def fault(q):
            q.write();self.c.rows[q.name][0]['decision']='review';raise APIError()
        self.c.write_faults=[fault]
        with self.assertRaises(ResolverWriteConflict):self.decision().execute()
        self.assertEqual(self.c.write_calls,1)
    def test_confirmation_failure_does_not_repeat_uncertain_write(self):
        self.c.write_faults=['after'];self.c.read_faults=[None]+[APIError()]*5
        with self.assertRaises(DatabaseReadError):self.decision().execute()
        self.assertEqual(self.c.write_calls,1)
    def test_permission_write_error_not_retried(self):
        self.c.write_faults=[APIError(403)]
        with self.assertRaises(APIError):self.decision().execute()
        self.assertEqual(self.c.write_calls,1)
    def test_foreign_key_write_error_not_retried(self):
        self.c.write_faults=[APIError('23503')]
        with self.assertRaises(APIError):self.decision().execute()
        self.assertEqual(self.c.write_calls,1)
    def test_persistent_write_stops_after_five_attempts(self):
        self.c.write_faults=['before']*8
        with self.assertRaises(ResolverDatabaseError):self.decision().execute()
        self.assertEqual(self.c.write_calls,5)
    def test_run_creation_uncertain_commit_reuses_one_uuid(self):
        self.c.write_faults=['after'];self.db.table('event_resolution_runs').insert({'run_key':'r'}).select('resolution_run_id').execute()
        self.assertEqual(self.c.write_calls,1);self.assertEqual(len(self.c.rows['event_resolution_runs']),1)
    def test_run_creation_uuid_is_fixed_before_retries(self):
        self.c.write_faults=['before'];self.db.table('event_resolution_runs').insert({'run_key':'r'}).execute()
        self.assertEqual(self.c.payloads[0]['resolution_run_id'],self.c.payloads[1]['resolution_run_id'])
    def test_late_insert_commit_unique_violation_confirmed(self):
        def first(q):self.late=copy.deepcopy(q.payload);raise APIError()
        def second(q):self.c.rows[q.name].append(self.late);raise APIError('23505')
        self.c.write_faults=[first,second];self.db.table('event_resolution_runs').insert({'run_key':'r'}).execute()
        self.assertEqual(len(self.c.rows['event_resolution_runs']),1)
    def test_wrong_upsert_key_blocked_before_write(self):
        with self.assertRaises(ResolverDatabaseError):self.db.table('events').upsert({'x':1},on_conflict='x').execute()
        self.assertEqual(self.c.write_calls,0)
    def test_unkeyed_bulk_insert_blocked(self):
        with self.assertRaises(ResolverDatabaseError):self.db.table('events').insert({'title':'x'})
        with self.assertRaises(ResolverDatabaseError):self.db.table('event_resolution_runs').insert([{}])
    def test_missing_update_target_fails(self):
        with self.assertRaises(ResolverDatabaseError):self.db.table('events').update({'title':'x'}).eq('event_id','none').execute()
        self.assertEqual(self.c.write_calls,0)
    def test_update_without_primary_key_blocked(self):
        with self.assertRaises(ResolverDatabaseError):self.db.table('events').update({'title':'x'}).execute()
    def test_update_lost_response_is_confirmed(self):
        self.c.rows['events']=[{'event_id':'e','event_state':'active'}];self.c.write_faults=['after']
        self.db.table('events').update({'cluster_confidence':.9}).eq('event_id','e').execute();self.assertEqual(self.c.write_calls,1)
    def test_unsaved_update_retried_against_unchanged_baseline(self):
        self.c.rows['events']=[{'event_id':'e','event_state':'active'}];self.c.write_faults=['before']
        self.db.table('events').update({'cluster_confidence':.9}).eq('event_id','e').execute();self.assertEqual(self.c.write_calls,2)
    def test_pagination_respects_server_cap_without_losing_rows(self):
        self.c.rows['articles']=[{'article_id':str(n)} for n in range(7)];self.c.page_cap=2
        self.assertEqual(len(self.db.table('articles').select('*').execute().data),7);self.assertEqual(self.c.read_calls,5)
    def test_all_batched_ids_are_retained(self):
        self.c.rows['articles']=[{'article_id':str(n)} for n in range(67)]
        r=self.db.table('articles').select('*').in_('article_id',[str(n) for n in range(67)]).execute()
        self.assertEqual(len(r.data),67)
        self.assertTrue(all(len(v)<=25 for q in self.c.builders for _,op,v in q.filters if op=='in'))
    def test_midpage_failure_does_not_duplicate_rows(self):
        self.c.rows['articles']=[{'article_id':str(n)} for n in range(4)];self.c.page_cap=2;self.c.read_faults=[None,APIError(),None]
        rows=self.db.table('articles').select('*').execute().data;self.assertEqual(len(rows),4);self.assertEqual(len({r['article_id'] for r in rows}),4)
    def test_nonadvancing_page_rejected(self):
        self.c.rows['articles']=[{'article_id':'a'}];self.c.ignore_range=True
        with self.assertRaises(ResolverDatabaseError):self.db.table('articles').select('*').execute()
    def test_invalid_read_cannot_become_empty(self):
        self.c.invalid_read=True
        with self.assertRaises(ResolverDatabaseError):self.db.table('articles').select('*').execute()
    def test_empty_success_write_is_confirmed(self):
        self.c.empty_write=True;self.decision().execute();self.assertEqual(self.c.write_calls,1)
    def test_empty_success_without_commit_rejected(self):
        self.c.no_commit=True
        with self.assertRaises(ResolverDatabaseError):self.decision().execute()
    def test_unknown_table_delete_and_rpc_blocked(self):
        with self.assertRaises(ResolverDatabaseError):self.db.table('auth.users')
        with self.assertRaises(AttributeError):self.db.table('events').delete()
        with self.assertRaises(AttributeError):self.db.rpc('any')
    def test_count_and_limit_preserved(self):
        self.c.rows['events']=[{'event_id':str(n)} for n in range(5)]
        self.assertEqual(self.db.table('events').select('event_id',count='exact').execute().count,5)
        self.assertEqual(self.db.table('events').select('event_id').order('event_id',desc=True).limit(1).execute().data,[{'event_id':'4'}])
    def test_payload_frozen_at_builder_creation(self):
        p={'resolution_run_id':'r','article_id':'a','decision':'new_event'}
        q=self.db.table('event_assignment_decisions').upsert(p,on_conflict='resolution_run_id,article_id');p['decision']='bad';q.execute()
        self.assertEqual(self.c.rows['event_assignment_decisions'][0]['decision'],'new_event')
    def test_retry_logs_do_not_leak_source_text(self):
        self.c.write_faults=['before'];log=io.StringIO()
        with redirect_stdout(log):self.decision(evidence={'private':'source-do-not-log'}).execute()
        self.assertIn('DB WRITE RETRY',log.getvalue());self.assertNotIn('source-do-not-log',log.getvalue())

class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.c=MemoryClient();self.db=ResolverDatabase(self.c,sleep=lambda _:None)
        self.id='78266b3a-534e-40e2-a5e9-0c7a20b75378'
        self.c.rows['collection_runs']=[{'run_id':self.id,'run_key':'week37','workflow_run_id':'34807583987','started_at':'2026-09-14','status':'success'},
            {'run_id':'98266b3a-534e-40e2-a5e9-0c7a20b75379','run_key':'newer','workflow_run_id':'99999','started_at':'2026-09-21','status':'success'}]
    def test_weekly_rerun_is_pinned_not_latest(self):
        r=resolver_collection(self.db,{'GITHUB_RUN_ID':'34807583987','GITHUB_WORKFLOW':'Weekly Observatory Pipeline'});self.assertEqual(r['run_id'],self.id)
    def test_missing_weekly_collection_cannot_fallback(self):
        with self.assertRaises(ResolverDatabaseError):resolver_collection(self.db,{'GITHUB_RUN_ID':'888','GITHUB_WORKFLOW':'Weekly Observatory Pipeline'})
    def test_standalone_can_use_latest(self):
        self.assertEqual(resolver_collection(self.db,{'GITHUB_RUN_ID':'777','GITHUB_WORKFLOW':'Resolve AI News Into Events'})['run_key'],'newer')
    def test_explicit_id_is_respected(self):self.assertEqual(resolver_collection(self.db,{'AIEO_COLLECTION_RUN_ID':self.id})['run_key'],'week37')
    def test_invalid_explicit_id_rejected(self):
        with self.assertRaises(ResolverDatabaseError):resolver_collection(self.db,{'AIEO_COLLECTION_RUN_ID':'invalid'})
        self.assertEqual(self.c.read_calls,0)
    def test_explicit_absent_id_cannot_fallback(self):
        with self.assertRaises(ResolverDatabaseError):resolver_collection(self.db,{'AIEO_COLLECTION_RUN_ID':'11111111-1111-1111-1111-111111111111'})
    def test_failed_collection_read_does_not_choose_another_week(self):
        self.c.read_faults=[APIError()]*5
        with patch('classification_database_reads.time.sleep'):
            with self.assertRaises(DatabaseReadError):resolver_collection(self.db,{'GITHUB_RUN_ID':'34807583987','GITHUB_WORKFLOW':'Weekly Observatory Pipeline'})
        self.assertEqual(self.c.read_calls,5)

class SourceIntegrationTests(unittest.TestCase):
    def test_resolver_client_wrapped_before_first_read(self):
        tree=ast.parse((SCRIPTS/'resolve_events.py').read_text())
        main=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        calls=[n for n in ast.walk(main) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name)]
        creates=[n for n in calls if n.func.id=='create_client'];wrapped=[n for n in calls if n.func.id=='ResolverDatabase'];reads=[n for n in calls if n.func.id=='latest_collection']
        self.assertEqual(len(creates),1);self.assertEqual(len(wrapped),1)
        self.assertLess(creates[0].lineno,wrapped[0].lineno);self.assertLess(wrapped[0].lineno,reads[0].lineno)
    def test_real_resolver_database_helpers_use_confirmed_writes(self):
        source=(SCRIPTS/'resolve_events.py').read_text();tree=ast.parse(source)
        names={'utc_now','iso_z','first_row','insert_decision','register_model','start_resolution_run','finish_resolution_run','link_article_to_event'}
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        self.assertEqual(len(funcs),len(names))
        module=ast.Module(body=ast.parse('from __future__ import annotations').body+funcs,type_ignores=[])
        env={'datetime':datetime,'timezone':timezone,'RESOLVER_VERSION':'test','ResolverError':RuntimeError}
        exec(compile(ast.fix_missing_locations(module),str(SCRIPTS/'resolve_events.py'),'exec'),env)
        c=MemoryClient();db=ResolverDatabase(c,sleep=lambda _:None);c.write_faults=['after']
        self.assertTrue(env['insert_decision'](db,{'resolution_run_id':'r','article_id':'a','decision':'new_event'}));self.assertEqual(c.write_calls,1)
        c.write_faults=['after'];rid,key=env['start_resolution_run'](db,collection_run_id='c',embedding_model_version_id='m',pair_model_version_id='p',verifier_model_version_id='v')
        self.assertTrue(rid);self.assertTrue(key)
        c.write_faults=['after'];env['link_article_to_event'](db,event_id='e',article=SimpleNamespace(article_id='a'),similarity=1,canonical=True)
        env['finish_resolution_run'](db,resolution_run_id=rid,status='success',article_count=1,counts={'existing_assignment':0,'auto_merge':0,'new_event':1,'review':0,'verifier_calls':0},active_event_count=1,pending_event_count=0)
        self.assertTrue(env['register_model'](db,name='model',revision='sha',task='task',language_scope='multilingual',notes='test'))
        for table in ('event_assignment_decisions','event_resolution_runs','event_articles','model_versions'):self.assertEqual(len(c.rows[table]),1)

if __name__=='__main__':unittest.main()
