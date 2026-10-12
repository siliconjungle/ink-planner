"""Defaulted physical fields must not discard valid planner proposals."""
import json,subprocess,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'knowledge'),str(ROOT/'planner')]
from ink_knowledge import Store
from ink_knowledge.recipes import load
with tempfile.TemporaryDirectory(prefix='ink-defaulted-plan-')as temporary:
 d=Path(temporary);Store.publish(d/'knowledge',[],{});source=d/'source.ink';source.write_text('module defaulted;fn run(xs:List<u32>)->List<u32>{return xs.map(fn(x)=>x+1);}')
 registry=load()[0];registry['recipes']=[r for r in registry['recipes']if r['function']=='physical'];file=d/'registry.json';file.write_text(json.dumps(registry))
 r=subprocess.run([sys.executable,str(ROOT/'planner/compile_plan.py'),'--compiler',str(Path(sys.argv[1]).resolve()),'--source',str(source),'--knowledge-root',str(d/'knowledge'),'--registry',str(file),'--budget','4','--state-limit','8','--time-seconds','10','--target','rust','-o',str(d/'plan.json')],capture_output=True,text=True);assert r.returncode==0,r.stderr
 receipt=json.loads((d/'plan.json.receipt.json').read_bytes());assert len(receipt['candidates'])>1,receipt['rejected'];assert not any(e['reason']=='plan checker binding mismatch'for e in receipt['rejected']);print('defaulted physical proposals admitted; baseline retained without comparable measurements')
