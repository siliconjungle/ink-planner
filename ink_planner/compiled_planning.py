"""Recursive offline search over checked, typed implementation states.

All knowledge families can propose edges after every admitted state. A new source
image invalidates stale representations/properties in the independent checker.
Search estimates and memo identities are untrusted; emitted plans replay proofs.
"""
import argparse, atexit, copy, hashlib, heapq, json, os, select, shutil, subprocess, sys, tempfile, time
from collections import deque
from pathlib import Path
from ink_knowledge import Store, canonical, identity
from ink_knowledge.recipes import load, coverage
from ink_knowledge.implementation_candidates import candidate_entry, proof_views
from .implementation_selection import measurement_id
from .representation_selection import rank

SEMANTICS='ink-compiled-plan-v1'

def code_fingerprint():
    import ink_knowledge,producers
    roots=[('knowledge',Path(ink_knowledge.__file__).parent),('producers',Path(producers.__file__).parent),('planner',Path(__file__).parent)]
    return identity({label+'/'+str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for label,root in roots for p in sorted(root.rglob('*'))if p.suffix in {'.py','.json','.gz'}and p.is_file()})


def fingerprint(state):
    facts=set()
    for package in state.get('properties',[]):
        for fact in package['facts']:
            facts.add(identity({k:v for k,v in fact.items()if k!='proof'}))
    return identity(dict(source=state['source'],representation=(state.get('representation')or{}).get('representation'),
        definitions=state.get('definition_choices')or{},physical=state.get('physical'),route=state.get('route'),properties=sorted(facts)))

def explore(initial,expand,check,priority,budget=16384,state_limit=4096,time_seconds=3600,memory_bytes=512_000_000):
    """The callback boundary admits complete proof chains, never hash equality.
    Invalid proofs do not mark their prospective target state as visited.
    """
    started=time.monotonic();first=check(initial)
    states={fingerprint(first):(initial,first)};agenda=[];serial=0;attempts=0;edges=[];rejections=[];retained=len(canonical(initial))+len(canonical(first))
    heapq.heappush(agenda,(priority(first),serial,fingerprint(first)))
    stopped=None
    while agenda:
        if time.monotonic()-started>=time_seconds:stopped='time';break
        _,_,parent=heapq.heappop(agenda);package,state=states[parent]
        for proposal in expand(state,states):
            if time.monotonic()-started>=time_seconds:stopped='time';break
            if attempts>=budget:stopped='proof-attempts';break
            if proposal.get('status')!='proposal':continue
            steps=proposal.get('steps',[proposal.get('step')])
            if not steps or any(step is None for step in steps):continue
            candidate=copy.deepcopy(package);candidate['steps'].extend(steps)
            attempts+=1
            try:checked=check(candidate)
            except (ValueError,OSError,subprocess.TimeoutExpired)as error:
                rejections.append(dict(parent=parent,recipe=proposal.get('recipe'),label=proposal.get('label'),reason=str(error)[-2000:]));continue
            child=fingerprint(checked)
            edges.append(dict(source=parent,target=child,step=identity(steps),kinds=[step['kind']for step in steps],recipe=proposal.get('recipe')))
            if child in states:continue
            size=len(canonical(candidate))+len(canonical(checked))
            if len(states)>=state_limit or retained+size>memory_bytes:
                stopped='states'if len(states)>=state_limit else'memory';break
            retained+=size;states[child]=(candidate,checked);serial+=1
            heapq.heappush(agenda,(priority(checked),serial,child))
        if stopped:break
    return states,dict(attempts=attempts,admitted_states=len(states),edges=edges,rejected=rejections,
        stop_reason=stopped or'fixed-point',remaining=len(agenda),retained_bytes=retained,
        elapsed_seconds=str(time.monotonic()-started),scope='Exact typed states and checked edges; bounded compilation, no runtime optimisation probes')

class Planner:
    def __init__(self,compiler,original,root,directory,recipes,registry_digest,options):
        self.compiler,self.original,self.root,self.directory=compiler,original,root,directory
        self.recipes,self.registry_digest,self.options=recipes,registry_digest,options
        self.snapshot=Store(root,getattr(options,'snapshot',None)).snapshot_id
        self.compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest();self.notes=[];self.models={};self.admissions={};self.serial=0
        self.session=None;self.reused_steps=0;self.recipe_cache_hits=0;atexit.register(self.close)
        self.deadline=time.monotonic()+getattr(options,"time_seconds",getattr(options,"planning_seconds",3600))
        self.code_hash=code_fingerprint()
    def invoke(self,*args,timeout=None):
        result=subprocess.run([str(self.compiler),*map(str,args)],capture_output=True,text=True,timeout=timeout or self.options.check_timeout)
        if result.returncode:raise ValueError(result.stderr[-3000:])
        return json.loads(result.stdout) if result.stdout.lstrip().startswith(('{','[')) else result.stdout
    def close(self):
        if self.session is not None:
            session=self.session;self.session=None
            session.kill();session.wait();session.stdin.close();session.stdout.close();session.stderr.close()
    def receive(self):
        if not select.select([self.session.stdout],[],[],self.options.check_timeout)[0]:
            self.close();raise ValueError('plan checker process time budget')
        line=self.session.stdout.readline(128_000_001)
        if not line.endswith('\n')or len(line)>128_000_000:self.close();raise ValueError('plan checker response byte budget')
        return json.loads(line)
    def check(self,package):
        key=identity(dict(compiler=self.compiler_hash,package=package))
        if key in self.admissions:return self.admissions[key]
        if self.session is None:
            self.session=subprocess.Popen([str(self.compiler),'plan-session',str(self.original),'--target',self.options.target],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            if not self.receive().get('ready'):self.close();raise ValueError('plan checker not ready')
        self.session.stdin.write(canonical(package).decode()+'\n');self.session.stdin.flush()
        receipt=self.receive()
        if receipt.get('error'):raise ValueError(receipt['error'])
        if receipt.get('status')!='checked'or receipt.get('plan_sha256')!=identity(package):raise ValueError('plan checker binding mismatch')
        self.reused_steps+=receipt.get('reused_steps',0)
        state=receipt['state'];state['capability']=receipt.get('capability')or dict(emittable=True);self.admissions[key]=state
        if len(self.admissions)>128:self.admissions.pop(next(iter(self.admissions)))
        return state
    def model(self,state):
        if state['source']not in self.models:
            core=self.directory/(state['source']+'.core.json');core.write_bytes(canonical(state['core'])+b'\n')
            model=self.directory/(state['source']+'.model.json');self.invoke('emit-action-model',core,'--core','-o',model)
            value=json.loads(model.read_bytes())
            if value['source']!=state['source']:raise ValueError('proposal model source mismatch')
            self.models[state['source']]=value
        return self.models[state['source']]
    def expand(self,state,states):
        try:model=self.model(state)
        except (ValueError,subprocess.TimeoutExpired)as error:
            # Pure rewrites/routing/physical candidates do not require an action
            # projection. Unsupported state correspondence is reported explicitly.
            model=None;self.notes.append(dict(source=state['source'],recipe='action-projection',status='declined',reason=str(error)))
        pool={r['representation']:r for _,s in states.values()if s['source']==state['source']and(r:=s.get('representation'))}
        context=dict(state,compiler=str(self.compiler),python=sys.executable,knowledge_root=str(self.root),model=model,
            knowledge_snapshot=self.snapshot,representation_pool=list(pool.values()),emit_target=self.options.target,check_timeout=self.options.check_timeout,
            proposal_timeout=self.options.proposal_timeout,semantic_budget=self.options.semantic_budget,
            semantic_rounds=self.options.semantic_rounds,candidate_limit=self.options.candidate_limit)
        path=self.directory/'recipe-context.json';path.write_bytes(canonical(context)+b'\n')
        groups=[];proposal_bytes=0
        for recipe in self.recipes:
            proposals=[]
            if time.monotonic()>=self.deadline:
                self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='deferred',reason='planning time budget'))
                continue
            output=self.directory/'recipe-output.jsonl'
            output.unlink(missing_ok=True)
            key=identity(dict(recipe=recipe,compiler=self.compiler_hash,code=self.code_hash,snapshot=self.snapshot,target=self.options.target,
                inputs={k:context.get(k)for k in recipe.get('inputs',context.keys())},
                budgets={k:context.get(k)for k in ['check_timeout','proposal_timeout','semantic_budget','semantic_rounds','candidate_limit']}))
            cached=self.directory/('recipe-'+key+'.jsonl')
            try:
                environment=dict(os.environ,INK_COMPILER=str(self.compiler),PYTHONPATH=os.pathsep.join([str(self.root),str(Path(__file__).parents[1]),os.environ.get('PYTHONPATH','')]))
                command=[sys.executable,'-m','ink_knowledge.recipes','--recipe',recipe['id'],'--stream','--context',str(path),'-o',str(output)]
                if self.options.registry:command+=['--registry',str(self.options.registry)]
                if cached.exists():
                    shutil.copyfile(cached,output);self.recipe_cache_hits+=1
                else:
                    try:
                        result=subprocess.run(command,capture_output=True,text=True,timeout=min(self.options.proposal_timeout,max(0.01,self.deadline-time.monotonic())),env=environment)
                        if result.returncode:self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='partial',reason=result.stderr[-2000:]))
                    except subprocess.TimeoutExpired:
                        self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='partial',reason='proposal process time budget'))
                if output.stat().st_size>128_000_000:raise ValueError('recipe output byte budget')
                if not cached.exists():shutil.copyfile(output,cached)
                with output.open()as stream:
                    header=json.loads(next(stream));response=[]
                    if header['registry_sha256']!=self.registry_digest:raise ValueError('recipe registry changed')
                    for line in stream:
                        if not line.endswith('\n'):break
                        response.append(json.loads(line))
                counts={}
                for row in response:
                    counts[row['status']]=counts.get(row['status'],0)+1
                    if row['status']=='declined':self.notes.append(dict(source=state['source'],recipe=recipe['id'],**row))
                    elif row['status']=='proposal':proposals.append(dict(row,recipe=recipe['id']))
                    elif row['status']=='migration':
                        before=self.directory/'migration-from.json';after=self.directory/'migration-to.json';bridge=self.directory/'migration.json';source=self.directory/'migration-core.json'
                        before.write_bytes(canonical(row['source'])+b'\n');after.write_bytes(canonical(row['target'])+b'\n');bridge.write_bytes(canonical(row['bridge'])+b'\n');source.write_bytes(canonical(state['core'])+b'\n')
                        receipt=self.invoke('check-migration',source,before,after,bridge,'--core')
                        self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='checked-migration',receipt=receipt))
                    elif row['status']=='plan':
                        # Every imported edge is replayed at this source; its
                        # independent input binding may decline on a later image.
                        proposals.append(dict(status='proposal',label=row['label'],steps=row['package']['steps'],recipe=recipe['id']))
                    else:raise ValueError('unhandled recipe result '+row['status'])
                self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='visited',counts=counts))
            except (ValueError,KeyError,OSError,StopIteration,subprocess.TimeoutExpired)as error:
                self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='declined',reason=str(error)[-2000:]))
            if proposals:
                size=sum(len(canonical(proposal))for proposal in proposals)
                if proposal_bytes+size>getattr(self.options,'memory_bytes',512_000_000):
                    self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='deferred',reason='proposal byte budget'))
                else:groups.append(deque(proposals));proposal_bytes+=size
        # Discovery covers every family before proof attempts. Round-robin
        # admission prevents a prolific producer from consuming the whole budget.
        while groups:
            remaining=[]
            for group in groups:
                yield group.popleft()
                if group:remaining.append(group)
            groups=remaining
    def priority(self,state):
        # The structural hypothesis orders exploration only. It cannot select a
        # physically different implementation without comparable observations.
        from ink_knowledge.cost_models import structural_prices
        prices=structural_prices();score=0;pending=[state['core']['program']]
        while pending:
            v=pending.pop()
            if isinstance(v,dict):
                if 'Method'in v:score+=prices.get('method:'+v['Method'][1],prices.get('estimate:collection',40))
                if 'Call'in v:score+=prices.get('call:'+v['Call'][0],prices.get('estimate:scalar',1))
                pending.extend(v.values())
            elif isinstance(v,list):pending.extend(v)
        return score

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--core',action='store_true');p.add_argument('--knowledge-root',type=Path,required=True)
    p.add_argument('--snapshot',type=Path);p.add_argument('--registry',type=Path);p.add_argument('--target',default='rust');p.add_argument('-o',type=Path,required=True)
    p.add_argument('--budget',type=int,default=16384);p.add_argument('--state-limit',type=int,default=4096)
    p.add_argument('--time-seconds',type=int,default=3600);p.add_argument('--memory-bytes',type=int,default=512_000_000)
    p.add_argument('--check-timeout',type=int,default=120);p.add_argument('--proposal-timeout',type=int,default=600)
    p.add_argument('--semantic-budget',type=int,default=4096);p.add_argument('--semantic-rounds',type=int,default=None)
    p.add_argument('--candidate-limit',type=int,default=4096);p.add_argument('--emit',type=Path)
    for name in ['profile','workload','target-profile']:p.add_argument('--'+name,type=Path)
    p.add_argument('--migration-count',type=int,default=0);p.add_argument('--memory-limit',type=int)
    args=p.parse_args()
    if any(getattr(args,n)<0 for n in ['budget','state_limit','time_seconds','memory_bytes','check_timeout','proposal_timeout','semantic_budget','candidate_limit']):p.error('planning budgets must be nonnegative')
    if args.state_limit<1 or args.memory_bytes<1 or args.check_timeout<1 or args.proposal_timeout<1:p.error('positive state/memory/process budgets required')
    if args.semantic_rounds is not None and args.semantic_rounds<0:p.error('semantic rounds must be nonnegative')
    root=args.knowledge_root.resolve();compiler=args.compiler.resolve();store=Store(root,args.snapshot)
    store.refresh_index()
    registry,digest=load(args.registry,store)
    with tempfile.TemporaryDirectory(prefix='ink-compiled-planning-')as temporary:
        directory=Path(temporary);original=directory/'original.json'
        if args.core:original.write_bytes(args.source.read_bytes())
        else:
            result=subprocess.run([str(compiler),'emit-core',str(args.source),'-o',str(original)],capture_output=True,text=True,timeout=args.check_timeout)
            if result.returncode:raise ValueError(result.stderr)
        source=json.loads(subprocess.check_output([str(compiler),'check-core',str(original)],text=True,timeout=args.check_timeout))['core_sha256']
        initial=dict(schema=1,semantics=SEMANTICS,input_core_sha256=source,steps=[])
        planner=Planner(compiler,original,root,directory,registry['recipes'],digest,args)
        states,receipt=explore(initial,planner.expand,planner.check,planner.priority,args.budget,args.state_limit,args.time_seconds,args.memory_bytes)
        if hashlib.sha256(compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash:raise ValueError('compiler or planning knowledge changed during search')
        from ink_knowledge.implementation_candidates import publish_candidates
        packages=[package for package,_ in states.values()]
        if any(getattr(args,n)is not None for n in ['profile','workload','target_profile'])and not all(getattr(args,n)is not None for n in ['profile','workload','target_profile']):p.error('selection needs profile, workload and target-profile together')
        store,candidates=publish_candidates(root,packages)
        measurements=[measurement_id(c,planner.compiler_hash,args.target)for c in candidates]
        baseline=initial;selected=baseline;selection=dict(reason='missing comparable lifecycle evidence; baseline retained')
        if all(getattr(args,n)is not None for n in ['profile','workload','target_profile']):
            profile=json.loads(args.profile.read_bytes());workload=json.loads(args.workload.read_bytes());target=json.loads(args.target_profile.read_bytes())
            if target.get('backend')!=args.target:raise ValueError('measurement backend must match emission lane')
            eligible=[m for m,(_,s)in zip(measurements,states.values())if s.get('capability',{}).get('emittable',True)]
            chosen,selection=rank(store,source,eligible,target,workload,profile,measurements[0],args.migration_count,args.memory_limit,{m:'migrate:'+measurements[0]+':portable:'+m for m in measurements[1:]})
            selected=packages[measurements.index(chosen)]
        # A selected packet must also pass fresh whole-chain replay before emission.
        checked=planner.check(selected)
        replay=directory/'frozen-replay.json';replay.write_bytes(canonical(selected)+b'\n')
        planner.invoke('check-plan',original,replay,'--core','--target',args.target)
        if hashlib.sha256(compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash:raise ValueError('compiler or planning knowledge changed during planning')
        if load(args.registry,Store(root,root/'store/snapshots'/f'{planner.snapshot}.json'))[1]!=digest:raise ValueError('recipe registry changed during planning')
        args.o.parent.mkdir(parents=True,exist_ok=True);args.o.write_bytes(canonical(selected)+b'\n')
        receipt.update(dict(schema=1,semantics=SEMANTICS,status='checked',source=source,compiler_sha256=planner.compiler_hash,
            registry_sha256=digest,producer_sha256=planner.code_hash,reused_proof_steps=planner.reused_steps,recipe_cache_hits=planner.recipe_cache_hits,source_snapshot=planner.snapshot,publication_snapshot=store.snapshot_id,coverage=coverage(Store(root,root/'store/snapshots'/f'{planner.snapshot}.json')),recipes=planner.notes,
            selection=selection,selected=identity(selected),candidates=[dict(candidate=c,measurement=m,plan=identity(pkg),state=fingerprint(state),capability=state.get('capability'))for c,m,(pkg,state)in zip(candidates,measurements,states.values())]))
        Path(str(args.o)+'.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        planner.close()
        if args.emit:
            command=[str(compiler),'emit-plan',str(original),str(args.o.resolve()),'--core','--target',args.target,'-o',str(args.emit)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=args.check_timeout)
            if result.returncode:raise ValueError(result.stderr)
        print(json.dumps({k:v for k,v in receipt.items()if k not in {'edges','rejected','recipes','candidates','coverage'}}))

if __name__=='__main__':main()
