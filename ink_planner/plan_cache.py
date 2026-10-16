"""Bounded disk cache of untrusted search hints, always replayed by the checker."""
import hashlib, json, os, stat, sys, tempfile
from pathlib import Path

MAX_BYTES=16_000_000


def fingerprint(compiler, core, snapshot, cost_model, budget, rounds, context=None, search_options=None):
    from importlib.resources import files
    import ink_knowledge, producers
    def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    code={}
    for label,root in [('planner',Path(__file__).parent),('knowledge',Path(ink_knowledge.__file__).parent),('producers',Path(producers.__file__).parent)]:
        for path in sorted(root.rglob('*.py')):code[label+'/'+str(path.relative_to(root))]=digest(path)
        for path in sorted((root/'data').rglob('*')):
            if path.is_file():code[label+'/'+str(path.relative_to(root))]=digest(path)
    for name in ['structural-costs.json','producer-catalog.json','implementation-recipes.json','property-probes.json']:
        code['knowledge/'+name]=hashlib.sha256(files('ink_knowledge').joinpath(name).read_bytes()).hexdigest()
    key=dict(schema=1,compiler=digest(compiler),core=digest(core),snapshot=digest(snapshot),
             cost_model=None if cost_model is None else digest(cost_model),budget=budget,rounds=rounds,search_options=search_options,
             context=None if context is None else digest(context),code=code,python=list(sys.version_info[:3]))
    return hashlib.sha256(json.dumps(key,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load(directory, key):
    try:
        with (Path(directory)/(key+'.json')).open('rb') as file:raw=file.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:return None
        json.loads(raw)
        return raw
    except (OSError,ValueError,RecursionError):return None


def save(directory, key, raw):
    if len(raw)>MAX_BYTES:raise ValueError('plan cache byte limit')
    return write_hint(directory,key,raw,256,prefix='.ink-plan-')


def write_hint(directory,key,raw,keep,*,prefix='.ink-cache-'):
    """Best-effort atomic hint storage; authoritative output is separate.

    Return False on storage/eviction failure. Genuine serialization and other
    programming errors remain errors. A cached packet still requires replay.
    """
    directory=Path(directory)
    try:
        directory.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=prefix,dir=directory)
        try:
            with os.fdopen(fd,'wb') as file:file.write(raw)
            os.replace(tmp,directory/(key+'.json'))
        finally:
            try:os.unlink(tmp)
            except FileNotFoundError:pass
        prune(directory,keep)
    except OSError:
        return False
    return True


def prune(directory,keep):
    """Evict only cache-owned regular files; tolerate concurrent removal."""
    # Cache eviction changes only future search cost, never proof authority.
    entries=[]
    for path in directory.glob('*.json'):
        if len(path.stem)!=64 or any(c not in '0123456789abcdef' for c in path.stem):continue
        try:info=path.lstat()
        except FileNotFoundError:continue  # Another planner may evict it first.
        if stat.S_ISREG(info.st_mode):entries.append((info.st_mtime_ns,path.name,path))
    for _,_,path in sorted(entries,reverse=True)[keep:]:
        try:path.unlink()
        except FileNotFoundError:pass
