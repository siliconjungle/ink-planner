"""Per-function semantic planning with a reusable cache.

Rewrites apply inside one function body, so each function can be searched on
its own and the chains concatenated. A function's cache key binds everything
its proofs may read: the compiler, the knowledge snapshot and search options
(`context`), its own semantic body, and the core definitions of every
function it can reach, plus the program's records. Editing one function
re-plans that function and its callers only.

A cached chain is a hint, never authority: the combined chain is checked
against the actual module like any other proposal. Each function is checked
with the already accepted prefix. Rejected hints are searched again within the
same global check/time budgets; they cannot invalidate accepted earlier work.
"""
import hashlib
import json
import time
from pathlib import Path

SCHEMA = 'ink-modular-plan-cache-v2'


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


def cached_applications(directory, fingerprint, name):
    from .plan_cache import load
    raw = load(directory, fingerprint)
    if raw is None:
        return None
    cached = json.loads(raw)
    if not isinstance(cached, dict) or set(cached) != {'schema', 'function', 'applications'}:
        return None
    applications = cached['applications']
    if cached['schema'] != SCHEMA or cached['function'] != name or not isinstance(applications, list) or len(applications) > 4096:
        return None
    for step in applications:
        if not isinstance(step, dict) or set(step) != {'function', 'path', 'law', 'arguments', 'premises'}:
            return None
        if step['function'] != name or not isinstance(step['law'], str) or not step['law']:
            return None
        if not isinstance(step['path'], list) or len(step['path']) > 128 or any(type(i) is not int or i < 0 for i in step['path']):
            return None
        if not isinstance(step['arguments'], dict) or not isinstance(step['premises'], list):
            return None
    return applications


def plan(subject, catalogue, check, search, program, cache_dir, context, **options):
    """Per-function hints; every accepted extension checks the complete prefix.

    The global budget counts actual checker invocations, including cache replay
    and rejected hints. The deadline gates new work; an in-flight synchronous
    checker call still finishes normally. The caller owns final output replay.
    """
    from .plan_cache import save, MAX_BYTES
    cache_dir = Path(cache_dir)
    names = sorted(subject['functions'])
    total_budget = options.pop('budget', 4096)
    time_seconds = options.pop('time_seconds', 600)
    if type(total_budget) is not int or total_budget < 0 or time_seconds < 0:
        raise ValueError('modular search budgets')
    deadline = time.monotonic() + time_seconds
    attempts = rejected = generated = 0
    timed_out = False
    search_timed_out = memory_exhausted = False
    rounds = 0
    rows, combined = [], []
    alternatives = options.pop('alternatives', None)
    refresh = options.pop('refresh', None)
    bodies = dict(subject['functions'])

    def available():
        nonlocal timed_out
        if time.monotonic() >= deadline:
            timed_out = True
            return False
        return attempts < total_budget

    def checked(applications):
        nonlocal attempts, rejected
        if not available():
            return False
        attempts += 1
        accepted = bool(check(applications))
        rejected += not accepted
        return accepted

    for index, name in enumerate(names):
        fingerprint = function_key(name, subject, program, context)
        cached = cached_applications(cache_dir, fingerprint, name)
        if cached is not None and any(step['law'] not in catalogue['objects'] for step in cached):
            cached = None
        row = dict(function=name, cache='miss', steps=0, attempts=0, cache_rejected=False)
        started_attempts = attempts
        applications = None
        if cached is not None:
            if not cached or checked(combined + cached):
                applications = cached
                row['cache'] = 'hit'
                if cached and alternatives is not None:
                    alternatives.append(combined + cached)
            elif available():
                row['cache_rejected'] = True
        if applications is None:
            applications = []
            searched = False
            if available():
                searched = True
                # Divide remaining work without manufacturing a positive quota
                # when the caller gave zero. Unused work flows to later functions.
                quota = (total_budget - attempts + len(names) - index - 1) // (len(names) - index)
                duration = max(0, deadline - time.monotonic()) / (len(names) - index)
                sub = dict(subject, functions={name: subject['functions'][name]})
                class Alternatives:
                    def append(self, chain):
                        if alternatives is not None:
                            alternatives.append(combined + chain)
                call_options = dict(options, context_bodies=dict(bodies))
                if refresh is not None:
                    call_options['refresh'] = lambda local: refresh(dict(bodies, **local))
                if alternatives is not None:
                    call_options['alternatives'] = Alternatives()
                # Search checks local candidates against all previously accepted
                # functions. No post-hoc concatenation can introduce an unchecked
                # cross-function interaction or require unbudgeted full retries.
                applications, receipt = search(sub, catalogue,
                    lambda chain: checked(combined + chain), budget=quota,
                    time_seconds=duration, **call_options)
                generated += receipt.get('generated_proposals', 0)
                limits = receipt.get('memo', {})
                rounds += limits.get('rounds', 0)
                memory_exhausted |= limits.get('memory_exhausted', False)
                search_timed_out |= limits.get('timed_out', False)
                row['search_limits'] = limits
                row.update(initial_cost=receipt['initial_cost'], selected_cost=receipt['selected_cost'])
            raw = (_key(dict(schema=SCHEMA, function=name, applications=applications)) + '\n').encode()
            if searched and len(applications) <= 4096 and len(raw) <= MAX_BYTES:
                try:
                    save(cache_dir, fingerprint, raw)
                except OSError:
                    pass  # A disposable cache write cannot discard checked work.
        row.update(steps=len(applications), attempts=attempts-started_attempts)
        rows.append(row)
        combined += applications
        # Keep refresh discovery on the exact admitted intermediate syntax.
        from .semantic_search import replace, substitute
        for step in applications:
            law = catalogue['objects'].get(step['law'])
            if law is not None:
                bodies[name] = replace(bodies[name], step['path'], substitute(law['to'], step['arguments']))
    receipt = dict(attempts=attempts, rejected=rejected, generated_proposals=generated,
                   exhausted=attempts >= total_budget, estimate='structural; not measured runtime',
                   memo=dict(rounds=rounds, timed_out=timed_out or search_timed_out, memory_exhausted=memory_exhausted),
                   modular=dict(functions=rows, hits=sum(r['cache']=='hit' for r in rows),
                                misses=sum(r['cache']=='miss' for r in rows),
                                replanned=any(r['cache_rejected'] for r in rows),
                                scope='Every cache replay and search extension checks the complete accepted prefix; one global check budget and deadline'))
    return combined, receipt
