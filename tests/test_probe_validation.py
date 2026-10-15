import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from ink_planner.probe_validation import reference_phases,ProbeInputError

class ProbeValidation(unittest.TestCase):
 def fixture(self,root):
  source=root/'core.json';source.write_text(json.dumps({'program':{'contracts':{'precondition_checks':{'entry':'predicate'}}}}))
  calls=[]
  def invoke(command,original,function,args,*flags,**kwargs):
   value=json.loads(args.read_text())[0];calls.append((function,value))
   if function=='predicate':return 'true'if value else'false'
   if value==0:raise ValueError('contract precondition failed')
   return value
  planner=SimpleNamespace(original=source,directory=root,invoke=invoke)
  rows=[dict(function='entry',args=[x])for x in [0,1,2]]
  return planner,dict(schema=3,phases={'one':[dict(r,boundary='portable')for r in rows],'two':[dict(r,boundary='compact')for r in rows]},profile={'one':7,'two':11}),calls
 def test_only_false_guards_filter_and_transfer_boundaries_reuse_reference(self):
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,calls=self.fixture(Path(tmp));phases,receipt=reference_phases(planner,spec,lambda:1,automatic=True)
   self.assertEqual([[r['expected']for r in rows]for rows in phases.values()],[[1,2],[1,2]])
   self.assertEqual(spec['profile'],{'one':7,'two':11})
   self.assertEqual(calls,[('predicate',0),('predicate',1),('entry',1),('predicate',2),('entry',2)])
   self.assertEqual((receipt['accepted_calls'],receipt['guard_executions'],receipt['reference_executions'],receipt['reused_calls']),(4,3,2,3))
   self.assertEqual(len(receipt['rejected']),2);self.assertTrue(receipt['complete'])
   other=json.loads(json.dumps(spec));other['phases']['one'][1]['args']=[3]
   _,changed=reference_phases(planner,other,lambda:1,automatic=True)
   self.assertNotEqual(receipt['accepted_inputs_sha256'],changed['accepted_inputs_sha256'])
 def test_explicit_workloads_still_execute_and_reject_invalid_calls(self):
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,calls=self.fixture(Path(tmp))
   with self.assertRaisesRegex(ValueError,'contract precondition failed'):reference_phases(planner,spec,lambda:1)
   self.assertEqual(calls,[('entry',0)])
 def test_no_required_phase_can_disappear(self):
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,_=self.fixture(Path(tmp));spec['phases']['two']=spec['phases']['two'][:1]
   with self.assertRaises(ProbeInputError)as error:reference_phases(planner,spec,lambda:1,automatic=True)
   receipt=error.exception.input_validation;self.assertFalse(receipt['complete']);self.assertEqual(receipt['phases']['two']['accepted'],0)
   self.assertNotIn('accepted_inputs_sha256',receipt)
 def test_nonboolean_guard_and_admitted_execution_errors_propagate(self):
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,_=self.fixture(Path(tmp));planner.invoke=lambda *a,**kw:1
   with self.assertRaisesRegex(ValueError,'Bool'):reference_phases(planner,spec,lambda:1,automatic=True)
   def failure(command,original,function,*a,**kw):
    if function=='predicate':return True
    raise ValueError('source body failed')
   planner.invoke=failure
   with self.assertRaisesRegex(ValueError,'source body failed'):reference_phases(planner,spec,lambda:1,automatic=True)
 def test_source_mutation_and_guard_timeout_are_not_filtered(self):
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,_=self.fixture(Path(tmp));original=planner.invoke
   def mutate(*args,**kwargs):
    result=original(*args,**kwargs);planner.original.write_text('{}');return result
   planner.invoke=mutate
   with self.assertRaisesRegex(ValueError,'source changed'):reference_phases(planner,spec,lambda:1,automatic=True)
   planner,spec,_=self.fixture(Path(tmp))
   import subprocess
   def timeout(*a,**kw):raise subprocess.TimeoutExpired('guard',1)
   planner.invoke=timeout
   with self.assertRaises(subprocess.TimeoutExpired):reference_phases(planner,spec,lambda:1,automatic=True)
 def test_error_values_are_ordinary_results(self):
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,_=self.fixture(Path(tmp));planner.original.write_text(json.dumps({'program':{}}));planner.invoke=lambda *a,**kw:{'Err':'ordinary source result'}
   phases,receipt=reference_phases(planner,spec,lambda:1,automatic=True)
   self.assertEqual(receipt['accepted_calls'],6);self.assertEqual(receipt['rejected'],[])
   self.assertTrue(all(r['expected']=={'Err':'ordinary source result'}for rows in phases.values()for r in rows))

 def test_floats_signed_zero_and_exact_integer_packets_keep_distinct_keys(self):
  from ink_planner.probe_validation import input_identity
  self.assertNotEqual(input_identity(0.0),input_identity(-0.0))
  self.assertNotEqual(input_identity(1),input_identity(1.0))
  self.assertNotEqual(input_identity(2**64-1),input_identity(float(2**64-1)))
  self.assertNotEqual(input_identity({'integer':'1'}),input_identity(1))
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,_=self.fixture(Path(tmp));planner.original.write_text(json.dumps({'program':{}}))
   planner.invoke=lambda command,original,function,args,*a,**kw:json.loads(args.read_text())[0]
   rows=[dict(function='entry',args=[x],boundary='trusted')for x in [0.0,-0.0,1.1,1.10000001]]
   spec['phases']={'one':rows,'two':rows};phases,receipt=reference_phases(planner,spec,lambda:1,automatic=True)
   self.assertEqual(receipt['reference_executions'],4);self.assertEqual(receipt['reused_calls'],4)
   import math
   self.assertEqual(math.copysign(1,phases['one'][1]['expected']),-1)

 def test_typed_reference_identities_survive_guard_filtering_and_reuse(self):
  with tempfile.TemporaryDirectory()as tmp:
   planner,spec,calls=self.fixture(Path(tmp))
   def typed(function,args):
    path=planner.directory/'typed-input.json';path.write_text(json.dumps(args))
    result=planner.invoke('run',planner.original,function,path)
    return (json.loads(result)if isinstance(result,str)else result),format(args[0],'064x')
   phases,receipt=reference_phases(planner,spec,lambda:1,automatic=True,reference=typed)
   self.assertEqual([[r['input_sha256']for r in rows]for rows in phases.values()],[[format(1,'064x'),format(2,'064x')]]*2)
   self.assertEqual(receipt['reference_executions'],2);self.assertEqual(receipt['reused_calls'],3)
   ordinary,plain_receipt=reference_phases(planner,spec,lambda:1,automatic=True)
   self.assertEqual(receipt['accepted_inputs_sha256'],plain_receipt['accepted_inputs_sha256'])
   self.assertEqual([{k:v for k,v in row.items()if k!='input_sha256'}for row in phases['one']],ordinary['one'])
