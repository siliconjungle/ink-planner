"""Offline model fitting. Hosts execute profiles and never conduct search trials."""
import json,math,statistics
from ink_knowledge.cost_models import publish_model,load_model
from ink_knowledge.store import Store,read,identity,hash_id
from .joint_domain import derive
FEATURES=('input_elements','input_bytes','output_capacity_elements','dispatches','iterations')


def fit(store,rows,target_key):
    """Rows bind an immutable observation to exact compiled scope and boundary.

    Features are measured workload provenance, never sampled application values.
    Exact joint convex support restricts prediction to observed combinations and
    their interpolations. This does not establish timing accuracy or confidence.
    """
    if not isinstance(target_key,str)or not target_key or not isinstance(rows,list)or not rows or len(rows)>4096:raise ValueError('invalid fitting request')
    groups={};refs=[];cohorts={}
    for row in rows:
        id=hash_id(row['observation']);observation=read(store.data/'observations'/f'{id}.json');Store.validate_observation(observation)
        if identity(observation)!=id:raise ValueError('observation hash mismatch')
        backend=observation['target']['backend']
        if backend not in ('cpu','javascript','gpu'):raise ValueError('unsupported observed backend')
        shape=observation['workload']['shape']
        features=shape.get('features')if isinstance(shape,dict)else None
        if not isinstance(features,dict)or not features or set(features)-set(FEATURES):raise ValueError('observation requires explicit workload features')
        if any(type(n)not in(int,float)or not math.isfinite(n)or not 0<=n<=1e12 for n in features.values()):raise ValueError('invalid observed feature')
        boundary=observation['workload']['residency']
        if boundary not in ('captured-owned','resident-input-owned-readback'):raise ValueError('unsupported measured boundary')
        scope=row['scope']
        if not isinstance(scope,str)or not 0<len(scope)<=4096:raise ValueError('invalid compiled scope')
        key=(scope,boundary,backend)
        # Only the declared fitted features may vary within a backend model.
        # A scope label cannot make different binaries, machines or workload
        # protocols comparable. Different backends may have different drivers
        # and toolchains, so retain one exact provenance cohort per backend.
        cohort=identity(dict(program=observation['program'],plan=observation['plan'],
            entries=observation['entries'],target=observation['target'],
            workload={k:({n:v for n,v in shape.items()if n!='features'}if k=='shape'else v)
                      for k,v in observation['workload'].items()}))
        if key in cohorts and cohorts[key]!=cohort:
            raise ValueError('incomparable observation provenance for compiled scope/backend')
        cohorts[key]=cohort
        groups.setdefault(key,[]).append((features,statistics.median(int(n)for n in observation['metrics']['complete_call_ns'])))
        refs.append(id)
    models={}
    for(scope,boundary,backend),points in sorted(groups.items()):
        names=sorted(points[0][0])
        if any(sorted(f)!=names for f,_ in points):raise ValueError('incomparable feature schemas')
        scales=[max(1,max(f[n]for f,_ in points))for n in names]
        xs=[[1.]+[f[n]/scale for n,scale in zip(names,scales)]for f,_ in points];ys=[float(y)for _,y in points]
        weights=[0.]*(1+len(names))
        # Bounded nonnegative coordinate descent; no theorem or extrapolation claim.
        for _ in range(256):
            for j in range(len(weights)):
                numerator=sum(x[j]*(y-sum(w*v for k,(w,v)in enumerate(zip(weights,x))if k!=j))for x,y in zip(xs,ys));denominator=sum(x[j]**2 for x in xs)
                weights[j]=max(0.,numerator/denominator)if denominator else 0.
        terms={n:w/scale for n,w,scale in zip(names,weights[1:],scales)}
        if weights[0]>1e12 or any(w>1e12 for w in terms.values()):raise ValueError('profile coefficient budget')
        model=models.setdefault(scope,dict(boundary=boundary,domains={},backends={}))
        if model['boundary']!=boundary:raise ValueError('one boundary per compiled scope')
        domain={n:[min(f[n]for f,_ in points),max(f[n]for f,_ in points)]for n in names}
        if model['domains']:
            if set(model['domains'])!=set(domain):raise ValueError('incomparable backend features')
            domain={n:[max(model['domains'][n][0],d[0]),min(model['domains'][n][1],d[1])]for n,d in domain.items()}
            if any(lo>hi for lo,hi in domain.values()):raise ValueError('backend observation domains do not overlap')
        model['domains']=domain;model['backends'][backend]=dict(intercept_ns=weights[0],terms=terms,
            support=derive(names,[features for features,_ in points]))
    profile=dict(schema=3,target_key=target_key,models=models)
    id=publish_model(store,profile,refs);return id,load_model(store,id)


def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--knowledge-root',required=True);p.add_argument('--rows',required=True);p.add_argument('--target-key',required=True);p.add_argument('-o',required=True);a=p.parse_args()
    store=Store(a.knowledge_root);rows=json.loads(read_bytes(a.rows));id,profile=fit(store,rows,a.target_key)
    from pathlib import Path
    Path(a.o).write_text(json.dumps(profile,sort_keys=True,separators=(',',':'))+'\n');print(json.dumps(dict(model=id,observations=len(rows),authority='profitability only')))

def read_bytes(path):
    from pathlib import Path
    raw=Path(path).read_bytes()
    if len(raw)>4_000_000:raise ValueError('fitting request byte budget')
    return raw
if __name__=='__main__':main()
