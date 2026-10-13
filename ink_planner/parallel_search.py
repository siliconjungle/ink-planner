"""Bounded independent planner processes; source-bound proof replay at publication."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--compiler',type=Path,required=True);p.add_argument('--core',type=Path,required=True)
    p.add_argument('--snapshot',type=Path,required=True);p.add_argument('--knowledge-root',type=Path)
    p.add_argument('--workers',type=int,default=2);p.add_argument('--budget',type=int,default=128);p.add_argument('--rounds',type=int,default=12)
    p.add_argument('--seed',type=int,default=0);p.add_argument('--cost-model',type=Path)
    p.add_argument('--discovery-accelerator',action='store_true');p.add_argument('-o',type=Path,required=True)
    a=p.parse_args()
    if not 1<=a.workers<=16 or not 0<=a.budget<=4096 or not 0<=a.rounds<=64:p.error('bounded worker/search budgets required')
    compiler=a.compiler.resolve();core=a.core.resolve();original=hashlib.sha256(compiler.read_bytes()).hexdigest()
    base=[sys.executable,'-m','ink_planner.semantic_search','--compiler',str(compiler),'--core',str(core),'--snapshot',str(a.snapshot.resolve()),'--rounds',str(a.rounds)]
    for flag,path in [('--knowledge-root',a.knowledge_root),('--cost-model',a.cost_model)]:
        if path:base += [flag,str(path.resolve())]
    if a.discovery_accelerator:base += ['--discovery-accelerator']
    with tempfile.TemporaryDirectory(prefix='ink-parallel-planner-') as tmp:
        tmp=Path(tmp)
        def run(i,budget,strategy):
            output=tmp/('worker-'+str(i)+'.json');trace=tmp/('trace-'+str(i)+'.json')
            command=base+['--budget',str(budget),'-o',str(output),'--search-strategy',strategy,'--search-seed',str(a.seed+i),'--search-restarts','1','--search-trace',str(trace)]
            try:
                result=subprocess.run(command,capture_output=True,text=True,timeout=300)
                if result.returncode:return dict(worker=i,error=result.stderr[-4000:],budget=budget)
                receipt=json.loads(result.stdout.splitlines()[-1]);return dict(worker=i,budget=budget,receipt=receipt,path=output)
            except Exception as error:return dict(worker=i,budget=budget,error=str(error))
        baseline=run(-1,0,'baseline')
        if 'error' in baseline:raise ValueError('unchanged baseline failed: '+baseline['error'])
        quotas=[a.budget//a.workers+int(i<a.budget%a.workers) for i in range(a.workers)]
        with ThreadPoolExecutor(max_workers=a.workers) as pool:
            futures=[pool.submit(run,i,b,'baseline' if i==0 else 'stochastic') for i,b in enumerate(quotas)]
            runs=[f.result() for f in futures]
        valid=[baseline]+[r for r in runs if 'receipt' in r]
        best=min(valid,key=lambda r:(r['receipt']['selected_cost'],r['worker']))
        raw=best['path'].read_bytes();selected=tmp/'selected.json';selected.write_bytes(raw)
        subprocess.run([compiler,'check-selection',core,selected],check=True,capture_output=True,timeout=60)
        if hashlib.sha256(compiler.read_bytes()).hexdigest()!=original:raise ValueError('compiler changed during parallel search')
        a.o.parent.mkdir(parents=True,exist_ok=True);a.o.write_bytes(raw)
        receipt=dict(schema=1,compiler_sha256=original,budget=a.budget,workers=a.workers,seed=a.seed,
            selected_worker=best['worker'],initial_cost=baseline['receipt']['initial_cost'],selected_cost=best['receipt']['selected_cost'],
            attempts=sum(r['receipt']['attempts'] for r in runs if 'receipt' in r),
            runs=[{k:v for k,v in r.items() if k!='path'} for r in runs],authority='independent pure search processes; final exact proof replay')
        a.o.with_suffix('.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))


if __name__=='__main__':main()
