#!/usr/bin/env python3
"""Bounded external selection of checked routes by complete measured call cost.
Hashes associate measurements; only compiler checking establishes admissibility.
"""
import argparse,hashlib,json,math,statistics,subprocess
from pathlib import Path

def read(path,limit):
    with path.open('rb') as f: data=f.read(limit+1)
    if len(data)>limit:raise ValueError('input exceeds budget')
    return data

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler',required=True,type=Path);p.add_argument('--core',required=True,type=Path)
    p.add_argument('--baseline',required=True,type=Path);p.add_argument('--candidates',required=True,type=Path)
    p.add_argument('--knowledge-root',type=Path);p.add_argument('--target',type=Path);p.add_argument('--workload',type=Path);p.add_argument('--max-candidates',type=int,default=16)
    p.add_argument('--check-timeout-seconds',type=int,default=10)
    p.add_argument('-o','--output',required=True,type=Path);a=p.parse_args()
    if not 1<=a.check_timeout_seconds<=60:raise ValueError('checking time budget outside 1..60')
    if not 0<=a.max_candidates<=32:raise ValueError('candidate budget outside 0..32')
    candidates=json.loads(read(a.candidates,1_000_000))
    if not isinstance(candidates,list) or len(candidates)>32 or any(not (isinstance(c,str) or isinstance(c,dict) and set(c)=={'route','proof'} and all(isinstance(v,str) for v in c.values())) for c in candidates):raise ValueError('invalid candidate index')
    checked=[];rejected=[]
    paths=[(a.baseline.resolve(),None)]+[((a.candidates.parent/(c if isinstance(c,str) else c['route'])).resolve(), None if isinstance(c,str) else (a.candidates.parent/c['proof']).resolve()) for c in candidates[:a.max_candidates]]
    for i,(path,proof) in enumerate(paths):
        raw=read(path,16_000_000)
        try:
            command=[str(a.compiler.resolve()),'check-source-route',str(a.core.resolve()),str(path)]
            proof_bytes=read(proof,16_000_000) if proof else None
            if proof:command += ['--route-proof',str(proof)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=a.check_timeout_seconds)
        except subprocess.TimeoutExpired:
            if i==0:raise ValueError('baseline checking budget exhausted')
            rejected.append(str(path));continue
        if result.returncode:
            if i==0:raise ValueError('baseline failed checking: '+result.stderr)
            rejected.append(str(path));continue
        route=json.loads(raw)
        if i==0:
            stages=route['stages']
            if len(stages)!=1 or stages[0]['function']!=route['entry'] or stages[0]['backend']!='c' or stages[0]['domain']!='cpu' or route['output']!={'Stage':stages[0]['id']}:
                raise ValueError('baseline must be an unchanged whole-entry CPU stage')
        checked.append((hashlib.sha256(raw).hexdigest(),raw,json.loads(result.stdout),proof_bytes))
    if any(c[2]['core_sha256']!=checked[0][2]['core_sha256'] or c[2]['entry']!=checked[0][2]['entry'] for c in checked):raise ValueError('candidate core/entry mismatch')
    from ink_knowledge import Store,identity
    costs={};measurement=None
    supplied=[a.knowledge_root,a.target,a.workload]
    if any(supplied) and not all(supplied):raise ValueError('cost selection requires knowledge root, target and workload')
    if all(supplied):
        target=json.loads(read(a.target,64000));workload=json.loads(read(a.workload,64000))
        store=Store(a.knowledge_root)
        observations=store.observations(checked[0][2]['core_sha256'],[c[0] for c in checked],target,workload)
        samples={}
        for o in observations:samples.setdefault(o['plan'],[]).extend(int(x) for x in o['metrics']['complete_call_ns'])
        costs={p:statistics.median(xs)/1_000_000 for p,xs in samples.items()}
        measurement=dict(target=target,workload=workload,metric='complete-call-ns',observations=len(observations))
    # Without comparable baseline evidence, never infer a profitable alternative.
    selected=checked[0]
    if selected[0] in costs:
        for candidate in checked[1:]:
            if candidate[0] in costs and costs[candidate[0]]<costs[selected[0]]:selected=candidate
    a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(selected[1])
    selected_proof=None
    if selected[3] is not None:
        selected_proof=str(a.output)+'.proof.json';Path(selected_proof).write_bytes(selected[3])
    print(json.dumps(dict(status='checked',selected_sha256=selected[0],selected_proof=selected_proof,checked=len(checked),rejected=rejected,budget=a.max_candidates,remaining=len(candidates)-min(len(candidates),a.max_candidates),cost_ms=costs.get(selected[0]),measurement=measurement,scope='selection by untrusted performance evidence; every candidate checked independently; no runtime adaptation')))
if __name__=='__main__':main()
