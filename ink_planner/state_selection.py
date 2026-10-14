"""Paired offline state lifecycle selection. Proof and memory gates stay separate."""
from fractions import Fraction
from statistics import median
from ink_knowledge import identity
from ink_knowledge.state_selection import policy

def choose(report,profile,baseline,eligible,minimum_gain_ppm=100000):
    controls=policy();excluded={};estimates={}
    receipt=dict(pairing=report.get('pairing'),policy_sha256=identity(controls),minimum_gain_ppm=minimum_gain_ppm,
                 excluded=excluded,estimates=estimates,scope=controls['scope'])
    def retain(reason):return baseline,dict(receipt,reason=reason)
    if report.get('pairing')!='interleaved-state-lifecycle-v1':return retain('baseline retained; missing paired acquisition provenance')
    if type(minimum_gain_ppm)is not int or not 0<=minimum_gain_ppm<1000000:raise ValueError('state minimum gain')
    processes=report.get('processes')
    if type(processes)is not int or not controls['minimum_processes']<=processes<=8:return retain('baseline retained; insufficient fresh paired processes')
    if baseline not in eligible:return retain('baseline lacks comparable memory and lifecycle evidence')
    phases={p:n for p,n in profile.items()if n};costs={};rounds={}
    for row in report['rows']:
        plan=row['measurement'];phase=row['phase'];process=row.get('process')
        if plan not in eligible or phase not in phases:continue
        key=(plan,process,phase)
        if key in costs:raise ValueError('duplicate paired state phase')
        if type(process)is not int or not 0<=process<processes:raise ValueError('state process identity')
        samples=[int(x)for x in row['complete_call_ns']]
        required=1 if row.get('phase_kind')=='module_load' else controls['minimum_rounds']
        if len(samples)<required or any(x<=0 for x in samples):excluded[plan]='missing positive repeated lifecycle samples';continue
        costs[key]=median([Fraction(x)for x in samples]);rounds[key]=len(samples)
    totals={}
    for plan in eligible:
        if plan in excluded:continue
        keys=[(plan,i,p)for i in range(processes)for p in phases]
        if any(k not in costs for k in keys):excluded[plan]='missing complete paired process phases';continue
        totals[plan]=[sum(costs[plan,i,p]*n for p,n in phases.items())for i in range(processes)]
    if baseline not in totals:return retain('baseline retained; incomplete paired lifecycle')
    candidates=[]
    for plan,values in totals.items():
        if plan==baseline:continue
        if any(rounds[plan,i,p]!=rounds[baseline,i,p]for i in range(processes)for p in phases):
            excluded[plan]='unpaired round counts';continue
        ratios=[a/b for a,b in zip(values,totals[baseline])]
        estimates[plan]=dict(process_cost_ns=[str(x)for x in values],baseline_process_cost_ns=[str(x)for x in totals[baseline]],
                             paired_process_ratios=[str(x)for x in ratios],maximum_ratio=str(max(ratios)))
        if max(ratios)*1000000>=1000000-minimum_gain_ppm:excluded[plan]='minimum lifecycle gain not established in every paired process';continue
        candidates.append((median(ratios),plan))
    selected=min(candidates)[1]if candidates else baseline
    return selected,dict(receipt,reason='paired complete lifecycle gain in every fresh process'if selected!=baseline else'baseline retained; no consistent paired lifecycle improvement')
