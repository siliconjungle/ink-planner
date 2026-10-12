"""Select across every observed cohort with comparable complete measurements."""
from fractions import Fraction
from .representation_selection import rank

def select(store,source,plans,profiles,baseline,memory_limit=None):
    totals={p:Fraction(0) for p in plans};cohorts=[]
    for profile in profiles:
        _,receipt=rank(store,source,plans,profile['target'],profile['workload'],profile['counts'],baseline,memory_limit=memory_limit)
        costs=receipt['total_ns']
        # Missing any observed cohort excludes a candidate, including baseline.
        for plan in list(totals):
            if plan not in costs:del totals[plan]
            else:totals[plan]+=Fraction(costs[plan])
        cohorts.append(dict(evidence=profile['evidence'],saturated=profile['saturated'],selection=receipt))
    chosen=baseline
    if profiles and baseline in totals:
        chosen=min(totals,key=lambda p:(totals[p],p!=baseline,p))
    return chosen,dict(reason='observed usage with comparable measurements' if chosen!=baseline else 'baseline retained; no measured improvement across every observed cohort',costs={p:str(v) for p,v in totals.items()},cohorts=cohorts)
