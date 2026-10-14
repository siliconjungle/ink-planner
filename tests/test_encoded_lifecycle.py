import json,subprocess,tempfile,unittest
from pathlib import Path
from ink_planner.offline_measurements import read_workload,independent_workloads,RUNNER

class EncodedLifecycle(unittest.TestCase):
 def test_encoded_profiles_are_bounded_and_holdouts_require_new_values(self):
  with tempfile.TemporaryDirectory()as temporary:
   root=Path(temporary);train=root/'train.json';holdout=root/'holdout.json'
   spec=dict(schema=3,phases={'boundary':[dict(function='run',args=[1],boundary='compact')]},profile={'boundary':2},workload={})
   train.write_text(json.dumps(spec));read_workload(train)
   spec['phases']['boundary'][0]['boundary']='portable';holdout.write_text(json.dumps(spec))
   with self.assertRaisesRegex(ValueError,'overlap'):independent_workloads(train,holdout)
   spec['phases']['boundary'][0]['args']=[2];holdout.write_text(json.dumps(spec));independent_workloads(train,holdout)
   spec['phases']['boundary'][0]['boundary']='unchecked';holdout.write_text(json.dumps(spec))
   with self.assertRaisesRegex(ValueError,'boundary'):read_workload(holdout)
 def test_complete_encoded_outputs_and_input_ownership_gate_measurement(self):
  with tempfile.TemporaryDirectory()as temporary:
   root=Path(temporary);artifacts=[]
   metadata=dict(state={'roots':[]},named_types={},functions=[dict(name='run',internal=False,params=[dict(name='x',type={'kind':'u32'})],result={'kind':'u32'})])
   # These fixtures isolate transport-adapter validation, not runtime speed.
   for index,body in enumerate(['return Uint8Array.of(2);','return Uint8Array.of(2,99);','bytes[0]=99;return Uint8Array.of(2);']):
    path=root/f'{index}.mjs'
    path.write_text('export const metadata='+json.dumps(metadata)+';export function call(){return 2;} const args={encode:v=>Uint8Array.of(v.x)};const result={encode:v=>Uint8Array.of(v)};args.compact=()=>args;result.compact=()=>result;export const valueCodecs={run:{arguments:args,result}};export function callBinary(name,bytes){'+body+'} export const callCompact=callBinary;')
    artifacts.append(dict(measurement=str(index),path=str(path)))
   request=root/'request.json';request.write_text(json.dumps(dict(artifacts=artifacts,phases={'boundary':[dict(function='run',args=[1],expected=2,boundary='portable'),dict(function='run',args=[2],expected=2,boundary='compact')]})))
   result=subprocess.run(['node',str(RUNNER),str(request)],capture_output=True,text=True)
   self.assertEqual(result.returncode,0,result.stderr);report=json.loads(result.stdout)
   self.assertEqual({row['measurement']for row in report['rows']},{'0'})
   self.assertEqual({row['measurement']for row in report['rejected']},{'1','2'})
