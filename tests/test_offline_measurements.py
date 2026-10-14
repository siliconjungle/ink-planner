import hashlib,json,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from ink_knowledge import Store
from ink_planner.offline_measurements import read_workload,independent_workloads,acquire,validated,RUNNER
class OfflineMeasurements(unittest.TestCase):
    def test_input_fingerprints_use_typed_values_not_json_spelling(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);artifact=root/'baseline.mjs'
            meta=dict(state=None,named_types={},functions=[dict(name='run',internal=False,params=[dict(type={'kind':'f32'})],result={'kind':'u32'})])
            artifact.write_text('export const metadata='+json.dumps(meta)+';export function call(){return 1;}')
            def fingerprints(value):
                request=root/'request.json';request.write_text(json.dumps(dict(artifacts=[dict(measurement='baseline',path=str(artifact))],phases={'call:run':[dict(function='run',args=[value],expected=1)]})))
                result=subprocess.run(['node',str(RUNNER),str(request)],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                return json.loads(result.stdout)['input_fingerprints']
            self.assertEqual(fingerprints(1.1),fingerprints(1.10000001))
            self.assertEqual(fingerprints(1.1),fingerprints({'F32Bits':0x3f8ccccd}))
            self.assertNotEqual(fingerprints(1.1),fingerprints(1.2))
            meta['functions'][0]['params'][0]['type']={'kind':'record','fields':[{'name':'x','type':{'kind':'u32'}}]}
            artifact.write_text('export const metadata='+json.dumps(meta)+';export function call(){return 1;}')
            self.assertEqual(fingerprints({'x':1,'extra':2}),fingerprints({'extra':3,'x':1}))
    def test_candidate_must_validate_in_every_fresh_process(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);store=Store.publish(root/'knowledge',[]);marker=root/'first-import'
            compiler=root/'compiler';compiler.write_text('fixture')
            from ink_planner.compiled_planning import code_fingerprint
            fingerprint=code_fingerprint()
            meta=dict(state=None,named_types={},functions=[dict(name='run',internal=False,params=[],result={'kind':'u32'})])
            class Emitter:
                directory=root;original=root/'source';compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest();code_hash=fingerprint
                options=SimpleNamespace(target='javascript',measure_processes=3,measure_timeout=30,check_timeout=30,reference_budget=12345)
                def invoke(self,command,*args,**kwargs):
                    if command=='run':
                        assert args[-2:]==('--evaluation-budget','12345')
                        return 1
                    artifact=args[-1]
                    source='export const metadata='+json.dumps(meta)+';'
                    if artifact.name.startswith('b'):
                        source+="import fs from 'node:fs';const seen=fs.existsSync("+json.dumps(str(marker))+");fs.writeFileSync("+json.dumps(str(marker))+",'seen');export function call(){return seen?2:1;}"
                    else:source+='export function call(){return 1;}'
                    artifact.write_text(source)
            Emitter.compiler=compiler
            workload=root/'workload.json';workload.write_text(json.dumps(dict(schema=1,phases={'call:run':[dict(function='run',args=[])]},profile={'call:run':1},workload=dict(shape={},distribution='controls',concurrency=1,residency='captured'))))
            baseline='a'*64;candidate='b'*64
            from ink_knowledge.selection_probes import policy
            # Automatic controls must retain the configured process count too.
            target,shape,weights,receipt=acquire(Emitter(),store,[{'input_core_sha256':'c'*64}]*2,[{}]*2,[baseline,candidate],workload,policy())
            self.assertEqual(receipt['acquisition']['processes'],3)
            self.assertEqual(receipt['reference_budget'],12345)
            self.assertEqual({row['measurement']for row in receipt['acquisition']['rows']},{baseline})
            self.assertEqual({row['measurement']for row in receipt['rejected']},{candidate})
            self.assertEqual(receipt['validated_measurements'],[baseline])
            # Old successful measurements must not override a fresh rejection.
            # Synthetic costs exercise this policy, not machine performance.
            from ink_planner.representation_selection import rank
            peaks=[row['peak_rss_bytes']for row in receipt['acquisition']['rows']]
            self.assertTrue(all(type(p)is int and p>0 for p in peaks))
            self.assertIn('whole-host-peak-rss',target['bridge'])
            # The real acquisition reaches the memory gate: an insufficient
            # budget fails closed and a sufficient observed budget is admitted.
            with self.assertRaisesRegex(ValueError,'memory constraint'):
                rank(store,'c'*64,[baseline],target,shape,weights,baseline,memory_limit=min(peaks)-1)
            admitted,_=rank(store,'c'*64,[baseline],target,shape,weights,baseline,memory_limit=max(peaks))
            self.assertEqual(admitted,baseline)
            history=SimpleNamespace(iter_observations=lambda program,plans,target,workload:[dict(plan=m,metrics=dict(complete_call_ns=['1'if m==candidate else '100']*3,peak_bytes=None))for m in plans])
            old,_=rank(history,'c'*64,[baseline,candidate],{}, {'distribution':'history'},{'call:run':1},baseline,sample_guard=True)
            fresh,_=rank(history,'c'*64,validated([baseline,candidate],receipt),{}, {'distribution':'history'},{'call:run':1},baseline,sample_guard=True)
            self.assertEqual(old,candidate);self.assertEqual(fresh,baseline)
            self.assertEqual(len(receipt['observations']),3)
    def test_holdout_checks_actual_input_disjointness_and_fixed_objective(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);first=root/'train.json';second=root/'holdout.json'
            spec=dict(schema=1,phases={'call:run':[dict(function='run',args=[[1,2],1])]},profile={'call:run':10},workload={})
            first.write_text(json.dumps(spec));second.write_text(json.dumps(spec,indent=4))
            with self.assertRaisesRegex(ValueError,'overlap'):independent_workloads(first,second)
            spec['phases']['call:run'][0]['args']=[[3,4],1];second.write_text(json.dumps(spec));independent_workloads(first,second)
            spec['profile']['call:run']=20;second.write_text(json.dumps(spec))
            with self.assertRaisesRegex(ValueError,'objective'):independent_workloads(first,second)
    def test_training_inputs_and_lifecycle_weights_are_bounded(self):
        with tempfile.TemporaryDirectory()as tmp:
            path=Path(tmp)/'workload.json'
            good=dict(schema=1,phases={'call:run':[dict(function='run',args=[[1,2],1])]},profile={'call:run':1},workload={})
            path.write_text(json.dumps(good));read_workload(path)
            for weight in [-1,True,1_000_000_001]:
                good['profile']['call:run']=weight;path.write_text(json.dumps(good))
                with self.assertRaises(ValueError):read_workload(path)
    def test_wrong_output_and_input_mutation_are_not_measured(self):
        with tempfile.TemporaryDirectory()as tmp:
            root=Path(tmp);artifacts=[]
            meta=dict(state=None,named_types={},functions=[dict(name='run',internal=False,params=[dict(type={'kind':'list','element':{'kind':'u32'}})],result={'kind':'u32'})])
            bodies=['return args[0][0];','return 99;','args[0][0]=5;return 1;']
            for i,body in enumerate(bodies):
                path=root/f'{i}.mjs';path.write_text('export const metadata='+json.dumps(meta)+'; export function call(name,args){'+body+'}')
                artifacts.append(dict(measurement=str(i),path=str(path)))
            request=root/'request.json';request.write_text(json.dumps(dict(artifacts=artifacts,phases={'call:run':[dict(function='run',args=[[1]],expected=1)]})))
            r=subprocess.run(['node',str(RUNNER),str(request)],capture_output=True,text=True)
            self.assertEqual(r.returncode,0,r.stderr);report=json.loads(r.stdout)
            self.assertEqual({x['measurement']for x in report['rows']},{'0'})
            self.assertEqual({x['measurement']for x in report['rejected']},{'1','2'})
            artifacts[0]['path']=artifacts[1]['path'];request.write_text(json.dumps(dict(artifacts=artifacts,phases={'call:run':[dict(function='run',args=[[1]],expected=1)]})))
            self.assertNotEqual(subprocess.run(['node',str(RUNNER),str(request)],capture_output=True).returncode,0)
