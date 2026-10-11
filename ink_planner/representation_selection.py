"""Bounded external selection of proof-checked physical representations.

Complete lifecycle measurements rank candidates; they never discharge proofs.
Missing comparable baseline evidence retains the checked baseline. Migration
cost and optional measured-memory constraints are included before switching.
"""
import argparse,hashlib,json,subprocess,tempfile
from fractions import Fraction
from pathlib import Path
from .route_selection import read

def phase_workload(workload,phase):
    return dict(workload,distribution=dict(workload=workload['distribution'],phase=phase))

def rank(store,program,plans,target,workload,counts,baseline,migration_count=0,memory_limit=None,migration_phases=None):
    if not isinstance(counts,dict) or not 1<=len(counts)<=64 or any(not isinstance(k,str) or not (k in ('initialise','teardown','check','compile') or k.startswith('action:') and len(k)>7) or type(v) is not int or not 0<=v<=1_000_000_000 for k,v in counts.items()):
        raise ValueError('invalid bounded lifecycle profile')
    if type(migration_count) is not int or not 0<=migration_count<=1_000_000:raise ValueError('invalid migration count')
    if memory_limit is not None and (type(memory_limit) is not int or memory_limit<0):raise ValueError('invalid memory limit')
    required={p:n for p,n in counts.items() if n};phases=set(required)
    if not required:raise ValueError('lifecycle profile has no work')
    migration_phases=migration_phases or {}
    if migration_count:phases.update(migration_phases.get(p,'migrate:'+baseline) for p in plans if p!=baseline)
    measured={p:{} for p in plans};evidence={p:[] for p in plans}
    for phase in sorted(phases):
        observations=store.observations(program,plans,target,phase_workload(workload,phase))
        grouped={}
        for obj in observations:grouped.setdefault(obj['plan'],[]).append(obj)
        for plan,objects in grouped.items():
            samples=[int(n) for obj in objects for n in obj['metrics']['complete_call_ns']]
            # Exact arithmetic avoids rounding a near-tie into a cheaper plan.
            samples.sort();middle=len(samples)//2
            median=Fraction(samples[middle]) if len(samples)%2 else Fraction(samples[middle-1]+samples[middle],2)
            peaks=[obj['metrics']['peak_bytes'] for obj in objects]
            measured[plan][phase]=(median,max(peaks) if all(p is not None for p in peaks) else None)
            evidence[plan].extend(hashlib.sha256(__import__('ink_knowledge').canonical(obj)).hexdigest() for obj in objects)
    totals={};excluded={}
    for plan in plans:
        weights=dict(required)
        if migration_count and plan!=baseline:weights[migration_phases.get(plan,'migrate:'+baseline)]=migration_count
        missing=[p for p in weights if p not in measured[plan]]
        if missing:excluded[plan]='missing phases: '+','.join(sorted(missing));continue
        if memory_limit is not None:
            peaks=[measured[plan][p][1] for p in weights]
            if any(p is None for p in peaks):excluded[plan]='unmeasured memory';continue
            if max(peaks)>memory_limit:excluded[plan]='measured memory exceeds limit';continue
        totals[plan]=sum((measured[plan][p][0]*n for p,n in weights.items()),Fraction(0))
    selected=baseline
    if baseline in totals:
        for plan in plans:
            if plan in totals and totals[plan]<totals[selected]:selected=plan
    elif memory_limit is not None:
        raise ValueError('baseline cannot satisfy the measured memory constraint')
    return selected,dict(total_ns={p:str(n) for p,n in totals.items()},excluded=excluded,observations={p:sorted(set(ids)) for p,ids in evidence.items()},baseline_comparable=baseline in totals)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('compiler','source','baseline','candidates','knowledge-root','target','workload','profile'):
        p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--max-candidates',type=int,default=16);p.add_argument('--check-timeout-seconds',type=int,default=30)
    p.add_argument('--migration-count',type=int,default=0);p.add_argument('--memory-limit',type=int)
    p.add_argument('-o','--output',type=Path,required=True);a=p.parse_args()
    if not 0<=a.max_candidates<=32 or not 1<=a.check_timeout_seconds<=60:raise ValueError('selection budget')
    candidates=json.loads(read(a.candidates,1_000_000))
    if not isinstance(candidates,list) or len(candidates)>32:raise ValueError('candidate index budget')
    entries=[dict(request=str(a.baseline.resolve()))]
    for candidate in candidates[:a.max_candidates]:
        if isinstance(candidate,str):candidate=dict(request=candidate)
        if not isinstance(candidate,dict) or set(candidate)-{'request','bridge'} or not isinstance(candidate.get('request'),str) or any(not isinstance(v,str) for v in candidate.values()):raise ValueError('candidate index format')
        entries.append({k:str((a.candidates.parent/v).resolve()) for k,v in candidate.items()})
    checked=[];rejected=[]
    with tempfile.TemporaryDirectory(prefix="ink-representation-selection-") as temporary:
        for i,entry in enumerate(entries):
            try:
                raw=read(Path(entry['request']),16_000_000);request=json.loads(raw)
                staged=Path(temporary)/f'{i}.json';staged.write_bytes(raw)
                result=subprocess.run([str(a.compiler.resolve()),'check-representation',str(a.source.resolve()),str(staged)],capture_output=True,text=True,timeout=a.check_timeout_seconds)
                if result.returncode:raise ValueError(result.stderr[:500])
                receipt=json.loads(result.stdout)
                if receipt.get('status')!='checked' or receipt.get('representation')!=request.get('representation') or receipt.get('source')!=request.get('input_core_sha256'):raise ValueError('checker receipt mismatch')
                if i and receipt['source']!=checked[0]['source']:raise ValueError('source mismatch')
                bridge_raw=None
                if i and a.migration_count and receipt['representation']!=checked[0]['plan']:
                    if 'bridge' not in entry:raise ValueError('migration requires a checked bridge')
                    bridge_raw=read(Path(entry['bridge']),16_000_000)
                    staged_bridge=Path(temporary)/f'{i}.bridge.json';staged_bridge.write_bytes(bridge_raw)
                    migration=subprocess.run([str(a.compiler.resolve()),'check-migration',str(a.source.resolve()),str(Path(temporary)/'0.json'),str(staged),str(staged_bridge)],capture_output=True,text=True,timeout=a.check_timeout_seconds)
                    if migration.returncode:raise ValueError(migration.stderr[:500])
                    bridge=json.loads(migration.stdout)
                    if bridge.get('status')!='checked' or bridge.get('from')!=checked[0]['plan'] or bridge.get('to')!=receipt['representation']:raise ValueError('migration endpoints mismatch')
                checked.append(dict(plan=receipt['representation'],measurement=hashlib.sha256(raw).hexdigest(),source=receipt['source'],raw=raw,bridge=bridge_raw))
            except (ValueError,OSError,KeyError,subprocess.TimeoutExpired) as e:
                if i==0:raise ValueError('baseline checking failed: '+str(e)) from e
                rejected.append(dict(request=entry['request'],reason=str(e)))
    from ink_knowledge import Store
    store=Store(a.knowledge_root)
    target=json.loads(read(a.target,64000));workload=json.loads(read(a.workload,64000));profile=json.loads(read(a.profile,64000))
    # Validate provenance using the same canonical observation contract.
    Store.validate_observation(dict(schema=1,program=checked[0]['source'],entries=[],plan=checked[0]['plan'],target=target,workload=workload,metrics=dict(complete_call_ns=['0'],peak_bytes=None)))
    migration_phases={c['measurement']:'migrate:'+checked[0]['measurement']+':'+hashlib.sha256(c['bridge']).hexdigest() for c in checked if c['bridge'] is not None}
    selected,costs=rank(store,checked[0]['source'],list(dict.fromkeys(c['measurement'] for c in checked)),target,workload,profile,checked[0]['measurement'],a.migration_count,a.memory_limit,migration_phases)
    chosen=next(c for c in checked if c['measurement']==selected)
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(chosen['raw'])
    bridge_path=None
    if chosen['bridge'] is not None:
        bridge_path=str(a.output)+'.bridge.json';Path(bridge_path).write_bytes(chosen['bridge'])
    receipt=dict(schema=1,status='checked',selected=chosen['plan'],request_sha256=hashlib.sha256(chosen['raw']).hexdigest(),source=chosen['source'],snapshot=store.snapshot_id,profile=profile,target=target,workload=workload,migration_count=a.migration_count,bridge=bridge_path,bridge_sha256=(hashlib.sha256(chosen['bridge']).hexdigest() if chosen['bridge'] is not None else None),memory_limit=a.memory_limit,checked=len(checked),rejected=rejected,remaining=max(0,len(candidates)-a.max_candidates),costs=costs,scope='external measured lifecycle selection; replay rechecks request and bridge; no live adaptation')
    Path(str(a.output)+'.selection.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if __name__=='__main__':main()
