"""Bounded offline Bayesian exploration of eligible pure configurations.

Host callbacks collect complete-call observations. No runtime trials, semantic
admission, arbitrary commands or automatic profile promotion occur here.
"""
import argparse
import hashlib
import json
import math
import random
import statistics
from pathlib import Path


def vectors(configs):
    names=sorted({k for c in configs for k in c})
    if not names or any(set(c)!=set(names) for c in configs): raise ValueError('configuration dimensions')
    columns=[]
    for name in names:
        values=[c[name] for c in configs]
        if all(type(v) in (int,float) and math.isfinite(v) for v in values):
            lo,hi=min(values),max(values);columns.append([(v-lo)/max(1e-12,hi-lo) for v in values])
        elif all(type(v) in (str,bool) for v in values):
            for choice in sorted(set(values),key=str):columns.append([int(v==choice) for v in values])
        else: raise ValueError('finite numeric/categorical configuration required')
    if len(columns)>64: raise ValueError('configuration dimension budget')
    return [list(x) for x in zip(*columns)]


def kernel(a,b): return math.exp(-sum((x-y)**2 for x,y in zip(a,b))/2)


def acquisition(xs, measured, values):
    if not measured:return [0.0]*len(xs)
    lo=min(values);scale=max(1.0,max(values)-lo);ys=[(y-lo)/scale for y in values]
    matrix=[[kernel(xs[i],xs[j])+(1e-5 if i==j else 0) for j in measured] for i in measured]
    # Factor once. Re-solving a dense system for every unmeasured candidate
    # would spend cubic work per prediction and defeat bounded autotuning.
    n=len(measured);factor=[[0.0]*n for _ in range(n)]
    try:
        for i in range(n):
            for j in range(i+1):
                value=matrix[i][j]-sum(factor[i][k]*factor[j][k] for k in range(j))
                factor[i][j]=math.sqrt(value) if i==j else value/factor[j][j]
        def forward(rhs):
            out=[]
            for i in range(n):out.append((rhs[i]-sum(factor[i][j]*out[j] for j in range(i)))/factor[i][i])
            return out
        temp=forward(ys);alpha=[0.0]*n
        for i in range(n-1,-1,-1):alpha[i]=(temp[i]-sum(factor[j][i]*alpha[j] for j in range(i+1,n)))/factor[i][i]
    except (ValueError,ZeroDivisionError):return [0.0]*len(xs)
    scores=[]
    for x in xs:
        cov=[kernel(x,xs[i]) for i in measured]
        mean=sum(a*b for a,b in zip(cov,alpha))
        projected=forward(cov);variance=max(0,1-sum(a*a for a in projected))
        scores.append(mean-2*math.sqrt(variance))
    return scores


def objective(observation, expected_calls):
    samples=observation.get('samples_ns')
    if observation.get('correct') is not True or observation.get('fallback',False):return None
    if not isinstance(samples,list) or not 1<=len(samples)<=256 or any(type(x) is not int or not 0<x<=10**15 for x in samples):raise ValueError('complete-call samples')
    if 'setup_ns' not in observation:return None
    setup=observation['setup_ns']
    if type(setup) is not int or not 0<=setup<=10**15:raise ValueError('measured setup cost')
    return statistics.median(samples)+setup/expected_calls


def tune(configurations,evaluate,*,budget=16,seed=0,expected_calls=1,context=None,heldout=None):
    if not 1<=len(configurations)<=4096 or not 1<=budget<=64 or not 1<=expected_calls<=10**9:raise ValueError('offline tuning budget')
    context=context or {}
    if not all(isinstance(context.get(k),str) and context[k] for k in ('target','code','boundary','workload')):raise ValueError('exact tuning context required')
    configs=json.loads(json.dumps(configurations,allow_nan=False));xs=vectors(configs)
    if len(json.dumps(configs).encode())>4_000_000:raise ValueError('configuration byte budget')
    rng=random.Random(seed);observations=[];measured=[];values=[];best=0;baseline=None
    for iteration in range(min(budget,len(configs))):
        if iteration==0:index=0
        elif len(measured)<3:
            index=rng.choice([i for i in range(len(configs)) if i not in measured])
        else:
            scores=acquisition(xs,measured,values)
            index=min((i for i in range(len(configs)) if i not in measured),key=lambda i:(scores[i],i))
        try:
            record=evaluate(json.loads(json.dumps(configs[index])))
            cost=objective(record,expected_calls)
        except Exception as error:
            record=dict(correct=False,error=str(error));cost=None
        if iteration==0:
            if cost is None:raise ValueError('baseline observation failed')
            baseline=cost
        measured.append(index);values.append(cost if cost is not None else baseline*10)
        observations.append(dict(index=index,configuration=configs[index],observation=record,objective_ns=cost))
        if cost is not None and cost<values[measured.index(best)]:best=index
    proposal=best;released=0;checks=[]
    if proposal!=0 and values[measured.index(proposal)]<baseline*.9 and heldout is not None:
        try:
            checks=heldout(configs[0],configs[proposal])
            if not isinstance(checks,list) or not 1<=len(checks)<=256:raise ValueError('bounded holdout evidence required')
            # Both warm and setup-amortised objectives must win on every holdout.
            if checks and all(c.get('correct') is True and c['candidate_ns']<c['baseline_ns']*.9 and c['candidate_total_ns']<c['baseline_total_ns']*.9 and all(type(c[k]) in (int,float) and math.isfinite(c[k]) and c[k]>0 for k in ('candidate_ns','baseline_ns','candidate_total_ns','baseline_total_ns')) for c in checks):released=proposal
        except Exception as error:checks=[dict(correct=False,error=str(error))]
    receipt=dict(schema=1,seed=seed,budget=budget,expected_calls=expected_calls,context=context,
        observations=observations,proposal=configs[proposal],selected=configs[released],released=bool(released),heldout=checks,
        authority='offline profitability proposal only; eligibility/proof replay belongs to existing checker; no online trials')
    receipt['training_sha256']=hashlib.sha256(json.dumps(observations,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    return receipt


def main():
    p=argparse.ArgumentParser();p.add_argument('--observations',type=Path,required=True);p.add_argument('--budget',type=int,default=16)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--expected-calls',type=int,default=1);p.add_argument('-o',type=Path,required=True);p.add_argument('--knowledge-root',type=Path)
    a=p.parse_args();data=json.loads(a.observations.read_text());configs=[r['configuration'] for r in data['rows']]
    def lookup(c):return data['rows'][configs.index(c)]['observation']
    result=tune(configs,lookup,budget=a.budget,seed=a.seed,expected_calls=a.expected_calls,context=data['context'])
    a.o.parent.mkdir(parents=True,exist_ok=True);a.o.write_text(json.dumps(result,indent=2)+'\n')
    if a.knowledge_root:
        from ink_knowledge.search_artifacts import publish
        ident=publish(a.knowledge_root,'observations',result);print(json.dumps(dict(observation=ident)))


if __name__=='__main__':main()
