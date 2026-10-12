"""Forty-one real checked packets; synthetic lifecycle costs test policy only."""
import hashlib,json,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import canonical,Store
from ink_knowledge.implementation_candidates import identity_selection,publish_candidates
from ink_planner.implementation_selection import measurement_id
from ink_planner.representation_selection import phase_workload
compiler,source=(Path(p).resolve()for p in sys.argv[1:])
with tempfile.TemporaryDirectory(prefix='ink-compiled-pool-')as temporary:
 root=Path(temporary);core=root/'source.json'
 source_id=json.loads(subprocess.check_output([compiler,'emit-core',source,'-o',core],text=True))['core_sha256']
 rewrite=identity_selection(source_id)
 packages=[dict(schema=1,semantics='ink-compiled-plan-v1',input_core_sha256=source_id,steps=[dict(kind='rewrite',package=rewrite)for _ in range(n)])for n in range(41)]
 store,ids=publish_candidates(root/'knowledge',packages)
 compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest()
 target=dict(hardware='synthetic policy test',driver='test',toolchain='test',backend='rust',bridge='native')
 workload=dict(shape={'rows':16},distribution='policy only',concurrency=1,residency='host')
 phases=['check','compile','initialise','action:zero','checkpoint','restore']
 for index,candidate in enumerate(ids):
  for phase in phases:
   cost=1 if index==40 else 100
   store.observe(dict(schema=1,program=source_id,entries=[candidate],plan=measurement_id(candidate,compiler_hash,'rust'),target=target,workload=phase_workload(workload,phase),metrics=dict(complete_call_ns=[str(cost)],peak_bytes=100)))
 paths={}
 for name,value in [('candidates',ids[1:]),('target',target),('workload',workload),('profile',{p:1 for p in phases})]:
  paths[name]=root/(name+'.json');paths[name].write_bytes(canonical(value))
 output=root/'selected.json'
 command=[sys.executable,'planner/select_implementation.py','--compiler',str(compiler),'--source',str(source),'--knowledge-root',str(store.root),'--emit-target','rust','--baseline',ids[0],'--no-proposals','--memory-limit','100','-o',str(output)]
 for name,path in paths.items():command+=['--'+name,str(path)]
 result=subprocess.run(command,capture_output=True,text=True,timeout=600)
 assert result.returncode==0,result.stderr
 receipt=json.loads(result.stdout)
 assert receipt['checked']==41 and receipt['remaining']==0 and not receipt['rejected'],receipt
 assert receipt['selected']==ids[40],receipt
 assert receipt['costs']['baseline_comparable']is True
 print('41 source-bound compiled packets checked; complete lifecycle + measured memory policy selected candidate41 beyond old32 cap')
