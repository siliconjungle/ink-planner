"""Bounded untrusted selected-plan cache. Every hit requires fresh whole replay."""
import hashlib,json,os,tempfile,time
from pathlib import Path
from ink_knowledge import identity

LIMIT=16000000

def key(planner,source,snapshot,options,usage_profiles):
    inputs={}
    for name in ['profile','workload','target_profile','measure_workload','measure_holdout','work_model','work_workload']:
        path=getattr(options,name,None)
        inputs[name]=hashlib.sha256(path.read_bytes()).hexdigest()if path else None
    # Target availability never follows from this key; fresh checker replay also
    # checks the selected target on a hit. Host fingerprints bind cost reuse.
    try:
        from .offline_measurements import target_context
        host=target_context(planner)
    except (ValueError,OSError):host=None
    fields=['target','planning_effort','budget','state_limit','time_seconds','memory_bytes','check_timeout',
            'proposal_timeout','recipe_workers','semantic_seconds','semantic_budget','semantic_rounds','candidate_limit',
            'measure_timeout','measure_processes','minimum_measured_gain_ppm','measurement_mode','migration_count','memory_limit']
    return identity(dict(schema=1,source=source,snapshot=snapshot,compiler=planner.compiler_hash,code=planner.code_hash,
                         registry=planner.registry_digest,options={k:getattr(options,k,None)for k in fields},
                         inputs=inputs,usage=usage_profiles,host=host))

def load(root,key):
    path=Path(root)/'compiled-cache'/f'{key}.json'
    try:
        if path.stat().st_size>LIMIT:return None
        value=json.loads(path.read_bytes())
        if value['schema']!=1 or value['key']!=key or not 0<=time.time()-value['created_unix']<=86400:return None
        return value
    except (OSError,ValueError,KeyError,TypeError):return None

def save(root,key,package,receipt):
    directory=Path(root)/'compiled-cache';directory.mkdir(exist_ok=True)
    # Cached alternatives remain historical diagnostics. Only the selected
    # packet is replayed on a hit, never reported as newly searched/admitted.
    compact={k:v for k,v in receipt.items()if k not in ['edges','rejected','recipes','candidates']}
    value=dict(schema=1,key=key,created_unix=int(time.time()),package=package,receipt=compact)
    # Receipts contain timing/model floats; only knowledge objects use the
    # stricter integer-only canonical encoding. Cache packets are replayed.
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),allow_nan=False).encode()+b'\n'
    if len(raw)>LIMIT:return
    fd,tmp=tempfile.mkstemp(prefix='.ink-cache-',dir=directory)
    try:
        with os.fdopen(fd,'wb')as f:f.write(raw)
        os.replace(tmp,directory/f'{key}.json')
    finally:
        if Path(tmp).exists():Path(tmp).unlink()
    for path in sorted(directory.glob('*.json'),key=lambda p:p.stat().st_mtime_ns,reverse=True)[128:]:
        try:path.unlink()
        except FileNotFoundError:pass
