"""Per-function semantic planning with a reusable cache.

Rewrites apply inside one function body, so each function can be searched on
its own and the chains concatenated. A function's cache key binds everything
its proofs may read: the compiler, the knowledge snapshot and search options
(`context`), its own semantic body, and the core definitions of every
function it can reach, plus the program's records. Editing one function
re-plans that function and its callers only.

A cached chain is a hint, never authority: the combined chain is checked
against the actual module like any other proposal. If it is rejected, every
function is searched again and the cache is rewritten.
"""
import hashlib
import json
from pathlib import Path

SCHEMA = 'ink-modular-plan-cache-v1'


def _key(v):
    return json.dumps(v, sort_keys=True, separators=(',', ':'))


def callees(name, functions, seen=None):
    """Names reachable from `name` through direct calls in core bodies."""
    seen = set() if seen is None else seen
    if name in seen or name not in functions:
        return seen
    seen.add(name)

    def walk(e):
        if isinstance(e, dict):
            call = e.get('Call')
            if isinstance(call, list) and call and isinstance(call[0], str):
                callees(call[0], functions, seen)
            for v in e.values():
                walk(v)
        elif isinstance(e, list):
            for v in e:
                walk(v)
    walk(functions[name]['body'])
    return seen


def function_key(name, subject, program, context):
    functions = {f['name']: f for f in program['functions']}
    reach = sorted(callees(name, functions))
    material = dict(schema=SCHEMA, context=context, function=name, body=subject['functions'][name],
                    definitions=[functions[n] for n in reach], records=program.get('records', {}),
                    enums=program.get('enums', {}))
    return hashlib.sha256(_key(material).encode()).hexdigest()


def plan(subject, catalogue, check, search, program, cache_dir, context, **options):
    """Returns (applications, receipt). `search` has semantic_search.search's signature."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    names = sorted(subject['functions'])
    total_budget = options.pop('budget', 4096)
    time_seconds = options.pop('time_seconds', 600)
    chains, rows = {}, []
    misses = [n for n in names if not (cache_dir / (function_key(n, subject, program, context) + '.json')).exists()]
    share_budget = max(1, total_budget // max(1, len(misses)))
    share_time = max(1, time_seconds // max(1, len(misses)))

    def searched(name):
        sub = dict(subject, functions={name: subject['functions'][name]})
        applications, receipt = search(sub, catalogue, check, budget=share_budget, time_seconds=share_time, **options)
        return applications, receipt

    for name in names:
        path = cache_dir / (function_key(name, subject, program, context) + '.json')
        if path.exists():
            cached = json.loads(path.read_text())
            if cached.get('schema') == SCHEMA and all(a.get('function') == name for a in cached['applications']):
                chains[name] = cached['applications']
                rows.append(dict(function=name, cache='hit', steps=len(chains[name])))
                continue
        applications, receipt = searched(name)
        chains[name] = applications
        rows.append(dict(function=name, cache='miss', steps=len(applications), attempts=receipt['attempts'],
                         initial_cost=receipt['initial_cost'], selected_cost=receipt['selected_cost']))
    combined = [a for n in names for a in chains[n]]
    replanned = False
    if combined and not check(combined):
        # A stale or wrong hint costs one rejected check, then a full re-plan.
        replanned = True
        for name in names:
            chains[name], _ = searched(name)
        combined = [a for n in names for a in chains[n]]
        if combined and not check(combined):
            combined = []
    for name in names:
        path = cache_dir / (function_key(name, subject, program, context) + '.json')
        if any(r['function'] == name and r['cache'] == 'miss' for r in rows) or replanned:
            path.write_text(_key(dict(schema=SCHEMA, function=name, applications=chains[name])) + '\n')
    receipt = dict(attempts=sum(r.get('attempts', 0) for r in rows), rejected=0, generated_proposals=0,
                   exhausted=False, estimate='structural; not measured runtime',
                   modular=dict(functions=rows, hits=sum(1 for r in rows if r['cache'] == 'hit'),
                                misses=sum(1 for r in rows if r['cache'] == 'miss'), replanned=replanned,
                                scope='Cached per-function chains are hints; the combined chain is checked against the module'))
    return combined, receipt
