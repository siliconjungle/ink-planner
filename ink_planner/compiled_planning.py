"""Recursive offline search over checked, typed implementation states.

All knowledge families can propose edges after every admitted state. A new source
image invalidates stale representations/properties in the independent checker.
Search estimates and memo identities are untrusted; emitted plans replay proofs.
"""
import argparse, atexit, copy, hashlib, heapq, json, os, select, shutil, subprocess, sys, tempfile, time
from collections import deque
from pathlib import Path
from ink_knowledge import Store, canonical, identity
from ink_knowledge.recipes import load, indexed_coverage
from ink_knowledge.implementation_candidates import candidate_entry, proof_views
from .implementation_selection import measurement_id
from .representation_selection import rank

SEMANTICS='ink-compiled-plan-v1'
class CheckerBudgetError(ValueError):
    """A candidate was not checked; this is not an admission rejection."""

# Explicit command-line budgets override these named, bounded offline efforts.
EFFORTS={
    'quick':dict(budget=2048,state_limit=32,time_seconds=8,semantic_budget=2048,semantic_seconds=8,proposal_timeout=8),
    'balanced':dict(budget=32768,state_limit=512,time_seconds=60,semantic_budget=8192,semantic_seconds=30,proposal_timeout=60),
    'thorough':dict(budget=131072,state_limit=4096,time_seconds=300,semantic_budget=32768,semantic_seconds=120,proposal_timeout=180),
}

def effort_options(args):
    for name,value in EFFORTS[args.planning_effort].items():
        if getattr(args,name) is None:setattr(args,name,value)
    return args

def code_fingerprint():
    import ink_knowledge,producers
    roots=[('knowledge',Path(ink_knowledge.__file__).parent),('producers',Path(producers.__file__).parent),('planner',Path(__file__).parent)]
    # Installed authoring resources can change proposals without changing a
    # Python module or database snapshot. Bind those bytes to cache/cost reuse.
    authoring=Path(ink_knowledge.__file__).parent.parent
    roots.extend((name,authoring/name)for name in ['libraries','research/general-laws','research/verified-kernel'])
    return identity({label+'/'+str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest()
        for label,root in roots for p in sorted(root.rglob('*'))if p.suffix in {'.py','.json','.gz','.mjs','.rs','.txt','.ink','.lean'}and p.is_file()})


def protocol_identity(plan):
    if plan and set(plan)<= {'schema','semantics','input_core_sha256','actions','direct_reads','output_publication','snapshot_rows','columns'} and plan.get('semantics')=='ink-state-protocol-v1' and not any(plan.get(k)for k in ['actions','direct_reads','output_publication','snapshot_rows','columns']):
        return None
    return plan

def fingerprint(state):
    facts=set()
    for package in state.get('properties',[]):
        for fact in package['facts']:
            facts.add(identity({k:v for k,v in fact.items()if k!='proof'}))
    return identity(dict(source=state['source'],representation=(state.get('representation')or{}).get('representation'),
        definitions=state.get('definition_choices')or{},physical=state.get('physical'),maintained=state.get('maintained'),protocol=protocol_identity(state.get('protocol')),route=state.get('route'),resources=state.get('resources'),properties=sorted(facts)))

def explore(initial,expand,check,priority,budget=16384,state_limit=4096,time_seconds=3600,memory_bytes=512_000_000):
    """The callback boundary admits complete proof chains, never hash equality.
    Invalid proofs do not mark their prospective target state as visited.
    """
    started=time.monotonic();first=check(initial)
    states={fingerprint(first):(initial,first)};agenda=[];serial=0;attempts=0;edges=[];rejections=[];retained=len(canonical(initial))+len(canonical(first))
    heapq.heappush(agenda,(priority(first),serial,fingerprint(first)))
    stopped=None;unchecked_checks=0
    while agenda:
        if time.monotonic()-started>=time_seconds:stopped='time';break
        _,_,parent=heapq.heappop(agenda);package,state=states[parent]
        expansion=expand(state,states)
        try:
            for proposal in expansion:
                if time.monotonic()-started>=time_seconds:stopped='time';break
                if attempts>=budget:stopped='proof-attempts';break
                if proposal.get('status')=='search-limit':stopped=proposal['reason'];break
                if proposal.get('status')!='proposal':continue
                steps=proposal.get('steps',[proposal.get('step')])
                if not steps or any(step is None for step in steps):continue
                candidate=copy.deepcopy(package);candidate['steps'].extend(steps)
                attempts+=1
                try:checked=check(candidate)
                except (ValueError,OSError,subprocess.TimeoutExpired)as error:
                    limited=isinstance(error,(CheckerBudgetError,subprocess.TimeoutExpired))
                    unchecked_checks+=int(limited)
                    rejections.append(dict(parent=parent,recipe=proposal.get('recipe'),label=proposal.get('label'),reason=str(error)[-2000:],gate='checker-budget'if limited else'admission'));continue
                child=fingerprint(checked)
                edges.append(dict(source=parent,target=child,step=identity(steps),kinds=[step['kind']for step in steps],recipe=proposal.get('recipe')))
                if child in states:continue
                size=len(canonical(candidate))+len(canonical(checked))
                if len(states)>=state_limit or retained+size>memory_bytes:
                    stopped='states'if len(states)>=state_limit else'memory';break
                retained+=size;states[child]=(candidate,checked);serial+=1
                heapq.heappush(agenda,(priority(checked),serial,child))
        finally:
            if hasattr(expansion,'close'):expansion.close()
        if stopped:break
    return states,dict(attempts=attempts,admitted_states=len(states),edges=edges,rejected=rejections,
        stop_reason=stopped or('checker-time'if unchecked_checks else'fixed-point'),unchecked_checks=unchecked_checks,remaining=len(agenda),retained_bytes=retained,
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
            session.kill();session.wait();session.stdin.close();session.stdout.close();
            if session.stderr is not None:session.stderr.close()
    def receive(self,deadline=None):
        deadline=deadline or time.monotonic()+self.options.check_timeout
        buffer=getattr(self,'response_buffer',bytearray())
        self.response_buffer=buffer
        while True:
            at=buffer.find(b'\n')
            if at>=0:
                line=bytes(buffer[:at]);del buffer[:at+1]
                return json.loads(line)
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([self.session.stdout],[],[],remaining)[0]:
                self.close();raise CheckerBudgetError('plan checker process time budget')
            chunk=os.read(self.session.stdout.fileno(),65536)
            if not chunk or len(buffer)+len(chunk)>128_000_000:
                self.close();raise ValueError('plan checker response byte budget')
            buffer.extend(chunk)
    def send(self,payload,deadline):
        fd=self.session.stdin.fileno();os.set_blocking(fd,False);offset=0
        while offset<len(payload):
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([],[fd],[],remaining)[1]:
                self.close();raise CheckerBudgetError('plan checker process time budget')
            try:offset+=os.write(fd,payload[offset:offset+65536])
            except BlockingIOError:continue
            except BrokenPipeError:
                self.close();raise ValueError('plan checker input closed')
    def check(self,package):
        key=identity(dict(compiler=self.compiler_hash,package=package))
        if key in self.admissions:return self.admissions[key]
        if self.session is None:
            self.session=subprocess.Popen([str(self.compiler),'plan-session',str(self.original),'--target',self.options.target],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            self.response_buffer=bytearray()
            if not self.receive().get('ready'):self.close();raise ValueError('plan checker not ready')
        deadline=time.monotonic()+self.options.check_timeout
        self.send(canonical(package)+b'\n',deadline)
        receipt=self.receive(deadline)
        if receipt.get('error'):raise ValueError(receipt['error'])
        if receipt.get('status')!='checked'or receipt.get('request_sha256',receipt.get('plan_sha256'))!=identity(package):raise ValueError('plan checker binding mismatch')
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
            usage_profile=getattr(self.options,'observed_resources',None),knowledge_snapshot=self.snapshot,representation_pool=list(pool.values()),emit_target=self.options.target,check_timeout=self.options.check_timeout,
            proposal_timeout=self.options.proposal_timeout,semantic_budget=self.options.semantic_budget,
            semantic_rounds=self.options.semantic_rounds,candidate_limit=self.options.candidate_limit)
        context['semantic_seconds']=getattr(self.options,'semantic_seconds',600)
        path=self.directory/('context-'+identity(context)+'.json');path.write_bytes(canonical(context)+b'\n')
        jobs=[];counts={};headers=set();invalid=set();proposal_bytes=0;complete=set()
        environment=dict(os.environ,INK_COMPILER=str(self.compiler),PYTHONPATH=os.pathsep.join([str(self.root),str(Path(__file__).parents[1]),os.environ.get('PYTHONPATH','')]))
        for recipe in self.recipes:
            missing=[name for name in recipe.get('requires',[])if context.get(name)is None]
            if missing:
                self.notes.append(dict(source=state['source'],recipe=recipe['id'],status='declined',reason='missing inputs: '+','.join(missing)));continue
            key=identity(dict(recipe=recipe,source=state['source'],compiler=self.compiler_hash,code=self.code_hash,snapshot=self.snapshot,target=self.options.target,
                inputs={k:context.get(k)for k in recipe.get('inputs',context.keys())},
                budgets={k:context.get(k)for k in ['check_timeout','proposal_timeout','semantic_budget','semantic_rounds','semantic_seconds','candidate_limit']}))
            output=self.directory/('output-'+key+'.jsonl')
            command=[sys.executable,'-m','ink_knowledge.recipes','--recipe',recipe['id'],'--stream','--context',str(path),'-o',str(output)]
            if self.options.registry:command+=['--registry',str(self.options.registry)]
            jobs.append(dict(recipe=recipe,key=key,command=command,env=environment,output=output,
                cache=self.directory/('recipe-'+key+'.jsonl'),stdout=self.directory/(key+'.stdout'),stderr=self.directory/(key+'.stderr')))
        from .recipe_streams import run
        stream=run(jobs,getattr(self.options,'recipe_workers',2),self.deadline,self.options.proposal_timeout,
            min(128_000_000,getattr(self.options,'memory_bytes',512_000_000)))
        try:
            for job,event in stream:
                recipe=job['recipe'];name=recipe['id'];key=job['key']
                if event['kind']=='done':
                    complete.add(name)
                    if event['cached']:self.recipe_cache_hits+=1
                    if event['complete']and key in headers and key not in invalid:
                        if not event['cached']:shutil.copyfile(job['output'],job['cache'])
                        status='visited';reason=None
                    else:
                        status='partial';reason=event['reason']or'invalid recipe stream'
                    self.notes.append(dict(source=state['source'],recipe=name,status=status,reason=reason,counts=counts.get(key,{}),elapsed_seconds=event['elapsed_seconds']));continue
                if key in invalid:continue
                try:
                    row=json.loads(event['raw'])
                    if key not in headers:
                        if row!={'recipe':name,'registry_sha256':self.registry_digest}:raise ValueError('recipe header/registry changed')
                        headers.add(key);continue
                    status=row['status'];counts.setdefault(key,{})[status]=counts.get(key,{}).get(status,0)+1
                    if status=='declined':self.notes.append(dict(source=state['source'],recipe=name,**row));continue
                    if status=='diagnostic':
                        # Untrusted search observations for planning diagnostics only.
                        self.notes.append(dict(source=state['source'],recipe=name,status='diagnostic',label=row.get('label'),diagnostics=row.get('diagnostics')));continue
                    if status=='migration':
                        before=self.directory/'migration-from.json';after=self.directory/'migration-to.json';bridge=self.directory/'migration.json';source=self.directory/'migration-core.json'
                        before.write_bytes(canonical(row['source'])+b'\n');after.write_bytes(canonical(row['target'])+b'\n');bridge.write_bytes(canonical(row['bridge'])+b'\n');source.write_bytes(canonical(state['core'])+b'\n')
                        receipt=self.invoke('check-migration',source,before,after,bridge,'--core')
                        self.notes.append(dict(source=state['source'],recipe=name,status='checked-migration',receipt=receipt));continue
                    if status=='plan':row=dict(status='proposal',label=row['label'],steps=row['package']['steps'])
                    elif status!='proposal':raise ValueError('unhandled recipe result '+status)
                    size=len(event['raw'])
                    if proposal_bytes+size>getattr(self.options,'memory_bytes',512_000_000):
                        self.notes.append(dict(source=state['source'],recipe=name,status='deferred',reason='proposal byte budget'));continue
                    proposal_bytes+=size
                    yield dict(row,recipe=name)
                except (ValueError,KeyError,TypeError,OSError,subprocess.TimeoutExpired)as error:
                    invalid.add(key);self.notes.append(dict(source=state['source'],recipe=name,status='declined',reason=str(error)[-2000:]))
        finally:stream.close()
        unfinished=False
        for job in jobs:
            if job['recipe']['id']not in complete:
                unfinished=True
                self.notes.append(dict(source=state['source'],recipe=job['recipe']['id'],status='deferred',reason='planning time budget'))
        if unfinished:yield dict(status='search-limit',reason='time')
    def priority(self,state):
        # The structural hypothesis orders exploration only. It cannot select a
        # physically different implementation without comparable observations.
        from ink_knowledge.cost_models import structural_prices
        prices=structural_prices();score=0
        program=state['core']['program'];frequencies=getattr(self.options,'observed_frequencies',{})
        pending=[(f.get('body',{}),frequencies.get(('action:' if 'kind' in f else 'call:')+f['name'],1)) for f in [*program.get('functions',[]),*program.get('actions',[])]]
        while pending:
            v,weight=pending.pop()
            if isinstance(v,dict):
                if 'Method'in v:score+=weight*prices.get('method:'+v['Method'][1],prices.get('estimate:collection',40))
                if 'Call'in v:score+=weight*prices.get('call:'+v['Call'][0],prices.get('estimate:scalar',1))
                pending.extend((child,weight) for child in v.values())
            elif isinstance(v,list):pending.extend((child,weight) for child in v)
        return score

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler',type=Path,required=True);p.add_argument('--source',type=Path,required=True)
    p.add_argument('--contract-proofs',type=Path);p.add_argument('--core',action='store_true');p.add_argument('--knowledge-root',type=Path,required=True)
    p.add_argument('--termination-proofs',type=Path)
    p.add_argument('--snapshot',type=Path);p.add_argument('--registry',type=Path);p.add_argument('--target',default='rust');p.add_argument('-o',type=Path,required=True)
    p.add_argument('--planning-effort',choices=sorted(EFFORTS),default='balanced')
    p.add_argument('--budget',type=int);p.add_argument('--state-limit',type=int)
    p.add_argument('--time-seconds',type=int);p.add_argument('--memory-bytes',type=int,default=512_000_000)
    p.add_argument('--check-timeout',type=int,default=120);p.add_argument('--proposal-timeout',type=int)
    p.add_argument('--recipe-workers',type=int,default=2,help='bounded simultaneous recipe processes')
    p.add_argument('--semantic-seconds',type=int,help='semantic search time cap per producer')
    p.add_argument('--semantic-budget',type=int);p.add_argument('--semantic-rounds',type=int,default=None)
    p.add_argument('--candidate-limit',type=int,default=4096);p.add_argument('--emit',type=Path)
    for name in ['profile','workload','target-profile']:p.add_argument('--'+name,type=Path)
    p.add_argument('--measure-workload',type=Path,help='acquire comparable offline observations before selection')
    p.add_argument('--measure-holdout',type=Path,help='independent inputs that may reject, but never choose, the calibrated alternative')
    p.add_argument('--minimum-measured-gain-ppm',type=int,default=100000,help='minimum observed lifecycle gain for freshly calibrated alternatives (default 10%%)')
    p.add_argument('--measure-timeout',type=int,default=120)
    p.add_argument('--measure-processes',type=int,default=3,help='fresh offline timing processes, sharing the measurement time budget')
    p.add_argument('--measurement-mode',choices=['auto','off'],default='auto',help='automatically acquire bounded pure typed probes when no workload evidence exists')
    p.add_argument('--cache',choices=['auto','off'],default='auto',help='reuse same-epoch bounded plans with fresh proof replay')
    p.add_argument('--work-workload',type=Path,help='reference-interpreter work of each candidate on these calls (deterministic, no timing)')
    p.add_argument('--work-model',type=Path,help='held-out-validated work model; selects by reference work when no comparable timings exist')
    p.add_argument('--keep-candidates',type=Path,help='write every admitted candidate package to this directory')
    p.add_argument('--migration-count',type=int,default=0);p.add_argument('--memory-limit',type=int)
    args=effort_options(p.parse_args())
    if not 1<=args.recipe_workers<=8:p.error('recipe workers must be between 1 and 8')
    if not 0<=args.semantic_seconds<=86400:p.error('semantic seconds must be between 0 and 86400')
    if any(getattr(args,n)<0 for n in ['budget','state_limit','time_seconds','memory_bytes','check_timeout','proposal_timeout','semantic_budget','candidate_limit']):p.error('planning budgets must be nonnegative')
    if args.state_limit<1 or args.memory_bytes<1 or args.check_timeout<1 or args.proposal_timeout<1:p.error('positive state/memory/process budgets required')
    if args.semantic_rounds is not None and args.semantic_rounds<0:p.error('semantic rounds must be nonnegative')
    if not 1<=args.measure_timeout<=600:p.error('measurement process budget must be between 1 and 600 seconds')
    if not 1<=args.measure_processes<=8:p.error('measurement processes must be between 1 and 8')
    if not 0<=args.minimum_measured_gain_ppm<1_000_000:p.error('minimum measured gain must be between 0 and 999999 ppm')
    if args.measure_holdout and not args.measure_workload:p.error('holdout requires a measurement workload')
    if args.measure_holdout and args.target!='javascript':p.error('typed holdout requires the JavaScript measurement adapter')
    if args.measure_holdout:
        from .offline_measurements import independent_workloads
        independent_workloads(args.measure_workload,args.measure_holdout)
    if args.work_model and not args.work_workload:p.error('--work-model needs --work-workload')
    work_model=None
    if args.work_model:
        from .work_model import load as load_work_model
        work_model=load_work_model(args.work_model)
    root=args.knowledge_root.resolve();compiler=args.compiler.resolve();store=Store(root,args.snapshot)
    store.ensure_index()
    registry,digest=load(args.registry,store)
    with tempfile.TemporaryDirectory(prefix='ink-compiled-planning-')as temporary:
        directory=Path(temporary);original=directory/'original.json'
        if args.core:original.write_bytes(args.source.read_bytes())
        else:
            result=subprocess.run([str(compiler),'emit-core',str(args.source),'-o',str(original),*(['--contract-proofs',str(args.contract_proofs)] if args.contract_proofs else []),*(['--termination-proofs',str(args.termination_proofs)] if args.termination_proofs else [])],capture_output=True,text=True,timeout=args.check_timeout)
            if result.returncode:raise ValueError(result.stderr)
        source=json.loads(subprocess.check_output([str(compiler),'check-core',str(original)],text=True,timeout=args.check_timeout))['core_sha256']
        from ink_knowledge.usage_profiles import discover
        usage_profiles=discover(store,source,args.target)
        args.observed_frequencies={}
        for profile in usage_profiles:
            for phase,count in profile['counts'].items():args.observed_frequencies[phase]=args.observed_frequencies.get(phase,0)+count
        args.observed_resources=dict(peak_pending=max((p['peak_pending'] for p in usage_profiles),default=0),peak_pending_bytes=max((p['peak_pending_bytes'] for p in usage_profiles),default=0)) if usage_profiles else None
        initial=dict(schema=1,semantics=SEMANTICS,input_core_sha256=source,steps=[])
        planner=Planner(compiler,original,root,directory,registry['recipes'],digest,args)
        from . import compiled_cache
        cache_key=compiled_cache.key(planner,source,store.snapshot_id,args,usage_profiles)
        cached=compiled_cache.load(root,cache_key)if args.cache=='auto'and not args.measure_workload else None
        if cached is not None:
            try:
                packet=cached['package']
                if packet['input_core_sha256']!=source:raise ValueError('cached source differs')
                replay=directory/'cached-plan.json';replay.write_bytes(canonical(packet)+b'\n')
                planner.invoke('check-plan',original,replay,'--core','--target',args.target)
                if code_fingerprint()!=planner.code_hash or hashlib.sha256(compiler.read_bytes()).hexdigest()!=planner.compiler_hash:raise ValueError('execution machinery changed during cached replay')
                if args.emit:planner.invoke('emit-plan',original,replay,'--core','--target',args.target,'-o',args.emit)
                args.o.parent.mkdir(parents=True,exist_ok=True);args.o.write_bytes(canonical(packet)+b'\n')
                receipt=dict(cached['receipt'],cache=dict(hit=True,key=cache_key,origin_created_unix=cached['created_unix'],scope='Historical search/cost diagnostics reused; only selected full chain freshly replayed'))
                from .planning_diagnostics import diagnose
                receipt['diagnostics']=diagnose(receipt)
                Path(str(args.o)+'.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
                print(json.dumps(dict(status='checked',cache=receipt['cache'],source=source,selected=identity(packet))))
                planner.close();return
            except (ValueError,KeyError,TypeError,OSError,subprocess.TimeoutExpired)as error:
                planner.notes.append(dict(recipe='compiled-cache',status='declined',reason=str(error)[-2000:]))
        states,receipt=explore(initial,planner.expand,planner.check,planner.priority,args.budget,args.state_limit,args.time_seconds,args.memory_bytes)
        if hashlib.sha256(compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash:raise ValueError('compiler or planning knowledge changed during search')
        from ink_knowledge.implementation_candidates import publish_candidates
        packages=[package for package,_ in states.values()]
        if any(getattr(args,n)is not None for n in ['profile','workload','target_profile'])and not all(getattr(args,n)is not None for n in ['profile','workload','target_profile']):p.error('selection needs profile, workload and target-profile together')
        store,candidates=publish_candidates(root,packages)
        measurements=[measurement_id(c,planner.compiler_hash,args.target)for c in candidates]
        baseline=initial;selected=baseline;selection=dict(reason='missing comparable lifecycle evidence; baseline retained')
        if usage_profiles and not args.measure_workload and not any(getattr(args,n)is not None for n in ['profile','workload','target_profile']):
            from .usage_selection import select
            eligible=[m for m,(_,s)in zip(measurements,states.values())if s.get('capability',{}).get('emittable',True)]
            chosen,selection=select(store,source,eligible,usage_profiles,measurements[0],args.memory_limit)
            selected=packages[measurements.index(chosen)]
        acquisition=None
        automatic_probe=None
        if args.measurement_mode=='auto'and not args.measure_workload and not usage_profiles and not any(getattr(args,n)is not None for n in ['profile','workload','target_profile']):
            from ink_knowledge.selection_probes import create
            try:spec,automatic_probe=create(json.loads(original.read_bytes()))
            except (ValueError,TypeError,KeyError)as error:spec=None;automatic_probe=dict(reason='typed probes declined: '+str(error))
            if args.target not in {'javascript','rust'}:
                spec=None;automatic_probe['reason']='no installed offline measurement adapter for '+args.target
            if args.memory_limit is not None or args.migration_count:
                spec=None;automatic_probe['reason']='synthetic pure timing probes do not establish memory or migration lifecycle costs'
            if spec is not None:
                args.measure_workload=directory/'automatic-probes.json';args.measure_workload.write_text(json.dumps(spec,allow_nan=False))
        if args.measure_workload:
            if any(getattr(args,n)is not None for n in ['profile','workload','target_profile']) or args.migration_count:raise ValueError('offline pure-call acquisition cannot combine existing profiles or migration')
            from .offline_measurements import acquire,validated
            from ink_knowledge.selection_probes import policy
            from .measured_selection import shortlist
            controls=policy() if automatic_probe is not None else None;checked_states=[s for _,s in states.values()]
            indices=shortlist(checked_states,planner.priority,controls['candidates']) if controls else list(range(len(packages)))
            try:
                target,workload,profile,acquisition=acquire(planner,store,[packages[i]for i in indices],[checked_states[i]for i in indices],[measurements[i]for i in indices],args.measure_workload,controls)
                acquisition['shortlist']=dict(measured_indices=indices,admitted=len(packages),limit=controls['candidates'] if controls else len(packages),unmeasured_reason='bounded automatic measurement candidate budget')
            except (ValueError,OSError,subprocess.TimeoutExpired)as error:
                if automatic_probe is None:raise
                automatic_probe['reason']='offline probes declined: '+str(error)[-2000:]
        elif all(getattr(args,n)is not None for n in ['profile','workload','target_profile']):
            profile=json.loads(args.profile.read_bytes());workload=json.loads(args.workload.read_bytes());target=json.loads(args.target_profile.read_bytes())
        if acquisition is not None or all(getattr(args,n)is not None for n in ['profile','workload','target_profile']):
            if target.get('backend')!=args.target:raise ValueError('measurement backend must match emission lane')
            eligible=[m for m,(_,s)in zip(measurements,states.values())if s.get('capability',{}).get('emittable',True)]
            if acquisition is not None:eligible=validated(eligible,acquisition)
            guard=dict(minimum_gain_ppm=args.minimum_measured_gain_ppm,sample_guard=True)if acquisition is not None and automatic_probe is None else {}
            chosen,selection=rank(store,source,eligible,target,workload,profile,measurements[0],args.migration_count,args.memory_limit,
                {m:'migrate:'+measurements[0]+':portable:'+m for m in measurements[1:]},**guard)
            if acquisition is not None and automatic_probe is not None:
                from .measured_selection import choose
                setup={r['measurement']:int(r.get('setup_ns',float(r.get('module_load_ms','0'))*1000000))for r in acquisition['acquisition'].get('module_load',[])}
                chosen,guard=choose(acquisition['acquisition'],profile,measurements[0],controls,setup)
                if chosen not in selection['total_ns']:chosen=measurements[0]
                selection['uncertainty_guard']=guard
                selection['reason']=guard['reason']
                selection['selected_measurement']=chosen
            if acquisition is not None:
                if args.measure_holdout:
                    selected_indices=[0] if chosen==measurements[0] else [0,measurements.index(chosen)]
                    held_target,held_workload,held_profile,held_acquisition=acquire(planner,store,[packages[i]for i in selected_indices],[list(states.values())[i][1]for i in selected_indices],[measurements[i]for i in selected_indices],args.measure_holdout)
                    if set(acquisition['acquisition']['input_fingerprints'])&set(held_acquisition['acquisition']['input_fingerprints']):raise ValueError('holdout normalized typed inputs overlap selection inputs')
                    if held_workload==workload:raise ValueError('holdout must have independent input provenance')
                    if held_profile!=profile:raise ValueError('holdout must retain the lifecycle objective weights')
                    held_choice,held_selection=rank(store,source,validated([measurements[i]for i in selected_indices],held_acquisition),held_target,held_workload,held_profile,measurements[0],
                        minimum_gain_ppm=args.minimum_measured_gain_ppm,sample_guard=True)
                    proposed=chosen
                    if held_choice!=chosen:chosen=measurements[0]
                    selection['holdout']=dict(proposed=proposed,accepted=chosen==proposed,selection=held_selection,acquisition=held_acquisition,
                        scope='Independent rejection gate; holdout does not rank new alternatives')
            selected=packages[measurements.index(chosen)]
        work=None
        if args.work_workload:
            from .work_model import acquire as acquire_work
            work=acquire_work(planner,packages,[s for _,s in states.values()],measurements,args.work_workload)
        # Timings, when present, always decide. The work model only replaces
        # the evidence-free default, and only with a validated calibration.
        if work_model is not None and selected is baseline and selection.get('reason','').startswith('missing comparable'):
            from .work_model import select as select_by_work
            eligible=[m for m,(_,s)in zip(measurements,states.values())if s.get('capability',{}).get('emittable',True)]
            chosen,selection=select_by_work(work_model[0],work_model[1],work,measurements,eligible,measurements[0],
                memory_limit=args.memory_limit,migration_count=args.migration_count)
            selected=packages[measurements.index(chosen)]
        if args.keep_candidates:
            args.keep_candidates.mkdir(parents=True,exist_ok=True)
            for m,package in zip(measurements,packages):(args.keep_candidates/(m+'.plan.json')).write_bytes(canonical(package)+b'\n')
        # A selected packet must also pass fresh whole-chain replay before emission.
        checked=planner.check(selected)
        replay=directory/'frozen-replay.json';replay.write_bytes(canonical(selected)+b'\n')
        planner.invoke('check-plan',original,replay,'--core','--target',args.target)
        if hashlib.sha256(compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash:raise ValueError('compiler or planning knowledge changed during planning')
        if load(args.registry,Store(root,root/'store/snapshots'/f'{planner.snapshot}.json'))[1]!=digest:raise ValueError('recipe registry changed during planning')
        args.o.parent.mkdir(parents=True,exist_ok=True);args.o.write_bytes(canonical(selected)+b'\n')
        receipt.update(dict(schema=1,semantics=SEMANTICS,status='checked',source=source,compiler_sha256=planner.compiler_hash,
            registry_sha256=digest,producer_sha256=planner.code_hash,planning_budgets=dict(effort=args.planning_effort,recipe_workers=args.recipe_workers,**{name:getattr(args,name)for name in EFFORTS['balanced']}),reused_proof_steps=planner.reused_steps,recipe_cache_hits=planner.recipe_cache_hits,source_snapshot=planner.snapshot,publication_snapshot=store.snapshot_id,coverage=indexed_coverage(Store(root,root/'store/snapshots'/f'{planner.snapshot}.json')),recipes=planner.notes,
            selection=selection,automatic_probe=automatic_probe,measurement_acquisition=acquisition,work_acquisition=work,selected=identity(selected),candidates=[dict(candidate=c,measurement=m,plan=identity(pkg),state=fingerprint(state),capability=state.get('capability'))for c,m,(pkg,state)in zip(candidates,measurements,states.values())]))
        from .planning_diagnostics import diagnose
        receipt['diagnostics']=diagnose(receipt)
        Path(str(args.o)+'.receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
        planner.close()
        if args.emit:
            command=[str(compiler),'emit-plan',str(original),str(args.o.resolve()),'--core','--target',args.target,'-o',str(args.emit)]
            result=subprocess.run(command,capture_output=True,text=True,timeout=args.check_timeout)
            if result.returncode:raise ValueError(result.stderr)
        if args.cache=='auto':
            compiled_cache.save(root,cache_key,selected,receipt)
            # Ordinary publication appends source-bound candidates. The exact
            # output snapshot is a second valid key, never an arbitrary latest.
            args.measure_workload=None if automatic_probe is not None else args.measure_workload
            next_key=compiled_cache.key(planner,source,Store(root).snapshot_id,args,usage_profiles)
            compiled_cache.save(root,next_key,selected,receipt)
        print(json.dumps({k:v for k,v in receipt.items()if k not in {'edges','rejected','recipes','candidates','coverage','diagnostics'}}))

if __name__=='__main__':main()
