"""Modular search composes checked prefixes and respects shared resource limits."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from ink_planner.modular import SCHEMA, cached_applications, function_key, plan
from ink_planner.semantic_search import search
from ink_planner.target_costs import COMPONENTS, TargetCostModel
from ink_planner import plan_cache
from producers.semantic_laws import value, var, word, op


class ModularPlanning(unittest.TestCase):
    def fixture(self, count=3):
        x=var(value('U32'),'x');source=op(x['sort'],'binary:+',x,word('U32',0))
        names=[f'f{i}' for i in range(count)]
        subject=dict(functions=dict.fromkeys(names,source))
        catalogue=dict(roots=['law'],objects={'law':dict(params={'x':x['sort']},conditions=[],dependencies=[],**{'from':source,'to':x})})
        program=dict(functions=[dict(name=n,params=[['x','U32']],result='U32',body={'Return':0}) for n in names])
        return subject,catalogue,program

    def test_global_budget_and_prefixes_include_cache_replay(self):
        subject,catalogue,program=self.fixture()
        with tempfile.TemporaryDirectory() as tmp:
            calls=[]
            def check(chain):calls.append(copy.deepcopy(chain));return True
            apps,r=plan(subject,catalogue,check,search,program,tmp,{},budget=2)
            self.assertEqual(r['attempts'],len(calls));self.assertLessEqual(len(calls),2)
            self.assertEqual(len(apps),2)
            self.assertEqual([len(c)for c in calls],[1,2])
            calls.clear()
            apps,r=plan(subject,catalogue,check,search,program,tmp,{},budget=1)
            self.assertEqual(len(calls),1);self.assertEqual(r['attempts'],1)
            self.assertEqual(len(apps),1);self.assertEqual(r['modular']['hits'],1)
            self.assertEqual(apps,calls[-1])

    def test_zero_budget_or_time_never_calls_checker_and_does_not_overwrite_hint(self):
        subject,catalogue,program=self.fixture(1)
        with tempfile.TemporaryDirectory() as tmp:
            apps,_=plan(subject,catalogue,lambda _:True,search,program,tmp,{},budget=3)
            saved={p.name:p.read_bytes()for p in Path(tmp).glob('*.json')}
            for options in [dict(budget=0),dict(time_seconds=0)]:
                with self.subTest(options=options):
                    selected,r=plan(subject,catalogue,lambda _:self.fail('zero budget checked'),search,program,tmp,{},**options)
                    self.assertEqual(selected,[]);self.assertEqual(r['attempts'],0)
                    self.assertEqual(saved,{p.name:p.read_bytes()for p in Path(tmp).glob('*.json')})

    def test_rejected_cached_prefix_is_replanned_with_remaining_budget(self):
        subject,catalogue,program=self.fixture(2)
        with tempfile.TemporaryDirectory() as tmp:
            plan(subject,catalogue,lambda _:True,search,program,tmp,{},budget=8)
            calls=[]
            def check(chain):
                calls.append(copy.deepcopy(chain))
                return len(chain)==1
            apps,r=plan(subject,catalogue,check,search,program,tmp,{},budget=3)
            self.assertEqual(r['attempts'],len(calls));self.assertEqual(len(calls),3)
            self.assertEqual(r['rejected'],2);self.assertTrue(r['modular']['replanned'])
            self.assertEqual(len(apps),1);self.assertEqual(apps,calls[0])

    def test_deadline_is_shared_across_functions(self):
        subject,catalogue,program=self.fixture(3);clock=[0.0];calls=[]
        def check(chain):calls.append(chain);clock[0]+=2;return True
        with tempfile.TemporaryDirectory() as tmp, patch('ink_planner.modular.time.monotonic',side_effect=lambda:clock[0]):
            apps,r=plan(subject,catalogue,check,search,program,tmp,{},budget=8,time_seconds=1)
        self.assertEqual(len(calls),1);self.assertEqual(len(apps),1)
        self.assertTrue(r['memo']['timed_out'])

    def test_malformed_oversized_and_missing_law_caches_are_misses(self):
        subject,catalogue,program=self.fixture(1)
        with tempfile.TemporaryDirectory() as tmp:
            key=function_key('f0',subject,program,{})
            path=Path(tmp)/(key+'.json')
            malformed=['{', '[]', '{}', json.dumps(dict(schema=SCHEMA,function='other',applications=[])),
                json.dumps(dict(schema=SCHEMA,function='f0',applications=[{}])),
                json.dumps(dict(schema=SCHEMA,function='f0',applications=[dict(function='f0',path=[],law='missing',arguments={},premises=[])]))]
            for text in malformed:
                path.write_text(text)
                apps,r=plan(subject,catalogue,lambda _:True,search,program,tmp,{},budget=2)
                self.assertEqual(len(apps),1);self.assertEqual(r['modular']['misses'],1)
            path.write_text(' '*1025)
            with patch.object(plan_cache,'MAX_BYTES',1024):
                self.assertIsNone(cached_applications(tmp,key,'f0'))

    def test_atomic_cache_failure_preserves_prior_bytes_and_checked_result(self):
        subject,catalogue,program=self.fixture(1)
        with tempfile.TemporaryDirectory() as tmp:
            key=function_key('f0',subject,program,{})
            path=Path(tmp)/(key+'.json');path.write_text('{old invalid hint')
            before=path.read_bytes()
            with patch('ink_planner.plan_cache.os.replace',side_effect=OSError('injected')):
                apps,r=plan(subject,catalogue,lambda _:True,search,program,tmp,{},budget=2)
            self.assertEqual(len(apps),1);self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(Path(tmp).glob('.ink-plan-*')),[])

    def test_refresh_and_alternatives_include_accepted_prefix(self):
        subject,catalogue,program=self.fixture(2)
        alternatives=[];refreshed=[]
        def refresh(bodies):refreshed.append(copy.deepcopy(bodies));return {}
        with tempfile.TemporaryDirectory() as tmp:
            apps,r=plan(subject,catalogue,lambda _:True,search,program,tmp,{},budget=4,
                        refresh=refresh,alternatives=alternatives)
        self.assertEqual([len(a)for a in alternatives],[1,2]);self.assertEqual(alternatives[-1],apps)
        self.assertTrue(all(set(b)=={'f0','f1'}for b in refreshed))
        self.assertTrue(any(b['f0']['node']=={'Free':'x'}for b in refreshed))

    def test_schema2_search_keeps_helper_context(self):
        subject,catalogue,program=self.fixture(2)
        x=var(value('U32'),'x')
        subject['functions']['f1']=op(x['sort'],'call:f0',x)
        model=TargetCostModel(dict(schema=2,target='javascript',boundary='owned',cardinality=dict(default=1,by_parameter={}),weights=dict.fromkeys(COMPONENTS,1)))
        seen=[]
        def run(*args,**kwargs):
            apps,r=search(*args,**kwargs);seen.append(r);return apps,r
        with tempfile.TemporaryDirectory() as tmp:
            plan(subject,catalogue,lambda _:True,run,program,tmp,{},budget=4,operation_costs=model,core_program=program)
        self.assertEqual(len(seen),2)
        self.assertTrue(all(set(r['initial_components']['estimates'])=={'f0','f1'} for r in seen))
        self.assertTrue(all(r['initial_components']['complete'] for r in seen))
        self.assertNotEqual(seen[0]['initial_components']['definitions_consumed'],seen[1]['initial_components']['definitions_consumed'])

    def test_function_keys_invalidate_callers_without_invalidating_unrelated_function(self):
        subject,catalogue,program=self.fixture(3)
        program['functions'][1]['body']={'Call':['f0',[]]}
        keys=lambda p:{n:function_key(n,subject,p,{})for n in subject['functions']}
        before=keys(program);changed=copy.deepcopy(program)
        changed['functions'][0]['body']={'Return':1}
        after=keys(changed)
        self.assertNotEqual(before['f0'],after['f0']);self.assertNotEqual(before['f1'],after['f1'])
        self.assertEqual(before['f2'],after['f2'])

    def test_real_cli_schema2_modular_cache_and_alternative_stream(self):
        from ink_knowledge import Store, semantic_entry, canonical, identity
        from producers.semantic_laws import law
        root=Path(__file__).resolve().parents[2]
        compiler=Path(os.environ.get('INK_COMPILER',root/'target/debug/ink')).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);source=d/'source.ink';core=d/'core.json';values=d/'values.json'
            source.write_text('module modular;fn first(x:u32)->u32{return x+0;}fn second(x:u32)->u32{return x+0;}')
            result=subprocess.run([str(compiler),'emit-core',str(source),'-o',str(core)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            x=var(value('U32'),'x');obj=semantic_entry(law({'x':x['sort']},op(x['sort'],'binary:+',x,word('U32',0)),x,'Normalize'))
            store=Store.publish(d/'store',[obj]);store.ensure_index()
            model=dict(schema=2,target='javascript',boundary='owned',cardinality=dict(default=1,by_parameter={}),weights=dict.fromkeys(COMPONENTS,1))
            (d/'cost.json').write_text(json.dumps(model));values.write_text('[4294967295]')
            for iteration in range(3):
                if iteration==2:
                    for path in (d/'cache').glob('*.json'):
                        hint=json.loads(path.read_text())
                        if hint['function']=='first':
                            hint['applications'][0]['arguments']['x']=word('U32',0)
                            path.write_text(json.dumps(hint))
                command=[sys.executable,str(root/'planner/plan.py'),'--compiler',str(compiler),'--core',str(core),
                    '--snapshot',str(d/'store/store/snapshot.json'),'--cost-model',str(d/'cost.json'),
                    '--modular-cache',str(d/'cache'),'-o',str(d/'selection.json'),'--budget','16',
                    '--alternatives',str(d/'alternatives.jsonl'),'--stream-alternatives']
                result=subprocess.run(command,capture_output=True,text=True,timeout=60)
                self.assertEqual(result.returncode,0,result.stderr)
                receipt=json.loads(result.stdout);self.assertLessEqual(receipt['attempts'],16)
                self.assertEqual(receipt['initial_components']['complete'],True)
                self.assertGreater(receipt['initial_cost'],receipt['selected_cost'])
                self.assertEqual(receipt['modular']['hits'],[0,2,1][iteration])
                if iteration==2:
                    self.assertGreaterEqual(receipt['rejected'],1)
                    self.assertTrue(receipt['modular']['replanned'])
                package=json.loads((d/'selection.json').read_text());self.assertEqual(len(package['applications']),2)
                for row in (d/'alternatives.jsonl').read_text().splitlines():
                    path=d/'alternative.json';path.write_text(row)
                    checked=subprocess.run([str(compiler),'check-selection',str(core),str(path)],capture_output=True,text=True)
                    self.assertEqual(checked.returncode,0,checked.stderr)
                for name in ['first','second']:
                    result=subprocess.run([str(compiler),'run',str(core),name,str(values),'--core','--selection',str(d/'selection.json')],capture_output=True,text=True)
                    self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(json.loads(result.stdout),4294967295)


if __name__=='__main__':unittest.main()
