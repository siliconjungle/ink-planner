#!/usr/bin/env python3
"""Untimed real-checker/snapshot replay over an existing helper-bound DB law.
Synthetic prices demonstrate plumbing only, never measured profitability.
"""
import hashlib,json,os,subprocess,sys
from pathlib import Path
B=Path(__file__).resolve().parents[2];sys.path[:0]=[str(B/'planner'),str(B/'knowledge')]
from ink_knowledge import Store
from ink_planner.physical_features import file_hash,load_observation
from producers.target_work40_sidecar import produce


def main():
 r=Path(os.environ.get('INK_PHYSICAL40_INTEGRATION_DIR',str(B/'reports/web-stack-engine-40-2026-10-11/cost/checker-integration'))).resolve();r.mkdir(parents=True,exist_ok=False)
 old=B/'reports/web-stack-proofs-38-2026-10-11/targets/targets/pipeline38_left_padded_zip_heldout_u32'
 snapshot=B/'knowledge/store/snapshots/8d1f3460cc2aa16bfd176081e2b7fab552e9ec8ea858755c566b3708107c80ef.json';store=Store(B/'knowledge',snapshot=snapshot)
 compiler=B/'reports/web-stack-proofs-32-2026-10-11/main-integration/ink-main-publish'
 parser=B/'reports/web-stack-engine-40-2026-10-11/cost/tools/node_modules/acorn/dist/acorn.mjs'
 input_core=old/'literal.core.json';entry='pipeline38_left_padded_zip_heldout_u32'
 p=json.loads((old/'prior.selection.json').read_text());p['knowledge']=store.bundle([]);(r/'baseline.selection.json').write_text(json.dumps(p,indent=2)+'\n')
 fixtures=[]
 for n in(0,1,2,7,33):
  for k in(0,7):
   xs=[((i*13)^0xffffffff)&0xffffffff for i in range(n)];ys=[7,0,0xffffffff];seed=7;out=[]
   for i,x in enumerate(xs):
    a=((divmod(x,k)[0]if k else 0)+(divmod(i,k)[1]if k else 0)+seed)%2**32
    b=(x+k if i<seed else x*3)%2**32
    out.append(((divmod(a,k)[0]if k else 0)+b if a<b else(divmod(b,k)[1]if k else 0)+a)%2**32)
   for boundary in('owned','into'):fixtures.append(dict(id=f'{n}-{k}-{boundary}',entry=entry,args=[xs,ys,seed,k],expected=out,boundary=boundary))
 fixture=r/'fixtures.json';fixture.write_text(json.dumps(fixtures)+'\n');candidates=[];logs=[]
 def run(command):
  result=subprocess.run(list(map(str,command)),capture_output=True,text=True,env=dict(os.environ,PYTHONPATH=str(B/'planner')+':'+str(B/'knowledge')),timeout=60)
  logs.append(dict(command=list(map(str,command)),exit=result.returncode,stdout=result.stdout,stderr=result.stderr))
  if result.returncode:raise ValueError(result.stderr)
  return result
 for variant in('prior','checked'):
  directory=r/variant;directory.mkdir();artifact=old/variant/'program.mjs';selection=r/'baseline.selection.json'if variant=='prior'else old/'checked.selection.json';core=old/(variant+'.core.json')
  instrumented=directory/'program.instrumented.mjs';instrumentation=directory/'instrumentation.json';trace=directory/'trace.json'
  run(['node',B/'knowledge/producers/target_work40_js.mjs',artifact,instrumented,instrumentation,parser])
  run(['node',B/'knowledge/producers/target_work40_replay.mjs',artifact,instrumented,instrumentation,fixture,trace])
  cohort=dict(target='javascript',host=dict(engine='node',version=run(['node','--version']).stdout.strip()),boundary='owned',emitter_sha256=file_hash(compiler),entry_abi='ink-javascript-trusted-values-v2')
  paths=dict(original_core=input_core,selected_core=core,selection=selection,artifact=artifact,fixture=fixture,instrumenter=B/'knowledge/producers/target_work40_js.mjs',instrumented=instrumented,instrumentation=instrumentation,parser=parser,runner=B/'knowledge/producers/target_work40_replay.mjs',trace=trace)
  rows=produce(directory/'sidecars',paths,cohort)
  features=next(Path(x['path'])for x in rows if x['fixture_id']=='33-7-owned');cohort=load_observation(features)['cohort']
  candidates.append(dict(id=variant,selection=str(selection),selected_core=str(core),artifact=str(artifact),features=str(features)))
 model=dict(schema=1,kind='ink-target-work-prices-v1',cohort=cohort,weights=dict(invocations=1,loop_iterations=1,allocation_events=1,copy_bytes=1),training=dict(protocol_sha256='0'*64,observations_sha256='0'*64,scope='Synthetic interface test weights; not calibrated and no performance claim'))
 (r/'model.json').write_text(json.dumps(model)+'\n');index=r/'candidates.json';index.write_text(json.dumps(candidates)+'\n')
 command=[sys.executable,'-m','ink_planner.physical_selection','--compiler',compiler,'--core',input_core,'--snapshot',snapshot,'--knowledge-root',B/'knowledge','--candidates',index,'--model',r/'model.json','--output',r/'selected.json']
 result=run(command);receipt=json.loads(result.stdout);assert receipt['selected']=='checked',receipt
 assert len(receipt['checked']['checked']['admission']['applied_laws'])>=1,receipt
 # Changing trace-bound emitted bytes removes that candidate's physical score;
 # it cannot promote a semantically unchecked replacement.
 bad=r/'bad-artifact.mjs';bad.write_bytes(candidates[1]['artifact'].encode());bad_index=r/'bad-candidates.json';wrong=[dict(x)for x in candidates];wrong[1]['artifact']=str(bad);bad_index.write_text(json.dumps(wrong)+'\n')
 bad_command=[bad_index if str(x)==str(index)else r/'bad-selected.json'if str(x)==str(r/'selected.json')else x for x in command]
 assert json.loads(run(bad_command).stdout)['selected']=='prior'
 bad_selection=r/'false.selection.json';false=json.loads(Path(candidates[1]['selection']).read_text());false['applications'][0]['path']=[999];bad_selection.write_text(json.dumps(false)+'\n');wrong=[dict(x)for x in candidates];wrong[1]['selection']=str(bad_selection);bad_index=r/'false-candidates.json';bad_index.write_text(json.dumps(wrong)+'\n')
 bad_command=[bad_index if str(x)==str(index)else r/'false-selected.json'if str(x)==str(r/'selected.json')else x for x in command]
 rejection=json.loads(run(bad_command).stdout);assert rejection['selected']=='prior'and'checked'in rejection['rejected']
 (r/'commands.json').write_text(json.dumps(logs,indent=2)+'\n')
 (r/'completion.json').write_text(json.dumps(dict(status='passed',frames=40,ordinary_db_applications=1,selected=receipt['selected'],snapshot=store.snapshot_id,positive='Existing arbitrary indexed/padded zip helper-bound DB law; exact original/selected core and target bytes replayed by real compiler.',negative=['Artifact binding change retains baseline','False rewrite path rejected by ordinary checker'],scope='Untimed synthetic cost plumbing only; no model calibration, speed or online selection claim'),indent=2)+'\n')
 print(json.dumps(dict(status='passed',frames=40,selected=receipt['selected'])))
if __name__=='__main__':main()
