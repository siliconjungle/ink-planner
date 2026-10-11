"""Actual database/checker replay with synthetic policy costs, not speed claims."""
import copy, hashlib, json, subprocess, sys, tempfile
from pathlib import Path
from ink_knowledge import Store, canonical
from ink_knowledge.implementation_candidates import identity_selection, publish_candidates
from ink_planner.implementation_selection import measurement_id, TARGETS, emission_args
from ink_planner.representation_selection import phase_workload

compiler, source, fixture = (Path(p).resolve() for p in sys.argv[1:])
request = json.loads((fixture/'delta.json').read_bytes())
program = request['input_core_sha256']
compiler_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
with tempfile.TemporaryDirectory(prefix='ink-db-implementation-integration-') as temporary:
    root = Path(temporary)
    bad = copy.deepcopy(request); bad['representation'] = '0'*64
    store, (baseline, alternative, invalid) = publish_candidates(root/'knowledge',
        [identity_selection(program), request, bad])
    publication_root = fixture.parent/'implementation-candidates'
    publication = json.loads((publication_root/'publication.json').read_bytes())
    published = Store(publication_root)
    assert publication['snapshot'] == published.snapshot_id
    assert publication['baseline'] == baseline and publication['candidates'] == [alternative]
    assert published.bundle([baseline,alternative])['objects'][alternative] == store.object(alternative)
    store.rebuild()
    target = dict(hardware='synthetic test', driver='test', toolchain='test', backend='rust', bridge='native')
    workload = dict(shape={'rows':2}, distribution='synthetic policy test', concurrency=1, residency='restored')
    ids = {candidate: measurement_id(candidate, compiler_hash, 'rust') for candidate in [baseline, alternative, invalid]}
    for candidate, init, query, update in [(baseline,10,100,1), (alternative,10000,1,100), (invalid,0,0,0)]:
        for phase, cost in [('initialise',init), ('action:zero',query), ('action:update',update),
                            ('migrate:'+ids[baseline]+':portable:'+ids[alternative],200)]:
            store.observe(dict(schema=1, program=program, entries=[candidate], plan=ids[candidate], target=target,
                workload=phase_workload(workload,phase), metrics=dict(complete_call_ns=[str(cost)], peak_bytes=100)))
    paths = {}
    for name, value in [('target',target), ('workload',workload), ('profile',{'action:zero':1}),
                        ('candidates',[invalid,alternative])]:
        paths[name] = root/(name+'.json'); paths[name].write_text(json.dumps(value))
    output = root/'selected.json'
    common = [sys.executable,'planner/select_implementation.py','--compiler',str(compiler),
              '--source',str(source),'--knowledge-root',str(store.root),'--emit-target','rust']
    def select(profile, *extra):
        paths['profile'].write_text(json.dumps(profile))
        command = common+['--baseline',baseline,'-o',str(output)]
        for name, path in paths.items(): command += ['--'+name,str(path)]
        r = subprocess.run(command+list(extra), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        receipt = json.loads(r.stdout)
        assert receipt['baseline_measurement'] == measurement_id(baseline,compiler_hash,common[-1])
        assert len(receipt['rejected']) == (0 if '--max-candidates' in extra and extra[extra.index('--max-candidates')+1] == '0' else 1)
        assert json.loads(output.read_bytes()) == store.object(receipt['selected'])['payload']['package']
        return receipt
    def replay(name='generated', expect=True, lane='rust'):
        destination = root/name
        command = [*common[:-1],lane,'--replay',str(output),'-o',str(destination)]
        r = subprocess.run(command, capture_output=True, text=True)
        if expect:
            assert r.returncode == 0, r.stderr
            return destination.read_text()
        assert r.returncode != 0 and not destination.exists(), r.stdout
    assert select({'initialise':1,'action:zero':1})['selected'] == baseline
    code = replay(); assert 'BTreeMap' in code and 'CheckedModule' not in code
    assert select({'action:zero':1,'action:update':100})['selected'] == baseline
    assert select({'action:zero':100,'action:update':1})['selected'] == alternative
    replay('representation.rs')
    assert select({'action:zero':1},'--migration-count','1')['selected'] == baseline
    assert select({'action:zero':100},'--migration-count','1')['selected'] == alternative
    assert select({'action:zero':100},'--max-candidates','0')['selected'] == baseline
    # Baseline is supplied database data, not a privileged source implementation.
    swapped = common+['--baseline',alternative,'-o',str(output)]
    paths['candidates'].write_text(json.dumps([baseline])); paths['profile'].write_text(json.dumps({'action:update':100}))
    for name,path in paths.items(): swapped += ['--'+name,str(path)]
    r = subprocess.run(swapped,capture_output=True,text=True); assert r.returncode == 0,r.stderr
    assert json.loads(r.stdout)['selected'] == baseline
    paths['candidates'].write_text(json.dumps([invalid,alternative])); select({'action:zero':100})
    receipt_file = Path(str(output)+'.selection.json'); original = json.loads(receipt_file.read_text())
    for field in ['source','compiler_sha256','emission_target','snapshot','selected','package_sha256']:
        changed = dict(original); changed[field] = '0'*64
        receipt_file.write_text(json.dumps(changed)); replay('rejected-'+field,False)
    receipt_file.write_text(json.dumps(original)); replay('rejected-lane',False,'javascript')
    output.write_bytes(canonical(bad)+b'\n')
    forged = dict(original, selected=invalid, package_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    receipt_file.write_text(json.dumps(forged)); replay('rejected-proof',False)
    output.write_bytes(canonical(request)+b'\n'); receipt_file.write_text(json.dumps(original))
    # Updating the database pointer/index preserves exact pinned replay.
    previous_snapshot = store.snapshot_id
    store, _ = publish_candidates(store.root,[identity_selection('1'*64)])
    assert store.snapshot_id != previous_snapshot; store.rebuild()
    replay('pinned.rs')
    target['driver'] = 'unmeasured'; paths['target'].write_text(json.dumps(target))
    assert select({'action:zero':100})['selected'] == baseline
    # All lanes use the same database package protocols and distinct evidence.
    measured = set()
    for lane in TARGETS:
        target['backend'] = lane; paths['target'].write_text(json.dumps(target))
        common[-1] = lane
        r = select({'action:zero':100}); assert r['selected'] == baseline
        measured.add(r['measurement'])
        for package in [identity_selection(program), request]:
            args = emission_args(package['semantics'],source,output,lane,root/'lane-output')
            assert args[0] in ('emit-rust','emit-c-project','build','emit-representation')
    assert len(measured) == len(TARGETS)
print('database candidates: proofs rechecked; no privileged baseline; lifecycle, frequency, migration and all-lane evidence isolation; exact pinned replay and corruption rejection')
