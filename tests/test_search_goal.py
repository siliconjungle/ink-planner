import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from ink_planner.search_goal import document
from ink_planner.compiled_planning import explore
from ink_planner.compiled_cache import key,save,load


class CompleteDocuments(unittest.TestCase):
    def test_lossless_scalar_tokens_keys_order_and_deep_structure(self):
        value={'sentinel':2**64-1,'exact':-(2**63),'nested':[None,True,False,'雪',{'__proto__':'own'}]}
        for _ in range(90):value=[value]
        flat=document(value)
        def rebuild(index):
            node=flat['nodes'][index];kind=node['kind'];text=node['value'];edges=node['edges']
            if kind==0:return None
            if kind==1:return text=='true'
            if kind==2:return int(text)
            if kind==3:return text
            if kind==4:return [rebuild(edge['node'])for edge in edges]
            return {edge['key']:rebuild(edge['node'])for edge in edges}
        self.assertEqual(rebuild(flat['root']),value)
        self.assertEqual(document({'b':2,'a':1}),document({'a':1,'b':2}))
        self.assertTrue(any(node['kind']==2 and node['value']==str(2**64-1)for node in flat['nodes']))
        too_deep=0
        for _ in range(130):too_deep=[too_deep]
        for bad in [too_deep,float('nan'),1.25,{1:'bad key'}]:
            with self.assertRaises(ValueError):document(bad)
        for bad in ['x'*1000,[None]*1000,{'key'+str(i):None for i in range(1000)}]:
            with self.assertRaisesRegex(ValueError,'byte budget'):document(bad,100)

    def test_goal_failure_rebuilds_the_existing_frontier_with_structural_priorities(self):
        active=True;generation=0;order=[]
        def check(packet):return {'source':packet['steps'][-1]['package']if packet['steps']else'root'}
        def expand(state,_):
            order.append(state['source'])
            if state['source']=='root':
                for name in ['a','b','failure']:
                    yield {'status':'proposal','step':{'kind':'rewrite','package':name}}
        def priority(state):
            nonlocal active,generation
            if state['source']=='failure':active=False;generation=1
            structural={'root':0,'a':1,'b':2,'failure':3}[state['source']]
            return -structural if active else structural
        _,receipt=explore({'steps':[]},expand,check,priority,priority_epoch=lambda:generation)
        self.assertEqual(order,['root','a','b','failure'])
        self.assertEqual(receipt['priority_rebuilds'],1)


class CapturedGoalCaches(unittest.TestCase):
    def test_context_code_runtime_and_process_budgets_bind_reuse(self):
        with patch('ink_planner.offline_measurements.target_context',return_value={}):
            planner=SimpleNamespace(compiler_hash='a'*64,registry_digest='b'*64,code_hash='c'*64,
                search_goal=SimpleNamespace(binding={'context':'d'*64,'module_sha256':'e'*64,'node':{'node':'fixture'}}))
            options=SimpleNamespace(search_goal_timeout=1.0,search_goal_memory_mb=128,search_goal_input_bytes=4000)
            original=key(planner,'source','snapshot',options,[])
            for field in ['context','module_sha256','node']:
                old=planner.search_goal.binding[field];planner.search_goal.binding[field]='different'
                self.assertNotEqual(original,key(planner,'source','snapshot',options,[]));planner.search_goal.binding[field]=old
            for field in ['search_goal_timeout','search_goal_memory_mb','search_goal_input_bytes']:
                old=getattr(options,field);setattr(options,field,old+1)
                self.assertNotEqual(original,key(planner,'source','snapshot',options,[]));setattr(options,field,old)

    def test_failed_and_malformed_goal_receipts_never_become_cached_search_results(self):
        with tempfile.TemporaryDirectory()as directory:
            ident='a'*64
            for goal in [[],True,'invalid',{'failures':1},{'disabled':True}]:
                save(directory,ident,{},dict(search_goal=goal));self.assertIsNone(load(directory,ident))
            save(directory,ident,{},dict(search_goal={'failures':0}))
            path=Path(directory)/'compiled-cache'/f'{ident}.json'
            value=json.loads(path.read_bytes());value['receipt']['search_goal']=False;path.write_text(json.dumps(value))
            self.assertIsNone(load(directory,ident))


if __name__=='__main__':unittest.main()
