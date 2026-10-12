"""Offline proposal of source-bound database implementations.

Producers supply definitions and proofs. Only ordinary checker acceptance admits
packages; profitability remains the existing target/workload lifecycle ranking.
"""
import hashlib,json,os,subprocess,sys,tempfile
from pathlib import Path
from ink_knowledge import Store,canonical
from ink_knowledge.implementation_candidates import identity_selection,publish_candidates,SEMANTICS


def discover(store,source):
    result=[]
    for ident in sorted(store.members()):
        obj=store.object(ident)
        if obj['kind']=='implementation' and obj['semantics']==SEMANTICS:
            package=obj['payload'].get('package',{})
            if package.get('input_core_sha256')==source:result.append(ident)
    return result


def propose(compiler,source_path,source,root,invoke,check_package,core,timeout=30,proposal_timeout=300):
    import producers.generic_indexed
    producer_root=Path(producers.generic_indexed.__file__).parent
    digest=hashlib.sha256(compiler.read_bytes())
    for path in sorted([*producer_root.glob('*.py'),*producer_root.glob('data/*')]):
        if path.is_file():digest.update(path.name.encode());digest.update(path.read_bytes())
    digest.update(str((timeout,proposal_timeout)).encode());revision=digest.hexdigest()
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
            # Recompute descriptions from checked source before using them as
            # discovery hints. Package admission still requires universal proofs.
            try:
                from producers.access_cache import discover
                access_file=scratch/'access.json'
                invoke(compiler,['emit-access',source_path,'-o',access_file],timeout)
                invoke(compiler,['check-access',source_path,access_file],timeout)
                access=json.loads(access_file.read_bytes())
                for query in discover(model,access)[:2]:
                    label='point-cache:'+query;request=scratch/('point-'+query)/'request.json'
                    try:
                        result=subprocess.run([sys.executable,'-m','producers.access_cache',str(model_file),
                            '--access',str(access_file),'--query',query,'-o',str(request)],
                            capture_output=True,text=True,timeout=proposal_timeout,
                            env={**os.environ,'INK_COMPILER':str(compiler),'PYTHONPATH':os.pathsep.join(
                                [str(producer_root.parent),os.environ.get('PYTHONPATH','')])})
                        if result.returncode:raise ValueError(result.stderr[-1000:])
                        package=json.loads(request.read_bytes())
                        check_package(compiler,source_path,core,package,scratch/('point-'+query+'.json'),timeout)
                        packages.append(package);notes.append(dict(proposal=label,status='checked',
                            discovery=json.loads(request.with_suffix('.discovery.json').read_bytes())))
                    except (ValueError,KeyError,OSError,subprocess.TimeoutExpired) as error:
                        notes.append(dict(proposal=label,status='declined',reason=str(error)[:1000]))
            except (ValueError,KeyError,OSError,subprocess.TimeoutExpired) as error:
                notes.append(dict(proposal='point access discovery',status='declined',reason=str(error)[:1000]))
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
    store,ids=publish_candidates(root,packages)
    candidates=list(dict.fromkeys(ids[1:]));recipe.parent.mkdir(parents=True,exist_ok=True)
    # IDs are discovery hints. The main selector always rechecks their packages.
    if all(n['status']=='checked' for n in notes):
        recipe.write_bytes(canonical(dict(baseline=ids[0],candidates=candidates,notes=notes))+b'\n')
    return store,ids[0],candidates,notes
