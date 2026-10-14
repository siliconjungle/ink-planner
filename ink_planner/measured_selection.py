"""Conservative paired selection of already checked, comparable implementations."""
import math, random, statistics
from ink_knowledge import identity

def proposal_families(states,edges):
    """First admitted ancestry, for allocating measurements, never authority.

    Edges are chronological admissions. Later routes to an existing state do
    not change the package retained for it and must not inflate its coverage.
    """
    if not states:return []
    families={states[0]:frozenset()}
    for edge in edges:
        if edge['target'] in families:continue
        if edge['source'] not in families:continue
        recipe=edge.get('recipe')
        families[edge['target']]=families[edge['source']]|({recipe} if recipe else set())
    return [families.get(state,frozenset()) for state in states]

def shortlist(states,priority,limit,families=None):
    """Preserve source diversity before filling physical variants of one source.

    Properties remain in search states and their proof packets. They do not by
    themselves describe another executable measurement boundary. A family is
    proposal provenance, not a quality score: reserve its first measurement
    before spending the rest of the budget on additional variants. The original
    source/cost ordering breaks ties and fills unused slots.
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
    order=list(dict.fromkeys(order))
    if families is None:return order[:limit]
    if len(families)!=len(states):raise ValueError('candidate family count mismatch')
    chosen=[0];covered=set(families[0])
    for i in order[1:]:
        if set(families[i])-covered:
            chosen.append(i);covered.update(families[i])
    return list(dict.fromkeys(chosen+order))[:limit]

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
    # Split the error budget between the overall gain gate and regression
    # evidence across the measured phases. Neither is a universal timing proof.
    alpha=(100-policy['confidence_percent'])/100
    quantile=1-alpha/(2*comparisons)
    lower_quantile=alpha/(2*comparisons*len(phases))
    resamples=[[rng.randrange(size)for _ in range(size)]for _ in range(policy['bootstrap_repetitions'])]
    estimates={};eligible=[];margin=1-policy['minimum_gain_percent']/100
    regression_limit=1+policy['maximum_phase_regression_percent']/100
    base=costs[baseline]
    for plan,values in costs.items():
        if plan==baseline:continue
        paired=[a/b for a,b in zip(values,base)]
        draws=sorted(sum(paired[i]for i in indices)/size for indices in resamples)
        upper=draws[min(len(draws)-1,math.ceil(quantile*len(draws))-1)]
        ratio=sum(paired)/size
        phase_estimates={};regressions=[]
        for phase in phases:
            paired_phase=[a/b for a,b in zip(rows[plan][phase],rows[baseline][phase])][:size]
            phase_draws=sorted(sum(paired_phase[i]for i in indices)/size for indices in resamples)
            lower=phase_draws[min(len(phase_draws)-1,math.floor(lower_quantile*len(phase_draws)))]
            phase_estimates[phase]=dict(candidate_over_baseline=str(sum(paired_phase)/size),lower_bound=str(lower))
            if lower>regression_limit:regressions.append(phase)
        estimates[plan]=dict(candidate_over_baseline=str(ratio),upper_bound=str(upper),comparisons=comparisons,rounds=size,
                            phases=phase_estimates,regressive_phases=regressions)
        if regressions:excluded[plan]='demonstrated complete-call regression in measured phase'
        elif upper<margin:eligible.append((ratio,plan))
        else:excluded[plan]='paired uncertainty or minimum lifecycle gain guard'
    selected=min(eligible)[1]if eligible else baseline
    return selected,dict(reason='paired complete-lifecycle improvement on selection inputs'if selected!=baseline else'baseline retained; no robust measured lifecycle improvement',
                         baseline_comparable=True,estimates=estimates,excluded=excluded,policy_sha256=identity(policy),
                         scope='Synthetic probes or supplied development calls; workload-specific profitability only. Phase checks exclude demonstrated call regressions, not all possible losses; setup remains in the total lifecycle gate. Proofs and target eligibility replay independently')
