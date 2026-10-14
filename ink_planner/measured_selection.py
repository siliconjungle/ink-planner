"""Conservative paired selection of already checked, comparable implementations."""
import math, random, statistics
from ink_knowledge import identity

def shortlist(states,priority,limit):
    """Preserve source diversity before filling physical variants of one source.

    Properties remain in search states and their proof packets. They do not by
    themselves describe another executable measurement boundary.
    """
    seen=set();groups={};baseline=states[0]['source']
    for i,state in enumerate(states):
        if not state.get('capability',{}).get('emittable',True):continue
        operational={k:state.get(k)for k in ['source','representation','definition_choices','physical','maintained','protocol','route','resources']}
        key=identity(operational)
        if key in seen:continue
        seen.add(key);groups.setdefault(state['source'],[]).append(i)
    for values in groups.values():values.sort(key=lambda i:(priority(states[i]),i))
    order=[0]
    first=sorted((v[0]for v in groups.values()if v[0]!=0),key=lambda i:(states[i]['source']==baseline,priority(states[i]),i))
    rest=sorted((i for values in groups.values()for i in values[1:]if i!=0),key=lambda i:(priority(states[i]),i))
    order.extend(first);order.extend(rest)
    return list(dict.fromkeys(order))[:limit]

def choose(report,profile,baseline,policy,setup_ns=None):
    """Paired bootstrap over interleaved rounds, with a bounded comparison guard.

    This is a profitability estimate for these inputs, not a mathematical fact
    about arbitrary input distributions. Full source proof replay is separate.
    """
    rows={};excluded={};setup_ns=setup_ns or {}
    for row in report['rows']:
        phase=row['phase'];plan=row['measurement'];samples=[int(x)for x in row['complete_call_ns']]
        if phase not in profile or len(samples)<9 or any(x<=0 for x in samples):
            excluded[plan]='missing positive repeated comparable samples';continue
        rows.setdefault(plan,{})[phase]=samples
    phases=[p for p,n in profile.items()if n]
    if baseline not in rows or set(phases)-set(rows[baseline]):
        return baseline,dict(reason='baseline lacks complete comparable phases',excluded=excluded)
    size=min(len(rows[baseline][p])for p in phases)
    costs={}
    for plan,values in rows.items():
        if set(phases)-set(values) or any(len(values[p])!=len(rows[baseline][p])for p in phases):
            excluded[plan]='missing paired lifecycle phases';continue
        costs[plan]=[sum(values[p][i]*profile[p]for p in phases)+setup_ns.get(plan,0)for i in range(size)]
    if baseline not in costs:return baseline,dict(reason='baseline lacks paired lifecycle samples',excluded=excluded)
    rng=random.Random(0x4d2a13);comparisons=max(1,len(costs)-1)
    # Bonferroni guards the bounded set of candidates measured in this batch.
    quantile=1-(100-policy['confidence_percent'])/100/comparisons
    resamples=[[rng.randrange(size)for _ in range(size)]for _ in range(policy['bootstrap_repetitions'])]
    estimates={};eligible=[];margin=1-policy['minimum_gain_percent']/100
    base=costs[baseline]
    for plan,values in costs.items():
        if plan==baseline:continue
        paired=[a/b for a,b in zip(values,base)]
        draws=sorted(sum(paired[i]for i in indices)/size for indices in resamples)
        upper=draws[min(len(draws)-1,math.ceil(quantile*len(draws))-1)]
        ratio=sum(paired)/size
        estimates[plan]=dict(candidate_over_baseline=str(ratio),upper_bound=str(upper),comparisons=comparisons,rounds=size)
        if upper<margin:eligible.append((ratio,plan))
        else:excluded[plan]='paired uncertainty or minimum lifecycle gain guard'
    selected=min(eligible)[1]if eligible else baseline
    return selected,dict(reason='paired complete-lifecycle improvement on selection inputs'if selected!=baseline else'baseline retained; no robust measured lifecycle improvement',
                         baseline_comparable=True,estimates=estimates,excluded=excluded,policy_sha256=identity(policy),
                         scope='Synthetic probes or supplied development calls; workload-specific profitability only; proofs and target eligibility replay independently')
