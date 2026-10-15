import unittest
from unittest.mock import patch
from ink_planner.semantic_search import search


def variable(name):
    return {'sort': {'Value': 'U32'}, 'node': {'Free': name}}


class SearchProofAlternatives(unittest.TestCase):
    def test_cost_defaults_are_read_once_per_search_and_refreshed_between_searches(self):
        from ink_knowledge.cost_models import structural_prices
        x=variable('x');source={'sort':x['sort'],'node':{'Op':['call:example',[x]]}}
        law=dict(params={'x':x['sort']},conditions=[],**{'from':source,'to':x})
        subject={'functions':{'a':source,'b':source}}
        catalogue={'roots':['law'],'objects':{'law':law}}
        defaults=structural_prices()
        with patch('ink_knowledge.cost_models.structural_prices',return_value=dict(defaults,**{'call:example':10})) as reads:
            apps,first=search(subject,catalogue,lambda _:True,width=1)
            self.assertEqual(reads.call_count,1)
            self.assertEqual(len(apps),2)
        with patch('ink_knowledge.cost_models.structural_prices',return_value=dict(defaults,**{'call:example':20})):
            _,second=search(subject,catalogue,lambda _:True,width=1)
            _,override=search(subject,catalogue,lambda _:True,width=1,operation_costs={'call:example':3})
        self.assertEqual(second['initial_cost']-first['initial_cost'],20)
        self.assertEqual(first['initial_cost']-override['initial_cost'],14)

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
        # Keep both proofs in the same premise class: the first rejection must
        # not deduplicate the still-valid alternative for the same result.
        rejected['conditions'] = valid['conditions'] = [{'from': variable('arg'), 'to': variable('arg')}]
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
                               budget=64, width=1, rounds=32,strategy="beam")
        self.assertEqual(len(apps), 15)
        self.assertEqual(receipt['attempts'], 15)
        self.assertGreater(receipt['generated_proposals'], 300)
        self.assertFalse(receipt['exhausted'])

    def test_cli_rejects_round_limits_outside_the_documented_bound(self):
        import subprocess
        import sys
        from pathlib import Path
        script = Path(__file__).resolve().parents[1] / 'plan.py'
        for rounds in [-1]:
            p = subprocess.run([sys.executable, str(script), '--compiler', 'unused',
                '--core', 'unused', '--snapshot', 'unused', '-o', 'unused',
                '--rounds', str(rounds)], capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)
            self.assertIn('rounds must be nonnegative', p.stderr)

    def test_memo_keeps_a_costly_branch_after_the_beam_is_full(self):
        x=variable('x');op=lambda n:dict(sort=x['sort'],node={'Op':[n,[x]]})
        source=op('call:start');cheap=op('call:dead');barrier=op('call:barrier')
        objects={'cheap':dict(params={'x':x['sort']},conditions=[],**{'from':source,'to':cheap}),
                 'barrier':dict(params={'x':x['sort']},conditions=[],**{'from':source,'to':barrier}),
                 'finish':dict(params={'x':x['sort']},conditions=[],**{'from':barrier,'to':x})}
        args=({'functions':{'f':source}},{'roots':list(objects),'objects':objects},lambda _:True)
        costs={'call:start':1000,'call:dead':1,'call:barrier':500}
        old,_=search(*args,width=1,strategy='beam',operation_costs=costs)
        new,receipt=search(*args,width=1,strategy='memo',operation_costs=costs)
        self.assertEqual([a['law']for a in old],['cheap'])
        self.assertEqual([a['law']for a in new],['barrier','finish'])
        self.assertEqual(receipt['memo']['states'],4)

    def test_premise_search_can_cross_more_than_three_supporting_lemmas(self):
        from ink_planner.semantic_search import condition_proof
        x=variable('x');op=lambda i:dict(sort=x['sort'],node={'Op':['call:q'+str(i),[x]]})
        objects={str(i):dict(params={'x':x['sort']},**{'from':op(i),'to':x},conditions=[]if i==7 else[dict(**{'from':op(i+1),'to':x})])for i in range(8)}
        proof=condition_proof(dict(**{'from':op(0),'to':x}),dict(objects=objects))
        depth=0
        while isinstance(proof,dict)and 'Use'in proof:
            depth+=1;premises=proof['Use']['premises'];proof=premises[0]if premises else None
        self.assertEqual(depth,8)

    def test_admitted_alternatives_are_retained_even_when_not_cheapest(self):
        x=variable('x');op=lambda n:dict(sort=x['sort'],node={'Op':[n,[x]]})
        objects={n:dict(params={'x':x['sort']},conditions=[],**{'from':op('start'),'to':op(n)})for n in ['cheap','expensive']}
        alternatives=[]
        search({'functions':{'f':op('start')}},{'roots':list(objects),'objects':objects},lambda _:True,
            operation_costs={'start':100,'cheap':1,'expensive':200},alternatives=alternatives)
        self.assertEqual({a[-1]['law']for a in alternatives},{'cheap','expensive'})
    def test_exact_property_certificate_is_reused_as_a_premise(self):
        from ink_planner.semantic_search import condition_proof
        x=variable('x');goal=dict(**{'from':dict(sort=x['sort'],node={'Op':['length',[x]]}),'to':x})
        proof={'Use':dict(law='published-property',arguments={},premises=[])}
        self.assertEqual(condition_proof(goal,dict(objects={},certificates=[dict(goal=goal,proof=proof)])),proof)
        other=dict(goal,to=dict(sort=x['sort'],node={'Word':2}))
        self.assertEqual(condition_proof(other,dict(objects={},certificates=[dict(goal=goal,proof=proof)])),'Normalize')


class BinderSafeRebuilding(unittest.TestCase):
    def test_extract_preserves_local_lambdas_and_shifts_only_outer_captures(self):
        import copy
        from ink_planner.semantic_search import extract_hole, match, substitute
        x=variable('x');bound=lambda i:dict(sort=x['sort'],node={'Bound':i})
        arrow={'Arrow':[x['sort'],x['sort']]}
        lam=lambda body:dict(sort=arrow,node={'Lambda':body})
        closed=lam(bound(0))
        self.assertEqual(extract_hole(closed,1),closed)
        body=dict(sort=x['sort'],node={'Op':['call:pair',[bound(0),bound(2)]]})
        nested=lam(body);saved=copy.deepcopy(nested)
        extracted=extract_hole(nested,1)
        self.assertEqual([a['node']['Bound'] for a in extracted['node']['Lambda']['node']['Op'][1]],[0,1])
        self.assertEqual(nested,saved)
        hole=dict(sort=arrow,node={'Free':'f'})
        pattern=dict(sort={'Arrow':[x['sort'],arrow]},node={'Lambda':hole})
        actual=dict(sort=pattern['sort'],node={'Lambda':nested})
        bindings={}
        self.assertTrue(match(pattern,actual,{'f':arrow},bindings))
        self.assertEqual(substitute(pattern,bindings),actual)
        for forbidden in [bound(0),lam(bound(1)),lam(lam(bound(2)))]:
            with self.assertRaises(ValueError):extract_hole(forbidden,1)
        # More than one pattern binder, including deeply nested local identity.
        self.assertEqual(extract_hole(lam(lam(bound(0))),3),lam(lam(bound(0))))
        with self.assertRaises(ValueError):extract_hole(lam(bound(2)),2)

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

class ExactModuleSerialization(unittest.TestCase):
    def test_shared_and_rebuilt_bodies_have_exact_keys_under_eviction(self):
        from ink_planner.semantic_search import ModuleKeys,key
        import copy
        x=variable('x');body=dict(sort=x['sort'],node={'Op':['call:leaf',[x]]})
        keys=ModuleKeys(4096);first={'z':body,'a':x,'quoted"\u00e9':body}
        self.assertEqual(keys.module(first),key(first));misses=keys.misses
        self.assertEqual(keys.module(dict(reversed(list(first.items())))),key(first))
        self.assertEqual(keys.misses,misses);self.assertGreater(keys.hits,0)
        changed=dict(first,a=dict(sort=x['sort'],node={'Bound':0}))
        self.assertNotEqual(keys.module(changed),keys.module(first));self.assertEqual(keys.module(changed),key(changed))
        self.assertEqual(keys.module(copy.deepcopy(first)),key(first))
        tiny=ModuleKeys(1)
        for rows in [first,changed,{'new':body},{}]:self.assertEqual(tiny.module(rows),key(rows));self.assertLessEqual(tiny.bytes,1)


from ink_planner.semantic_search import interleave_premise_classes

def term(name,*args):return dict(sort={'Value':'U32'},node={'Op':[name,list(args)]})
def var(name):return dict(sort={'Value':'U32'},node={'Free':name})

class PremiseFairness(unittest.TestCase):
    def test_class_order_is_stable_complete_and_does_not_use_names(self):
        rows=[(i,None,None,str(i),{'conditions':([]if i in [3,7]else[{}])},None)for i in range(10)]
        ordered=interleave_premise_classes(rows)
        self.assertEqual([r[0]for r in ordered],[3,0,7,1,2,4,5,6,8,9])
        self.assertEqual(interleave_premise_classes(rows[:3]),rows[:3])
        self.assertEqual(interleave_premise_classes([]),[])

    def test_derived_guards_have_a_separate_fair_queue(self):
        laws = [dict(conditions=[], dependencies=['proof']),
                dict(conditions=[{}], dependencies=[]),
                dict(conditions=[], dependencies=[]),
                dict(conditions=[], dependencies=['other'])]
        rows = [(i,None,None,str(i),law,None) for i,law in enumerate(laws)]
        self.assertEqual([r[0] for r in interleave_premise_classes(rows)], [2,1,0,3])

    def test_rejected_shortcuts_cannot_starve_checked_alternative(self):
        source=term('source',var('input'))
        params={'x':{'Value':'U32'}}
        base=dict(params=params,**{'from':term('source',var('x'))},proof='Normalize',dependencies=[])
        laws={str(i):dict(base,to=term('shortcut'+str(i),var('x')),conditions=[{'from':var('x'),'to':term('zero')}])for i in range(12)}
        laws['alternative']=dict(base,to=term('alternative',var('x')),conditions=[])
        catalogue=dict(roots=list(laws),objects=laws)
        costs={'source':100,'alternative':50,**{'shortcut'+str(i):0 for i in range(12)}}
        seen=[]
        def check(proposal):
            seen.append(proposal[-1]['law'])
            return proposal[-1]['law']=='alternative'
        applications,receipt=search(dict(functions={'run':source}),catalogue,check,budget=2,rounds=1,operation_costs=costs)
        self.assertEqual(seen[0],'alternative')
        self.assertEqual(applications[-1]['law'],'alternative')
        rejected,_=search(dict(functions={'run':source}),catalogue,lambda _:False,budget=2,rounds=1,operation_costs=costs)
        self.assertEqual(rejected,[])



if __name__ == '__main__':
    unittest.main()
