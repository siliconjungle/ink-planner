"""Offline proposal of source-bound database implementations.

Producers supply definitions and proofs. Only ordinary checker acceptance admits
packages; profitability remains the existing target/workload lifecycle ranking.
"""
import hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import Store,canonical
from ink_knowledge.implementation_candidates import identity_selection,publish_candidates,SEMANTICS


def discover(store,source):
    result=[];after=None
    while True:
        ids=store.query(kind='implementation',semantics=SEMANTICS,after=after,limit=1024)
        for ident in ids:
            # The disposable index narrows discovery only. Authenticate the
            # wrapper, then let the selector check its complete proof package.
            obj=store.object(ident)
            if obj['kind']=='implementation' and obj['semantics']==SEMANTICS:
                package=obj['payload'].get('package',{})
                if package.get('input_core_sha256')==source:result.append(ident)
        if len(ids)<1024:break
        after=ids[-1]
    return result


def recipe_revision(compiler,producer_root,timeout,proposal_timeout):
    digest=hashlib.sha256(compiler.read_bytes())
    for path in sorted([*producer_root.glob('*.py'),*(producer_root/'data').rglob('*')]):
        if path.is_file():digest.update(str(path.relative_to(producer_root)).encode());digest.update(path.read_bytes())
    digest.update(str((timeout,proposal_timeout)).encode())
    return digest.hexdigest()


def propose(compiler,source_path,source,root,invoke,check_package,core,timeout=30,proposal_timeout=300):
    import producers.generic_indexed
    producer_root=Path(producers.generic_indexed.__file__).parent
    revision=recipe_revision(compiler,producer_root,timeout,proposal_timeout)
    recipe=Path(root)/'proposals'/source/(revision+'.json')
    if recipe.exists():
        try:
            saved=json.loads(recipe.read_bytes());store=Store(root)
            ids=[saved['baseline'],*saved['candidates']]
            if all(i in store.members() and store.object(i)['payload']['package']['input_core_sha256']==source for i in ids):
                return store,saved['baseline'],saved['candidates'],saved['notes']
        except (ValueError,KeyError,OSError):pass
    packages=[identity_selection(source)];notes=[]
    with tempfile.TemporaryDirectory(prefix='ink-automatic-proposals-') as temporary:
        scratch=Path(temporary);model_file=scratch/'model.json'
        try:
            invoke(compiler,['emit-action-model',source_path,'-o',model_file],timeout)
            model=json.loads(model_file.read_bytes())
            if model['source']!=source:raise ValueError('proposal source binding mismatch')
            for batching in [False,True]:
                label='grouped' if batching else 'indexed';request=scratch/label/'request.json'
                try:
                    command=[sys.executable,'-m','producers.generic_indexed',str(model_file),'-o',str(request)]
                    if not batching:command.append('--no-batching')
                    paths=[str(producer_root.parent)]
                    if os.environ.get('PYTHONPATH'):paths.append(os.environ['PYTHONPATH'])
                    result=subprocess.run(command,capture_output=True,text=True,timeout=proposal_timeout,env={**os.environ,'INK_COMPILER':str(compiler),'PYTHONPATH':os.pathsep.join(paths)})
                    if result.returncode:raise ValueError(result.stderr[-1000:])
                    package=json.loads(request.read_bytes())
                    check_package(compiler,source_path,core,package,scratch/(label+'.json'),timeout)
                    packages.append(package);notes.append(dict(proposal=label,status='checked',discovery=json.loads(request.with_suffix('.discovery.json').read_bytes())))
                except (ValueError,KeyError,OSError,subprocess.TimeoutExpired) as error:
                    notes.append(dict(proposal=label,status='declined',reason=str(error)[:1000]))
        except (ValueError,KeyError,OSError,subprocess.TimeoutExpired) as error:
            notes.append(dict(proposal='source projection',status='declined',reason=str(error)[:1000]))
    store,ids=publish_candidates(root,packages,shared_knowledge=True)
    candidates=list(dict.fromkeys(ids[1:]));recipe.parent.mkdir(parents=True,exist_ok=True)
    # IDs are discovery hints. The main selector always rechecks their packages.
    if all(n['status']=='checked' for n in notes):
        recipe.write_bytes(canonical(dict(baseline=ids[0],candidates=candidates,notes=notes))+b'\n')
    return store,ids[0],candidates,notes
