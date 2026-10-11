"""Real checker integration; synthetic costs exercise policy, never speed claims."""
import hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import Store
from ink_planner.representation_selection import phase_workload

compiler,source,fixture=map(Path,sys.argv[1:])
fixture=fixture.resolve(); compiler=compiler.resolve(); source=source.resolve()
with tempfile.TemporaryDirectory(prefix='ink-selection-integration-') as temporary:
    root=Path(temporary); store=Store.publish(root/'knowledge',[]);store.rebuild()
    baseline=fixture/'refresh.json'; alternative=fixture/'delta.json'
    ids={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in [baseline,alternative]}
    request=json.loads(baseline.read_bytes());program=request['input_core_sha256']
    target=dict(hardware='synthetic test',driver='test',toolchain='test',backend='Rust',bridge='native')
    workload=dict(shape={'rows':2},distribution='synthetic policy test',concurrency=1,residency='restored')
    for p,init,query,migrate in [(baseline,1,100,0),(alternative,10000,1,50)]:
        for phase,cost in [('initialise',init),('action:zero',query),('migrate:'+ids[baseline]+':'+hashlib.sha256((fixture/'forward.json').read_bytes()).hexdigest(),migrate)]:
            store.observe(dict(schema=1,program=program,entries=[],plan=ids[p],target=target,workload=phase_workload(workload,phase),metrics=dict(complete_call_ns=[str(cost)],peak_bytes=100)))
    invalid=root/'invalid.json';bad=json.loads(alternative.read_bytes());bad['representation']='0'*64;invalid.write_text(json.dumps(bad))
    store.observe(dict(schema=1,program=program,entries=[],plan=hashlib.sha256(invalid.read_bytes()).hexdigest(),target=target,workload=phase_workload(workload,'action:zero'),metrics=dict(complete_call_ns=['0'],peak_bytes=0)))
    paths={}
    for name,value in [('target',target),('workload',workload),('profile',{'action:zero':100}),('candidates',[{'request':str(invalid)},{'request':str(alternative),'bridge':str(fixture/'forward.json')}])]:
        paths[name]=root/(name+'.json');paths[name].write_text(json.dumps(value))
    output=root/'selected.json'
    def run(*extra):
        command=[sys.executable,'planner/select_representation.py','--compiler',str(compiler),'--source',str(source),'--baseline',str(baseline),'--knowledge-root',str(store.root),'-o',str(output)]
        for name,path in paths.items():command+=['--'+name,str(path)]
        r=subprocess.run(command+list(extra),capture_output=True,text=True);assert r.returncode==0,r.stderr
        receipt=json.loads(r.stdout);assert receipt['request_sha256']==hashlib.sha256(output.read_bytes()).hexdigest()
        checked=subprocess.run([str(compiler),'check-representation',str(source),str(output)],capture_output=True,text=True);assert checked.returncode==0,checked.stderr
        return receipt
    selected=run('--migration-count','1');assert output.read_bytes()==alternative.read_bytes();assert len(selected['rejected'])==1
    assert Path(selected['bridge']).read_bytes()==(fixture/'forward.json').read_bytes()
    paths['profile'].write_text(json.dumps({'initialise':1,'action:zero':1}));run();assert output.read_bytes()==baseline.read_bytes()
    paths['profile'].write_text(json.dumps({'action:zero':100}));run('--max-candidates','0');assert output.read_bytes()==baseline.read_bytes()
    target['driver']='unmeasured';paths['target'].write_text(json.dumps(target));run();assert output.read_bytes()==baseline.read_bytes()
    target['driver']='test';paths['target'].write_text(json.dumps(target))
    paths['candidates'].write_text(json.dumps([{'request':str(alternative),'bridge':str(invalid)}]));selected=run('--migration-count','1');assert len(selected['rejected'])==1;assert output.read_bytes()==baseline.read_bytes()
print('real checker selection: invalid candidate/bridge rejected, lifecycle/migration charged, no evidence/budget retains baseline')
