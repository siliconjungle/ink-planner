"""Bounded untrusted selected-plan cache. Every hit requires fresh whole replay."""
import hashlib,json,os,subprocess,tempfile,time
from pathlib import Path
from ink_knowledge import identity
from .plan_cache import prune
from .measurement_context import configuration

LIMIT=16000000

def execution_binding(planner,options):
    # Target availability never follows from this binding; selected packets
    # still need fresh whole-chain replay. Keep cheap controls on host failure.
    try:
        from .offline_measurements import target_context
        host=target_context(planner)
    except (ValueError,OSError,subprocess.SubprocessError):host=None
    return dict(configuration=configuration(options),host=host)

def key(planner,source,snapshot,options,usage_profiles,*,execution=None):
    inputs={}
    for name in ['profile','workload','target_profile','measure_workload','measure_holdout','work_model','work_workload']:
        path=getattr(options,name,None)
        inputs[name]=hashlib.sha256(path.read_bytes()).hexdigest()if path else None
    execution=execution if execution is not None else execution_binding(planner,options)
    fields=['target','planning_effort','budget','state_limit','expansion_limit','time_seconds','memory_bytes','check_timeout',
            'proposal_timeout','recipe_workers','semantic_seconds','semantic_budget','semantic_rounds','candidate_limit',
            'measure_timeout','reference_budget','measure_processes','minimum_measured_gain_ppm','measurement_mode','migration_count','memory_limit',
            'search_goal_memory_mb','search_goal_input_bytes']
    goal=getattr(planner,'search_goal',None)
    return identity(dict(schema=1,source=source,snapshot=snapshot,compiler=planner.compiler_hash,code=planner.code_hash,
                         registry=planner.registry_digest,execution=execution,options={k:getattr(options,k,None)for k in fields},
                         inputs=inputs,usage=usage_profiles,search_goal=goal.binding if goal else None,
                         search_goal_timeout=str(getattr(options,'search_goal_timeout',None))))

def load(root,key):
    path=Path(root)/'compiled-cache'/f'{key}.json'
    try:
        with path.open('rb') as file:raw=file.read(LIMIT+1)
        if len(raw)>LIMIT:return None
        value=json.loads(raw)
        if not isinstance(value,dict) or not isinstance(value.get('receipt'),dict):return None
        if value['schema']!=1 or value['key']!=key or not 0<=time.time()-value['created_unix']<=86400:return None
        if value['receipt'].get('unchecked_checks',0):return None
        goal=value['receipt'].get('search_goal')
        if goal is not None and(not isinstance(goal,dict)or goal.get('failures',0)or goal.get('disabled',False)):return None
        return value
    except (OSError,ValueError,KeyError,TypeError,RecursionError):return None

def save(root,key,package,receipt):
    # A deadline leaves candidate applicability unknown. Do not turn a transient
    # missed check into a cached baseline that suppresses subsequent exploration.
    if receipt.get('unchecked_checks',0):return
    goal=receipt.get('search_goal')
    if goal is not None and(not isinstance(goal,dict)or goal.get('failures',0)or goal.get('disabled',False)):return
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
    prune(directory,128)
