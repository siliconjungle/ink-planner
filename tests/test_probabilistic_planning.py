import copy
import unittest
import subprocess
import sys
from ink_planner.stochastic_search import Policy,search_restarts
from ink_planner.semantic_search import search
from ink_planner.autotune import tune
from ink_planner.synthesis import synthesize,evaluate,binary,variable,word,SORT
from ink_planner.neighbourhood import Neighbourhood,sketch
from ink_planner.adaptive_search import select


class ProbabilisticSearch(unittest.TestCase):
    def fixture(self):
        x=variable('x');source=binary('+',x,word(0))
        base=dict(params={'x':SORT},conditions=[],**{'from':source,'to':x})
        objects={'bad':dict(base,conditions=[{'from':x,'to':word(0)}]),'good':base}
        return {'functions':{'f':source}},{'roots':list(objects),'objects':objects}
    def test_restarts_share_budget_and_never_accept_rejected_candidate(self):
        subject,catalogue=self.fixture()
        for seed in range(10):
            calls=[]
            def check(apps):calls.append(apps);return apps[-1]['law']=='good'
            apps,r=search_restarts(search,subject,catalogue,check,budget=8,restarts=4,seed=seed)
            self.assertLessEqual(len(calls),8);self.assertEqual(len(calls),r['attempts'])
            self.assertTrue(all(a['law']=='good' for a in apps))
    def test_seeded_search_replays_and_rejection_keeps_baseline(self):
        s,c=self.fixture();run=lambda:search_restarts(search,s,c,lambda _:False,budget=8,seed=72)
        a,r=run();b,q=run();self.assertEqual(a,b);self.assertEqual(a,[])
        self.assertEqual(r['attempts'],q['attempts']);self.assertEqual(r['selected_cost'],r['initial_cost'])
        self.assertEqual(search_restarts(search,s,c,lambda _:True,budget=0)[0],[])
    def test_failed_selfhost_cannot_admit_invalid_proof(self):
        s,c=self.fixture()
        class Broken:
            def score(self,_):raise TimeoutError('device lost')
        apps,r=search_restarts(search,s,c,lambda _:False,budget=8,scorer=Broken())
        self.assertEqual(apps,[]);self.assertTrue(any(x.get('selfhost_failures') for x in r['search_policy']['runs']))
    def test_removed_learned_search_flags_are_rejected(self):
        base=['--compiler','unused','--core','unused','--snapshot','unused','-o','unused']
        for module in ['semantic_search','parallel_search','adaptive_search']:
            for flags in [['--search-model','unused']]+([['--search-strategy','learned']] if module=='semantic_search' else []):
                with self.subTest(module=module,flags=flags):
                    required=['--knowledge-root','unused','--target-context','unused'] if module=='adaptive_search' else []
                    run=subprocess.run([sys.executable,'-m','ink_planner.'+module,*base,*required,*flags],capture_output=True,text=True)
                    self.assertEqual(run.returncode,2)
                    self.assertIn(flags[0],run.stderr)
    def test_removed_trainer_module_is_unavailable(self):
        run=subprocess.run([sys.executable,'-m','ink_planner.search_policy','--rows','unused','-o','unused'],capture_output=True,text=True)
        self.assertNotEqual(run.returncode,0)
        self.assertIn('No module named ink_planner.search_policy',run.stderr)
    def test_removed_learned_policy_is_rejected(self):
        s,c=self.fixture()
        with self.assertRaises(ValueError):search_restarts(search,s,c,lambda _:True,strategy='learned')
        with self.assertRaises(ValueError):Policy(strategy='learned')
    def test_synthesis_proposals_need_proof_even_when_all_samples_match(self):
        x=variable('x');source=x
        for _ in range(12):source=binary('+',source,x)
        target,r=synthesize(source,{'x':SORT},lambda _:False,seed=12,budget=128)
        self.assertEqual(target,source);self.assertGreater(r['proof_attempts'],0)
        target,r=synthesize(source,{'x':SORT},lambda t:True,seed=12,budget=128)
        self.assertLess(r['selected_nodes'],r['initial_nodes'])
        for v in [0,1,2,2**31,2**32-1]:self.assertEqual(evaluate(target,{'x':v}),evaluate(source,{'x':v}))
    def test_ann_hints_keep_all_candidates_and_cannot_admit_false_proof(self):
        s,c=self.fixture()
        class FailedProvider:
            def Index(self,**kwargs):raise RuntimeError('ANN unavailable')
        ann=Neighbourhood(c,provider=(FailedProvider(),None))
        self.assertFalse(ann.receipt['active']);self.assertEqual(ann.hints(s['functions']['f']),())
        self.assertEqual(search(s,c,lambda _:False,neighbourhood=ann)[0],[])
        ann.index=object()
        pending=[(0,{},[{'law':'bad'}]),(1,{},[{'law':'good'}])]
        reordered=ann.order(pending,{'good'},1)
        self.assertEqual(len(reordered),len(pending));self.assertEqual(reordered[0],pending[1])
        self.assertEqual(ann.order(pending,{'good'},0)[0],pending[0])
        ann.index=object();ann.np=None
        self.assertEqual(ann.hints(s['functions']['f']),());self.assertFalse(ann.receipt['active'])
        self.assertEqual(len(sketch(s['functions']['f'])),64)


class OfflineTuning(unittest.TestCase):
    context=dict(target='test-machine',code='test-code',boundary='complete-owned',workload='training-cohort')
    def test_holdout_required_for_release_and_losses_remain(self):
        configs=[dict(workers=i) for i in range(5)]
        costs=[100,80,50,110,140]
        evaluate=lambda c:dict(correct=True,samples_ns=[costs[c['workers']]]*3,setup_ns=0)
        r=tune(configs,evaluate,budget=5,context=self.context)
        self.assertEqual(r['proposal'],configs[2]);self.assertEqual(r['selected'],configs[0]);self.assertFalse(r['released'])
        holdout=lambda a,b:[dict(correct=True,candidate_ns=50,baseline_ns=100,candidate_total_ns=60,baseline_total_ns=100)]
        r=tune(configs,evaluate,budget=5,context=self.context,heldout=holdout)
        self.assertTrue(r['released']);self.assertEqual(len(r['observations']),5)
        losing=lambda a,b:[dict(correct=True,candidate_ns=50,baseline_ns=100,candidate_total_ns=101,baseline_total_ns=100)]
        self.assertFalse(tune(configs,evaluate,budget=5,context=self.context,heldout=losing)['released'])
    def test_setup_incorrect_results_and_device_fallback_are_not_winners(self):
        configs=[dict(target=x) for x in ['cpu','gpu','broken']]
        def evaluate(c):
            return dict(correct=c['target']!='broken',samples_ns=[1 if c['target']!='cpu' else 100],setup_ns=10000 if c['target']=='gpu' else 0)
        r=tune(configs,evaluate,budget=3,context=self.context)
        self.assertEqual(r['proposal'],configs[0])
        def fail(c):return dict(correct=True,fallback=c['target']!='cpu',samples_ns=[100],setup_ns=0)
        self.assertEqual(tune(configs,fail,budget=3,context=self.context)['proposal'],configs[0])
        with self.assertRaises(ValueError):tune(configs,lambda c:dict(correct=True,samples_ns=[1]),budget=3,context=self.context)
    def test_adaptive_selection_needs_observed_size_context_and_equal_quality(self):
        features=dict(catalogue_entries=1000,core_nodes=20,budget=64,rounds=12)
        rows=[dict(strategy=s,correct=True,training_ns=[n]*4,heldout=[dict(round=i,ns=n,selected_cost=10) for i in range(3)]) for s,n in [('baseline',100),('hnsw',50)]]
        profile=dict(schema=1,context=self.context,cohorts=[dict(features=features,inputs=dict(core=None,snapshot=None),rows=rows)])
        self.assertEqual(select(profile,self.context,features)['strategy'],'hnsw')
        small={**features,'catalogue_entries':10}
        small_rows=copy.deepcopy(rows);small_rows[1]['training_ns']=[150]*4
        profile['cohorts'].append(dict(features=small,inputs=dict(core=None,snapshot=None),rows=small_rows))
        self.assertEqual(select(profile,self.context,small)['strategy'],'baseline')
        self.assertEqual(select(profile,self.context,{**features,'catalogue_entries':1001})['strategy'],'baseline')
        self.assertEqual(select(profile,{},features)['strategy'],'baseline')
        obsolete=copy.deepcopy(profile);obsolete['cohorts'][0]['rows'][1]['strategy']='learned'
        self.assertEqual(select(obsolete,self.context,features)['strategy'],'baseline')
        rows[1]['heldout'][0]['selected_cost']=11
        self.assertEqual(select(profile,self.context,features)['strategy'],'baseline')
        rows[1]['heldout'][0]['selected_cost']=10;rows[1]['heldout'][0]['ns']=95
        self.assertEqual(select(profile,self.context,features)['strategy'],'baseline')


if __name__=='__main__':unittest.main()
