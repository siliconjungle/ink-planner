import unittest
from ink_planner.semantic_search import search


def variable(name):
    return {'sort': {'Value': 'U32'}, 'node': {'Free': name}}


class SearchProofAlternatives(unittest.TestCase):
    def test_diverse_beam_retains_a_more_expensive_route_to_no_materialisation(self):
        x=variable('x'); seq={'sort':{'Value':{'List':'U32'}},'node':{'Free':'xs'}}
        op=lambda name,args:dict(sort=x['sort'],node={'Op':[name,args]})
        source=op('call:start',[x,seq])
        mapped=dict(sort=seq['sort'],node={'Op':['method:map',[seq,dict(sort={'Arrow':[x['sort'],x['sort']]},node={'Lambda':dict(sort=x['sort'],node={'Bound':0})})]]})
        base=dict(params={'x':x['sort'],'xs':seq['sort']},conditions=[],**{'from':source})
        objects={f'cheap{i}':dict(base,to=op(f'call:temptation{i}',[mapped])) for i in range(2)}
        barrier=op('call:barrier',[x])
        objects['barrier']=dict(base,to=barrier)
        objects['finish']=dict(params={'x':x['sort']},conditions=[],**{'from':barrier,'to':x})
        costs={'call:start':1000,'call:barrier':500,'call:temptation0':0,'call:temptation1':0,'method:map':1}
        args=({'functions':{'f':source}},{'roots':list(objects),'objects':objects},lambda _:True)
        ordinary,old=search(*args,width=2,rounds=2,operation_costs=costs,diverse=False)
        selected,new=search(*args,width=2,rounds=2,operation_costs=costs)
        self.assertEqual([a['law'] for a in selected],['barrier','finish'])
        self.assertLess(new['selected_cost'],old['selected_cost'])
        self.assertEqual(new['beam_diversity']['selected']['allocation_events'],0)
        # Rejecting that route must still preserve only checked alternatives.
        rejected,_=search(*args[:2],lambda apps:all(a['law']!='barrier' for a in apps),width=2,rounds=2,operation_costs=costs)
        self.assertNotIn('barrier',[a['law'] for a in rejected])
    def test_rejected_proof_does_not_hide_valid_proof_for_same_result(self):
        x = variable('x')
        source = {'sort': x['sort'], 'node': {'Op': ['call:example', [x]]}}
        pattern = {'sort': x['sort'], 'node': {'Op': ['call:example', [variable('arg')]]}}
        base = {'params': {'arg': x['sort']}, 'from': pattern, 'to': variable('arg'), 'conditions': []}
        rejected, valid = dict(base), dict(base)
        rejected['conditions'] = [{'from': variable('arg'), 'to': variable('arg')}]
        attempts = []
        def checker(applications):
            attempts.append(applications[-1]['law'])
            return applications[-1]['law'] == 'valid'
        applications, receipt = search({'functions': {'f': source}},
            {'roots': ['rejected', 'valid'], 'objects': {'rejected': rejected, 'valid': valid}}, checker)
        self.assertEqual(attempts, ['rejected', 'valid'])
        self.assertEqual(applications[0]['law'], 'valid')
        self.assertLess(receipt['selected_cost'], receipt['initial_cost'])

    def test_rejection_and_budget_exhaustion_preserve_baseline(self):
        x = variable('x')
        source = {'sort': x['sort'], 'node': {'Op': ['call:example', [x]]}}
        law = {'params': {'x': x['sort']}, 'from': source, 'to': x, 'conditions': []}
        for budget in [0, 1]:
            applications, receipt = search({'functions': {'f': source}},
                {'roots': ['rejected'], 'objects': {'rejected': law}}, lambda _: False, budget=budget)
            self.assertEqual(applications, [])
            self.assertEqual(receipt['selected_cost'], receipt['initial_cost'])
            self.assertLessEqual(receipt['attempts'], budget)

    def test_round_limit_can_cover_more_than_twelve_independent_functions(self):
        x = variable('x')
        source = {'sort': x['sort'], 'node': {'Op': ['call:example', [x]]}}
        law = {'params': {'x': x['sort']}, 'from': source, 'to': x, 'conditions': []}
        subject = {'functions': {f'f{i:02}': source for i in range(15)}}
        catalogue = {'roots': ['valid'], 'objects': {'valid': law}}
        partial, limited = search(subject, catalogue, lambda _: True,
                                  budget=256, width=1, rounds=12)
        complete, receipt = search(subject, catalogue, lambda _: True,
                                   budget=256, width=1, rounds=32)
        self.assertEqual(len(partial), 12)
        self.assertEqual(len(complete), 15)
        self.assertLess(receipt['selected_cost'], limited['selected_cost'])
        self.assertLessEqual(receipt['attempts'], 256)
        baseline, _ = search(subject, catalogue, lambda _: True, rounds=0)
        self.assertEqual(baseline, [])

    def test_large_catalogue_checks_ranked_beam_without_starving_later_functions(self):
        x = variable('x')
        source = {'sort': x['sort'], 'node': {'Op': ['call:example', [x]]}}
        cheaper = {'params': {'x': x['sort']}, 'from': source, 'to': x, 'conditions': []}
        objects = {'best': cheaper}
        # Numerous valid but higher-cost alternatives precede the useful law.
        for i in range(300):
            target = {'sort': x['sort'], 'node': {'Op': [f'other:{i}', [x]]}}
            objects[f'other{i}'] = dict(cheaper, to=target)
        catalogue = {'roots': list(objects)[1:] + ['best'], 'objects': objects}
        subject = {'functions': {f'f{i:02}': source for i in range(15)}}
        apps, receipt = search(subject, catalogue, lambda _: True,
                               budget=64, width=1, rounds=32)
        self.assertEqual(len(apps), 15)
        self.assertEqual(receipt['attempts'], 15)
        self.assertGreater(receipt['generated_proposals'], 300)
        self.assertFalse(receipt['exhausted'])

    def test_cli_rejects_round_limits_outside_the_documented_bound(self):
        import subprocess
        import sys
        from pathlib import Path
        script = Path(__file__).resolve().parents[1] / 'plan.py'
        for rounds in [-1, 65]:
            p = subprocess.run([sys.executable, str(script), '--compiler', 'unused',
                '--core', 'unused', '--snapshot', 'unused', '-o', 'unused',
                '--rounds', str(rounds)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn('rounds must be between 0 and 64', p.stderr)


class BinderSafeRebuilding(unittest.TestCase):
    def test_shift_and_substitution_preserve_nested_binders_and_input(self):
        import copy
        from ink_planner.semantic_search import shift, substitute, extract_hole
        x=variable('x'); bound=lambda i: dict(sort=x['sort'],node={'Bound':i})
        body=dict(sort=x['sort'],node={'Op':['call:pair',[bound(0),bound(1),x]]})
        tree=dict(sort={'Arrow':[x['sort'],x['sort']]},node={'Lambda':body})
        saved=copy.deepcopy(tree)
        result=substitute(tree,{'x':bound(0)})
        self.assertEqual([t['node']['Bound'] for t in result['node']['Lambda']['node']['Op'][1]],[0,1,1])
        self.assertEqual(tree,saved)
        self.assertEqual(shift(shift(tree,1),-1),tree)
        with self.assertRaises(ValueError): extract_hole(tree,1)
        # Returned trees must not alias nested input containers.
        result['sort']['Arrow'][0]['Value']='U64'
        self.assertEqual(tree,saved)


if __name__ == '__main__':
    unittest.main()
