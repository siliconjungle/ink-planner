#!/usr/bin/env python3
"""External literal call partitioning. Core checks source equality, not this tool.
Placement is explicit candidate input; no profile establishes a semantic fact.
"""
import argparse, json, subprocess, tempfile
from pathlib import Path

SEMANTICS = 'ink-literal-source-routing-v1'
def produce(core, identity, entry, placements, baseline=False):
    functions={f['name']:f for f in core['program']['functions']}
    f=functions[entry]
    if any(k not in functions or v not in ('cpu','gpu') for k,v in placements.items()): raise ValueError('invalid placement configuration')
    inputs={name: i for i,(name,_) in enumerate(f['params'])}
    stages=[];memo={}
    def reference(expr,want):
        if isinstance(expr,dict) and 'Var' in expr and expr['Var'] in inputs:
            return {'Input':inputs[expr['Var']]}
        if isinstance(expr,dict) and 'Num' in expr and want in ('U32','U64'):
            n=expr['Num']
            return {'Literal':{want:str(n) if want=='U64' else n}}
        if isinstance(expr,dict) and 'Bool' in expr and want=='Bool':
            return {'Literal':{'Bool':expr['Bool']}}
        if not isinstance(expr,dict) or 'Call' not in expr: raise ValueError('not a literal call composition')
        name,args=expr['Call']
        if name not in functions: raise ValueError('builtin or unsupported composition')
        callee=functions[name]
        if len(args)!=len(callee['params']): raise ValueError('arity mismatch')
        refs=[reference(a,t) for a,(_,t) in zip(args,callee['params'])]
        key=json.dumps([name,refs],sort_keys=True,separators=(',',':'))
        if key in memo:return memo[key]
        if len(stages)>=32: raise ValueError('stage budget exhausted')
        id=f's{len(stages)}'
        mode=placements.get(name,'cpu')
        if mode not in ('cpu','gpu'): raise ValueError('placement must be cpu or gpu')
        stages.append(dict(id=id,function=name,backend='gpu' if mode=='gpu' else 'c',domain=mode,arguments=refs))
        memo[key]={'Stage':id}
        return memo[key]
    try:
        if baseline: raise ValueError('explicit baseline')
        output=reference(f['body'],f['result'])
    except ValueError:
        # Failure/exhaustion retains unchanged compiled entry, not a partial plan.
        stages=[dict(id='whole',function=entry,backend='c',domain='cpu',arguments=[{'Input':i} for i in range(len(f['params']))])]
        output={'Stage':'whole'}
    return dict(schema=1,semantics=SEMANTICS,input_core_sha256=identity,entry=entry,stages=stages,output=output)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--core',required=True,type=Path);p.add_argument('--entry',required=True)
    p.add_argument('--proof',type=Path,help='general semantic equivalence evidence for proved routing');p.add_argument('--baseline',action='store_true');p.add_argument('--placements',type=Path);p.add_argument('--compiler',required=True,type=Path)
    p.add_argument('-o','--output',required=True,type=Path)
    p.add_argument('--check-timeout-seconds',type=int,default=10)
    a=p.parse_args()
    if not 1<=a.check_timeout_seconds<=60:raise ValueError('checking time budget outside 1..60')
    def bounded(path,limit):
        with path.open('rb') as f:data=f.read(limit+1)
        if len(data)>limit:raise ValueError('input exceeds budget')
        return data
    compiler=a.compiler.resolve();core=a.core.resolve()
    checked=json.loads(subprocess.check_output([str(compiler),'check-core',str(core)],text=True,timeout=a.check_timeout_seconds))
    candidate=produce(json.loads(bounded(core,16_000_000)),checked['core_sha256'],a.entry,json.loads(bounded(a.placements,1_000_000)) if a.placements else {}, a.baseline)
    if a.proof: candidate['semantics']='ink-proved-source-routing-v1'
    with tempfile.TemporaryDirectory(prefix='ink-source-route-') as directory:
        path=Path(directory)/'route.json';path.write_text(json.dumps(candidate,sort_keys=True,indent=2)+'\n')
        command=[str(compiler),'check-source-route',str(core),str(path)]
        if a.proof: command += ['--route-proof',str(a.proof.resolve())]
        receipt=json.loads(subprocess.check_output(command,text=True,timeout=a.check_timeout_seconds))
        a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_bytes(path.read_bytes())
    print(json.dumps(dict(status=receipt['status'],entry=a.entry,stages=len(candidate['stages']),source='external literal partitioning; explicit placements; no profitability claim')))
if __name__=='__main__':main()
