"""Offline complete-call observations for admitted pure implementation plans.
JavaScript trusted calls and native Rust JSON calls have distinct boundaries.
Other execution ABIs decline explicitly; observations never substitute for proof.
"""
import hashlib,json,platform,re,shutil,subprocess,time
from pathlib import Path
from ink_knowledge import canonical,identity,Store
from .representation_selection import phase_workload

RUNNER=Path(__file__).with_name('measure_calls.mjs')

def read_workload(path):
    raw=path.read_bytes()
    if len(raw)>16_000_000:raise ValueError('measurement workload byte budget')
    v=json.loads(raw)
    if set(v)!={'schema','phases','profile','workload'} or v['schema']!=1:raise ValueError('measurement workload schema')
    if not isinstance(v['phases'],dict) or not 1<=len(v['phases'])<=64 or set(v['phases'])!=set(v['profile']):raise ValueError('measurement phases/profile mismatch')
    calls=0
    for phase,rows in v['phases'].items():
        if not isinstance(phase,str) or not re.fullmatch(r'[A-Za-z0-9:_\-.]{1,128}',phase):raise ValueError('measurement phase name')
        n=v['profile'][phase]
        if type(n) is not int or not 0<n<=1_000_000_000:raise ValueError('positive lifecycle weights required')
        if not isinstance(rows,list) or not rows:raise ValueError('measurement phase needs calls')
        for row in rows:
            if set(row)!={'function','args'} or not isinstance(row['function'],str) or not isinstance(row['args'],list):raise ValueError('measurement call fields')
            calls+=1
    if calls>512:raise ValueError('measurement call budget')
    # All supplied calls are selection/training data. Independent evaluation must
    # use different inputs and is never passed back to this acquisition step.
    return v,hashlib.sha256(raw).hexdigest()

def target_context(planner):
    if planner.options.target=='javascript':
        executable=shutil.which('node')
        if not executable:raise ValueError('offline measurement requires Node.js')
        version=subprocess.check_output([executable,'--version'],text=True).strip();runner=RUNNER
        bridge='trusted-values-v3;mean-complete-call;full-bit-aware-output-consumption'
    elif planner.options.target=='rust':
        from .native_measurements import RUNNER as runner
        executable=shutil.which('rustc')
        if not executable:raise ValueError('offline measurement requires Rust')
        version=subprocess.check_output([executable,'--version'],text=True).strip()
        bridge='generated-json-call-v1;mean-complete-call;full-bit-aware-output-consumption'
    else:raise ValueError('no installed offline measurement adapter for '+planner.options.target)
    return dict(hardware=platform.machine()+':'+platform.processor()+':'+platform.node(),driver=platform.system()+':'+platform.release(),toolchain='compiler:'+planner.compiler_hash+';host:'+version+';adapter:'+hashlib.sha256(runner.read_bytes()).hexdigest(),backend=planner.options.target,bridge=bridge)

def artifact_digest(path):
    if path.is_file():return hashlib.sha256(path.read_bytes()).hexdigest()
    return identity({str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest()for p in sorted(path.rglob('*'))if p.is_file()})

def native_project(source,directory,package):
    """Wrap literal emitted Rust without changing any generated source bytes."""
    (directory/'src').mkdir(parents=True)
    shutil.copyfile(source,directory/'src/lib.rs')
    (directory/'Cargo.toml').write_text('[package]\nname='+json.dumps(package)+'\nversion="0.1.0"\nedition="2021"\n[lib]\ncrate-type=["rlib"]\n[workspace]\n[dependencies]\nnum-bigint="=0.4.8"\nsha2="=0.10.9"\nserde_json={version="=1.0.151",features=["float_roundtrip"]}\n')
    return directory

def acquire(planner,store,packages,states,measurements,path,controls=None):
    acquisition_started=time.monotonic();deadline=acquisition_started+planner.options.measure_timeout
    def remaining():
        seconds=deadline-time.monotonic()
        if seconds<=0:raise ValueError('offline measurement acquisition time budget')
        return min(seconds,planner.options.check_timeout)
    spec,digest=read_workload(path)
    target=target_context(planner);controls=controls or {}
    workload=dict(spec['workload'])
    if set(workload)!={'shape','distribution','concurrency','residency'}:raise ValueError('measurement workload provenance fields')
    workload['shape']=dict(inputs_sha256=digest,declared=workload['shape'])
    if workload['concurrency']!=1:raise ValueError('serial call adapter requires concurrency=1')
    Store.validate_observation(dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=measurements[0],target=target,workload=workload,metrics=dict(complete_call_ns=['0'],peak_bytes=None)))
    phases={};oracle_started=time.monotonic()
    # Reference execution is independently implemented and outside timing.
    for phase,rows in spec['phases'].items():
        phases[phase]=[]
        for i,row in enumerate(rows):
            args=planner.directory/'reference-args.json';args.write_text(json.dumps(row['args'],allow_nan=False))
            expected=planner.invoke('run',planner.original,row['function'],args,'--core',timeout=remaining())
            if isinstance(expected,str):expected=json.loads(expected)
            phases[phase].append(dict(row,expected=expected))
    reference_seconds=str(time.monotonic()-oracle_started)
    artifacts=[];rejected=[]
    for package,state,measurement in zip(packages,states,measurements):
        if not state.get('capability',{}).get('emittable',True):
            rejected.append(dict(measurement=measurement,reason='backend capability'));continue
        staged=planner.directory/(measurement+'.plan.json');staged.write_bytes(canonical(package)+b'\n')
        artifact=planner.directory/(measurement+('.mjs'if planner.options.target=='javascript'else'.rust'));begin=time.monotonic()
        try:
            planner.invoke('emit-plan',planner.original,staged,'--core','--target',planner.options.target,'-o',artifact,timeout=remaining())
            package_name='ink-observed-'+measurement[:24]
            if planner.options.target=='rust':artifact=native_project(artifact,planner.directory/(measurement+'.native'),package_name)
            artifacts.append(dict(measurement=measurement,path=str(artifact),package=package_name,artifact_sha256=artifact_digest(artifact),emission_seconds=str(time.monotonic()-begin)))
        except (ValueError,OSError,subprocess.TimeoutExpired)as error:rejected.append(dict(measurement=measurement,reason=str(error)[-2000:]))
    if not artifacts or artifacts[0]['measurement']!=measurements[0]:raise ValueError('baseline could not be measured')
    native_build=None
    if planner.options.target=='javascript':
        request=planner.directory/'measure-calls.json';request.write_text(json.dumps(dict(artifacts=artifacts,phases=phases,measurement=controls),allow_nan=False))
        result=subprocess.run([shutil.which('node'),'--max-old-space-size='+str(max(32,min(4096,getattr(planner.options,'memory_bytes',512000000)//1000000))),str(RUNNER),str(request)],capture_output=True,text=True,timeout=remaining())
        if result.returncode:raise ValueError('offline measurement failed: '+result.stderr[-2000:])
        report=json.loads(result.stdout)
    else:
        from .native_measurements import measure
        report,native_build=measure(planner,artifacts,phases,controls,deadline)
    # Do not persist observations under a fingerprint that changed during the
    # acquisition, even when final compiler replay would reject the build.
    from .compiled_planning import code_fingerprint
    if hashlib.sha256(planner.compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash or target_context(planner)!=target:
        raise ValueError('execution machinery changed during offline acquisition')
    observations=[]
    accepted=set()
    for row in report['rows']:
        if row['measurement'] not in {a['measurement']for a in artifacts} or row['phase'] not in spec['phases']:raise ValueError('measurement response binding mismatch')
        Store.validate_observation(dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=row['measurement'],target=target,workload=phase_workload(workload,row['phase']),metrics=dict(complete_call_ns=row['complete_call_ns'],peak_bytes=None)))
        obj=dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=row['measurement'],target=target,workload=phase_workload(workload,row['phase']),metrics=dict(complete_call_ns=row['complete_call_ns'],peak_bytes=None))
        observations.append(store.observe(obj));accepted.add(row['measurement'])
    if measurements[0] not in accepted:raise ValueError('baseline failed measurement validation')
    return target,workload,spec['profile'],dict(schema=1,adapter=target['bridge'],workload_sha256=digest,reference_seconds=reference_seconds,acquisition_seconds=str(time.monotonic()-acquisition_started),observations=observations,artifacts=artifacts,native_build=native_build,rejected=rejected+report['rejected'],acquisition=report,scope='Offline selection inputs; average whole call and full result consumption; separate setup; no exact peak memory evidence; no runtime probes')
