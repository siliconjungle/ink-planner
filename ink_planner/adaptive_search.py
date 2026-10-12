"""Offline-evidence selection of bounded search approaches; no online trials.

Only exactly observed source/catalogue/toolchain/target cohorts are comparable.
Missing, stale, lossy or marginal evidence selects ordinary search. This is a
compile-time wrapper; source-bound checker admission remains in the child planner.
"""
import argparse
import hashlib
import json
import statistics
import subprocess
import sys
from pathlib import Path

STRATEGIES = {'baseline','stochastic','learned','parallel','hnsw','accelerated-discovery'}


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def code_identity():
    import ink_knowledge,producers
    rows={}
    for label,root in [('planner',Path(__file__).parent),('knowledge',Path(ink_knowledge.__file__).parent),('producers',Path(producers.__file__).parent)]:
        for path in sorted(root.rglob('*')):
            if path.is_file() and path.suffix in ('.py','.ink','.mjs','.json','.gz'):rows[label+'/'+str(path.relative_to(root))]=digest(path)
    return hashlib.sha256(json.dumps(rows,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def select(profile, context, features):
    fallback=dict(strategy='baseline',reason='missing comparable heldout evidence')
    try:
        common={k:v for k,v in context.items() if k not in ('core','snapshot')}
        inputs={k:context.get(k) for k in ('core','snapshot')}
        if profile['schema']!=1 or profile['context']!=common:return fallback
        cohorts=profile['cohorts']
        if not isinstance(cohorts,list) or not 1<=len(cohorts)<=256:return fallback
        matching=[c for c in cohorts if c['features']==features and c['inputs']==inputs]
        if len(matching)!=1:return fallback
        cohort=matching[0];rows=cohort['rows']
        if not isinstance(rows,list) or not 1<=len(rows)<=16:return fallback
        def evidence(row):
            if row['strategy'] not in STRATEGIES or row.get('correct') is not True or row.get('fallback',False):raise ValueError('invalid strategy evidence')
            samples=row['training_ns'];heldout=row['heldout']
            if not isinstance(samples,list) or not 1<=len(samples)<=256 or any(type(x) is not int or not 0<x<=10**15 for x in samples):raise ValueError('search timing samples')
            if not isinstance(heldout,list) or not 1<=len(heldout)<=256:raise ValueError('heldout search evidence')
            if any(type(x.get('ns')) is not int or not 0<x['ns']<=10**15 or type(x.get('selected_cost')) not in (int,float) or not 0<=x['selected_cost']<=10**15 for x in heldout):raise ValueError('heldout time/quality')
            return statistics.median(samples)
        if len({r['strategy'] for r in rows})!=len(rows):return fallback
        base=next(r for r in rows if r['strategy']=='baseline');baseline=evidence(base)
        eligible=[]
        for row in rows:
            try:
                cost=evidence(row)
                if row['strategy']=='baseline' or cost>=baseline*.9:continue
                if len(row['heldout'])!=len(base['heldout']):continue
                if all(c['round']==b['round'] and c['ns']<b['ns']*.9 and c['selected_cost']<=b['selected_cost'] for c,b in zip(row['heldout'],base['heldout'])):
                    eligible.append((cost,row['strategy']))
            except (ValueError,TypeError,KeyError,StopIteration):continue
        if eligible:return dict(strategy=min(eligible)[1],reason='observed cohort; >10% complete-call heldout gain without selected-cost loss')
    except (ValueError,TypeError,KeyError,StopIteration):pass
    return fallback


def main():
    p=argparse.ArgumentParser();p.add_argument('--compiler',type=Path,required=True);p.add_argument('--core',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--knowledge-root',type=Path,required=True)
    p.add_argument('--profile',type=Path);p.add_argument('--target-context',type=Path,required=True)
    p.add_argument('--budget',type=int,default=128);p.add_argument('--rounds',type=int,default=12);p.add_argument('--seed',type=int,default=0)
    p.add_argument('--search-model',type=Path);p.add_argument('-o',type=Path,required=True);a=p.parse_args()
    if not 0<=a.budget<=4096 or not 0<=a.rounds<=64:p.error('bounded search budgets required')
    from ink_knowledge import Store
    context=dict(compiler=digest(a.compiler),core=digest(a.core),snapshot=digest(a.snapshot),
        target=digest(a.target_context),code=code_identity(),python=list(sys.version_info[:3]),
        model=None if a.search_model is None else digest(a.search_model))
    core=json.loads(a.core.read_text())
    def count(value):return 1+sum(count(x) for x in value.values()) if isinstance(value,dict) else sum(count(x) for x in value) if isinstance(value,list) else 0
    features=dict(catalogue_entries=len(Store(a.knowledge_root,a.snapshot).members()),core_nodes=count(core),budget=a.budget,rounds=a.rounds)
    profile=None
    if a.profile:
        try:
            if a.profile.stat().st_size>16_000_000:raise ValueError('adaptive profile byte limit')
            profile=json.loads(a.profile.read_text())
        except (OSError,ValueError):pass
    decision=select(profile,context,features);strategy=decision['strategy']
    if strategy in ('learned','parallel') and a.search_model is None:strategy='baseline';decision=dict(strategy=strategy,reason='missing learned model')
    module='ink_planner.parallel_search' if strategy=='parallel' else 'ink_planner.semantic_search'
    command=[sys.executable,'-m',module,'--compiler',str(a.compiler.resolve()),'--core',str(a.core.resolve()),
        '--snapshot',str(a.snapshot.resolve()),'--knowledge-root',str(a.knowledge_root.resolve()),
        '--budget',str(a.budget),'--rounds',str(a.rounds),'-o',str(a.o.resolve())]
    if strategy=='parallel':command+=['--workers','2','--seed',str(a.seed),'--search-model',str(a.search_model.resolve())]
    elif strategy in ('stochastic','learned'):
        command+=['--search-strategy',strategy,'--search-restarts','4','--search-seed',str(a.seed)]
        if strategy=='learned':command+=['--search-model',str(a.search_model.resolve())]
    elif strategy=='hnsw':command+=['--ann-hints']
    elif strategy=='accelerated-discovery':command+=['--discovery-accelerator']
    run=subprocess.run(command,capture_output=True,text=True,timeout=600)
    if run.returncode:raise RuntimeError(run.stderr[-4000:])
    # Child planner has already checked the exact selected source-bound package.
    if any(digest(path)!=context[key] for key,path in [('compiler',a.compiler),('core',a.core),('snapshot',a.snapshot),('target',a.target_context)]):
        raise ValueError('adaptive search inputs changed')
    receipt=json.loads(run.stdout.splitlines()[-1]);receipt['adaptive']=dict(decision=decision,features=features,context=context,
        authority='saved compile-time search hints only; no online profitability trials or proof authority')
    a.o.with_suffix('.adaptive.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':main()
