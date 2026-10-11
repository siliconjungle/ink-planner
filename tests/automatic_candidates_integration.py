"""Cold automatic proposal, complete checker admission, and existing measured rank.

Synthetic costs exercise policy only; the companion benchmark supplies speed evidence.
"""
import hashlib,json,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import Store,canonical
from ink_planner.implementation_selection import measurement_id
from ink_planner.representation_selection import phase_workload
compiler,source=(Path(x).resolve() for x in sys.argv[1:])
with tempfile.TemporaryDirectory(prefix='ink-automatic-integration-') as temporary:
 root=Path(temporary);database=root/'knowledge';output=root/'selected.json'
 target=dict(hardware='synthetic policy test',driver='test',toolchain='test',backend='javascript',bridge='host')
 workload=dict(shape={'rows':16},distribution='paired edits',concurrency=1,residency='restored')
 paths={name:root/(name+'.json') for name in ['target','workload','profile']}
 for name,value in [('target',target),('workload',workload)]:paths[name].write_bytes(canonical(value))
 command=[sys.executable,'planner/select_implementation.py','--compiler',str(compiler),'--source',str(source),'--knowledge-root',str(database),'--emit-target','javascript','--check-timeout-seconds','300','--proposal-timeout-seconds','600','-o',str(output)]
 for name,path in paths.items():command+=['--'+name,str(path)]
 def select(profile):
  paths['profile'].write_bytes(canonical(profile));r=subprocess.run(command,capture_output=True,text=True,timeout=1800);assert r.returncode==0,r.stderr;return json.loads(r.stdout)
 first=select({'action:erase':100})
 assert first['checked']==3 and len(first['proposals'])==2 and not first['rejected'],first
 assert first['selected']==first['baseline'] and first['costs']['baseline_comparable'] is False
 assert all(p['status']=='checked' for p in first['proposals'])
 assert any('grouped' in d for p in first['proposals'] for d in p['discovery'])
 recipes=list((database/'proposals'/first['source']).glob('*.json'));assert len(recipes)==1
 recipe=json.loads(recipes[0].read_bytes());indexed,grouped=recipe['candidates'];baseline=recipe['baseline']
 store=Store(database);compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest()
 for candidate,init,edit in [(baseline,1,100),(indexed,100,20),(grouped,200,5)]:
  for phase,cost in [('initialise',init),('action:erase',edit)]:
   store.observe(dict(schema=1,program=first['source'],entries=[candidate],plan=measurement_id(candidate,compiler_hash,'javascript'),target=target,workload=phase_workload(workload,phase),metrics=dict(complete_call_ns=[str(cost)],peak_bytes=100)))
 assert select({'action:erase':100})['selected']==grouped
 assert select({'initialise':1,'action:erase':1})['selected']==baseline
 assert select({'initialise':1,'action:erase':2})['selected']==indexed
 assert select({'action:erase':100})['selected']==grouped
 replay=[sys.executable,'planner/select_implementation.py','--compiler',str(compiler),'--source',str(source),'--knowledge-root',str(database),'--emit-target','javascript','--check-timeout-seconds','300','--replay',str(output),'-o',str(root/'generated.mjs')]
 r=subprocess.run(replay,capture_output=True,text=True,timeout=600);assert r.returncode==0,r.stderr
 assert (root/'generated.mjs').is_file()
 target['driver']='unmeasured';paths['target'].write_bytes(canonical(target));assert select({'action:erase':100})['selected']==baseline
 print('Cold proposals checked; measured grouped/indexed/baseline selection; exact replay; stale target fallback passed.')
