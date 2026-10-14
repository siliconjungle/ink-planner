import unittest
from ink_planner.compiled_planning import explore,structural_priority,workload_frequencies

class WorkloadPriorities(unittest.TestCase):
    def test_phase_labels_bind_actual_calls_and_reachable_helpers(self):
        spec=dict(profile={'training-a':2,'training-b':3},phases={
            'training-a':[dict(function='main',args=[])],
            'training-b':[dict(function='main',args=[]),dict(function='other',args=[])]})
        self.assertEqual(workload_frequencies(spec),{'call:main':5,'call:other':3})
        function=lambda name,body:dict(name=name,params=[],body=body)
        core=dict(program=dict(records={},functions=[
            function('main',{'Call':['helper',[]]}),
            function('helper',{'Call':['scalar',[]]}),
            function('unused',{'Method':[{'Var':'xs'},'expensive',[]]})]))
        prices={'estimate:scalar':1,'estimate:collection':100}
        self.assertEqual(structural_priority(core,{'call:main':5},prices),10)
        self.assertEqual(structural_priority(core,{},prices),102)
        # Recursive reachability is finite; it does not unfold an unbounded
        # runtime call count or claim a timing prediction.
        core['program']['functions'][1]['body']={'Call':['main',[]]}
        self.assertEqual(structural_priority(core,{'call:main':5},prices),10)

    def test_copy_hypothesis_prices_immutable_fields_and_excludes_owners(self):
        def fixture(ty):
            return dict(program=dict(records={'Input':[['payload',ty]]},functions=[
                dict(name='main',params=[['input',{'Named':'Input'}]],body={'Call':['helper',[{'Var':'input'}]]}),
                dict(name='helper',params=[['input',{'Named':'Input'}]],body={'Num':0})]))
        prices={'estimate:scalar':1,'estimate:immutable_collection_argument':40}
        self.assertEqual(structural_priority(fixture({'List':'U32'}),{'call:main':1},prices),41)
        self.assertEqual(structural_priority(fixture({'Buffer':'U32'}),{'call:main':1},prices),1)
        self.assertEqual(structural_priority(fixture('U32'),{'call:main':1},prices),1)

class ExpansionDiagnostics(unittest.TestCase):
    def test_state_limit_close_records_unfinished_recipe_families(self):
        import tempfile,time
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import patch
        from ink_knowledge import canonical
        from ink_planner.compiled_planning import Planner
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);planner=Planner.__new__(Planner)
            planner.directory=planner.root=root;planner.compiler=root/'compiler'
            planner.compiler_hash='compiler';planner.code_hash='code';planner.snapshot='snapshot'
            planner.registry_digest='registry';planner.deadline=time.monotonic()+5
            planner.options=SimpleNamespace(target='rust',registry=None,check_timeout=1,proposal_timeout=1,semantic_budget=1,semantic_rounds=1,candidate_limit=4,memory_bytes=1000000)
            planner.recipes=[dict(id=name,inputs=['core']) for name in ['fast','still-working','not-started']]
            planner.notes=[];planner.recipe_cache_hits=0;planner.model=lambda _:None
            closed=[]
            def stream(jobs,*args):
                try:
                    yield jobs[0],dict(kind='line',raw=canonical(dict(recipe='fast',registry_sha256='registry')))
                    yield jobs[0],dict(kind='line',raw=canonical(dict(status='proposal',label='candidate',step=dict(kind='physical',package={}))))
                finally:closed.append(True)
            state=dict(source='source',core=dict(program={}))
            with patch('ink_planner.recipe_streams.run',stream):
                expansion=planner.expand(state,{})
                self.assertEqual(next(expansion)['recipe'],'fast');expansion.close()
            self.assertEqual(closed,[True])
            deferred={x['recipe'] for x in planner.notes if x['status']=='deferred'}
            self.assertEqual(deferred,{'fast','still-working','not-started'})
            self.assertFalse(any(x['status']=='visited' for x in planner.notes))

class RecursivePlans(unittest.TestCase):
    def test_maintained_roles_are_distinct_from_baseline_and_each_other(self):
        from ink_planner.compiled_planning import fingerprint
        baseline=dict(source='a'*64)
        first=dict(baseline,maintained=dict(package_sha256='b'*64,keeps=['total']))
        second=dict(baseline,maintained=dict(package_sha256='c'*64,keeps=['total']))
        self.assertEqual(len({fingerprint(baseline),fingerprint(first),fingerprint(second)}),3)
    def test_semantic_search_instantiates_after_an_admitted_intermediate(self):
        from ink_planner.semantic_search import search
        from planner.tests.test_term_matching import var,term
        x=var('x')
        first=dict(params={'x':x['sort']},conditions=[],**{'from':term('method:a',x),'to':term('method:b',x)})
        second=dict(params={'x':x['sort']},conditions=[],**{'from':term('method:b',x),'to':x})
        catalogue=dict(roots=['first'],objects={'first':first})
        def refresh(bodies):
            return {'second':second}if bodies['f']==term('method:b',var('input'))else{}
        applications,receipt=search(dict(functions={'f':term('method:a',var('input'))}),catalogue,lambda _:True,budget=8,refresh=refresh)
        self.assertEqual([a['law']for a in applications],['first','second'])
        self.assertLess(receipt['selected_cost'],receipt['initial_cost'])
    def test_revisits_layers_beyond_old_round_bound_and_stops_cycles(self):
        initial=dict(steps=[])
        def check(p):
            value=0
            for step in p['steps']:
                if step['package']['from']!=value:raise ValueError('stale source')
                value=step['package']['to']
            return dict(source=str(value),physical=None,properties=[],representation=None,route=None)
        def expand(state,_):
            n=int(state['source'])
            if n<80:yield dict(status='proposal',step=dict(kind='rewrite'if n%2 else'physical',package=dict(**{'from':n,'to':n+1})))
            yield dict(status='proposal',step=dict(kind='rewrite',package=dict(**{'from':n,'to':0})))
        states,receipt=explore(initial,expand,check,lambda s:-int(s['source']),budget=200,time_seconds=30)
        self.assertIn('80',{s['source']for _,s in states.values()})
        self.assertEqual(receipt['stop_reason'],'fixed-point')
        self.assertEqual(len(states),81)
    def test_rejected_proof_does_not_hide_a_valid_edge(self):
        def check(p):
            if p['steps']and p['steps'][-1]['package'].get('bad'):raise ValueError('false equality')
            return dict(source='selected'if p['steps']else'baseline',physical=None,properties=[])
        def expand(state,_):
            if state['source']=='baseline':
                for bad in [True,False]:yield dict(status='proposal',step=dict(kind='rewrite',package=dict(bad=bad)))
        states,receipt=explore(dict(steps=[]),expand,check,lambda _:0,time_seconds=30)
        self.assertEqual(len(states),2);self.assertEqual(len(receipt['rejected']),1)
    def test_resource_exhaustion_keeps_admitted_baseline(self):
        check=lambda p:dict(source=str(len(p['steps'])),physical=None,properties=[])
        expand=lambda s,_:iter([dict(status='proposal',step=dict(kind='rewrite',package={}))])
        for options in [dict(budget=0),dict(state_limit=1),dict(time_seconds=0),dict(memory_bytes=1)]:
            states,receipt=explore(dict(steps=[]),expand,check,lambda _:0,**options)
            self.assertEqual(len(states),1)
            self.assertNotEqual(receipt['stop_reason'],'fixed-point')

    def test_checker_deadline_keeps_searching_and_reports_unchecked_edge(self):
        from ink_planner.compiled_planning import CheckerBudgetError
        def check(p):
            if p['steps'] and p['steps'][-1]['package']['slow']:
                raise CheckerBudgetError('plan checker process time budget')
            return dict(source='alternative'if p['steps']else'baseline')
        def expand(state,_):
            if state['source']=='baseline':
                for slow in [True,False]:
                    yield dict(status='proposal',step=dict(kind='rewrite',package=dict(slow=slow)))
        states,receipt=explore(dict(steps=[]),expand,check,lambda _:0)
        self.assertEqual(len(states),2)
        self.assertEqual(receipt['stop_reason'],'checker-time')
        self.assertEqual(receipt['unchecked_checks'],1)
        self.assertEqual(receipt['rejected'][0]['gate'],'checker-budget')

    def test_subprocess_deadline_does_not_claim_fixed_point(self):
        import subprocess
        def check(p):
            if p['steps']:raise subprocess.TimeoutExpired('checker',1)
            return dict(source='baseline')
        def expand(state,_):
            yield dict(status='proposal',step=dict(kind='rewrite',package={}))
        states,receipt=explore(dict(steps=[]),expand,check,lambda _:0)
        self.assertEqual(len(states),1)
        self.assertEqual(receipt['stop_reason'],'checker-time')
        self.assertEqual(receipt['unchecked_checks'],1)

if __name__=='__main__':unittest.main()

class EffortAndCleanup(unittest.TestCase):
    def test_explicit_budgets_override_effort_presets(self):
        from types import SimpleNamespace
        from ink_planner.compiled_planning import effort_options,EFFORTS
        for effort in EFFORTS:
            args=SimpleNamespace(planning_effort=effort,**{k:None for k in EFFORTS[effort]})
            args.time_seconds=17;effort_options(args)
            self.assertEqual(args.time_seconds,17);self.assertEqual(args.budget,EFFORTS[effort]['budget'])
    def test_search_closes_suspended_recipe_generator_at_state_bound(self):
        closed=[]
        def expand(state,_):
            try:
                yield dict(status='proposal',step=dict(kind='rewrite',package={}))
                raise AssertionError('must not resume after state bound')
            finally:closed.append(True)
        check=lambda p:dict(source=str(len(p['steps'])),properties=[])
        explore(dict(steps=[]),expand,check,lambda _:0,state_limit=1,time_seconds=1)
        self.assertEqual(closed,[True])

class ProducerDeadline(unittest.TestCase):
    def test_unfinished_producers_do_not_report_a_fixed_point(self):
        check=lambda p:dict(source='original',properties=[])
        expand=lambda *_:iter([dict(status='search-limit',reason='time')])
        _,receipt=explore(dict(steps=[]),expand,check,lambda _:0,time_seconds=10)
        self.assertEqual(receipt['stop_reason'],'time')


class MemoryFrontier(unittest.TestCase):
    def test_oversized_chain_does_not_hide_shorter_chain_to_same_state(self):
        from ink_knowledge import canonical
        initial=dict(steps=[])
        step=lambda padding:dict(kind='rewrite',package=dict(padding=padding))
        small=dict(steps=[step('')]);large=dict(steps=[step('x'*4096)])
        def check(packet):return dict(source='alternative'if packet['steps']else'baseline')
        size=lambda p:len(canonical(p))+len(canonical(check(p)))
        cap=size(initial)+size(small)
        def expand(state,_):
            if state['source']=='baseline':
                for p in [large,small]:yield dict(status='proposal',steps=p['steps'])
        states,receipt=explore(initial,expand,check,lambda _:0,memory_bytes=cap)
        self.assertEqual(len(states),2)
        self.assertTrue(any(p==small for p,_ in states.values()))
        self.assertEqual(receipt['retained_bytes'],cap)
        self.assertEqual(receipt['memory_deferred'],1)
        self.assertEqual(receipt['stop_reason'],'memory')
        self.assertFalse(receipt['rejected'])

    def test_large_candidates_do_not_starve_queued_states(self):
        initial=dict(steps=[])
        def check(packet):
            last=packet['steps'][-1]['package']if packet['steps']else{}
            return dict(source=last.get('to','baseline'))
        def expand(state,_):
            if state['source']=='baseline':
                for name,padding in [('branch',''),('oversized','x'*10000)]:
                    yield dict(status='proposal',step=dict(kind='rewrite',package=dict(to=name,padding=padding)))
            elif state['source']=='branch':
                yield dict(status='proposal',step=dict(kind='rewrite',package=dict(to='leaf')))
        states,receipt=explore(initial,expand,check,lambda _:0,memory_bytes=2000)
        self.assertEqual({s['source']for _,s in states.values()},{'baseline','branch','leaf'})
        self.assertEqual(receipt['memory_deferred'],1)
        self.assertLessEqual(receipt['retained_bytes'],2000)

    def test_deferred_stream_still_obeys_attempt_budget_and_closes(self):
        closed=[]
        def expand(*_):
            try:
                while True:
                    yield dict(status='proposal',step=dict(kind='rewrite',package=dict(padding='x'*10000)))
            finally:closed.append(True)
        states,receipt=explore(dict(steps=[]),expand,
            lambda p:dict(source='other'if p['steps']else'baseline'),lambda _:0,
            budget=3,memory_bytes=100)
        self.assertEqual(len(states),1)
        self.assertEqual(receipt['attempts'],3)
        self.assertEqual(receipt['memory_deferred'],3)
        self.assertEqual(receipt['stop_reason'],'proof-attempts')
        self.assertEqual(closed,[True])
