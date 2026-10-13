"""Offline complete-call observations for admitted pure implementation plans.

The installed adapter executes JavaScript trusted values. Other execution ABIs
are declined explicitly; observations never substitute for proof replay.
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

def independent_workloads(training,holdout):
    first,_=read_workload(training);second,_=read_workload(holdout)
    if first['profile']!=second['profile']:raise ValueError('holdout must retain the lifecycle objective weights')
    def calls(spec):
        return {json.dumps([row['function'],row['args']],sort_keys=True,separators=(',',':'),allow_nan=False)
                for rows in spec['phases'].values()for row in rows}
    if calls(first)&calls(second):raise ValueError('holdout inputs overlap selection inputs')

def acquire(planner,store,packages,states,measurements,path):
    spec,digest=read_workload(path)
    if planner.options.target!='javascript':raise ValueError('no installed offline measurement adapter for '+planner.options.target)
    node=shutil.which('node')
    if not node:raise ValueError('offline measurement requires Node.js')
    version=subprocess.check_output([node,'--version'],text=True).strip()
    target=dict(hardware=platform.machine()+':'+platform.processor()+':'+platform.node(),driver=platform.system()+':'+platform.release(),toolchain='compiler:'+planner.compiler_hash+';node:'+version+';adapter:'+hashlib.sha256(RUNNER.read_bytes()).hexdigest(),backend='javascript',bridge='trusted-values-v2;complete-output-consumption')
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
            expected=planner.invoke('run',planner.original,row['function'],args,'--core')
            if isinstance(expected,str):expected=json.loads(expected)
            phases[phase].append(dict(row,expected=expected))
    reference_seconds=str(time.monotonic()-oracle_started)
    artifacts=[];rejected=[]
    for package,state,measurement in zip(packages,states,measurements):
        if not state.get('capability',{}).get('emittable',True):
            rejected.append(dict(measurement=measurement,reason='backend capability'));continue
        staged=planner.directory/(measurement+'.plan.json');staged.write_bytes(canonical(package)+b'\n')
        artifact=planner.directory/(measurement+'.mjs');begin=time.monotonic()
        try:
            planner.invoke('emit-plan',planner.original,staged,'--core','--target','javascript','-o',artifact)
            artifacts.append(dict(measurement=measurement,path=str(artifact),artifact_sha256=hashlib.sha256(artifact.read_bytes()).hexdigest(),emission_seconds=str(time.monotonic()-begin)))
        except (ValueError,OSError,subprocess.TimeoutExpired)as error:rejected.append(dict(measurement=measurement,reason=str(error)[-2000:]))
    if not artifacts or artifacts[0]['measurement']!=measurements[0]:raise ValueError('baseline could not be measured')
    request=planner.directory/'measure-calls.json';request.write_text(json.dumps(dict(artifacts=artifacts,phases=phases),allow_nan=False))
    processes=getattr(planner.options,'measure_processes',3)
    if type(processes) is not int or not 1<=processes<=8:raise ValueError('measurement process count')
    reports=[];started=time.monotonic()
    for process in range(processes):
        remaining=planner.options.measure_timeout-(time.monotonic()-started)
        if remaining<=0:raise ValueError('offline measurement process budget exhausted')
        result=subprocess.run([node,str(RUNNER),str(request)],capture_output=True,text=True,timeout=remaining)
        if result.returncode:raise ValueError('offline measurement failed: '+result.stderr[-2000:])
        reports.append(json.loads(result.stdout))
    # A candidate must validate in every fresh process. A timing from a rejected
    # process cannot survive through another process's successful observation.
    accepted_everywhere={a['measurement']for a in artifacts}
    for report in reports:accepted_everywhere&={row['measurement']for row in report['rows']}
    fingerprints=reports[0]['input_fingerprints']
    if any(r['input_fingerprints']!=fingerprints for r in reports):raise ValueError('measurement normalized inputs changed across processes')
    report=dict(schema=1,processes=processes,rows=[dict(row,process=index)for index,r in enumerate(reports)for row in r['rows']if row['measurement']in accepted_everywhere],
                input_fingerprints=fingerprints,rejected=[row for r in reports for row in r['rejected']],module_load=[dict(row,process=index)for index,r in enumerate(reports)for row in r['module_load']])
    observations=[]
    accepted=set()
    for row in report['rows']:
        if row['measurement'] not in {a['measurement']for a in artifacts} or row['phase'] not in spec['phases']:raise ValueError('measurement response binding mismatch')
        Store.validate_observation(dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=row['measurement'],target=target,workload=phase_workload(workload,row['phase']),metrics=dict(complete_call_ns=row['complete_call_ns'],peak_bytes=None)))
        obj=dict(schema=1,program=packages[0]['input_core_sha256'],entries=[],plan=row['measurement'],target=target,workload=phase_workload(workload,row['phase']),metrics=dict(complete_call_ns=row['complete_call_ns'],peak_bytes=None))
        observations.append(store.observe(obj));accepted.add(row['measurement'])
    if measurements[0] not in accepted:raise ValueError('baseline failed measurement validation')
    return target,workload,spec['profile'],dict(schema=1,adapter='pure-javascript-calls-v1',workload_sha256=digest,reference_seconds=reference_seconds,observations=observations,artifacts=artifacts,rejected=rejected+report['rejected'],acquisition=report,scope='Offline selection inputs; whole call and full result consumption; no exact peak memory evidence; no runtime probes')
