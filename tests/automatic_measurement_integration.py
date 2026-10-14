"""Normal compilation creates its own typed selection inputs and proof packets.
Development integration only; performance generalisation uses the reserved corpus.
"""
import json,os,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import Store,semantic_entry
from ink_knowledge.store import entry
from ink_knowledge.recipes import load
from producers.list_slice_laws import named_laws

compiler=Path(sys.argv[1]).resolve();repo=Path(__file__).resolve().parents[2]
target=sys.argv[2]if len(sys.argv)>2 else'javascript'
with tempfile.TemporaryDirectory(prefix='ink-automatic-measurement-integration-')as tmp:
    root=Path(tmp);source=root/'example.ink'
    source.write_text('module example;pub fn run(xs:List<u32>,n:u32)->List<u32>{return xs.map(fn(x)=>(x*13)+7).take(n);}\n')
    objects=[semantic_entry(v)for _,v in named_laws()]
    objects.append(entry('definition','ink-semantic-producers-v1',dict(module='producers.list_slice_laws')))
    store=Store.publish(root/'knowledge',objects);store.refresh_index()
    registry,_=load();registry['recipes']=[r for r in registry['recipes']if r['id']=='semantic-laws']
    reg=root/'recipes.json';reg.write_text(json.dumps(registry));plan=root/'plan.json'
    output=root/('selected.mjs'if target=='javascript'else'selected')
    command=[sys.executable,'-m','ink_planner.compiled_planning','--compiler',str(compiler),'--source',str(source),
             '--knowledge-root',str(store.root),'--registry',str(reg),'--target',target,'--planning-effort','quick',
             '--budget','128','--state-limit','16','--time-seconds','15','--semantic-budget','128','--semantic-seconds','10',
             '--measure-timeout','120','--emit',str(output),'-o',str(plan)]
    environment=dict(os.environ,INK_DISTRIBUTION=str(repo),PYTHONPATH=os.pathsep.join([str(repo/'knowledge'),str(repo/'planner')]))
    result=subprocess.run(command,env=environment,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    packet=json.loads(plan.read_text());receipt=json.loads(Path(str(plan)+'.receipt.json').read_text())
    assert receipt['automatic_probe']['calls'] and receipt['measurement_acquisition'],receipt
    shortlist=receipt['measurement_acquisition']['shortlist']
    from ink_knowledge.selection_probes import policy
    acquisition=receipt['measurement_acquisition']['acquisition']
    assert acquisition['processes']==policy()['processes']==3,acquisition
    assert {row['process']for row in acquisition['rows']}=={0,1,2},acquisition
    assert all(e['processes']==3 for e in receipt['selection']['uncertainty_guard']['estimates'].values())
    guard=receipt['selection']['uncertainty_guard']
    chosen=receipt['selection']['selected_measurement']
    if chosen in guard['estimates']:
        estimate=guard['estimates'][chosen]
        assert not estimate['regressive_phases'] and not estimate['uncertain_phases'],estimate
        tolerance=1+policy()['maximum_phase_regression_percent']/100
        assert all(float(p['upper_bound'])<=tolerance for p in estimate['phases'].values()),estimate

    assert shortlist['allocation']=='first admitted recipe ancestry before repeated family variants',shortlist
    assert set(shortlist['measured_families'])<=set(shortlist['admitted_families']),shortlist
    assert 'semantic-laws' in shortlist['measured_families'],shortlist
    if target=='javascript':
        # Profitability is workload/host dependent. Acquisition must compare
        # admitted alternatives; an uncertainty-guarded baseline is valid.
        assert len(receipt['measurement_acquisition']['validated_measurements'])>=2,receipt
        if not packet['steps']:
            assert receipt['selection']['reason']=='baseline retained; no robust measured lifecycle improvement',receipt['selection']
    subprocess.run([str(compiler),'check-plan',str(source),str(plan),'--target',target],check=True,capture_output=True)
    result=subprocess.run(command,env=environment,text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    warm=json.loads(Path(str(plan)+'.receipt.json').read_text())
    assert warm['cache']['hit'] and json.loads(plan.read_text())==packet,warm
    if target=='javascript':
        assert warm['diagnostics']['fresh_work']==dict(search=False,measurements=False,selected_replay_reported=True)
        # Keep the source/key bindings but corrupt the proof-bearing packet.
        # Cache membership must not turn malformed proof data into authority.
        for file in (store.root/'compiled-cache').glob('*.json'):
            cached=json.loads(file.read_text())
            cached['package']['steps'].append(dict(kind='rewrite',package={}))
            file.write_text(json.dumps(cached))
        result=subprocess.run(command,env=environment,text=True,capture_output=True)
        assert result.returncode==0,result.stderr
        recovered=json.loads(Path(str(plan)+'.receipt.json').read_text())
        assert any(n.get('recipe')=='compiled-cache' and n.get('status')=='declined' for n in recovered['recipes']),recovered
        subprocess.run([str(compiler),'check-plan',str(source),str(plan),'--target',target],check=True,capture_output=True)
        cases=[]
        for xs in [[],[0,2,4294967295],[19,1,5,3,7],list(range(193))]:
            for n in [0,1,4,len(xs),len(xs)+1,4294967295]:
                args=root/'args.json';args.write_text(json.dumps([xs,n]))
                expected=json.loads(subprocess.check_output([str(compiler),'run',str(source),'run',str(args)],text=True))
                cases.append(dict(args=[xs,n],expected=expected))
        cases_file=root/'held-out.json';cases_file.write_text(json.dumps(cases));driver=root/'check.mjs'
        driver.write_text("import fs from 'node:fs';import assert from 'node:assert/strict';const m=await import(process.argv[2]);for(const c of JSON.parse(fs.readFileSync(process.argv[3]))){const before=structuredClone(c.args);assert.deepStrictEqual(m.call('run',c.args),c.expected);assert.deepStrictEqual(c.args,before);}console.log('24 independent boundary inputs passed');")
        subprocess.run(['node',str(driver),output.as_uri(),str(cases_file)],check=True)
    print(target,'automatic typed probes acquired; complete source chain replayed; warm cache freshly replayed',
          'selected steps',len(packet['steps']))
