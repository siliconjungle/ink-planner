"""Ordinary discovery composes initially unmatched templates and checks each edge."""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import Store
from ink_knowledge.recipes import load

compiler=Path(sys.argv[1]).resolve();repo=Path(__file__).resolve().parents[2]
templates=json.loads((repo/'reports/knowledge-growth-2026-10-13/growth/templates.json').read_text())
with tempfile.TemporaryDirectory(prefix='ink-composed-templates-integration-')as tmp:
    root=Path(tmp);source=root/'example.ink'
    source.write_text('module composition;pub fn run(xs:List<u32>,cut:u32)->Bool{return foldl(xs.filter(fn(x)=>x>=16).map(fn(x)=>x==cut),false,fn(x)=>fn(a)=>a || x);}\n')
    store=Store.publish(root/'knowledge',[r['object']for r in templates]);store.refresh_index()
    registry,_=load();registry['recipes']=[r for r in registry['recipes']if r['id']=='semantic-laws']
    reg=root/'recipes.json';reg.write_text(json.dumps(registry));plan=root/'plan.json';output=root/'output.mjs'
    environment=dict(os.environ,INK_DISTRIBUTION=str(repo),PYTHONPATH=os.pathsep.join([str(repo/'knowledge'),str(repo/'planner')]))
    command=[sys.executable,'-m','ink_planner.compiled_planning','--compiler',str(compiler),'--source',str(source),
             '--knowledge-root',str(store.root),'--registry',str(reg),'--target','javascript',
             '--budget','256','--state-limit','32','--time-seconds','20','--semantic-budget','256','--semantic-seconds','15',
             '--cache','off','--emit',str(output),'-o',str(plan)]
    result=subprocess.run(command,env=environment,capture_output=True,text=True);assert result.returncode==0,result.stderr
    receipt=json.loads(Path(str(plan)+'.receipt.json').read_text());store=Store(store.root)
    lengths=[]
    for candidate in receipt['candidates']:
        package=store.object(candidate['candidate'])['payload']['package']
        lengths.append(sum(len(s['package'].get('applications',[]))for s in package['steps']if s['kind']=='rewrite'))
        path=root/'candidate.json';path.write_text(json.dumps(package))
        subprocess.run([str(compiler),'check-plan',str(source),str(path)],check=True,capture_output=True)
    assert max(lengths)>=3,(lengths,receipt['recipes'])
    assert receipt['measurement_acquisition'],receipt.get('automatic_probe')
    packet=json.loads(plan.read_text());assert packet['steps'],receipt['selection']
    print('Map/filter/absorbing composition discovered with ordinary search; every candidate freshly replayed; automatic measured selection released',len(packet['steps']),'plan steps')
