"""Optional seeded stochastic proposal ordering. No semantic authority.

Fixed structural scores order proposals. Only the caller's checker can admit
a candidate. Seeded restarts share one proof-attempt budget and preserve baseline.
"""
import hashlib
import json
import math
import random
import time

FEATURES = ('relative_cost', 'steps', 'conditions', 'nodes', 'depth')


def features(candidate, initial):
    score, bodies, applications, _, law, _ = candidate
    nodes = depth = 0
    def walk(t, d=0):
        nonlocal nodes, depth
        nodes += 1; depth = max(depth, d)
        n = t['node']
        children = n.get('Op', [None, []])[1] if isinstance(n, dict) and 'Op' in n else [n['Lambda']] if isinstance(n, dict) and 'Lambda' in n else n['Cons'] if isinstance(n, dict) and 'Cons' in n else []
        for child in children: walk(child, d + 1)
    for body in bodies.values(): walk(body)
    return [math.log1p(max(0, score) / max(1, initial)), math.log1p(len(applications)),
            math.log1p(len(law['conditions'])), math.log1p(nodes), math.log1p(depth)]


class Policy:
    def __init__(self, strategy='stochastic', seed=0, scorer=None):
        if strategy != 'stochastic' or type(seed) is not int:
            raise ValueError('search policy')
        self.strategy=strategy; self.seed=seed; self.rng=random.Random(seed)
        self.rows=[]
        self.scorer=scorer;self.scorer_failures=[]
    def order(self, pending, initial, round_index):
        if not pending: return pending
        ranked=[]
        batch=[features(c,initial) for c in pending];scores=None
        if self.scorer is not None:
            try:scores=self.scorer.score(batch)
            except Exception as error:self.scorer_failures.append(str(error))
        for j,candidate in enumerate(pending):
            utility=.5/(1+max(0,candidate[0])/max(1,initial)+len(candidate[2])*.05)
            if scores is not None:utility/=1+scores[j]/(1024*1024)
            noise=-math.log(max(1e-15,self.rng.random()))
            ranked.append((noise/max(1e-9,utility),candidate))
        ranked.sort(key=lambda x:x[0]); ordered=[x[1] for x in ranked]
        # Every other expansion reserves the ordinary cheapest proposal. Other
        # expansions explore even with width=1; restart zero is ordinary search.
        if round_index % 2 == 0:
            first=pending[0]; ordered=[first]+[x for x in ordered if x is not first]
        return ordered
    def check(self, candidate, initial, callback, proposal):
        start=time.perf_counter_ns(); accepted=bool(callback(proposal))
        self.rows.append(dict(features=features(candidate,initial),accepted=accepted,
            elapsed_ns=time.perf_counter_ns()-start,seed=self.seed,
            proposal_sha256=hashlib.sha256(json.dumps(proposal,sort_keys=True,separators=(',',':')).encode()).hexdigest()))
        return accepted


def search_restarts(search, subject, catalogue, check, *, budget=128, restarts=4, strategy='stochastic', seed=0, scorer=None, **options):
    if not 0 <= budget <= (1_000_000 if strategy=='baseline' else 4096) or not 1 <= restarts <= 16: raise ValueError('restart proof budget')
    if strategy not in ('baseline','stochastic'): raise ValueError('search strategy')
    if strategy=='baseline': restarts=1
    best=None; receipts=[]; rows=[]; remaining=budget
    for i in range(restarts):
        quota=remaining//(restarts-i)
        policy=None if strategy=='baseline' or (i==0 and restarts>1) else Policy(strategy,seed+i,scorer=scorer)
        run_options=dict(options)
        if 'engine_strategy' in run_options:run_options['strategy']=run_options.pop('engine_strategy')
        if i==0 and restarts>1:run_options['neighbourhood']=None
        apps,receipt=search(subject,catalogue,check,budget=quota,policy=policy,**run_options)
        remaining-=receipt['attempts']; receipts.append(receipt)
        if policy is not None:
            rows.extend(policy.rows);receipt['selfhost_failures']=policy.scorer_failures
        if best is None or receipt['selected_cost']<best[1]['selected_cost']: best=(apps,receipt)
    result=dict(best[1]); result.update(attempts=sum(r['attempts'] for r in receipts),
        rejected=sum(r['rejected'] for r in receipts),generated_proposals=sum(r['generated_proposals'] for r in receipts),
        search_policy=dict(strategy=strategy,seed=seed,restarts=restarts,budget=budget,runs=receipts),check_rows=rows)
    return best[0],result
