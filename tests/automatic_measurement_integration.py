"""Normal compilation creates its own typed selection inputs and proof packets.
Development integration only; performance generalisation uses the reserved corpus.
"""
import hashlib,json,os,subprocess,sys,tempfile
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
    if target=='rust':
        source.write_text(source.read_text()+'pub fn floating(x:f32)->f32{return x;}\n')
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
    peaks=[row['peak_rss_bytes']for row in acquisition['rows']]
    assert all(type(peak)is int and peak>0 for peak in peaks),acquisition
    assert 'whole-host-peak-rss' in receipt['measurement_acquisition']['adapter']
    from ink_planner.representation_selection import rank
    observations=[json.loads((store.data/'observations'/(ident+'.json')).read_text())
                  for ident in receipt['measurement_acquisition']['observations']]
    first=observations[0];workload=dict(first['workload'],distribution=first['workload']['distribution']['workload'])
    counts={o['workload']['distribution']['phase']:1 for o in observations}
    baseline=receipt['measurement_acquisition']['artifacts'][0]['measurement']
    memory_args=(store,packet['input_core_sha256'],[baseline],first['target'],workload,counts,baseline)
    try:rank(*memory_args,memory_limit=min(peaks)-1)
    except ValueError as error:assert 'memory constraint'in str(error),error
    else:raise AssertionError('native/JavaScript observations must enforce the memory limit')
    assert rank(*memory_args,memory_limit=max(peaks))[0]==baseline
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
    if target=='rust':
        # Real explicit native acquisition must retain typed fingerprints and
        # finish its independent holdout path, even when baseline is retained.
        def workload(path,xs,n,scalar):
            spec=dict(schema=1,phases={'call:run':[dict(function='run',args=[xs,n])],
                'call:floating':[dict(function='floating',args=[scalar])]},
                profile={'call:run':1,'call:floating':1},
                workload=dict(shape={'fixture':'typed holdout'},distribution='controls',concurrency=1,residency='complete call'))
            path.write_text(json.dumps(spec))
        training,holdout,bad=root/'training.json',root/'holdout.json',root/'alias.json'
        workload(training,[7,19,43,91,83],3,1.1)
        workload(holdout,[11,23,47,97],2,1.2)
        workload(bad,[11,23,47,97],2,1.10000001)
        from ink_planner.offline_measurements import independent_workloads
        independent_workloads(training,bad)  # Raw JSON alone cannot find this alias.
        def explicit(workload_path,label):
            cmd=list(command);destination=root/(label+'.plan.json');emitted=root/(label+'.rs')
            cmd[cmd.index('-o')+1]=str(destination);cmd[cmd.index('--emit')+1]=str(emitted)
            cmd+=['--measure-workload',str(training),'--measure-holdout',str(workload_path),'--cache','off','--measure-processes','3']
            result=subprocess.run(cmd,env=environment,text=True,capture_output=True,timeout=240)
            return result,destination,emitted
        valid,valid_plan,_=explicit(holdout,'independent')
        assert valid.returncode==0,valid.stderr
        observed=json.loads(Path(str(valid_plan)+'.receipt.json').read_text())
        selected=observed['measurement_acquisition']['acquisition'];held=observed['selection']['holdout']['acquisition']['acquisition']
        assert len(selected['input_fingerprints'])==len(held['input_fingerprints'])==2
        assert not set(selected['input_fingerprints'])&set(held['input_fingerprints'])
        assert {row['process']for row in selected['rows']}=={0,1,2}
        assert {row['process']for row in held['rows']}=={0,1,2}
        rejected,bad_plan,bad_output=explicit(bad,'overlap')
        assert rejected.returncode!=0
        assert 'holdout normalized typed inputs overlap selection inputs'in rejected.stderr,rejected.stderr
        assert not bad_plan.exists()and not bad_output.exists()
        invalid_digest=hashlib.sha256(bad.read_bytes()).hexdigest()
        assert all(json.loads(path.read_text())['workload']['shape']['inputs_sha256']!=invalid_digest
                   for path in (store.data/'observations').glob('*.json'))
        print('native independent holdout completed; rounded-float alias rejected before held-out cost publication')
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
