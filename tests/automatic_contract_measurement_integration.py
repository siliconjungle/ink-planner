"""Actual checked source guards, oracle values, both offline adapters and refusal.
Timing observations validate acquisition only, not a compiler speed claim.
"""
import copy,hashlib,json,subprocess,sys,tempfile
from pathlib import Path
from types import SimpleNamespace
from ink_knowledge import Store
from ink_knowledge.selection_probes import create,policy
from ink_knowledge.implementation_candidates import publish_candidates
from ink_planner.compiled_planning import Planner,code_fingerprint
from ink_planner.implementation_selection import measurement_id
from ink_planner.offline_measurements import acquire
from ink_planner.probe_validation import reference_phases,ProbeInputError

compiler=Path(sys.argv[1]).resolve();reports=[]
with tempfile.TemporaryDirectory(prefix='ink-contract-probes-')as tmp:
 root=Path(tmp);source=root/'source.ink'
 source.write_text('''module Probes;
spec fn positive(x:u32)->u32 requires x>0; ensures result==x;
implementation positive_body for positive{return x;}
spec fn square_domain(xs:List<u32>,width:u32)->u32 requires width>0 && length(xs)==width*width; ensures result==width;
implementation square_body for square_domain{return width;}
pub fn ordinary(x:u32)->u32{return x+1;}
''')
 goals=root/'goals.json';subprocess.run([str(compiler),'emit-contract-obligations',str(source),'-o',str(goals)],check=True,capture_output=True)
 obligations=json.loads(goals.read_text());evidence=root/'proofs.json';evidence.write_text(json.dumps(dict(schema=1,semantics='ink-contracts-v1',source_sha256=obligations['source_sha256'],proofs={g['name']:'Normalize'for g in obligations['pure']})))
 original=root/'core.json';subprocess.run([str(compiler),'emit-core',str(source),'--contract-proofs',str(evidence),'-o',str(original)],check=True,capture_output=True)
 core=json.loads(original.read_text());source_id=json.loads(subprocess.check_output([str(compiler),'check-core',str(original)],text=True))['core_sha256']
 class Actual:
  invoke=Planner.invoke
  directory=root
  compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest();code_hash=code_fingerprint()
 Actual.compiler=compiler;Actual.original=original
 for target in ['javascript','rust']:
  planner=Actual();planner.options=SimpleNamespace(target=target,measure_processes=1,measure_timeout=120,check_timeout=30,memory_bytes=512000000)
  spec,authored=create(core,target);assert set(c['function']for rows in spec['phases'].values()for c in rows)=={'positive','square_domain','ordinary'}
  phases,validated=reference_phases(planner,spec,lambda:30,automatic=True)
  assert validated['complete']and validated['rejected']and all(rows for rows in phases.values()),validated
  assert all(row['args'][0]>0 for rows in phases.values()for row in rows if row['function']=='positive')
  assert all(len(row['args'][0])==row['args'][1]**2 and row['args'][1]>0 for rows in phases.values()for row in rows if row['function']=='square_domain')
  assert set(validated['phases'])==set(spec['profile'])
  package=dict(schema=1,semantics='ink-compiled-plan-v1',input_core_sha256=source_id,steps=[])
  store,candidates=publish_candidates(root/('knowledge-'+target),[package]);planner.root=store.root;measurement=measurement_id(candidates[0],planner.compiler_hash,target)
  path=root/('workload-'+target+'.json');path.write_text(json.dumps(spec))
  try:acquire(planner,store,[package],[{}],[measurement],path,policy())
  except ValueError as error:assert 'precondition failed'in str(error),error
  else:raise AssertionError('supplied workload errors must remain strict')
  _,workload,profile,receipt=acquire(planner,store,[package],[{}],[measurement],path,policy(),automatic_inputs=True)
  assert profile==spec['profile']and receipt['validated_measurements']==[measurement]
  assert receipt['input_validation']['accepted_inputs_sha256']==validated['accepted_inputs_sha256']
  assert workload['shape']['inputs_sha256']==validated['accepted_inputs_sha256']
  assert workload['shape']['inputs_sha256']!=workload['shape']['proposed_inputs_sha256']
  bad=copy.deepcopy(spec);phase=next(p for p,rows in bad['phases'].items()if rows[0]['function']=='positive');bad['phases'][phase]=[dict(bad['phases'][phase][0],args=[0])];path.write_text(json.dumps(bad));before=Store(store.root).publication_id
  try:acquire(planner,store,[package],[{}],[measurement],path,policy(),automatic_inputs=True)
  except ProbeInputError as error:assert error.input_validation['phases'][phase]['accepted']==0
  else:raise AssertionError('empty required phase was silently dropped')
  assert Store(store.root).publication_id==before,'failed acquisition published observations'
  reports.append(dict(target=target,authored=authored,validation=receipt['input_validation'],observations=len(receipt['observations']),validated_measurements=receipt['validated_measurements'],explicit_errors_preserved=True,empty_phase_declined_without_publication=True))
 # Even an apparently true guard in transport cannot replace checked source.
 bad=copy.deepcopy(core);guard=bad['program']['contracts']['precondition_checks']['positive']
 next(f for f in bad['program']['functions']if f['name']==guard)['body']={'Bool':True};original.write_text(json.dumps(bad))
 spec=dict(schema=1,phases={'must-check':[dict(function='positive',args=[0])]},profile={'must-check':1})
 try:reference_phases(planner,spec,lambda:30,automatic=True)
 except ValueError as error:assert 'precondition ingress guard differs'in str(error),error
 else:raise AssertionError('forged guard bypassed checker')
result=dict(status='passed',targets=reports,forged_guard_rejected=True,scope='Source, proof replay, oracle and offline measurement integration; no profitability or runtime speed claim')
if len(sys.argv)>2:Path(sys.argv[2]).write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps({k:v for k,v in result.items()if k!='targets'}));print([(r['target'],r['validation']['proposed_calls'],r['validation']['accepted_calls'],r['observations'])for r in reports])
