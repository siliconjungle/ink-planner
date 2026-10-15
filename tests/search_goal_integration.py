"""Actual compiled Ink model, checked algebraic graph, and bounded search/cache."""
import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from ink_knowledge import Store,canonical
from ink_planner.compiled_planning import Planner,explore
from ink_planner.search_goal import SearchGoal

compiler,folder=(Path(p).resolve()for p in sys.argv[1:]);manifest=json.loads((folder/'graph.json').read_bytes())
source=folder/'source.core.json';goal=Path('examples/search-policy.ink').resolve()
knowledge=folder/'knowledge';Store.publish(knowledge,[])
initial=dict(schema=1,semantics='ink-compiled-plan-v1',input_core_sha256=manifest['initial'],steps=[])
def options(context):
 return SimpleNamespace(target='javascript',snapshot=None,time_seconds=30,check_timeout=30,
  observed_frequencies={'call:main':1},search_goal=goal,search_goal_core=False,search_goal_context=context,
  search_goal_context_wire=False,search_goal_entry='priority',search_goal_revision=1,
  search_goal_timeout=3,search_goal_memory_mb=128,search_goal_input_bytes=4_000_000,search_goal_node='node')

summaries={}
for mode,context in [('before',folder/'before.json'),('learned',folder/'after.json')]:
 directory=folder/mode;directory.mkdir(exist_ok=True)
 planner=Planner(compiler,source,knowledge,directory,[],'a'*64,options(context))
 def expand(state,_):
  for step in manifest['edges'].get(state['source'],[]):yield dict(status='proposal',step=step)
 states,receipt=explore(initial,expand,planner.check,planner.priority,budget=3,time_seconds=30,
                       expansion_limit=2,priority_epoch=planner.priority_epoch)
 sources={s['source']for _,s in states.values()}
 assert not planner.search_goal.disabled,planner.search_goal.failure
 assert (manifest['final']in sources)==(mode=='learned'),(mode,sources,receipt)
 assert receipt['attempts']==3
 receipt['search_goal']=planner.search_goal.export(directory/'result.json')
 summaries[mode]=dict(receipt=receipt,sources=sorted(sources))
 if mode=='learned':
  packet=next(p for p,s in states.values()if s['source']==manifest['final'])
  (folder/'selected.plan.json').write_bytes(canonical(packet)+b'\n')
  subprocess.run([compiler,'check-plan',source,folder/'selected.plan.json','--core','--target','javascript'],check=True,capture_output=True)
 planner.close()

# The real published-candidate producer discovers these proof packets from a
# private database. Even with better-ranked alternatives, missing cost evidence
# still retains the baseline. Model/core/context sidecars support exact replay.
from ink_knowledge.implementation_candidates import publish_candidates
publish_candidates(knowledge,[step['package']for steps in manifest['edges'].values()for step in steps])
registry=folder/'registry.json';registry.write_text(json.dumps(dict(schema=1,semantics='ink-planning-recipes-v1',recipes=[
 dict(id='published',module='producers.planning_recipes',function='published',parameters={},stage='rewrite',inputs=['core'])])))
command=[sys.executable,'-m','ink_planner.compiled_planning','--compiler',str(compiler),'--source',str(source),'--core',
 '--knowledge-root',str(knowledge),'--registry',str(registry),'--target','javascript','--time-seconds','30',
 '--measurement-mode','off','--budget','16','--state-limit','16','--search-goal',str(goal),'--search-goal-context',str(folder/'after.json')]
receipts=[]
for index in range(2):
 output=folder/f'cli{index}.plan.json'
 result=subprocess.run([*command,'-o',str(output)],capture_output=True,text=True,timeout=120)
 assert result.returncode==0,result.stderr
 receipt=json.loads(Path(str(output)+'.receipt.json').read_bytes())
 assert json.loads(output.read_bytes())['steps']==[],receipt
 assert 'missing comparable'in receipt['selection']['reason'],receipt
 assert receipt['admitted_states']>=4,receipt
 assert all((output.parent/name).is_file()for name in receipt['search_goal']['artifacts'].values())
 receipts.append(receipt)
assert not receipts[0].get('cache',{}).get('hit',False) and receipts[1]['cache']['hit']is True,receipts
assert receipts[0]['search_goal']['binding']==receipts[1]['search_goal']['binding']
assert receipts[1]['search_goal']['queries']==0
assert receipts[1]['search_goal']['historical']['queries']>=4
replay=folder/'replayed.plan.json'
goal_core=folder/receipts[0]['search_goal']['artifacts']['core']
goal_context=folder/receipts[0]['search_goal']['artifacts']['context']
replayed=[*command];replayed[replayed.index(str(goal))]=str(goal_core)
replayed[replayed.index(str(folder/'after.json'))]=str(goal_context)
result=subprocess.run([*replayed,'--search-goal-core','-o',str(replay)],capture_output=True,text=True,timeout=120)
assert result.returncode==0,result.stderr
assert json.loads(replay.read_bytes())==json.loads((folder/'cli0.plan.json').read_bytes())

# Invalid proofs never reach the model; they still consume an admission attempt.
directory=folder/'adversarial';directory.mkdir(exist_ok=True)
planner=Planner(compiler,source,knowledge,directory,[],'a'*64,options(folder/'after.json'))
bad=json.loads(json.dumps(manifest['edges'][manifest['initial']][0]));bad['package']['applications'][0]['path']=[999999]
def adversarial(state,states):
 if state['source']==manifest['initial']:yield dict(status='proposal',step=bad)
 yield from expand(state,states)
states,rejected=explore(initial,adversarial,planner.check,planner.priority,budget=4,time_seconds=30,
                        expansion_limit=3,priority_epoch=planner.priority_epoch)
assert len(rejected['rejected'])==1 and rejected['rejected'][0]['gate']=='admission',rejected
assert manifest['final']in {state['source']for _,state in states.values()}
assert planner.search_goal.queries==len(states),'only newly checked states acquire priorities'
planner.close()

# Exercise the actual worker contract, with exact >2^53 state/context/frequency
# data, nonfinite results, a timed-out computation and a stale response identity.
contract=Path('tests/fixtures/search-goal-contract.ink').resolve();context_path=folder/'contract-context.json'
context_path.write_text(json.dumps(dict(word=2**64-1,token=str(2**64-1),iterations=1_000_000_000,value=0)))
worker_rows=[]
for entry in ['priority','invalid','expensive','stale']:
 directory=folder/('worker-'+entry);directory.mkdir(exist_ok=True)
 worker=SearchGoal(compiler,contract,context_path,'priority'if entry=='stale'else entry,directory,timeout=3)
 child=worker.process
 if entry=='expensive':worker.timeout=0.01
 if entry=='stale':
  worker.close()
  worker.process=subprocess.Popen(['node','-e',"process.stdin.once('data',()=>console.log(JSON.stringify({id:999,score:-999})))"],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
  child=worker.process
 state=dict(source='a'*64,sentinel=2**64-1)
 value=worker.score(state,'b'*64,'javascript',{'call:main':2**64-1},10)
 if entry=='priority':
  assert value==7 and not worker.disabled,(value,worker.failure)
  assert worker.score(state,'b'*64,'javascript',{'call:main':2**64-1},10)==7
  assert worker.hits==1
  # Mutating the original context file cannot change a captured model revision.
  old=context_path.read_bytes();context_path.write_text('{}')
  assert worker.score(state,'c'*64,'javascript',{'call:main':2**64-1},10)==7
  context_path.write_bytes(old)
  original=worker.module.read_bytes();worker.module.write_bytes(original+b'\n// tampered artifact\n')
  try:worker.export(directory/'tampered.json');raise AssertionError('changed artifact accepted')
  except ValueError as error:assert 'artifact changed'in str(error)
  worker.module.write_bytes(original)
 else:
  assert value==10 and worker.disabled and worker.failures==1,(entry,value,worker.failure)
  assert child.poll()is not None
  assert worker.score(state,'b'*64,'javascript',{},10)==10 and worker.failures==1
 worker_rows.append(worker.export(directory/'receipt.json'));worker.close()

# Binary context transport retains raw F64 distinctions that semantic JSON
# cannot represent. Reuse a retained checked core image for each new context.
core=folder/'worker-priority/search-goal.core.json';module=folder/'worker-priority/search-goal.mjs'
wire_path=folder/'context.bin';bindings=[]
for bits in ['0','9223372036854775808','9221120237041095220']:
 script="""import fs from 'node:fs/promises';import {pathToFileURL} from 'node:url';
const [path,out,bits]=process.argv.slice(1);const module=await import(pathToFileURL(path));
const codec=module.compiledValues.priority.params[1];
await fs.writeFile(out,codec.encode(codec.capture({word:18446744073709551615n,token:'18446744073709551615',iterations:1,value:{F64Bits:bits}})));"""
 subprocess.run(['node','--input-type=module','-e',script,str(module),str(wire_path),bits],check=True,capture_output=True)
 directory=folder/('wire-'+bits);directory.mkdir(exist_ok=True)
 worker=SearchGoal(compiler,core,wire_path,'priority',directory,core=True,context_wire=True,timeout=3)
 assert worker.score(dict(source='a'*64,sentinel=2**64-1),'b'*64,'javascript',{'call:main':2**64-1},10)==7
 bindings.append(worker.binding['context']);worker_rows.append(worker.export(directory/'receipt.json'));worker.close()
assert len(set(bindings))==3,'wire capture must distinguish +0, -0 and raw NaN'

directory=folder/'worker-size';directory.mkdir(exist_ok=True)
worker=SearchGoal(compiler,core,context_path,'priority',directory,core=True,timeout=3,input_bytes=1024)
assert worker.score(dict(source='a'*64,large='x'*10000),'b'*64,'javascript',{},10)==10
assert worker.disabled and 'byte budget'in worker.failure
worker_rows.append(worker.export(directory/'receipt.json'));worker.close()

(folder/'search.json').write_text(json.dumps(dict(schema=1,status='passed',search=summaries,cli=receipts,
 workers=worker_rows,rejected_proofs=rejected,
 scope='Three real proof attempts per graph; compiled learned context changes the explored branch. Synthetic authored labels, no performance/generalization claim.'),indent=2)+'\n')
print('compiled learned search: full-chain replay, changed exploration, baseline evidence gate, retained model replay and cache passed')
