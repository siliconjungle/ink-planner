"""Focused installed-law fixture; no selection packet or workload is supplied."""
import json,os,subprocess,sys
from pathlib import Path
from ink_knowledge import Store,semantic_entry
from ink_knowledge.store import entry
from ink_knowledge.recipes import load
from producers.list_slice_laws import named_laws

compiler,source,output=map(lambda s:Path(s).resolve(),sys.argv[1:4])
output.mkdir(parents=True,exist_ok=True);repo=Path(__file__).resolve().parents[2]
store=Store.publish(output/'knowledge',[*[semantic_entry(v)for _,v in named_laws()],entry('definition','ink-semantic-producers-v1',dict(module='producers.list_slice_laws'))]);store.refresh_index()
registry,_=load();registry['recipes']=[r for r in registry['recipes']if r['id']=='semantic-laws']
path=output/'registry.json';path.write_text(json.dumps(registry))
subprocess.run([sys.executable,'-m','ink_planner.compiled_planning','--compiler',str(compiler),'--source',str(source),
                '--knowledge-root',str(store.root),'--registry',str(path),'--target','javascript',
                '--budget','128','--state-limit','16','--time-seconds','15','--semantic-budget','128',
                '--semantic-seconds','10','--cache','off','-o',str(output/'plan.json')],check=True,
               env=dict(os.environ,INK_DISTRIBUTION=str(repo),PYTHONPATH=os.pathsep.join([str(repo/'knowledge'),str(repo/'planner')])))
