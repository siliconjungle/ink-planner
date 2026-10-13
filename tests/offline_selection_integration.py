"""Actual offline acquisition, full proof replay and held-out value checks.

Development timing only: this script is an integration test, not a blinded
performance experiment or a whole-backend conformance suite.
"""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import Store,semantic_entry
from ink_knowledge.store import entry
from ink_knowledge.recipes import load
from producers.list_slice_laws import named_laws
compiler=Path(sys.argv[1]).resolve();repo=Path(__file__).resolve().parents[2]
with tempfile.TemporaryDirectory(prefix='ink-offline-selection-integration-')as tmp:
    root=Path(tmp);source=root/'example.ink'
    source.write_text('module example; pub fn run(xs:List<u32>,n:u32)->List<u32>{return xs.map(fn(x)=>(x*13)+7).take(n);}\n')
    objects=[semantic_entry(v)for _,v in named_laws()]
    objects.append(entry('definition','ink-semantic-producers-v1',dict(module='producers.list_slice_laws')))
    store=Store.publish(root/'knowledge',objects);store.refresh_index()
    registry,_=load();registry['recipes']=[r for r in registry['recipes']if r['id']=='semantic-laws'];reg=root/'recipes.json';reg.write_text(json.dumps(registry))
    spec=dict(schema=1,phases={'call:run':[dict(function='run',args=[list(range(32768)),3])]},profile={'call:run':1},workload=dict(shape={'rows':32768,'take':3},distribution='development acquisition',concurrency=1,residency='captured inputs; owned outputs'))
    workload=root/'workload.json';workload.write_text(json.dumps(spec));output=root/'selected.mjs'
    command=[str(compiler),'compile',str(source),'--optimise',str(store.root),'--registry',str(reg),'--target','javascript','--search-budget','128','--state-limit','16','--planning-seconds','15','--planning-effort','quick','--recipe-workers','2','--semantic-budget','128','--semantic-seconds','10','-o',str(output)]
    env=dict(os.environ,INK_DISTRIBUTION=str(repo))
    r=subprocess.run([*command,'--measurement-mode','off'],capture_output=True,text=True,env=env);assert r.returncode==0,r.stderr
    baseline=json.loads(Path(str(output)+'.planning.json').read_bytes());assert not baseline['steps']
    r=subprocess.run([*command,'--measure-workload',str(workload)],capture_output=True,text=True,env=env);assert r.returncode==0,r.stderr
    selected=json.loads(Path(str(output)+'.planning.json').read_bytes());assert selected['steps']
    receipt=json.loads(Path(str(output)+'.planning.json.receipt.json').read_bytes());assert receipt['planning_budgets']['effort']=='quick';assert receipt['planning_budgets']['time_seconds']==15;assert receipt['planning_budgets']['semantic_budget']==128;assert receipt['selection']['baseline_comparable'];assert receipt['measurement_acquisition']['observations']
    subprocess.run([str(compiler),'check-plan',str(source),str(output)+'.planning.json'],check=True,capture_output=True)
    cases=[]
    for xs in [[],[0,2,4294967295],[19,1,5,3,7],list(range(193))]:
        for n in [0,1,4,len(xs),len(xs)+1,4294967295]:
            args=root/'args.json';args.write_text(json.dumps([xs,n]))
            expected=json.loads(subprocess.check_output([str(compiler),'run',str(source),'run',str(args)],text=True))
            cases.append(dict(args=[xs,n],expected=expected))
    casefile=root/'held-out.json';casefile.write_text(json.dumps(cases));driver=root/'check.mjs'
    driver.write_text("import fs from 'node:fs';import assert from 'node:assert/strict';const m=await import(process.argv[2]);for(const c of JSON.parse(fs.readFileSync(process.argv[3]))){const before=structuredClone(c.args);assert.deepStrictEqual(m.call('run',c.args),c.expected);assert.deepStrictEqual(c.args,before);}console.log('24 independent boundary inputs passed');")
    subprocess.run(['node',str(driver),output.as_uri(),str(casefile)],check=True)
    print('no evidence retains baseline; measured acquisition selects checked slice/map plan; input/output boundaries passed')
