"""Offline state lifecycle acquisition with an independent source oracle.

Every measured history starts from the same portable source snapshot. Setup,
checkpoint and restore have explicit phases. Candidate state is isolated; fresh processes interleave the admitted candidates.
RSS conservatively includes all candidates and the adapter, never a heap guess.
"""
import hashlib,json,re,subprocess,time
from .measurement_context import execution_context,node_command
from pathlib import Path
from ink_knowledge import canonical,Store
from .representation_selection import phase_workload
from .offline_measurements import artifact_digest
RUNNER=Path(__file__).with_name('measure_state.mjs')
from .value_transfer import transfer

def read_workload(path):
    raw=path.read_bytes()
    if len(raw)>16_000_000:raise ValueError('state workload byte budget')
    v=json.loads(raw)
    if set(v)!={'schema','setup','phases','profile','workload'} or v['schema']!=2:raise ValueError('state workload schema')
    if not isinstance(v['setup'],list) or len(v['setup'])>4096:raise ValueError('state setup bound')
    if not isinstance(v['phases'],dict)or not 1<=len(v['phases'])<=64 or set(v['phases'])!=set(v['profile']):raise ValueError('state phases/profile mismatch')
    def calls(rows):
        if not isinstance(rows,list)or len(rows)>512:raise ValueError('state history bound')
        for c in rows:
            if not isinstance(c,dict)or set(c)!={'call','args'}or not isinstance(c['call'],str)or not isinstance(c['args'],list):raise ValueError('state history call')
    for c in v['setup']:calls([c])
    total=0
    for phase,body in v['phases'].items():
        if not isinstance(phase,str)or not re.fullmatch(r'[A-Za-z0-9:_\-.]{1,128}',phase):raise ValueError('state phase name')
        if type(v['profile'][phase])is not int or not 0<=v['profile'][phase]<=1_000_000_000:raise ValueError('state lifecycle weight')
        if not isinstance(body,dict)or body.get('kind')not in {'module_load','initialise','history','checkpoint','restore'}:raise ValueError('state phase kind')
        if body['kind']=='history':
            if set(body)!={'kind','calls'}or not body['calls']:raise ValueError('state history phase')
            calls(body['calls']);total+=len(body['calls'])
        elif set(body)!={'kind'}:raise ValueError('state lifecycle phase fields')
    if total>512 or not any(v['profile'].values()):raise ValueError('state history/objective budget')
    return v,hashlib.sha256(raw).hexdigest()

def fingerprints(spec):
    # Complete histories distinguish a held-out transition sequence. Parameterless
    # reads and shared initialisation can legitimately recur in both workloads.
    return {hashlib.sha256(canonical(dict(setup=spec['setup'],calls=p['calls']))).hexdigest()
            for p in spec['phases'].values()if p['kind']=='history'}

def acquire(planner,store,packages,states,measurements,path,controls=None):
    if planner.options.target!='javascript':raise ValueError('state lifecycle acquisition currently requires JavaScript')
    if controls:raise ValueError('state lifecycles need explicit histories and lifecycle weights')
    spec,digest=read_workload(path);start=time.monotonic();deadline=start+planner.options.measure_timeout
    def remaining():
        n=deadline-time.monotonic()
        if n<=0:raise ValueError('state lifecycle acquisition deadline')
        return min(n,planner.options.check_timeout)
    from .offline_measurements import target_context
    execution=execution_context(planner.options,state=True)
    target=target_context(planner,state=True,execution=execution)
    workload=dict(spec['workload'])
    if set(workload)!={'shape','distribution','concurrency','residency'}or workload['concurrency']!=1:raise ValueError('serial state workload provenance')
    workload['shape']=dict(inputs_sha256=digest,declared=workload['shape'])
    def oracle(calls,label):
        script=planner.directory/(label+'.history.json');script.write_text(json.dumps(calls,allow_nan=False))
        snapshot=planner.directory/(label+'.snapshot')
        result=planner.invoke('execute',planner.original,script,'--core','--portable','--snapshot-out',snapshot,timeout=remaining())
        return transfer(result),snapshot.read_bytes().hex()
    setup_expected,seed=oracle(spec['setup'],'state-seed')
    phases={}
    for name,phase in spec['phases'].items():
        history=phase.get('calls',[]);expected,snapshot=oracle([*spec['setup'],*history],'state-'+name)
        phases[name]=dict(phase,expected=expected,snapshot=snapshot)
        if 'calls'in phase:phases[name]['calls']=transfer(phase['calls'])
    reference_seconds=str(time.monotonic()-start);artifacts=[];rejected=[]
    for package,state,measurement in zip(packages,states,measurements):
        if not state.get('capability',{}).get('emittable',True):
            rejected.append(dict(measurement=measurement,reason='backend capability'));continue
        staged=planner.directory/(measurement+'.plan.json');staged.write_bytes(canonical(package)+b'\n')
        artifact=planner.directory/(measurement+'.state.mjs');begin=time.monotonic()
        try:
            planner.invoke('emit-plan',planner.original,staged,'--core','--target','javascript','-o',artifact,timeout=remaining())
            artifacts.append(dict(measurement=measurement,path=str(artifact),artifact_sha256=artifact_digest(artifact),emission_seconds=str(time.monotonic()-begin)))
        except (ValueError,OSError,subprocess.TimeoutExpired)as e:rejected.append(dict(measurement=measurement,reason=str(e)[-2000:]))
    if not artifacts or artifacts[0]['measurement']!=measurements[0]:raise ValueError('state baseline could not be emitted')
    inspected=subprocess.run(node_command(planner.options,RUNNER,'--describe',artifacts[0]['path'],describe=True),capture_output=True,text=True,timeout=remaining())
    if inspected.returncode:raise ValueError('state source schema: '+inspected.stderr[-2000:])
    metadata=json.loads(inspected.stdout);processes=getattr(planner.options,'measure_processes',3)
    if type(processes)is not int or not 1<=processes<=8:raise ValueError('state process bound')
    reports=[];accepted={a['measurement']for a in artifacts};module_load=[]
    request=planner.directory/'paired-state-measure.json'
    packet=dict(artifacts=artifacts,metadata=metadata,setup=transfer(spec['setup']),setup_expected=setup_expected,seed=seed,phases=phases)
    for process in range(processes):
        request.write_text(json.dumps(dict(packet,process_index=process),allow_nan=False))
        r=subprocess.run(node_command(planner.options,RUNNER,request,state=True),capture_output=True,text=True,timeout=remaining())
        if r.returncode:raise ValueError('paired state acquisition: '+r.stderr[-2000:])
        report=json.loads(r.stdout)
        if report.get('pairing')!='interleaved-state-lifecycle-v1':raise ValueError('state pairing receipt missing')
        complete={a['measurement']for a in artifacts if {row['phase']for row in report['rows']if row['measurement']==a['measurement']}==set(spec['phases'])}
        accepted&=complete
        rejected.extend(dict(row,process=process)for row in report['rejected'])
        reports.extend(dict(row,process=process)for row in report['rows'])
        module_load.extend(dict(row,process=process,peak_rss_bytes=report['peak_rss_bytes'])for row in report['module_load'])
    reports=[row for row in reports if row['measurement']in accepted]
    if measurements[0]not in accepted:raise ValueError('state baseline failed validation')
    from .compiled_planning import code_fingerprint
    if hashlib.sha256(planner.compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash or target_context(planner,state=True)!=target:raise ValueError('state execution machinery changed during acquisition')
    observations=[]
    for row in reports:
        obj=dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=row['measurement'],target=target,workload=phase_workload(workload,row['phase']),
                 metrics=dict(complete_call_ns=row['complete_call_ns'],peak_bytes=row['peak_rss_bytes']))
        Store.validate_observation(obj);observations.append(store.observe(obj))
    report=dict(schema=3,pairing='interleaved-state-lifecycle-v1',processes=processes,rows=reports,rejected=rejected,module_load=module_load,input_fingerprints=sorted(fingerprints(spec)))
    return target,workload,spec['profile'],dict(schema=2,adapter=target['bridge'],execution_context=execution,workload_sha256=digest,reference_seconds=reference_seconds,acquisition_seconds=str(time.monotonic()-start),observations=observations,
        validated_measurements=sorted(accepted),artifacts=artifacts,native_build=None,rejected=rejected,acquisition=report,
        scope='Offline independent state histories, exact outcomes/events/versions/portable snapshots; interleaved isolated candidate states in fresh processes; shared-process peak RSS bounds all candidates plus adapter; explicit setup/checkpoint/restore phases; no runtime probes')
