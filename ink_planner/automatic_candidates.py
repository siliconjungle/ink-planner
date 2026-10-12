"""Automatic discovery delegates every family to the knowledge recipe registry."""
import hashlib,json,tempfile
from pathlib import Path
from types import SimpleNamespace
from ink_knowledge import Store,canonical,identity
from ink_knowledge.implementation_candidates import identity_selection,publish_candidates,proof_views,SEMANTICS

def discover(store,source):
    result=[];after=None
    while True:
        page=store.query(kind='implementation',semantics=SEMANTICS,after=after,limit=1024)
        for ident in page:
            if store.object(ident)['payload']['package'].get('input_core_sha256')==source:result.append(ident)
        if len(page)<1024:break
        after=page[-1]
    return result

def propose(compiler,source_path,source,root,invoke,check_package,core,timeout=120,proposal_timeout=600,
            emit_target='rust',budget=16384,state_limit=4096,time_seconds=3600,registry_path=None,snapshot=None):
    from .compiled_planning import Planner,explore,code_fingerprint,SEMANTICS as PLAN
    from ink_knowledge.recipes import load
    import producers
    root=Path(root);compiler=Path(compiler).resolve();core=Path(core)
    if not (root/'store/snapshot.json').exists():Store.publish(root,[])
    input_store=Store(root,snapshot);registry,digest=load(registry_path,input_store);source_snapshot=input_store.snapshot_id
    Store(root).refresh_index()
    code=code_fingerprint()
    generated_path=root/'proposals'/source/'generated-objects.json'
    try:generated=set(json.loads(generated_path.read_bytes()))
    except (ValueError,OSError):generated=set()
    revision=identity(dict(compiler=hashlib.sha256(compiler.read_bytes()).hexdigest(),registry=digest,code=code,
        snapshot=input_store.snapshot_id if snapshot else identity(sorted(i for i in input_store.members() if i not in generated and input_store.object(i)['kind']!='implementation')),target=emit_target,timeout=timeout,proposal_timeout=proposal_timeout,budget=budget,states=state_limit,time=time_seconds))
    recipe=root/'proposals'/source/(revision+'.json')
    if recipe.exists():
        try:
            saved=json.loads(recipe.read_bytes());store=Store(root)
            ids=[saved['baseline'],*saved['candidates']]
            if all(i in store.members()and store.object(i)['payload']['package']['input_core_sha256']==source for i in ids):
                return store,saved['baseline'],saved['candidates'],saved['notes']
        except (ValueError,KeyError,OSError):pass
    initial=dict(schema=1,semantics=PLAN,input_core_sha256=source,steps=[])
    options=SimpleNamespace(check_timeout=timeout,proposal_timeout=proposal_timeout,target=emit_target,
        semantic_budget=4096,semantic_rounds=None,candidate_limit=4096,registry=registry_path,snapshot=snapshot,time_seconds=time_seconds)
    with tempfile.TemporaryDirectory(prefix='ink-automatic-recipes-')as temporary:
        planner=Planner(compiler,core.resolve(),root.resolve(),Path(temporary),registry['recipes'],digest,options)
        states,receipt=explore(initial,planner.expand,planner.check,planner.priority,budget=budget,state_limit=state_limit,time_seconds=time_seconds)
        # All retained alternatives are independently admitted complete chains.
        packages=[identity_selection(source),*[p for p,_ in states.values()if p['steps']]]
        planner.close()
        notes=[dict(proposal='recursive-registry',status='checked',search=receipt,recipes=planner.notes,registry=digest)]
    if hashlib.sha256(compiler.read_bytes()).hexdigest()!=planner.compiler_hash or code_fingerprint()!=planner.code_hash:raise ValueError('compiler or planning knowledge changed during search')
    store,ids=publish_candidates(root,packages)
    candidates=list(dict.fromkeys(ids[1:]));recipe.parent.mkdir(parents=True,exist_ok=True)
    recipe.write_bytes(canonical(dict(baseline=ids[0],candidates=candidates,notes=notes))+b'\n')
    generated.update(i for package in packages for view in proof_views(package)for i in view['objects']if i not in input_store.members())
    generated_path.write_bytes(canonical(sorted(generated))+b'\n')
    return store,ids[0],candidates,notes
