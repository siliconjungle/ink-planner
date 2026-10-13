"""Select across every observed cohort with comparable complete measurements."""
from fractions import Fraction
from .representation_selection import rank

def select(store,source,plans,profiles,baseline,memory_limit=None):
    totals={p:Fraction(0) for p in plans};cohorts=[];withheld={}
    for profile in profiles:
        _,receipt=rank(store,source,plans,profile['target'],profile['workload'],profile['counts'],baseline,memory_limit=memory_limit)
        costs=receipt['total_ns']
        # A plan that fails the uncertainty guard in any observed cohort is not
        # selected; summing cohorts must not hide an unreliable improvement.
        for plan,reason in ((receipt.get('uncertainty_guard') or {}).get('excluded') or {}).items():
            if plan!=baseline:withheld.setdefault(plan,reason)
        # Missing any observed cohort excludes a candidate, including baseline.
        for plan in list(totals):
            if plan not in costs:del totals[plan]
            else:totals[plan]+=Fraction(costs[plan])
        cohorts.append(dict(evidence=profile['evidence'],saturated=profile['saturated'],selection=receipt))
    chosen=baseline
    if profiles and baseline in totals:
        chosen=min((p for p in totals if p not in withheld),key=lambda p:(totals[p],p!=baseline,p))
    reason=('observed usage with comparable measurements' if chosen!=baseline else
            'baseline retained; no measured improvement across every observed cohort')
    return chosen,dict(reason=reason,costs={p:str(v) for p,v in totals.items()},cohorts=cohorts,
                       uncertainty_guard=dict(excluded=withheld,reason=reason))
