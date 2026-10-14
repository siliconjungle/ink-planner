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
    """Bootstrap fresh processes, then paired rounds within each process.

    Process means receive equal weight. This estimates profitability on these
    inputs; repeated rounds are not additional independent process launches.
    Full source proof replay remains separate.
    """
    rows={};excluded={};setup_ns=setup_ns or {}
    process_count=report.get('processes',1)
    if type(process_count)is not int or not 1<=process_count<=8:
        return baseline,dict(reason='invalid measurement process count',excluded=excluded)
    processes=list(range(process_count));phases=[p for p,n in profile.items()if n]
    if not phases:return baseline,dict(reason='baseline lacks complete comparable phases',excluded=excluded)
    for row in report['rows']:
        phase=row['phase'];plan=row['measurement'];process=row.get('process',0)
        samples=[int(x)for x in row['complete_call_ns']]
        if phase not in phases or type(process)is not int or process not in processes or not 9<=len(samples)<=64 or any(x<=0 for x in samples):
            excluded[plan]='missing positive repeated comparable samples';continue
        values=rows.setdefault(plan,{}).setdefault(process,{})
        if phase in values:excluded[plan]='duplicate process/phase samples';continue
        values[phase]=samples
    def complete(values):
        return set(values)==set(processes) and all(set(values[p])==set(phases) for p in processes)
    if baseline in excluded or baseline not in rows or not complete(rows[baseline]):
        return baseline,dict(reason='baseline lacks complete comparable phases',excluded=excluded)
    sizes={p:min(len(rows[baseline][p][phase])for phase in phases)for p in processes}
    costs={}
    for plan,values in rows.items():
        if plan in excluded:continue
        if not complete(values) or any(len(values[p][phase])!=len(rows[baseline][p][phase])for p in processes for phase in phases):
            excluded[plan]='missing paired lifecycle phases';continue
        setup=setup_ns.get(plan,0)
        if not isinstance(setup,dict):setup={p:setup for p in processes}
        if set(setup)!=set(processes) or any(type(n)is not int or n<0 for n in setup.values()):
            excluded[plan]='missing comparable process setup';continue
        costs[plan]={p:[sum(values[p][phase][i]*profile[phase]for phase in phases)+setup[p] for i in range(sizes[p])]for p in processes}
    if baseline not in costs:return baseline,dict(reason='baseline lacks paired lifecycle samples',excluded=excluded)
    rng=random.Random(0x4d2a13);comparisons=max(1,len(costs)-1)
    alpha=(100-policy['confidence_percent'])/100
    quantile=1-alpha/(2*comparisons)
    lower_quantile=alpha/(2*comparisons*len(phases))
    resamples=[]
    for _ in range(policy['bootstrap_repetitions']):
        draw=[]
        for _ in processes:
            p=rng.randrange(process_count)if process_count>1 else 0
            draw.append((p,[rng.randrange(sizes[p])for _ in range(sizes[p])]))
        resamples.append(draw)
    def draws(paired):
        return sorted(sum(sum(paired[p][i]for i in indices)/len(indices)for p,indices in draw)/process_count for draw in resamples)
    def mean(paired):return sum(sum(paired[p])/len(paired[p])for p in processes)/process_count
    estimates={};eligible=[];margin=1-policy['minimum_gain_percent']/100
    regression_limit=1+policy['maximum_phase_regression_percent']/100
    base=costs[baseline]
    for plan,values in costs.items():
        if plan==baseline:continue
        paired={p:[a/b for a,b in zip(values[p],base[p])]for p in processes}
        distribution=draws(paired)
        upper=distribution[min(len(distribution)-1,math.ceil(quantile*len(distribution))-1)]
        ratio=mean(paired);phase_estimates={};regressions=[]
        for phase in phases:
            paired_phase={p:[a/b for a,b in zip(rows[plan][p][phase],rows[baseline][p][phase])][:sizes[p]]for p in processes}
            distribution=draws(paired_phase)
            lower=distribution[min(len(distribution)-1,math.floor(lower_quantile*len(distribution)))]
            phase_estimates[phase]=dict(candidate_over_baseline=str(mean(paired_phase)),lower_bound=str(lower),
                process_ratios={str(p):str(sum(v)/len(v))for p,v in paired_phase.items()})
            if lower>regression_limit:regressions.append(phase)
        estimates[plan]=dict(candidate_over_baseline=str(ratio),upper_bound=str(upper),comparisons=comparisons,
                            processes=process_count,rounds_per_process={str(p):sizes[p]for p in processes},
                            process_ratios={str(p):str(sum(v)/len(v))for p,v in paired.items()},
                            phases=phase_estimates,regressive_phases=regressions)
        if regressions:excluded[plan]='demonstrated complete-call regression in measured phase'
        elif upper<margin:eligible.append((ratio,plan))
        else:excluded[plan]='paired uncertainty or minimum lifecycle gain guard'
    selected=min(eligible)[1]if eligible else baseline
    return selected,dict(reason='paired complete-lifecycle improvement on selection inputs'if selected!=baseline else'baseline retained; no robust measured lifecycle improvement',
                         baseline_comparable=True,estimates=estimates,excluded=excluded,policy_sha256=identity(policy),
                         scope='Synthetic probes or supplied development calls; workload-specific profitability only. Hierarchical process/paired-round bootstrap; phase checks exclude demonstrated call regressions, not all possible losses. Per-process setup remains in the total lifecycle gate. Proofs and target eligibility replay independently')
