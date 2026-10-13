import unittest
from ink_planner.compiled_planning import explore

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
