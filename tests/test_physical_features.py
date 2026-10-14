"""Offline target-work metadata cannot bypass admission or fill unknown work."""
import copy,json,math,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ink_planner.physical_features import COMPONENTS,BINDINGS,PhysicalPrices,file_hash,load_observation,rank_observations
from ink_planner import physical_features


def cohort(target='javascript',boundary='owned'):
    return dict(target=target,host=dict(engine='node',version='test'),boundary=boundary,emitter_sha256='1'*64,entry_abi='test')

def model():
    return dict(schema=1,kind='ink-target-work-prices-v1',cohort=cohort(),weights=dict(invocations=.5,loop_iterations=.125,allocation_events=2),training=dict(protocol_sha256='2'*64,observations_sha256='3'*64,scope='synthetic test, no speed claim'))

def observation(n=10):
    return dict(cohort=cohort(),entry='entry',fixture_id='fixture',components={k:n if k=='loop_iterations'else 1 if k in('invocations','allocation_events')else 0 for k in COMPONENTS},known={k:True for k in COMPONENTS},bindings={k:dict(sha256='4'*64)for k in BINDINGS})

class PhysicalFeatureTests(unittest.TestCase):
    def test_validated_prices_do_not_alias_caller_owned_dictionaries(self):
        data=model(); original=copy.deepcopy(data);prices=PhysicalPrices(data)
        expected=prices.score(observation())
        data['weights']['loop_iterations']=999
        data['cohort']['host']['engine']='changed'
        data['training']['scope']='changed'
        self.assertEqual(prices.score(observation()),expected)
        self.assertEqual(prices.data,original)

    def test_finite_ns_prices_not_boolean_or_unbounded(self):
        self.assertEqual(PhysicalPrices(model()).score(observation())[0],3.75)
        for bad in(True,-1,float('nan'),float('inf'),10**10):
            m=model();m['weights']['loop_iterations']=bad
            with self.subTest(bad=bad),self.assertRaises(ValueError):PhysicalPrices(m)
        m=model();m['trusted']=True
        with self.assertRaises(ValueError):PhysicalPrices(m)

    def test_unknown_required_zero_price_still_retains_baseline(self):
        a,b=observation(),observation(1);a['known']['allocation_events']=False
        selected,receipt=rank_observations(dict(base=a,fast=b),'base',PhysicalPrices(model()))
        self.assertEqual(selected,'base');self.assertFalse(receipt['baseline_comparable'])
        m=model();m['weights']['allocation_events']=0
        self.assertIsNone(PhysicalPrices(m).score(a)[0])

    def test_exact_fixture_cohort_and_source_not_shape_or_cross_target(self):
        for changes in(('fixture_id','different'),('entry','other')):
            a,b=observation(),observation(1);b[changes[0]]=changes[1]
            self.assertEqual(rank_observations(dict(base=a,fast=b),'base',PhysicalPrices(model()))[0],'base')
        for key,value in [('target','native-rust'),('boundary','into'),('emitter_sha256','5'*64),('host',dict(engine='v8',version='test'))]:
            a,b=observation(),observation(1);b['cohort'][key]=value
            self.assertEqual(rank_observations(dict(base=a,fast=b),'base',PhysicalPrices(model()))[0],'base')
        a,b=observation(),observation(1);b['bindings']['fixture']['sha256']='6'*64
        self.assertEqual(rank_observations(dict(base=a,fast=b),'base',PhysicalPrices(model()))[0],'base')

    def test_strictly_cheaper_and_ties_preserve_baseline(self):
        p=PhysicalPrices(model());a,b=observation(),observation(1)
        self.assertEqual(rank_observations(dict(base=a,fast=b),'base',p)[0],'fast')
        self.assertEqual(rank_observations(dict(base=a,tied=copy.deepcopy(a)),'base',p)[0],'base')

    def bound(self,root):
        for name in BINDINGS:(root/name).write_text(name)
        instrumentation=dict(artifact_sha256=file_hash(root/'artifact'),instrumented_sha256=file_hash(root/'instrumented'),instrumenter_sha256=file_hash(root/'instrumenter'))
        (root/'instrumentation').write_text(json.dumps(instrumentation))
        row=dict(id='fixture',entry='entry',boundary='owned',components=observation()['components'],known=observation()['known'],unknown=[])
        trace=dict(status='untimed-complete-output-replayed',artifact_sha256=file_hash(root/'artifact'),fixture_sha256=file_hash(root/'fixture'),instrumentation_sha256=file_hash(root/'instrumentation'),instrumented_sha256=file_hash(root/'instrumented'),runner_sha256=file_hash(root/'runner'),rows=[row])
        (root/'trace').write_text(json.dumps(trace))
        o=dict(schema=1,kind='ink-target-work-v1',cohort=cohort(),entry='entry',fixture_id='fixture',components=row['components'],known=row['known'],unknown=[],scope='offline test',bindings={name:dict(path=name,sha256=file_hash(root/name))for name in BINDINGS})
        p=root/'sidecar.json';p.write_text(json.dumps(o));return p,o

    def test_changed_bound_bytes_and_fabricated_vectors_rejected(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);path,o=self.bound(root);load_observation(path,dict(artifact=root/'artifact'))
            o['components']['loop_iterations']=0;path.write_text(json.dumps(o))
            with self.assertRaisesRegex(ValueError,'vector mismatch'):load_observation(path)
            path,o=self.bound(root);(root/'selected_core').write_text('different')
            with self.assertRaisesRegex(ValueError,'binding changed'):load_observation(path)

    def test_bool_counts_unknown_mask_and_excess_metadata_rejected(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d)
            for field,key,value in [('components','invocations',True),('known','copy_bytes',1),('components','loop_iterations',10**16)]:
                path,o=self.bound(root);o[field][key]=value;path.write_text(json.dumps(o))
                with self.assertRaises(ValueError):load_observation(path)
            path,o=self.bound(root);o['checked']=True;path.write_text(json.dumps(o))
            with self.assertRaises(ValueError):load_observation(path)

    def test_boundary_trace_requires_exact_actual_adapter_sources(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);path,o=self.bound(root)
            trace=json.loads((root/'trace').read_text());trace['boundary']={'runner_sha256':'9'*64};(root/'trace').write_text(json.dumps(trace));o['bindings']['trace']['sha256']=file_hash(root/'trace');path.write_text(json.dumps(o))
            with self.assertRaisesRegex(ValueError,'boundary bindings missing'):load_observation(path)

    def test_forged_emission_and_runner_trace_bindings_rejected(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);path,o=self.bound(root);trace=json.loads((root/'trace').read_text());trace['runner_sha256']='0'*64;(root/'trace').write_text(json.dumps(trace));o['bindings']['trace']['sha256']=file_hash(root/'trace');path.write_text(json.dumps(o))
            with self.assertRaisesRegex(ValueError,'replay bindings'):load_observation(path)

    def bound_with_boundary(self, root):
        path,o=self.bound(root)
        for name in physical_features.BOUNDARY_BINDINGS:(root/name).write_text(name)
        (root/'boundary_receipt').write_text(json.dumps(dict(
            runner_sha256=file_hash(root/'boundary_runner'),
            producer_sha256=file_hash(root/'boundary_extractor'),
            adapter_sha256=file_hash(root/'boundary_adapter'))))
        (root/'boundary_instrumentation').write_text(json.dumps(dict(
            artifact_sha256=file_hash(root/'boundary_adapter'),
            instrumented_sha256=file_hash(root/'boundary_instrumented'),
            instrumenter_sha256=file_hash(root/'instrumenter'))))
        trace=json.loads((root/'trace').read_text())
        trace['boundary']={key:file_hash(root/name) for name,key in [
            ('boundary_runner','runner_sha256'),('boundary_extractor','extractor_sha256'),
            ('boundary_adapter','adapter_sha256'),('boundary_instrumented','instrumented_sha256'),
            ('boundary_instrumentation','instrumentation_sha256'),('boundary_receipt','extraction_sha256')]}
        (root/'trace').write_text(json.dumps(trace))
        o['bindings'].update({name:dict(path=name,sha256=file_hash(root/name))
            for name in physical_features.BOUNDARY_BINDINGS})
        o['bindings']['trace']['sha256']=file_hash(root/'trace')
        path.write_text(json.dumps(o));return path,o

    def test_parsed_receipts_use_authenticated_bytes_after_concurrent_edits(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d);path,expected=self.bound_with_boundary(root)
            sentinel=root/'expected-artifact';sentinel.write_bytes((root/'artifact').read_bytes())
            changed=[]
            def hash_then_change(p):
                digest=file_hash(p)
                # Expected-candidate binding checks happen after all source
                # bindings have been authenticated, before trace validation.
                if Path(p)==sentinel:
                    for name in ('trace','instrumentation','boundary_receipt','boundary_instrumentation'):
                        (root/name).write_text('null');changed.append(name)
                return digest
            with patch.object(physical_features,'file_hash',hash_then_change):
                actual=load_observation(path,dict(artifact=sentinel))
            self.assertEqual(len(changed),4)
            self.assertEqual(actual,expected)

    def test_malformed_nested_receipts_fail_as_unavailable_metadata(self):
        with tempfile.TemporaryDirectory()as d:
            root=Path(d)
            for name in ('trace','instrumentation','boundary_receipt','boundary_instrumentation'):
                for bad in (None,[],0,'receipt'):
                    with self.subTest(name=name,bad=bad):
                        path,o=self.bound_with_boundary(root)
                        (root/name).write_text(json.dumps(bad))
                        o['bindings'][name]['sha256']=file_hash(root/name)
                        path.write_text(json.dumps(o))
                        with self.assertRaisesRegex(ValueError,'metadata object required'):
                            load_observation(path)
            for field,bad in [('rows',[None]),('rows',['row']),('boundary',None),('boundary',[])]:
                with self.subTest(field=field,bad=bad):
                    path,o=self.bound_with_boundary(root)
                    trace=json.loads((root/'trace').read_text());trace[field]=bad
                    (root/'trace').write_text(json.dumps(trace))
                    o['bindings']['trace']['sha256']=file_hash(root/'trace');path.write_text(json.dumps(o))
                    with self.assertRaises(ValueError):load_observation(path)

if __name__=='__main__':unittest.main()
