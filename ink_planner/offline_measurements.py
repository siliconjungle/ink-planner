"""Offline complete-call observations for admitted pure implementation plans.
JavaScript trusted calls and native Rust JSON calls have distinct boundaries.
Other execution ABIs decline explicitly; observations never substitute for proof.
"""
import hashlib,json,platform,re,shutil,subprocess,tempfile,time
from pathlib import Path
from ink_knowledge import canonical,identity,Store
from .representation_selection import phase_workload

RUNNER=Path(__file__).with_name('measure_calls.mjs')

def has_refinement(package):
    """Discovery hint only: the compiler rechecks the complete plan below."""
    if isinstance(package,dict):
        return package.get('contract_refinement') is not None or any(has_refinement(v) for v in package.values())
    return isinstance(package,list) and any(has_refinement(v) for v in package)

def refinement_phases(planner,package,state,staged,phases,timeout):
    """An inexact refinement needs its own independently interpreted oracle.

    Baseline equality would reject valid approximate/loose specifications. Never
    derive the expected result from emitted code or from candidate metadata.
    """
    calls=[dict(function=row['function'],args=row['args'])for rows in phases.values()for row in rows]
    if not 1<=len(calls)<=512:raise ValueError('refinement reference requires 1..512 calls')
    request=planner.directory/'refinement-reference-calls.json';request.write_text(json.dumps(calls,allow_nan=False))
    result=planner.invoke('plan-work',planner.original,staged,request,'--core',timeout=timeout)
    if not isinstance(result,dict) or result.get('status')!='checked' or result.get('semantics')!='ink-reference-work-v1' or result.get('input')!=package['input_core_sha256'] or result.get('plan_sha256')!=identity(package) or result.get('selected')!=state.get('source') or len(result.get('values',[]))!=len(calls):
        raise ValueError('refinement reference binding mismatch')
    values=iter(result['values'])
    return {phase:[dict(row,expected=next(values))for row in rows]for phase,rows in phases.items()}

def validated(measurements,acquisition):
    """Historical costs cannot resurrect a freshly rejected implementation."""
    accepted=set(acquisition['validated_measurements'])
    return [m for m in measurements if m in accepted]

def read_workload(path):
    raw=path.read_bytes()
    if len(raw)>16_000_000:raise ValueError('measurement workload byte budget')
    v=json.loads(raw)
    if not isinstance(v,dict):raise ValueError('measurement workload object')
    if v.get('schema')==2:
        from .state_measurements import read_workload as read_state
        return read_state(path)
    if set(v)!={'schema','phases','profile','workload'} or v['schema']not in (1,3):raise ValueError('measurement workload schema')
    if not isinstance(v['phases'],dict) or not 1<=len(v['phases'])<=64 or set(v['phases'])!=set(v['profile']):raise ValueError('measurement phases/profile mismatch')
    calls=0
    for phase,rows in v['phases'].items():
        if not isinstance(phase,str) or not re.fullmatch(r'[A-Za-z0-9:_\-.]{1,128}',phase):raise ValueError('measurement phase name')
        n=v['profile'][phase]
        if type(n) is not int or not 0<n<=1_000_000_000:raise ValueError('positive lifecycle weights required')
        if not isinstance(rows,list) or not rows:raise ValueError('measurement phase needs calls')
        for row in rows:
            fields={'function','args'}|({'boundary'}if v['schema']==3 else set())
            if set(row)!=fields or not isinstance(row['function'],str) or not isinstance(row['args'],list):raise ValueError('measurement call fields')
            if v['schema']==3 and row['boundary']not in ('trusted','portable','compact'):raise ValueError('measurement call boundary')
            calls+=1
    if calls>512:raise ValueError('measurement call budget')
    # All supplied calls are selection/training data. Independent evaluation must
    # use different inputs and is never passed back to this acquisition step.
    return v,hashlib.sha256(raw).hexdigest()

def independent_workloads(training,holdout):
    first,_=read_workload(training);second,_=read_workload(holdout)
    if first['schema']!=second['schema']:raise ValueError('holdout workload kind mismatch')
    if first['profile']!=second['profile']:raise ValueError('holdout must retain the lifecycle objective weights')
    if first['schema']==2:
        from .state_measurements import fingerprints
        if fingerprints(first)&fingerprints(second):raise ValueError('holdout state histories overlap selection histories')
        return
    def calls(spec):
        return {json.dumps([row['function'],row['args']],sort_keys=True,separators=(',',':'),allow_nan=False)
                for rows in spec['phases'].values()for row in rows}
    if calls(first)&calls(second):raise ValueError('holdout inputs overlap selection inputs')

def target_context(planner,state=False,encoded=False):
    if planner.options.target=='javascript':
        executable=shutil.which('node')
        if not executable:raise ValueError('offline measurement requires Node.js')
        version=subprocess.check_output([executable,'--version'],text=True).strip();runner=RUNNER
        bridge='trusted-values-v3;mean-complete-call;full-bit-aware-output-consumption'
        if encoded:bridge='schema-framed-owned-result-v1;whole-call-and-complete-byte-consumption'
        bridge+=';whole-host-peak-rss'
        if state:
            from .state_measurements import RUNNER as runner
            bridge='typed-bound-full-observation-v1;independent-state-history;portable-lifecycle;whole-host-peak-rss'
    elif planner.options.target=='rust':
        from .native_measurements import RUNNER as runner
        executable=shutil.which('rustc')
        if not executable:raise ValueError('offline measurement requires Rust')
        version=subprocess.check_output([executable,'--version'],text=True).strip()
        bridge='generated-json-call-v3;reference-typed-inputs-v1;sealed-request-v1;mean-complete-call;full-bit-aware-output-consumption;whole-host-peak-rss-wait4-v1'
    else:raise ValueError('no installed offline measurement adapter for '+planner.options.target)
    return dict(hardware=platform.machine()+':'+platform.processor()+':'+platform.node(),driver=platform.system()+':'+platform.release(),toolchain='compiler:'+planner.compiler_hash+';host:'+version+';adapter:'+hashlib.sha256(runner.read_bytes()).hexdigest(),backend=planner.options.target,bridge=bridge)

def artifact_digest(path):
    if path.is_file():return hashlib.sha256(path.read_bytes()).hexdigest()
    return identity({str(p.relative_to(path)):hashlib.sha256(p.read_bytes()).hexdigest()for p in sorted(path.rglob('*'))if p.is_file()})

def reference_receipt(receipt,source,function):
    """Bind an input identity to the reference decoder, not raw JSON spelling."""
    fields={'schema','semantics','input_core_sha256','selected_core_sha256','function','input_sha256','value'}
    if not isinstance(receipt,dict)or set(receipt)!=fields or type(receipt['schema'])is not int or receipt['schema']!=1 or receipt['semantics']!='ink-reference-call-v1':
        raise ValueError('invalid reference typed-input receipt')
    if receipt['input_core_sha256']!=source or receipt['selected_core_sha256']!=source or receipt['function']!=function:
        raise ValueError('reference typed-input receipt source/function mismatch')
    fingerprint=receipt['input_sha256']
    if not isinstance(fingerprint,str)or not re.fullmatch('[0-9a-f]{64}',fingerprint):raise ValueError('invalid reference input fingerprint')
    return receipt['value'],fingerprint

def native_project(source,directory,package):
    """Wrap literal emitted Rust without changing any generated source bytes."""
    (directory/'src').mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source,directory/'src/lib.rs')
    (directory/'Cargo.toml').write_text('[package]\nname='+json.dumps(package)+'\nversion="0.1.0"\nedition="2021"\n[lib]\ncrate-type=["rlib"]\n[workspace]\n[dependencies]\nnum-bigint="=0.4.8"\nsha2="=0.10.9"\nserde_json={version="=1.0.151",features=["float_roundtrip"]}\n')
    return directory

def acquire(planner,store,packages,states,measurements,path,controls=None,*,disjoint_inputs=(),automatic_inputs=False):
    acquisition_started=time.monotonic();deadline=acquisition_started+planner.options.measure_timeout
    def remaining():
        seconds=deadline-time.monotonic()
        if seconds<=0:raise ValueError('offline measurement acquisition time budget')
        return min(seconds,planner.options.check_timeout)
    spec,digest=read_workload(path)
    if not isinstance(disjoint_inputs,(list,tuple,set,frozenset))or len(disjoint_inputs)>512:raise ValueError('bounded disjoint input fingerprints required')
    if any(not isinstance(x,str)or not re.fullmatch('[0-9a-f]{64}',x)for x in disjoint_inputs):raise ValueError('invalid disjoint input fingerprints')
    forbidden=set(disjoint_inputs)
    if spec['schema']==2:
        if automatic_inputs:raise ValueError('automatic pure probes do not author state workloads')
        from .state_measurements import acquire as acquire_state,fingerprints
        if forbidden&fingerprints(spec):raise ValueError('holdout state histories overlap selection histories')
        return acquire_state(planner,store,packages,states,measurements,path,controls)
    if spec['schema']==3 and planner.options.target!='javascript':raise ValueError('encoded lifecycle adapter requires JavaScript')
    target=target_context(planner,encoded=spec['schema']==3);controls=controls or {}
    workload=dict(spec['workload'])
    if set(workload)!={'shape','distribution','concurrency','residency'}:raise ValueError('measurement workload provenance fields')
    workload['shape']=dict(inputs_sha256=digest,declared=workload['shape'])
    if workload['concurrency']!=1:raise ValueError('serial call adapter requires concurrency=1')
    Store.validate_observation(dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=measurements[0],target=target,workload=workload,metrics=dict(complete_call_ns=['0'],peak_bytes=None)))
    reference_budget=getattr(planner.options,'reference_budget',100_000_000)
    if type(reference_budget)is not int or not 1<=reference_budget<=10_000_000_000:raise ValueError('reference evaluation budget')
    # Training and rejection-only holdout must not overwrite one another's
    # native libraries, requests, locks or executable project inputs.
    directory=Path(tempfile.mkdtemp(prefix='measurement-',dir=planner.directory))
    oracle_started=time.monotonic()
    def reference(function,arguments):
        args=directory/'reference-args.json';args.write_text(json.dumps(arguments,allow_nan=False))
        extra=['--input-receipt']if planner.options.target=='rust'else []
        expected=planner.invoke('run',planner.original,function,args,'--core','--evaluation-budget',str(reference_budget),*extra,timeout=remaining())
        if isinstance(expected,str):expected=json.loads(expected)
        return reference_receipt(expected,packages[0]['input_core_sha256'],function)if extra else (expected,None)
    from .probe_validation import reference_phases
    phases,input_validation=reference_phases(planner,spec,remaining,automatic=automatic_inputs,reference=reference)
    input_fingerprints={row['input_sha256']for rows in phases.values()for row in rows if 'input_sha256'in row}
    if forbidden&input_fingerprints:raise ValueError('holdout normalized typed inputs overlap selection inputs')
    if automatic_inputs:
        workload['shape'].update(proposed_inputs_sha256=digest,inputs_sha256=input_validation['accepted_inputs_sha256'],input_validation=input_validation['semantics'])
    reference_seconds=str(time.monotonic()-oracle_started)
    artifacts=[];rejected=[]
    for package,state,measurement in zip(packages,states,measurements):
        if not state.get('capability',{}).get('emittable',True):
            rejected.append(dict(measurement=measurement,reason='backend capability'));continue
        staged=directory/(measurement+'.plan.json');staged.write_bytes(canonical(package)+b'\n')
        artifact=directory/(measurement+('.mjs'if planner.options.target=='javascript'else'.rust'));begin=time.monotonic()
        try:
            planner.invoke('emit-plan',planner.original,staged,'--core','--target',planner.options.target,'-o',artifact,timeout=remaining())
            package_name='ink-observed-'+measurement[:24]
            if planner.options.target=='rust':artifact=native_project(artifact,directory/(measurement+'.native'),package_name)
            observed=dict(measurement=measurement,path=str(artifact),package=package_name,artifact_sha256=artifact_digest(artifact),emission_seconds=str(time.monotonic()-begin))
            if has_refinement(package):
                observed['reference_phases']=refinement_phases(planner,package,state,staged,phases,remaining())
            artifacts.append(observed)
        except (ValueError,OSError,subprocess.TimeoutExpired)as error:rejected.append(dict(measurement=measurement,reason=str(error)[-2000:]))
    if not artifacts or artifacts[0]['measurement']!=measurements[0]:raise ValueError('baseline could not be measured: '+json.dumps(rejected)[-4000:])
    native_build=None
    if planner.options.target=='javascript':
        from .value_transfer import transfer
        transport=[dict(a,reference_phases=transfer(a['reference_phases']))if 'reference_phases'in a else a for a in artifacts]
        request=directory/'measure-calls.json';request.write_text(json.dumps(dict(artifacts=transport,phases=transfer(phases),measurement=controls,exact_transfer=True),allow_nan=False))
        processes=controls['processes'] if controls else getattr(planner.options,'measure_processes',3)
        if type(processes) is not int or not 1<=processes<=8:raise ValueError('measurement process count')
        reports=[]
        for process in range(processes):
            payload=json.loads(request.read_text());payload['process_index']=process
            request.write_text(json.dumps(payload,allow_nan=False))
            result=subprocess.run([shutil.which('node'),'--max-old-space-size='+str(max(32,min(4096,getattr(planner.options,'memory_bytes',512000000)//1000000))),str(RUNNER),str(request)],capture_output=True,text=True,timeout=remaining())
            if result.returncode:raise ValueError('offline measurement failed: '+result.stderr[-2000:])
            reports.append(json.loads(result.stdout))
        from .process_measurements import combine
        report=combine(reports,artifacts,fingerprints=True)
    else:
        from .native_measurements import measure
        report,native_build=measure(planner,artifacts,phases,controls,deadline)
        if report['input_fingerprints']!=sorted(input_fingerprints):raise ValueError('native typed-input receipt binding mismatch')
    if forbidden&set(report['input_fingerprints']):raise ValueError('holdout normalized typed inputs overlap selection inputs')
    # Do not persist observations under a fingerprint that changed during the
    # acquisition, even when final compiler replay would reject the build.
    from .compiled_planning import code_fingerprint
    if hashlib.sha256(planner.compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash or target_context(planner,encoded=spec['schema']==3)!=target:
        raise ValueError('execution machinery changed during offline acquisition')
    observations=[]
    accepted=set()
    for row in report['rows']:
        if row['measurement'] not in {a['measurement']for a in artifacts} or row['phase'] not in spec['phases']:raise ValueError('measurement response binding mismatch')
        peak=row.get('peak_rss_bytes')
        if type(peak)is not int or peak<=0:raise ValueError('missing or invalid whole-host peak RSS')
        Store.validate_observation(dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=row['measurement'],target=target,workload=phase_workload(workload,row['phase']),metrics=dict(complete_call_ns=row['complete_call_ns'],peak_bytes=peak)))
        obj=dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=row['measurement'],target=target,workload=phase_workload(workload,row['phase']),metrics=dict(complete_call_ns=row['complete_call_ns'],peak_bytes=peak))
        observations.append(store.observe(obj));accepted.add(row['measurement'])
    if measurements[0] not in accepted:raise ValueError('baseline failed measurement validation')
    memory_scope='conservative whole-process peak RSS including all candidates and adapter; not per-candidate heap'
    return target,workload,spec['profile'],dict(schema=1,adapter=target['bridge'],workload_sha256=digest,reference_seconds=reference_seconds,reference_budget=reference_budget,acquisition_seconds=str(time.monotonic()-acquisition_started),observations=observations,validated_measurements=sorted(accepted),artifacts=artifacts,native_build=native_build,rejected=rejected+report['rejected'],acquisition=report,**({'input_validation':input_validation}if automatic_inputs else {}),scope='Offline selection inputs; average whole call and full result consumption; separate setup; '+memory_scope+'; no runtime probes')
