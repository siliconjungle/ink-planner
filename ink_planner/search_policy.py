"""Optional learned/stochastic proposal ordering. No semantic authority.

Models predict admission utility, not truth. Only the caller's checker can admit
a candidate. Seeded restarts share one proof-attempt budget and preserve baseline.
"""
import argparse
import hashlib
import json
import math
import random
import time
from pathlib import Path

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


def validate_model(model):
    required={'schema', 'features', 'weights', 'intercept', 'training_sha256', 'rows'}
    if not isinstance(model, dict) or not required<=set(model) or set(model)-required not in (set(),{'effort_weights','effort_intercept'}):
        raise ValueError('search model fields')
    if model['schema'] != 1 or model['features'] != list(FEATURES) or len(model['weights']) != len(FEATURES):
        raise ValueError('search model schema')
    if any(type(x) not in (int, float) or not math.isfinite(x) or abs(x) > 1e6 for x in [model['intercept'], *model['weights']]):
        raise ValueError('search model finite weights')
    if type(model['rows']) is not int or not 1 <= model['rows'] <= 100000:
        raise ValueError('search model training rows')
    if not isinstance(model['training_sha256'], str) or len(model['training_sha256']) != 64 or any(c not in '0123456789abcdef' for c in model['training_sha256']):
        raise ValueError('search model training digest')
    if 'effort_weights' in model and (len(model['effort_weights'])!=len(FEATURES) or any(type(x) not in (int,float) or not math.isfinite(x) or abs(x)>1e6 for x in [model['effort_intercept'],*model['effort_weights']])):
        raise ValueError('search effort model')
    return model


def fit(rows, epochs=100):
    if not 1 <= len(rows) <= 100000 or not 1 <= epochs <= 1000: raise ValueError('training budget')
    for row in rows:
        if len(row['features']) != len(FEATURES) or type(row['accepted']) is not bool or any(type(x) not in (int,float) or not math.isfinite(x) or not 0 <= x <= 100 for x in row['features']):
            raise ValueError('search training row')
    weights = [0.0] * len(FEATURES); intercept = 0.0
    for _ in range(epochs):
        gradients = [0.0] * len(FEATURES); bias = 0.0
        for row in rows:
            z = max(-40, min(40, intercept + sum(w*x for w,x in zip(weights,row['features']))))
            error = 1/(1+math.exp(-z)) - row['accepted']; bias += error
            for i,x in enumerate(row['features']): gradients[i] += error*x
        intercept -= .05*bias/len(rows)
        weights = [w-.05*(g/len(rows)+.01*w) for w,g in zip(weights,gradients)]
    model=dict(schema=1,features=list(FEATURES),weights=weights,intercept=intercept,
        training_sha256=hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest(),rows=len(rows))
    if all(type(r.get('elapsed_ns')) is int and 0<r['elapsed_ns']<=10**15 for r in rows):
        bias=sum(math.log(r['elapsed_ns']) for r in rows)/len(rows);effort=[0.0]*len(FEATURES)
        for _ in range(epochs):
            gradient=[0.0]*len(FEATURES);delta=0.0
            for row in rows:
                error=bias+sum(w*x for w,x in zip(effort,row['features']))-math.log(row['elapsed_ns']);delta+=error
                for i,x in enumerate(row['features']):gradient[i]+=error*x
            bias-=.005*delta/len(rows);effort=[w-.005*(g/len(rows)+.01*w) for w,g in zip(effort,gradient)]
        model.update(effort_weights=effort,effort_intercept=bias)
    return validate_model(model)


class Policy:
    def __init__(self, strategy='stochastic', seed=0, model=None, explore=.2, scorer=None):
        if strategy not in ('stochastic','learned') or type(seed) is not int or not 0 <= explore <= 1:
            raise ValueError('search policy')
        self.strategy=strategy; self.seed=seed; self.rng=random.Random(seed)
        self.model=json.loads(json.dumps(validate_model(model))) if model is not None else None
        self.explore=explore; self.rows=[]
        self.scorer=scorer;self.scorer_failures=[]
    def order(self, pending, initial, round_index):
        if not pending: return pending
        ranked=[]
        batch=[features(c,initial) for c in pending];scores=None
        if self.scorer is not None:
            try:scores=self.scorer.score(batch)
            except Exception as error:self.scorer_failures.append(str(error))
        for j,candidate in enumerate(pending):
            xs=batch[j]
            probability=.5
            if self.model is not None:
                z=max(-40,min(40,self.model['intercept']+sum(w*x for w,x in zip(self.model['weights'],xs))))
                probability=1/(1+math.exp(-z))
            utility=probability/(1+max(0,candidate[0])/max(1,initial)+len(candidate[2])*.05)
            if self.model is not None and 'effort_weights' in self.model:
                effort=self.model['effort_intercept']+sum(w*x for w,x in zip(self.model['effort_weights'],xs))
                utility/=math.exp(max(0,min(40,effort)))/1e6
            if scores is not None:utility/=1+scores[j]/(1024*1024)
            noise=-math.log(max(1e-15,self.rng.random())) if self.strategy=='stochastic' or self.rng.random()<self.explore else 1.0
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


def search_restarts(search, subject, catalogue, check, *, budget=128, restarts=4, strategy='stochastic', seed=0, model=None, scorer=None, **options):
    if not 0 <= budget <= (1_000_000 if strategy=='baseline' else 4096) or not 1 <= restarts <= 16: raise ValueError('restart proof budget')
    if strategy not in ('baseline','stochastic','learned'): raise ValueError('search strategy')
    if strategy=='learned' and model is None: raise ValueError('learned search requires a model')
    if model is not None: validate_model(model)
    if strategy=='baseline': restarts=1
    best=None; receipts=[]; rows=[]; remaining=budget
    for i in range(restarts):
        quota=remaining//(restarts-i)
        policy=None if strategy=='baseline' or (i==0 and restarts>1) else Policy(strategy,seed+i,model,scorer=scorer)
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
        search_policy=dict(strategy=strategy,seed=seed,restarts=restarts,budget=budget,runs=receipts),training_rows=rows)
    return best[0],result


def main():
    p=argparse.ArgumentParser();p.add_argument('--rows',type=Path,required=True);p.add_argument('-o',type=Path,required=True)
    p.add_argument('--epochs',type=int,default=100);p.add_argument('--knowledge-root',type=Path)
    a=p.parse_args();data=json.loads(a.rows.read_text());rows=data['rows'] if isinstance(data,dict) else data;model=fit(rows,a.epochs)
    a.o.parent.mkdir(parents=True,exist_ok=True);a.o.write_text(json.dumps(model,indent=2)+'\n')
    if a.knowledge_root:
        from ink_knowledge.search_artifacts import publish
        observation=publish(a.knowledge_root,'observations',dict(kind='search-training',rows=rows))
        ident=publish(a.knowledge_root,'models',model,[observation]);print(json.dumps(dict(model=ident,observation=observation)))


if __name__=='__main__': main()
