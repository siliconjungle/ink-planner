"""Source costs remain untrusted; actual signatures/binders drive estimates."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'planner'))
from ink_planner.target_costs import COMPONENTS, TargetCostModel
from ink_planner.semantic_search import load_cost_model, search

U = {'Value': 'U32'}
B = {'Value': 'Bool'}
L = {'Value': {'List': 'U32'}}

def term(sort, node): return dict(sort=sort, node=node)
def free(name, sort=U): return term(sort, {'Free': name})
def bound(n=0, sort=U): return term(sort, {'Bound': n})
def word(n): return term(U, {'Word': n})
def op(name, *args, sort=U): return term(sort, {'Op': [name, list(args)]})
def lam(body, argument=U): return term({'Arrow': [argument, body['sort']]}, {'Lambda': body})
def mapped(xs, body): return op('method:map', xs, lam(body), sort=L)
def signature(name, params, result='U32'): return dict(name=name, params=params, result=result)
def program(*functions): return dict(functions=list(functions))
def model(**changes):
    data = dict(schema=2, target='javascript', boundary='owned',
                cardinality=dict(default=10, by_parameter={'xs': 7, 'ys': 3}),
                weights={k: (1 if k == 'unknown_work' else 0) for k in COMPONENTS})
    data.update(changes)
    return TargetCostModel(data)

class TargetComponentTests(unittest.TestCase):
    def test_generic_fold_keeps_initial_accumulator_and_tracks_growth(self):
        lb={'Value':{'List':'Bool'}}
        p=program(signature('entry',[['flags',{'List':'Bool'}],['seed',{'List':'U32'}],['ys',{'List':'U32'}]],{'List':'U32'}))
        m=model(cardinality=dict(default=4,by_parameter={'seed':3,'ys':2}))
        callback=lam(lam(bound(0,L),L),B)
        for name in ['call:foldr','call:foldl']:
            preserve=op(name,free('flags',lb),free('seed',L),callback,sort=L)
            r=m.estimate_function('entry',{'entry':preserve},p)
            self.assertTrue(r['complete']);self.assertEqual(r['components']['copy_bytes'],12)
            self.assertEqual(r['components']['traversal_elements'],4)
            grow=op(name,free('flags',lb),free('seed',L),lam(lam(op('method:concat',bound(0,L),free('ys',L),sort=L),L),B),sort=L)
            r=m.estimate_function('entry',{'entry':grow},p)
            self.assertTrue(r['complete']);self.assertEqual(r['components']['allocation_events'],4)
            self.assertEqual(r['components']['allocation_bytes'],128)
            self.assertEqual(r['components']['copy_bytes'],128)
            self.assertEqual(r['components']['traversal_elements'],36)

    def test_fold_fixed_points_are_bounded_and_growth_does_not_fake_completeness(self):
        p=program(signature('entry',[['xs',{'List':'U32'}]]))
        body=op('call:foldr',free('xs',L),word(0),lam(lam(op('binary:+',bound(1),bound()))))
        r=model(cardinality=dict(default=10**9,by_parameter={})).estimate_function('entry',{'entry':body},p)
        self.assertTrue(r['complete']);self.assertEqual(r['components']['scalar_ops'],10**9)
        self.assertEqual(r['components']['traversal_elements'],10**9)
        grow=op('call:foldr',free('xs',L),term(L,'Nil'),lam(lam(term(L,{'Cons':[bound(1),bound(0,L)]}),L)),sort=L)
        p=program(signature('entry',[['xs',{'List':'U32'}]],{'List':'U32'}))
        r=model(cardinality=dict(default=1000,by_parameter={})).estimate_function('entry',{'entry':grow},p)
        self.assertFalse(r['complete']);self.assertGreater(r['components']['unknown_work'],0)
        self.assertTrue(any('accumulator did not stabilize' in s for s in r['unknown_reasons']))

    def test_empty_folds_and_maps_do_not_execute_unreachable_callbacks(self):
        p=program(signature('entry',[['seed',{'List':'U32'}]],{'List':'U32'}))
        m=model(cardinality=dict(default=3,by_parameter={}))
        unsupported=op('method:sort',bound(0,L),sort=L)
        body=op('call:foldr',term(L,'Nil'),free('seed',L),lam(lam(unsupported,L)),sort=L)
        r=m.estimate_function('entry',{'entry':body},p)
        self.assertTrue(r['complete']);self.assertEqual(r['components']['traversal_elements'],0)
        self.assertEqual(r['components']['copy_bytes'],12)
        body=mapped(term(L,'Nil'),op('call:unsupported',bound()))
        r=m.estimate_function('entry',{'entry':body},p)
        self.assertTrue(r['complete']);self.assertEqual(r['components']['unknown_work'],0)

    def test_actual_compiler_generic_fold_subject_has_matching_collection_costs(self):
        compiler=Path(os.environ.get('INK_COMPILER',ROOT/'target/debug/ink')).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'fold.ink';core=root/'core.json';semantic=root/'semantic.json'
            source.write_text('module FoldCosts;'+''.join(
                f'fn {name}(flags:List<Bool>,seed:List<u32>,ys:List<u32>)->List<u32>{{return {fold}(flags,seed,fn(flag)=>fn(acc)=>acc.concat(ys));}}'
                for name,fold in [('grow','foldr'),('grow_left','foldl')]))
            for args in [('emit-core',source,'-o',core),('emit-semantic',core,'--core','-o',semantic)]:
                run=subprocess.run([str(compiler),*map(str,args)],capture_output=True,text=True,timeout=60)
                self.assertEqual(run.returncode,0,run.stderr)
            bodies=json.loads(semantic.read_text())['functions'];p=json.loads(core.read_text())['program']
            m=model(cardinality=dict(default=4,by_parameter={'seed':3,'ys':2}))
            for name in ['grow','grow_left']:
                r=m.estimate_function(name,bodies,p)
                self.assertTrue(r['complete'],r['unknown_reasons'])
                self.assertEqual(r['components']['allocation_events'],4)
                self.assertEqual(r['components']['allocation_bytes'],128)
                self.assertEqual(r['components']['traversal_elements'],36)

    def test_schema_finite_prices_and_cardinality_types(self):
        data = model().describe()
        data.pop('scope'); data.pop('units')
        data['weights']['scalar_ops'] = .125
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'model.json'; p.write_text(json.dumps(data))
            self.assertIsInstance(load_cost_model(p), TargetCostModel)
            for invalid in (True, -1, float('nan'), float('inf'), 10**10):
                bad = copy.deepcopy(data); bad['weights']['scalar_ops'] = invalid
                with self.subTest(invalid=invalid), self.assertRaises(ValueError): TargetCostModel(bad)
            for n in (True, 1.5, -1, 10**10):
                bad = copy.deepcopy(data); bad['cardinality']['default'] = n
                with self.assertRaises(ValueError): TargetCostModel(bad)
            bad = copy.deepcopy(data); bad['weights']['unknown_work'] = 0
            with self.assertRaises(ValueError): TargetCostModel(bad)
            bad = copy.deepcopy(data); bad['trusted'] = True
            with self.assertRaises(ValueError): TargetCostModel(bad)

    def test_signature_parameter_names_and_current_argument_binding(self):
        bodies = {'helper': mapped(free('source', L), op('binary:+', bound(), word(1))),
                  'entry': op('call:helper', free('ys', L), sort=L)}
        p = program(signature('helper', [['source', {'List': 'U32'}]], {'List': 'U32'}),
                    signature('entry', [['xs', {'List': 'U32'}], ['ys', {'List': 'U32'}]], {'List': 'U32'}))
        r = model().estimate_function('entry', bodies, p)
        self.assertEqual(r['components']['traversal_elements'], 3)
        self.assertEqual(r['components']['scalar_ops'], 3)
        self.assertEqual(len(r['definitions_consumed']), 2)
        bodies['entry'] = op('call:helper', free('xs', L), sort=L)
        self.assertEqual(model().estimate_function('entry', bodies, p)['components']['traversal_elements'], 7)

    def test_current_helper_rewrites_change_caller_vector_and_binding(self):
        p = program(signature('helper', [['x', 'U32']]), signature('entry', [['xs', {'List': 'U32'}]], {'List': 'U32'}))
        bodies = {'entry': mapped(free('xs', L), op('call:helper', bound())),
                  'helper': op('binary:+', free('x'), word(1))}
        before = model().estimate_function('entry', bodies, p)
        bodies['helper'] = op('binary:+', op('binary:*', free('x'), word(2)), word(1))
        after = model().estimate_function('entry', bodies, p)
        self.assertEqual(before['components']['scalar_ops'], 7)
        self.assertEqual(after['components']['scalar_ops'], 14)
        self.assertEqual(before['components']['loop_body_pressure'], 7)
        self.assertEqual(after['components']['loop_body_pressure'], 28)
        self.assertNotEqual(before['definitions_consumed'], after['definitions_consumed'])

    def test_shared_let_materializes_once_and_shadowing_captures(self):
        initial = mapped(free('xs', L), op('binary:+', bound(), word(1)))
        body = op('call:sum', bound(0, L))
        body = op('binary:+', body, op('call:sum', bound(0, L)))
        shared = op('apply', lam(body, L), initial)
        unshared = op('binary:+', op('call:sum', initial), op('call:sum', initial))
        p = program(signature('entry', [['xs', {'List': 'U32'}]]))
        a = model().estimate_function('entry', {'entry': shared}, p)['components']
        b = model().estimate_function('entry', {'entry': unshared}, p)['components']
        self.assertEqual(a['allocation_events'], 1); self.assertEqual(b['allocation_events'], 2)
        self.assertEqual(a['traversal_elements'], 21); self.assertEqual(b['traversal_elements'], 28)
        # Inner map binder shadows the let: Bound1 accesses captured scalar once
        # per iteration, without replaying the expression that initialized it.
        nested = op('apply', lam(mapped(free('xs', L), op('binary:+', bound(), bound(1)))),
                    op('binary:*', word(2), word(3)), sort=L)
        p = program(signature('entry', [['xs', {'List': 'U32'}]], {'List': 'U32'}))
        self.assertEqual(model().estimate_function('entry', {'entry': nested}, p)['components']['scalar_ops'], 8)

    def test_branches_max_not_sum_and_known_branch_shortcircuits(self):
        a = mapped(free('xs', L), op('binary:+', bound(), word(1)))
        b = mapped(free('ys', L), op('binary:*', bound(), word(2)))
        p = program(signature('entry', [['xs', {'List': 'U32'}], ['ys', {'List': 'U32'}], ['gate', 'Bool']], {'List': 'U32'}))
        r = model().estimate_function('entry', {'entry': op('call:choose', free('gate', B), a, b, sort=L)}, p)
        self.assertEqual(r['components']['traversal_elements'], 7)
        self.assertEqual(r['components']['allocation_events'], 1)
        r = model().estimate_function('entry', {'entry': op('call:choose', term(B, {'Bool': False}), a, b, sort=L)}, p)
        self.assertEqual(r['components']['traversal_elements'], 3)

    def test_zip_min_filter_upper_concat_empty(self):
        zipper = lam(lam(op('binary:-', bound(1), bound())))
        xs, ys = free('xs', L), free('ys', L)
        zipped = op('method:zip', xs, ys, zipper, sort=L)
        p = program(signature('entry', [['xs', {'List': 'U32'}], ['ys', {'List': 'U32'}]], {'List': 'U32'}))
        r = model().estimate_function('entry', {'entry': zipped}, p)
        self.assertEqual(r['components']['traversal_elements'], 3)
        self.assertEqual(r['components']['allocation_bytes'], 12)
        nil = term(L, 'Nil')
        concat = op('method:concat', nil, xs, sort=L)
        r = model().estimate_function('entry', {'entry': concat}, p)
        self.assertEqual(r['components']['copy_bytes'], 28)
        filtered = op('method:filter', xs, lam(op('binary:<', bound(), word(4), sort=B)), sort=L)
        r = model().estimate_function('entry', {'entry': op('method:zip', filtered, ys, zipper, sort=L)}, p)
        self.assertEqual(r['components']['traversal_elements'], 10)
        self.assertTrue(any('upper bound' in a for a in r['assumptions']))

    def test_unknown_short_circuit_left_does_not_make_right_literal_the_result(self):
        heavy = mapped(free('xs', L), op('binary:+', bound(), word(1)))
        light = mapped(free('ys', L), op('binary:+', bound(), word(1)))
        p = program(signature('entry', [['xs', {'List':'U32'}], ['ys', {'List':'U32'}], ['gate','Bool']], {'List':'U32'}))
        for operator in ('binary:&&', 'binary:||'):
            for right in (False, True):
                condition = op(operator, free('gate', B), term(B, {'Bool':right}), sort=B)
                for a,b in ((heavy,light),(light,heavy)):
                    r = model().estimate_function('entry', {'entry':op('call:choose',condition,a,b,sort=L)}, p)
                    # x && false and x || true are constant; x && true and
                    # x || false remain unknown and must price both branches.
                    absorbing = right == (operator == 'binary:||')
                    chosen = a if right else b
                    expected = (7 if chosen is heavy else 3) if absorbing else 7
                    with self.subTest(operator=operator,right=right,left_heavy=a is heavy):
                        self.assertEqual(r['components']['traversal_elements'], expected)

    def test_recursive_unknown_callback_capture_and_deep_fallback(self):
        p = program(signature('entry', [['xs', {'List': 'U32'}]], {'List': 'U32'}))
        cases = [op('call:entry', free('xs', L), sort=L),
                 mapped(free('xs', L), free('missing_capture')),
                 op('method:map', free('xs', L), free('f', {'Arrow': [U, U]}), sort=L)]
        deep = word(0)
        for _ in range(3000): deep = op('binary:+', deep, word(1))
        cases.append(deep)
        for body in cases:
            with self.subTest(case=body['node'].keys()):
                r = model().estimate_function('entry', {'entry': body}, p)
                self.assertFalse(r['complete']); self.assertGreater(r['components']['unknown_work'], 0)
                self.assertTrue(r['unknown_reasons'])

    def test_nested_collection_capture_is_priced_in_callback(self):
        cb = op('binary:+', bound(), op('call:sum', free('ys', L)))
        p = program(signature('entry', [['xs', {'List': 'U32'}], ['ys', {'List': 'U32'}]], {'List': 'U32'}))
        r = model().estimate_function('entry', {'entry': mapped(free('xs', L), cb)}, p)
        self.assertEqual(r['components']['traversal_elements'], 7 + 7*3)
        self.assertEqual(r['components']['scalar_ops'], 7*4)

    def test_gpu_vectors_disclose_physical_unknown_and_boundaries(self):
        p = program(signature('entry', [['xs', {'List': 'U32'}], ['ys', {'List': 'U32'}]], {'List': 'U32'}))
        body = op('method:scan', free('xs', L), sort=L)
        r = model(target='webgpu', boundary='gpu_upload_readback').estimate_function('entry', {'entry': body}, p)
        c = r['components']
        self.assertEqual(c['gpu_dispatches'], 1)
        self.assertEqual(c['upload_bytes'], 40); self.assertEqual(c['readback_bytes'], 28)
        self.assertFalse(r['complete']); self.assertGreater(c['unknown_work'], 0)
        self.assertTrue(any('physical lowering' in a for a in r['unknown_reasons']))
        r = model(target='webgpu', boundary='gpu_resident').estimate_function('entry', {'entry': body}, p)
        self.assertEqual(r['components']['upload_bytes'], 0); self.assertEqual(r['components']['readback_bytes'], 0)

    def test_boundary_alias_owned_copy_and_invocation(self):
        p = program(signature('entry', [['xs', {'List': 'U32'}]], {'List': 'U32'}))
        r = model().estimate_function('entry', {'entry': free('xs', L)}, p)
        self.assertEqual(r['components']['allocation_events'], 1)
        self.assertEqual(r['components']['copy_bytes'], 28)
        self.assertEqual(r['components']['invocations'], 1)
        r = model(boundary='into').estimate_function('entry', {'entry': free('xs', L)}, p)
        # Into must copy a borrowed return into its destination too.
        self.assertEqual(r['components']['copy_bytes'], 28)
        self.assertEqual(r['components']['allocation_events'], 0)

    def test_schema2_search_receipts_checker_and_schema1_unchanged(self):
        a = op('call:quot_or', free('x'), word(2), word(0))
        b = op('binary:+', free('x'), word(1))
        law = dict(params={'x': U}, conditions=[], **{'from': a, 'to': b})
        subject = {'functions': {'entry': a}}; catalogue = {'roots': ['law'], 'objects': {'law': law}}
        p = program(signature('entry', [['x', 'U32']]))
        weights = model().weights.copy(); weights['div_rem'] = 8.5; weights['scalar_ops'] = .5
        m = model(weights=weights)
        with self.assertRaises(ValueError): search(subject, catalogue, lambda _: True, operation_costs=m)
        apps, r = search(subject, catalogue, lambda _: False, operation_costs=m, core_program=p)
        self.assertEqual(apps, []); self.assertEqual(r['initial_components'], r['selected_components'])
        apps, r = search(subject, catalogue, lambda _: True, operation_costs=m, core_program=p)
        self.assertEqual([a['law'] for a in apps], ['law'])
        self.assertEqual(r['initial_components']['components']['div_rem'], 1)
        self.assertEqual(r['selected_components']['components']['div_rem'], 0)
        old = search(subject, catalogue, lambda _: True, operation_costs={'call:quot_or': 100, 'binary:+': 0})[1]
        self.assertNotIn('initial_components', old)
        self.assertEqual(old['estimate'], 'structural; not measured runtime')

    def test_optional_pressure_retains_staged_proven_alternative(self):
        xs = free('xs', L)
        staged = mapped(mapped(xs, op('binary:+', bound(), word(1))), op('binary:*', bound(), word(2)))
        fused = mapped(xs, op('binary:*', op('binary:+', bound(), word(1)), word(2)))
        law = dict(params={'xs': L}, conditions=[], **{'from': staged, 'to': fused})
        p = program(signature('entry', [['xs', {'List': 'U32'}]], {'List': 'U32'}))
        weights = model().weights.copy(); weights['loop_body_pressure'] = 1
        apps, r = search({'functions': {'entry': staged}}, {'roots': ['law'], 'objects': {'law': law}},
                         lambda _: True, operation_costs=model(weights=weights), core_program=p)
        self.assertEqual(apps, []); self.assertEqual(r['initial_components']['components']['loop_body_pressure'], 14)
        weights['loop_body_pressure'] = 0; weights['allocation_events'] = 1
        apps, r = search({'functions': {'entry': staged}}, {'roots': ['law'], 'objects': {'law': law}},
                         lambda _: True, operation_costs=model(weights=weights), core_program=p)
        self.assertEqual(len(apps), 1); self.assertEqual(r['selected_components']['components']['allocation_events'], 1)

class AgreeingBranchFacts(unittest.TestCase):
    def setup_case(self):
        self.heavy = mapped(free('xs', L), op('binary:+', bound(), word(1)))
        self.light = mapped(free('ys', L), op('binary:+', bound(), word(1)))
        self.p = program(signature('entry', [['gate', 'Bool'], ['xs', {'List':'U32'}], ['ys', {'List':'U32'}]], {'List':'U32'}))

    def test_agreeing_boolean_branches_select_only_the_reachable_outer_arm(self):
        self.setup_case()
        for left, right in [(True,True),(False,False),(True,False),(False,True)]:
            condition=op('call:choose', free('gate',B),term(B,{'Bool':left}),term(B,{'Bool':right}),sort=B)
            body=op('call:choose', condition, self.light, self.heavy,sort=L)
            result=model().estimate_function('entry',{'entry':body},self.p)
            self.assertTrue(result['complete'])
            self.assertEqual(result['components']['traversal_elements'],3 if left==right and left else 7)

    def test_agreeing_result_keeps_the_cost_of_evaluating_its_condition_and_arms(self):
        self.setup_case()
        expensive_true=op('apply',lam(term(B,{'Bool':True}),L),self.heavy,sort=B)
        condition=op('call:choose',free('gate',B),expensive_true,term(B,{'Bool':True}),sort=B)
        result=model().estimate_function('entry',{'entry':op('call:choose',condition,self.light,self.heavy,sort=L)},self.p)
        # Either branch can produce true, but producing it may still map xs.
        self.assertEqual(result['components']['traversal_elements'],7+3)
        self.assertEqual(result['components']['allocation_events'],2)
        self.assertEqual(result['components']['branches'],2)

    def test_agreeing_values_survive_current_helper_parameters(self):
        self.setup_case()
        self.p['functions'].append(signature('forward',[['flag','Bool']],'Bool'))
        body=op('call:choose',free('gate',B),term(B,{'Bool':True}),term(B,{'Bool':True}),sort=B)
        bodies={'forward':free('flag',B),'entry':op('call:choose',op('call:forward',body,sort=B),self.light,self.heavy,sort=L)}
        result=model().estimate_function('entry',bodies,self.p)
        self.assertEqual(result['components']['traversal_elements'],3)
        self.assertEqual(len(result['definitions_consumed']),2)

    def test_branch_selected_closures_remain_explicitly_unresolved(self):
        self.setup_case()
        callback=lam(op('binary:+',bound(),word(1)))
        chosen=op('call:choose',free('gate',B),callback,callback,sort=callback['sort'])
        result=model().estimate_function('entry',{'entry':op('method:map',free('ys',L),chosen,sort=L)},self.p)
        self.assertFalse(result['complete'])
        self.assertTrue(any('branch-selected callback' in r for r in result['unknown_reasons']))

    def test_actual_checked_source_and_reference_execution_match_the_known_branch(self):
        compiler=Path(os.environ.get('INK_COMPILER',ROOT/'target/debug/ink')).resolve()
        with tempfile.TemporaryDirectory() as temporary:
            d=Path(temporary);source=d/'branch.ink';core=d/'core.json';subject=d/'subject.json';args=d/'args.json'
            source.write_text('module BranchCosts;fn run(gate:Bool,xs:List<u32>,ys:List<u32>)->List<u32>{return choose(choose(gate,true,true),ys.map(fn(x)=>x+1),xs.map(fn(x)=>x+1));}')
            for command in [('emit-core',source,'-o',core),('emit-semantic',core,'--core','-o',subject)]:
                run=subprocess.run([str(compiler),*map(str,command)],capture_output=True,text=True,timeout=60)
                self.assertEqual(run.returncode,0,run.stderr)
            for gate in [False,True]:
                args.write_text(json.dumps([gate,list(range(7)),[10,20,30]]))
                run=subprocess.run([str(compiler),'run',str(core),'run',str(args),'--core'],capture_output=True,text=True,timeout=60)
                self.assertEqual(run.returncode,0,run.stderr)
                self.assertEqual(json.loads(run.stdout),[11,21,31])
            m=model(cardinality=dict(default=10,by_parameter={'xs':7,'ys':3}))
            result=m.estimate_function('run',json.loads(subject.read_text())['functions'],json.loads(core.read_text())['program'])
            self.assertTrue(result['complete'])
            self.assertEqual(result['components']['traversal_elements'],3)


if __name__ == '__main__': unittest.main()
