"""Seeded, bounded word-expression synthesis with exact checker admission.

Random observations propose affine models; stochastic expressions broaden search.
Fingerprints and counterexamples only reject candidates. Every accepted replacement
must carry an ordinary Ink proof and pass check-selection for the original core.
"""
import argparse
import hashlib
import json
import random
import subprocess
import tempfile
from pathlib import Path

MASK=(1<<32)-1
SORT={'Value':'U32'}


def word(n):return dict(sort=SORT,node={'Word':n&MASK})
def variable(n):return dict(sort=SORT,node={'Free':n})
def binary(op,a,b):return dict(sort=SORT,node={'Op':['binary:'+op,[a,b]]})


def evaluate(term,values,depth=0):
    if depth>64 or term['sort']!=SORT:raise ValueError('synthesis supports bounded pure u32 arithmetic')
    n=term['node']
    if 'Word' in n:return n['Word']&MASK
    if 'Free' in n:return values[n['Free']]&MASK
    if 'Op' not in n or len(n['Op'][1])!=2:raise ValueError('unsupported synthesis expression')
    op,args=n['Op'];a,b=(evaluate(x,values,depth+1) for x in args)
    if op=='binary:+':return (a+b)&MASK
    if op=='binary:-':return (a-b)&MASK
    if op=='binary:*':return (a*b)&MASK
    raise ValueError('unsupported synthesis operation')


def node_count(term):
    return 1+sum(node_count(x) for x in term['node'].get('Op',[None,[]])[1])


def word_program(term,name):
    def walk(t):
        n=t['node']
        if n=={'Free':name}:return []
        if 'Word' in n:return [(1,0),(0,n['Word'])]
        op,args=n['Op'];a,b=args
        if 'Word' not in b['node'] and op in ('binary:+','binary:*'):a,b=b,a
        if 'Word' not in b['node']:raise ValueError('word VM requires one live register')
        return walk(a)+[({'binary:+':0,'binary:*':1,'binary:-':2}[op],b['node']['Word'])]
    code=walk(term)
    if len(code)>256:raise ValueError('word VM instruction capacity')
    return [x for pair in code+[(0,0)]*(256-len(code)) for x in pair]


def synthesize(source,params,check,*,seed=0,budget=256,proof_budget=16,evaluator=None):
    if not 1<=budget<=4096 or not 0<=proof_budget<=128 or len(params)>16 or any(s!=SORT for s in params.values()):raise ValueError('synthesis budget/sorts')
    rng=random.Random(seed);names=sorted(params)
    fixtures=[{n:v for n in names} for v in (0,1,2,3,MASK,MASK-1,1<<31)]
    fixtures += [{n:rng.getrandbits(32) for n in names} for _ in range(32)]
    expected=[evaluate(source,x) for x in fixtures]
    fingerprint=lambda xs:hashlib.sha256(b''.join(x.to_bytes(4,'little') for x in xs)).hexdigest()
    expected_hash=fingerprint(expected);atoms=[variable(n) for n in names]+[word(n) for n in range(9)]
    zero={n:0 for n in names};constant=evaluate(source,zero);affine=word(constant)
    for name in names:
        probe=dict(zero);probe[name]=1;coefficient=(evaluate(source,probe)-constant)&MASK
        if coefficient:
            term=variable(name) if coefficient==1 else binary('*',variable(name),word(coefficient))
            affine=term if affine==word(0) else binary('+',affine,term)
    proposals=[affine,*atoms];seen=set();best=source;checks=0;counterexamples=[];attempts=[];selfhost_failures=[]
    for i in range(budget):
        if i<len(proposals):candidate=proposals[i]
        else:
            pool=atoms+[best,affine];candidate=binary(rng.choice(['+','-','*']),rng.choice(pool),rng.choice(pool))
        encoded=json.dumps(candidate,sort_keys=True,separators=(',',':'))
        if encoded in seen or node_count(candidate)>=node_count(best):continue
        seen.add(encoded)
        actual=[evaluate(candidate,x) for x in fixtures]
        if evaluator is not None and len(names)==1:
            try:
                remote=evaluator.evaluate([x[names[0]] for x in fixtures],word_program(candidate,names[0]))
                if remote!=actual:raise ValueError('word VM disagrees with independent evaluator')
                actual=remote
            except Exception as error:selfhost_failures.append(str(error))
        if fingerprint(actual)!=expected_hash or actual!=expected:
            mismatch=next(k for k,(a,b) in enumerate(zip(actual,expected)) if a!=b)
            counterexamples.append(dict(input=fixtures[mismatch],expected=expected[mismatch],actual=actual[mismatch]));continue
        if checks>=proof_budget:break
        checks+=1;accepted=bool(check(candidate));attempts.append(dict(candidate=candidate,accepted=accepted))
        if accepted:best=candidate
    return best,dict(schema=1,seed=seed,budget=budget,proof_budget=proof_budget,proof_attempts=checks,
        generated=len(seen),counterexamples=counterexamples,attempts=attempts,
        initial_nodes=node_count(source),selected_nodes=node_count(best),
        selfhost_failures=selfhost_failures,authority='samples/fingerprints reject only; ordinary source-bound proof replay admits every replacement')


def main():
    p=argparse.ArgumentParser();p.add_argument('--compiler',type=Path,required=True);p.add_argument('--core',type=Path,required=True)
    p.add_argument('--function',required=True);p.add_argument('--seed',type=int,default=0);p.add_argument('--budget',type=int,default=256)
    p.add_argument('--proof-budget',type=int,default=16);p.add_argument('--output-store',type=Path,required=True);p.add_argument('-o',type=Path,required=True)
    p.add_argument('--selfhost-endpoint')
    a=p.parse_args();compiler=a.compiler.resolve();core=a.core.resolve()
    compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest();core_hash=hashlib.sha256(core.read_bytes()).hexdigest()
    from ink_knowledge import Store,semantic_entry,identity
    with tempfile.TemporaryDirectory(prefix='ink-synthesis-') as tmp:
        tmp=Path(tmp);subject_path=tmp/'subject.json'
        subprocess.run([compiler,'emit-semantic',core,'--core','-o',subject_path],check=True,capture_output=True,timeout=60)
        subject=json.loads(subject_path.read_text());source=subject['functions'][a.function]
        names=set()
        def variables(t):
            n=t['node']
            if 'Free' in n:names.add(n['Free'])
            for child in n.get('Op',[None,[]])[1]:variables(child)
        variables(source);params={n:SORT for n in sorted(names)}
        accepted={};last_package=None
        def checker(candidate):
            nonlocal last_package
            law=dict(schema=1,semantics='ink-conditional-laws-v1',dependencies=[],params=params,
                conditions=[],**{'from':source,'to':candidate},proof='Normalize')
            obj=semantic_entry(law);ident=identity(obj);store=Store.publish(tmp/'proposal',[obj])
            package=dict(schema=1,semantics='ink-evidence-v1',input_core_sha256=subject['input_core_sha256'],
                knowledge=store.bundle([ident]),applications=[dict(function=a.function,path=[],law=ident,
                    arguments={n:variable(n) for n in params},premises=[])])
            path=tmp/'selection.json';path.write_text(json.dumps(package))
            result=subprocess.run([compiler,'check-selection',core,path],capture_output=True,text=True,timeout=30)
            if result.returncode:return False
            accepted[ident]=obj;last_package=package;return True
        from ink_planner.selfhost import Scorer
        evaluator=Scorer(a.selfhost_endpoint) if a.selfhost_endpoint else None
        candidate,receipt=synthesize(source,params,checker,seed=a.seed,budget=a.budget,proof_budget=a.proof_budget,evaluator=evaluator)
        provisional=Store.publish(tmp/'accepted',accepted.values())
        receipt['core_sha256']=subject['input_core_sha256'];receipt['compiler_sha256']=hashlib.sha256(compiler.read_bytes()).hexdigest()
        if last_package is None:
            last_package=dict(schema=1,semantics='ink-evidence-v1',input_core_sha256=subject['input_core_sha256'],knowledge=provisional.bundle([]),applications=[])
        final=tmp/'final.json';final.write_text(json.dumps(last_package,indent=2)+'\n')
        subprocess.run([compiler,'check-selection',core,final],check=True,capture_output=True,timeout=60)
        if hashlib.sha256(compiler.read_bytes()).hexdigest()!=compiler_hash or hashlib.sha256(core.read_bytes()).hexdigest()!=core_hash:raise ValueError('synthesis inputs changed during checking')
        published=Store.publish(a.output_store,accepted.values());receipt['snapshot']=published.snapshot_id
        a.o.parent.mkdir(parents=True,exist_ok=True);a.o.write_bytes(final.read_bytes())
        a.o.with_suffix('.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        print(json.dumps({k:v for k,v in receipt.items() if k not in ('attempts','counterexamples')}))


if __name__=='__main__':main()
