"""Offline packaging and empirical profiles for opt-in state adaptation.

Checks complete plans before emission. Cost samples are evidence, not proof.
No runtime search/fitting and no new representation authority.
"""
import argparse, hashlib, json, math, re, shutil, subprocess, tempfile
from pathlib import Path

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path,limit=4_000_000):
    raw=path.read_bytes()
    if len(raw)>limit:raise ValueError('adaptive input byte budget')
    return json.loads(raw)
def write(path,value):path.write_text(json.dumps(value,indent=2,allow_nan=False)+'\n')
DEFAULT_POLICY=dict(horizon_calls=4096,min_residence_calls=1024,stable_windows=3,min_gain=.15,migration_margin=2.,memory_limit_bytes=256*1024*1024,max_pause_ns=50_000_000.,max_switches=32)

def fit(bundle,observations,policy=None):
    if observations.get('binding')!=bundle['binding']:raise ValueError('adaptive measurement binding mismatch')
    variants={}
    def number(n):
        if isinstance(n,bool)or not isinstance(n,(int,float))or not math.isfinite(n)or not 0<=n<=1e15:raise ValueError('invalid timing sample')
        return n
    def samples(xs):
        if not isinstance(xs,list)or not 3<=len(xs)<=4096:raise ValueError('at least three bounded samples required')
        return [number(n)for n in xs]
    def peak(n):
        if type(n)is not int or not 1<=n<=9_007_199_254_740_991:raise ValueError('measured memory required')
        return n
    if set(observations['variants'])!=set(bundle['binding']['variants']):raise ValueError('variant measurement mismatch')
    for key,v in observations['variants'].items():
        if not isinstance(v['cells'],dict)or len(v['cells'])>256:raise ValueError('cell budget')
        cells={}
        for cell,xs in v['cells'].items():
            if not re.fullmatch(r'[A-Za-z0-9:_.,@\-]{1,256}',cell):raise ValueError('cost cell name')
            ns=samples(xs);cells[cell]=dict(low_ns=min(ns),high_ns=max(ns))
        variants[key]=dict(cells=cells,peak_bytes=peak(v['peak_bytes']))
    migrations=[];edges=set()
    if len(observations['migrations'])>240:raise ValueError('migration budget')
    for m in observations['migrations']:
        edge=(m['from'],m['to'])
        if edge in edges or edge[0]==edge[1]or any(n not in variants for n in edge):raise ValueError('migration endpoints')
        edges.add(edge);ns=samples(m['samples_ns'])
        lo,hi=m['min_state_elements'],m['max_state_elements']
        if type(lo)is not int or type(hi)is not int or not 0<=lo<=hi<=9_007_199_254_740_991:raise ValueError('migration state domain')
        migrations.append(dict(zip(('from','to'),edge),high_ns=max(ns),max_pause_ns=max(ns),peak_bytes=peak(m['peak_bytes']),min_state_elements=lo,max_state_elements=hi))
    result=dict(bundle);result['profile']=dict(binding=bundle['binding'],variants=variants,migrations=migrations)
    result['policy']={**DEFAULT_POLICY,**(policy or {})}
    result['measurement_scope']='Empirical extrema, not confidence intervals or future latency/memory guarantees. Exact workload cells only.'
    return result

def prepare(compiler,source,variants,target_key,output):
    compiler=compiler.resolve();source=source.resolve();spec=read(variants)
    if not isinstance(spec,list)or not 1<=len(spec)<=16:raise ValueError('variant budget')
    if output.exists():raise ValueError('output already exists')
    if not isinstance(target_key,str)or not 1<=len(target_key)<=256 or not target_key.isascii():raise ValueError('target provenance key')
    def run(*args):
        p=subprocess.run([str(compiler),*map(str,args)],capture_output=True,text=True,timeout=300)
        if p.returncode:raise ValueError(p.stderr[-4000:])
        return json.loads(p.stdout)if p.stdout.strip()else None
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent)as tmp:
        root=Path(tmp);core=root/'source.json';run('emit-core',source,'-o',core)
        checked=run('check-core',core);source_id=checked['core_sha256'];artifacts=[];ids={}
        for item in spec:
            ident=item['id']
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',ident)or ident in ids:raise ValueError('variant ID')
            packet=read(variants.parent/item['plan'],64_000_000)
            if packet.get('input_core_sha256')!=source_id:raise ValueError('plan source mismatch')
            plan=root/(ident+'.plan.json');write(plan,packet)
            run('check-plan',core,plan,'--core','--target','javascript')
            artifact=root/(ident+'.mjs');run('emit-plan',core,plan,'--core','--target','javascript','-o',artifact)
            ids[ident]=digest(artifact);artifacts.append(dict(id=ident,file=artifact.name,plan=plan.name))
        distribution=Path(__file__).resolve().parents[2]
        runtime=distribution/'runtime';abi=distribution/'abi'
        for name in ('adaptive-state.mjs','adaptive-profile.mjs','usage-window.mjs','adaptive-collections.mjs','objective.mjs'):shutil.copyfile(runtime/'hosts'/name,root/name)
        shutil.copyfile(abi/'src/selection.mjs',root/'ink-selection.mjs');shutil.copyfile(abi/'selection.wasm',root/'ink-selection.wasm')
        bundle=dict(schema=1,kind='ink-adaptive-state-v1',binding=dict(source=source_id,compiler=digest(compiler),target=target_key,boundary='owned-serial-state-v1',variants=ids),artifacts=artifacts,baseline=spec[0]['id'])
        write(root/'bundle.json',bundle);shutil.copytree(root,output)
    return bundle

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    a=sub.add_parser('prepare')
    for name in ('compiler','source','variants'):a.add_argument('--'+name,type=Path,required=True)
    a.add_argument('--target-key',required=True);a.add_argument('-o',type=Path,required=True)
    a=sub.add_parser('fit');a.add_argument('--bundle',type=Path,required=True);a.add_argument('--observations',type=Path,required=True);a.add_argument('--policy',type=Path);a.add_argument('-o',type=Path,required=True)
    a=p.parse_args()
    if a.command=='prepare':prepare(a.compiler,a.source,a.variants,a.target_key,a.o)
    else:write(a.o,fit(read(a.bundle),read(a.observations),read(a.policy)if a.policy else None))
if __name__=='__main__':main()
