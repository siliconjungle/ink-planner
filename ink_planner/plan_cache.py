"""Bounded disk cache of untrusted search hints, always replayed by the checker."""
import hashlib, json, os, sys, tempfile
from pathlib import Path

MAX_BYTES=16_000_000


def fingerprint(compiler, core, snapshot, cost_model, budget, rounds, context=None):
    from importlib.resources import files
    import ink_knowledge, producers
    def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    code={}
    for label,root in [('planner',Path(__file__).parent),('knowledge',Path(ink_knowledge.__file__).parent),('producers',Path(producers.__file__).parent)]:
        for path in sorted(root.rglob('*.py')):code[label+'/'+str(path.relative_to(root))]=digest(path)
        for path in sorted((root/'data').rglob('*.json')):code[label+'/'+str(path.relative_to(root))]=digest(path)
    for name in ['structural-costs.json','producer-catalog.json']:
        code['knowledge/'+name]=hashlib.sha256(files('ink_knowledge').joinpath(name).read_bytes()).hexdigest()
    key=dict(schema=1,compiler=digest(compiler),core=digest(core),snapshot=digest(snapshot),
             cost_model=None if cost_model is None else digest(cost_model),budget=budget,rounds=rounds,
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
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.ink-plan-',dir=directory)
    try:
        with os.fdopen(fd,'wb') as file:file.write(raw)
        os.replace(tmp,directory/(key+'.json'))
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
    # Cache eviction changes only future search cost, never proof authority.
    paths=[p for p in directory.glob('*.json') if len(p.stem)==64 and all(c in '0123456789abcdef' for c in p.stem)]
    for path in sorted(paths,key=lambda p:p.stat().st_mtime_ns,reverse=True)[256:]:
        try:path.unlink()
        except FileNotFoundError:pass
