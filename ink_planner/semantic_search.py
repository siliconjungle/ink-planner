#!/usr/bin/env python3
"""Bounded, untrusted search over arbitrary typed semantic term patterns.
No operation recognisers determine applicability. Every proposed step and every
premise is replayed by the independent compiler checker. Cost guides search only.
"""
import argparse
import copy
import hashlib
import json
import time
import subprocess
import tempfile
from pathlib import Path
if __package__:
    from .target_costs import TargetCostModel
else:
    from target_costs import TargetCostModel

SEMANTICS = 'ink-conditional-laws-v1'


def children(t):
    n = t['node']
    if 'Op' in n: return n['Op'][1]
    if 'Lambda' in n: return [n['Lambda']]
    if 'Cons' in n: return n['Cons']
    return []


def set_child(t, i, child):
    t = copy.deepcopy(t); n = t['node']
    if 'Op' in n: n['Op'][1][i] = child
    elif 'Lambda' in n: n['Lambda'] = child
    elif 'Cons' in n: n['Cons'][i] = child
    else: raise ValueError('invalid child')
    return t


def map_children(t, transform):
    """Rebuild once without copying siblings for every child replacement."""
    n = t['node']
    if isinstance(n, dict) and 'Op' in n:
        node = {'Op': [n['Op'][0], [transform(c) for c in n['Op'][1]]]}
    elif isinstance(n, dict) and 'Lambda' in n:
        node = {'Lambda': transform(n['Lambda'])}
    elif isinstance(n, dict) and 'Cons' in n:
        node = {'Cons': [transform(c) for c in n['Cons']]}
    else:
        return copy.deepcopy(t)
    return dict(sort=copy.deepcopy(t['sort']), node=node)


def shift(t, amount, cutoff=0):
    n = t['node']
    if isinstance(n, dict) and 'Bound' in n and n['Bound'] >= cutoff:
        out = copy.deepcopy(t)
        out['node']['Bound'] += amount
        if out['node']['Bound'] < cutoff: raise ValueError('binder capture')
        return out
    return map_children(t, lambda child: shift(child, amount, cutoff + int('Lambda' in n)))


def extract_hole(t, depth, local=0):
    n = t['node']
    if 'Bound' in n and local <= n['Bound'] < local + depth:
        raise ValueError('hole captures pattern binder')
    for child in children(t): extract_hole(child, depth, local + int('Lambda' in n))
    return shift(t, -depth)


def match(pattern, t, params, bindings, depth=0):
    if pattern['sort'] != t['sort']: return False
    n = pattern['node']; actual = t['node']
    if 'Free' in n and n['Free'] in params:
        try: arg = extract_hole(t, depth)
        except ValueError: return False
        name = n['Free']
        if name in bindings: return bindings[name] == arg
        bindings[name] = arg; return True
    if type(n) != type(actual): return False
    if not isinstance(n, dict): return n == actual
    if set(n) != set(actual): return False
    if 'Op' in n and n['Op'][0] != actual['Op'][0]: return False
    if not children(pattern): return n == actual
    if len(children(pattern)) != len(children(t)): return False
    return all(match(a, b, params, bindings, depth + int('Lambda' in n))
               for a, b in zip(children(pattern), children(t)))


def substitute(t, args, depth=0):
    n = t['node']
    if isinstance(n, dict) and 'Free' in n and n['Free'] in args:
        return shift(args[n['Free']], depth)
    return map_children(t, lambda child: substitute(child, args, depth + int('Lambda' in n)))


def positions(t, path=()):
    yield path, t
    for i, child in enumerate(children(t)): yield from positions(child, path + (i,))


def replace(t, path, new):
    if not path: return new
    return set_child(t, path[0], replace(children(t)[path[0]], path[1:], new))


def estimated_list_length(t, fallback):
    """Conservative structural cardinality for an optional search hypothesis."""
    n = t['node']
    if n == 'Nil': return 0
    if isinstance(n, dict):
        if 'Cons' in n: return 1 + estimated_list_length(n['Cons'][1], fallback)
        if 'Op' in n:
            name, args = n['Op']
            if name == 'method:concat' and len(args) == 2:
                return sum(estimated_list_length(a, fallback) for a in args)
            if name == 'method:zip' and len(args) >= 2:
                return min(estimated_list_length(a, fallback) for a in args[:2])
            if name in ('method:map', 'method:filter') and args:
                return estimated_list_length(args[0], fallback)
    return fallback


def cost(t, operation_costs=None):
    """Structural estimate, never runtime evidence or correctness authority."""
    if operation_costs is None:
        from ink_knowledge.cost_models import structural_prices
        operation_costs = structural_prices()
    else:
        from ink_knowledge.cost_models import structural_prices
        operation_costs = dict(structural_prices(), **operation_costs)
    return _cost(t,operation_costs)

def _cost(t,operation_costs):
    n = t['node']; costs = [_cost(x, operation_costs) for x in children(t)]
    if isinstance(n, dict) and 'Op' in n:
        name, args = n['Op']
        # A generic list callback repeats work; scalar work outside it does not.
        # This preset is an untrusted search hypothesis, never proof authority.
        # Inspect sorts rather than theorem IDs, backend names or callback shape.
        repetitions = (operation_costs or {}).get('estimate:loop_iterations', 1)
        if args and len(args) > 1 and isinstance(args[0].get('sort'), dict) and isinstance(args[-1].get('sort'), dict):
            first = args[0]['sort'].get('Value')
            if isinstance(first, dict) and 'List' in first and 'Arrow' in args[-1]['sort']:
                if (operation_costs or {}).get('estimate:list_cardinality', 0):
                    repetitions = estimated_list_length(args[0], repetitions)
                costs[-1] *= repetitions
        # A statically known lambda application reifies to a local binding,
        # not a dynamic callback invocation. The optional binding price still
        # counts evaluation of both the argument and the lambda body.
        binding = (operation_costs or {}).get('estimate:let_binding')
        if binding is not None and name == 'apply' and len(args) == 2:
            if isinstance(args[0].get('node'), dict) and 'Lambda' in args[0]['node']:
                return binding + sum(costs)
        if operation_costs is not None and name in operation_costs:
            return operation_costs[name] + sum(costs)
        if name == 'call:repeat':
            count = args[0]['node'].get('Nat', operation_costs.get('estimate:repeat_iterations', 0))
            return costs[1] + count * (costs[2] + operation_costs.get('estimate:repeat_step', 0))
        # Lists carry their length and support direct indexing. Computing the
        # receiver still pays its full structural cost, e.g. map/filter/concat.
        if name in ('method:at_or', 'call:count'):
            return operation_costs.get(name, operation_costs.get('estimate:scalar', 0)) + sum(costs)
        if name.startswith('method:') or name in ('call:sum', 'call:count', 'call:foldr', 'call:fold_until'):
            return operation_costs.get('estimate:collection', 0) + sum(costs)
        if name == 'nat.word': return operation_costs.get(name, 0)
        if name == 'apply': return operation_costs.get(name, 0) + sum(costs)
    return operation_costs.get('estimate:scalar', 0) + sum(costs)


def key(v): return json.dumps(v, sort_keys=True, separators=(',', ':'))

def condition_proof(goal, catalogue, depth=0, active=None, work=None):
    """General bounded proof production: instantiate a checked lemma, or lift
    child proofs through an unchanged operator. Normalization is the fallback.
    The checker, including actual branch facts, remains the only authority.
    """
    active=set() if active is None else active
    work=[4096] if work is None else work
    fingerprint=key(goal);work[0]-=1
    # Certificates are proposals until the final selection checker replays their
    # proof under the actual source environment and complete dependency view.
    for fact in catalogue.get('certificates',[]):
        if fact['goal']==goal:return fact['proof']
    if work[0]<0 or depth>=128 or fingerprint in active:return 'Normalize'
    active=active|{fingerprint}
    # Supporting lemmas need not be enabled as rewrite roots. The compiler
    # admits only the dependency closure, so unreachable objects cannot confer authority.
    for ident, law in catalogue['objects'].items():
        args = {}
        if match(law['from'], goal['from'], law['params'], args) and set(args) == set(law['params']):
            if substitute(law['to'], args) == goal['to']:
                premises = [condition_proof({'from': substitute(c['from'], args), 'to': substitute(c['to'], args)},
                                            catalogue, depth + 1, active, work) for c in law['conditions']]
                return {'Use': dict(law=ident, arguments=dict(sorted(args.items())), premises=premises)}
    left, right = goal['from'], goal['to']; ln, rn = left['node'], right['node']
    if left['sort'] == right['sort'] and isinstance(ln, dict) and isinstance(rn, dict) and 'Op' in ln and 'Op' in rn:
        if ln['Op'][0] == rn['Op'][0] and len(children(left)) == len(children(right)):
            proofs = [condition_proof({'from': a, 'to': b}, catalogue, depth + 1, active, work)
                      for a, b in zip(children(left), children(right))]
            if any(p != 'Normalize' for p in proofs): return {'Congruence': proofs}
    return 'Normalize'


def search(subject, catalogue, check, budget=4096, width=16, rounds=None, operation_costs=None, core_program=None, diverse=True, strategy="memo", memory_bytes=256_000_000, time_seconds=600, alternatives=None):
    if strategy not in {"memo","beam"}:raise ValueError("unknown search strategy")
    if budget<0 or width<1 or memory_bytes<1 or time_seconds<0 or (rounds is not None and rounds<0):raise ValueError("search budgets")
    started=time.monotonic();deferred=[];memo_edges=[];iteration=0;memory_exhausted=False;timed_out=False;prefix_states={}
    objects = catalogue['objects']
    # Index on the typed root constructor. New operation combinations require
    # data entries, never a new matching case. Root-variable laws are universal.
    index = {}
    def shape(t):
        n = t['node']
        return (key(t['sort']), 'Op:' + n['Op'][0] if isinstance(n, dict) and 'Op' in n else
                next(iter(n)) if isinstance(n, dict) else str(n))
    for ident in catalogue['roots']:
        law = objects[ident]; pattern = law['from']
        if isinstance(pattern['node'], dict) and 'Free' in pattern['node'] and pattern['node']['Free'] in law['params']:
            root = (key(pattern['sort']), '*')
        else: root = shape(pattern)
        index.setdefault(root, []).append(ident)
    component_model = operation_costs if isinstance(operation_costs, TargetCostModel) else None
    if component_model is not None and core_program is None:
        raise ValueError('schema2 requires actual core parameter signatures')
    def score_bodies(bodies):
        if component_model is not None:
            return component_model.estimate_module(bodies, core_program)['score_ns']
        return sum(cost(t, operation_costs) for t in bodies.values())
    from ink_knowledge.cost_models import structural_components
    dimensions = ('allocation_events', 'traversal_elements', 'copy_bytes')
    component_prices = structural_components()
    profile_cache = {}
    def profile(bodies):
        fingerprint = key(bodies)
        if fingerprint not in profile_cache:
            if component_model is not None:
                profile_cache[fingerprint] = component_model.estimate_module(bodies, core_program)['components']
            else:
                result = dict.fromkeys(dimensions, 0)
                for body in bodies.values():
                    for _, t in positions(body):
                        node = t['node']
                        name = node['Op'][0] if isinstance(node, dict) and 'Op' in node else next(iter(node)) if isinstance(node, dict) else node
                        for dimension in dimensions:
                            result[dimension] += component_prices.get(dimension, {}).get(name, 0)
                profile_cache[fingerprint] = result
            # Cache is disposable; deterministic bounds do not consume proof budget.
            if len(profile_cache) > 4096:
                result = profile_cache[fingerprint]
                profile_cache.clear()
                profile_cache[fingerprint] = result
        return profile_cache[fingerprint]
    start = subject['functions']; initial = score_bodies(start)
    frontier = [(initial, start, [])]; best = frontier[0]; seen = {key(start)}
    prefix_states[hashlib.sha256(key([]).encode()).hexdigest()]=key(start)
    rejected = 0; checked = 0; exhausted = False
    generated = 0;memo_bytes=len(key(start).encode());dropped=0
    while rounds is None or iteration<rounds:
        if time.monotonic()-started>=time_seconds:timed_out=True;break
        iteration+=1
        pending = deferred;old_pending=len(deferred);deferred=[]
        pending_seen = {key(x[2])for x in pending}
        pending_bytes=sum(len(x[3].encode())+len(key(x[2]).encode())for x in pending)
        for _, bodies, applications in frontier:
            for name, body in bodies.items():
                for path, target in positions(body):
                    ids = index.get(shape(target), []) + index.get((key(target['sort']), '*'), [])
                    for ident in ids:
                        law = objects[ident]; args = {}
                        if not match(law['from'], target, law['params'], args): continue
                        if set(args) != set(law['params']): continue
                        new = substitute(law['to'], args)
                        if new == target: continue
                        updated = dict(bodies); updated[name] = replace(body, path, new)
                        fingerprint = key(updated)
                        if fingerprint in seen: continue
                        step = dict(function=name, path=list(path), law=ident, arguments=dict(sorted(args.items())),
                                    premises=['Normalize'] * len(law['conditions']))
                        proposal = applications + [step]
                        proposal_key = key(proposal)
                        if proposal_key in pending_seen: continue
                        size=len(fingerprint.encode())+len(proposal_key.encode())
                        if memo_bytes+pending_bytes+size>memory_bytes:
                            memory_exhausted=True;dropped+=1;continue
                        pending_bytes+=size;pending_seen.add(proposal_key)
                        score = score_bodies(updated)
                        pending.append((score, updated, proposal, fingerprint, law, args))
        # Rank untrusted proposals before spending bounded checker work. Only
        # the best `width` admitted bodies can enter the next beam; checking
        # lower-ranked alternatives after filling it cannot improve that beam.
        # A rejection never marks a body seen or hides another proof of it.
        generated += len(pending)-old_pending
        pending.sort(key=lambda x: (x[0], len(x[2]), key(x[1])))
        if diverse and pending and width > 1:
            # Keep the cheapest proposal, then minima of different work profiles.
            # Never pretend repeated expressions are shared: each executed source
            # occurrence is counted. Only explicit source bindings supply sharing.
            preferred = [pending[0]]
            used = {pending[0][3]}
            for dimension in dimensions:
                winner = min(pending, key=lambda x: (profile(x[1]).get(dimension, 0), x[0], len(x[2]), x[3]))
                if winner[3] not in used and len(preferred) < width:
                    preferred.append(winner)
                    used.add(winner[3])
            pending = preferred + [x for x in pending if x not in preferred]
        candidates = []
        processed=0
        for processed, (score, updated, proposal, fingerprint, law, args) in enumerate(pending,1):
            if fingerprint in seen: continue
            if checked >= budget: exhausted = True; processed-=1; break
            checked += 1
            if not check(proposal):
                rejected += 1
                step = proposal[-1]
                premise_alternatives = [condition_proof({'from': substitute(c['from'], args),
                                                 'to': substitute(c['to'], args)}, catalogue)
                                for c in law['conditions']]
                if premise_alternatives == step['premises']: continue
                if checked >= budget: exhausted = True; break
                proposal = proposal[:-1] + [dict(step, premises=premise_alternatives)]
                checked += 1
                if not check(proposal): rejected += 1; continue
            seen.add(fingerprint)
            memo_bytes+=len(fingerprint.encode())+len(key(proposal).encode())
            if alternatives is not None:alternatives.append(proposal)
            parent=prefix_states[hashlib.sha256(key(proposal[:-1]).encode()).hexdigest()]
            prefix_states[hashlib.sha256(key(proposal).encode()).hexdigest()]=fingerprint
            memo_edges.append(dict(from_state=parent,to_state=fingerprint,law=proposal[-1]["law"],depth=len(proposal)))
            state = (score, updated, proposal)
            candidates.append(state)
            if score < best[0]: best = state
            if len(candidates) >= width: break
        if strategy=="memo":
            retained=0
            for item in pending[processed:]:
                if item[3]in seen:continue
                size=len(item[3].encode())+len(key(item[2]).encode())
                if memo_bytes+retained+size>memory_bytes:memory_exhausted=True;break
                deferred.append(item);retained+=size
        if exhausted or memory_exhausted:break
        if not candidates and not deferred:break
        frontier = candidates
    receipt = dict(initial_cost=initial, selected_cost=best[0], attempts=checked,
                   rejected=rejected, generated_proposals=generated, exhausted=exhausted, estimate='structural; not measured runtime')
    receipt["memo"]=dict(strategy=strategy,states=len(seen),edges=len(memo_edges),deferred=len(deferred),rounds=iteration,memory_exhausted=memory_exhausted,timed_out=timed_out,retained_memo_bytes=memo_bytes,dropped_proposals=dropped,scope="Exact typed term states; admitted edges only; no hash grants proof authority")
    receipt['beam_diversity'] = dict(enabled=diverse, dimensions=list(dimensions),
        initial=profile(start), selected=profile(best[1]), scope='Untrusted source-work hypotheses; no timing or proof authority')
    if component_model is not None:
        receipt['estimate'] = 'schema2 untrusted source component hypothesis in ns; not measured runtime or physical GPU cost'
        receipt['initial_components'] = component_model.estimate_module(start, core_program)
        receipt['selected_components'] = component_model.estimate_module(best[1], core_program)
    return best[2], receipt


def load_cost_model(path):
    """Untrusted operation prices guide search, never applicability or replay."""
    model=json.loads(Path(path).read_text())
    if isinstance(model, dict) and type(model.get('schema')) is int and model['schema'] == 2:
        return TargetCostModel(model)
    if not isinstance(model,dict) or set(model)!={'schema','operation_costs'} or type(model['schema']) is not int or model['schema']!=1:
        raise ValueError('invalid cost model schema')
    prices=model['operation_costs']
    if not isinstance(prices,dict) or len(prices)>1024:
        raise ValueError('invalid operation prices')
    for name,price in prices.items():
        if not isinstance(name,str) or not name or len(name)>256 or type(price) is not int or not 0<=price<=1000000:
            raise ValueError('invalid operation price')
    from ink_knowledge.cost_models import structural_prices
    return {**structural_prices(), **prices}


def selection_roots(applications):
    """Include selected rewrites and any laws used by nested premise proofs."""
    roots=set()
    def walk(v):
        if isinstance(v, dict):
            if 'law' in v: roots.add(v['law'])
            for child in v.values(): walk(child)
        elif isinstance(v, list):
            for child in v: walk(child)
    walk(applications)
    return sorted(roots)


def discovery_objects(store, roots, fetched=None):
    """Authenticate discovery bytes individually; no aggregate proof admission."""
    objects = {}
    pending = list(sorted(roots))
    while pending:
        ident = pending.pop()
        if ident in objects: continue
        obj = fetched[ident] if fetched is not None and ident in fetched else store.object(ident)
        objects[ident] = obj
        pending.extend(obj['dependencies'])
    return objects


class SelectionChecker:
    """Bounded checker processes; only exact proposals become proof authority."""
    def __init__(self, compiler, core, store, directory):
        self.compiler, self.core, self.store, self.directory = compiler, core, store, directory
        self.sessions = {}
        self.incremental_sessions = set()
        self.checked_roots = set()
        self.session_attempts = 0
        self.admission_batches = 0
        self.newly_admitted = 0
        self.rejected_closures = 0

    @staticmethod
    def close_session(session):
        session.stdin.close()
        try: session.wait(timeout=10)
        except subprocess.TimeoutExpired: session.kill(); session.wait()
        session.stdout.close(); session.stderr.close()

    def check(self, applications):
        roots = tuple(selection_roots(applications))
        # A successfully admitted superset can check a smaller proposal too.
        # Every proposal is rechecked against the original module in the child.
        available = next((k for k in self.sessions if set(roots) <= set(k)), None)
        if available is not None:
            roots_key = available
        else:
            roots_key = roots
        if roots not in self.sessions:
            if available is not None:
                session = self.sessions.pop(available)
                self.sessions[available] = session
            else:
                try: bundle = self.store.bundle(roots)
                except ValueError:
                    self.rejected_closures += 1
                    return False
                candidates = [k for k,s in self.sessions.items() if s in self.incremental_sessions]
                extended = False
                if candidates:
                    prior = max(candidates, key=lambda k: len(set(k) & set(roots)))
                    session = self.sessions[prior]
                    session.stdin.write(json.dumps({'admit': bundle}) + '\n'); session.stdin.flush()
                    response = session.stdout.readline()
                    result = json.loads(response) if response else {}
                    if result.get('admitted'):
                        self.admission_batches += 1
                        self.newly_admitted += result.get('added', 0)
                        roots_key = tuple(sorted(set(prior) | set(roots)))
                        del self.sessions[prior]
                        self.sessions[roots_key] = session
                        extended = True
                    elif result.get('error') != 'knowledge view schema/size limit':
                        self.rejected_closures += 1
                        return False
                    # A full union does not make a separately bounded closure invalid.
                if not extended:
                    if len(self.sessions) >= 8:
                        first = next(iter(self.sessions))
                        discarded = self.sessions.pop(first)
                        self.incremental_sessions.discard(discarded)
                        self.close_session(discarded)
                    path = self.directory / ('closure-' + hashlib.sha256(key(roots).encode()).hexdigest() + '.json')
                    path.write_text(json.dumps(bundle))
                    self.session_attempts += 1
                    session = subprocess.Popen([self.compiler, 'selection-session', self.core, path],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    ready = session.stdout.readline()
                    capability = json.loads(ready) if ready else {}
                    if not capability.get('ready'):
                        self.close_session(session)
                        self.rejected_closures += 1
                        return False
                    self.sessions[roots] = session
                    if capability.get('incremental'): self.incremental_sessions.add(session)
        session = self.sessions.pop(roots_key)
        self.sessions[roots_key] = session
        session.stdin.write(json.dumps(applications) + '\n'); session.stdin.flush()
        response = session.stdout.readline()
        checked = bool(response and json.loads(response).get('checked'))
        if checked: self.checked_roots.update(roots)
        return checked

    def close(self):
        for session in self.sessions.values(): self.close_session(session)
        self.sessions.clear()
        self.incremental_sessions.clear()


def discover_roots(store, sorts, terms=None):
    """Page type-indexed discovery independently of proof-check attempts."""
    roots = set()
    if terms is not None:
        from ink_knowledge.pattern_index import heads,candidates,targets
        pending=set().union(*(heads(t)for t in terms));seen=set()
        while pending:
            sort,head=pending.pop()
            if (sort,head)in seen:continue
            seen.add((sort,head));after=None
            while True:
                page=candidates(store,json.loads(sort),head,after=after,limit=1024)
                for ident in page:
                    if ident not in roots:roots.add(ident);pending.update(targets(store,ident)-seen)
                if len(page)<1024:break
                after=page[-1]
            if len(roots)>8192 or len(seen)>16384:raise ValueError('pattern discovery reachability budget')
        return roots
    for sort in sorts:
        after = None
        while True:
            page = store.query(kind='rewrite', semantics=SEMANTICS, result=sort,
                               after=after, limit=1024)
            roots.update(page)
            if len(page) < 1024: break
            after = page[-1]
    return roots


def main():
    a = argparse.ArgumentParser(); a.add_argument('--compiler', type=Path, required=True)
    a.add_argument('--core', type=Path, required=True); a.add_argument('--snapshot', type=Path, required=True)
    a.add_argument('--knowledge-root', type=Path)
    a.add_argument('-o', type=Path, required=True); a.add_argument('--budget', type=int, default=4096)
    a.add_argument('--cost-model',type=Path,help='Untrusted JSON operation prices; default structural estimate when omitted')
    a.add_argument('--compact-output',action='store_true',help='Write the selected proof closure without JSON indentation')
    a.add_argument('--rounds',type=int,default=None,help='optional expansion limit; omitted searches to a fixed point or resource budget')
    a.add_argument('--width',type=int,default=16)
    a.add_argument('--strategy',choices=['memo','beam'],default='memo')
    a.add_argument('--memory-bytes',type=int,default=256_000_000)
    a.add_argument('--time-seconds',type=int,default=600)
    a.add_argument('--alternatives',type=Path,help='write every admitted typed alternative for layered planning')
    a.add_argument('--properties',type=Path,help='source-bound property packets; final checker replays every reused premise')
    a.add_argument('--cache-dir',type=Path,help='bounded search-hint cache; every hit is proof-checked again')
    a.add_argument('--cache-context',type=Path,help='additional exact target/toolchain/workload/profile bytes in the cache key')
    args = a.parse_args()
    if args.rounds is not None and args.rounds<0: a.error('rounds must be nonnegative')
    if not 1<=args.width<=4096 or not 1<=args.memory_bytes<=16_000_000_000 or not 0<=args.time_seconds<=86400:a.error('search resource budget')
    operation_costs=load_cost_model(args.cost_model) if args.cost_model else None
    cost_model_receipt=None if args.cost_model is None else dict(path=str(args.cost_model.resolve()),sha256=hashlib.sha256(args.cost_model.read_bytes()).hexdigest(),scope='Untrusted compile-time search hypothesis, not measured runtime')
    if cost_model_receipt is not None:
        cost_model_receipt['model' if isinstance(operation_costs, TargetCostModel) else 'operation_costs'] = operation_costs.describe() if isinstance(operation_costs, TargetCostModel) else operation_costs
    if not 0 <= args.budget <= 1_000_000: a.error('budget must be between 0 and 1000000')
    compiler = args.compiler.resolve(); original_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
    from ink_knowledge import Store
    from ink_knowledge.proposals import generate
    snapshot = args.snapshot.resolve()
    storage = snapshot.parent.parent if snapshot.parent.name == 'snapshots' else snapshot.parent
    store = Store(args.knowledge_root or storage.parent, snapshot)
    search_options=dict(width=args.width,strategy=args.strategy,memory_bytes=args.memory_bytes,time_seconds=args.time_seconds)
    cache_key=None
    source_snapshot=store.snapshot
    if args.cache_dir and not args.alternatives and not args.properties:
        from ink_planner import plan_cache
        cache_key=plan_cache.fingerprint(compiler,args.core,snapshot,args.cost_model,args.budget,args.rounds,args.cache_context,search_options)
        cached=plan_cache.load(args.cache_dir,cache_key)
        if cached is not None:
            envelope=json.loads(cached)
            if not isinstance(envelope,dict) or envelope.get('schema')!=1 or envelope.get('key')!=cache_key or envelope.get('source_snapshot')!=source_snapshot or not isinstance(envelope.get('package'),dict):
                cached=None
            else:
                # Specialisation can produce a derived immutable snapshot. The
                # base pin binds cache reuse; the package remains an untrusted
                # search result, including its complete derived proof closure.
                cached=json.dumps(envelope['package'],separators=(',',':')).encode()
        if cached is not None:
            with tempfile.TemporaryDirectory(prefix='ink-plan-replay-') as temporary:
                path=Path(temporary)/'candidate.json';path.write_bytes(cached)
                replay=subprocess.run([compiler,'check-selection',args.core.resolve(),path],capture_output=True,text=True)
                if replay.returncode==0:
                    if hashlib.sha256(compiler.read_bytes()).hexdigest()!=original_hash:raise ValueError('compiler changed during cache replay')
                    if plan_cache.fingerprint(compiler,args.core,snapshot,args.cost_model,args.budget,args.rounds,args.cache_context,search_options)!=cache_key:raise ValueError('compilation inputs changed during cache replay')
                    args.o.parent.mkdir(parents=True,exist_ok=True)
                    package=json.loads(cached)
                    args.o.write_text(json.dumps(package,separators=(',',':'))+'\n' if args.compact_output else json.dumps(package,indent=2)+'\n')
                    print(json.dumps(dict(compiler_sha256=original_hash,cost_model=cost_model_receipt,cache=dict(key=cache_key,hit=True,authority='fresh local proof replay',evidence=json.loads(replay.stdout)))))
                    return
    with tempfile.TemporaryDirectory(prefix='ink-semantic-search-') as tmp:
        tmp = Path(tmp); subject_path = tmp / 'subject.json'; candidate = tmp / 'candidate.json'
        subprocess.run([compiler, 'emit-semantic', args.core.resolve(), '--core', '-o', subject_path], check=True, capture_output=True)
        subject = json.loads(subject_path.read_text())
        # Type-indexed discovery visits only value sorts present in this subject.
        # A rewrite preserves sort; all intermediate operations of each sort remain candidates.
        sorts={}
        def walk(v):
            if isinstance(v,dict):
                if 'sort' in v:sorts[key(v['sort'])]=v['sort']
                for x in v.values():walk(x)
            elif isinstance(v,list):
                for x in v:walk(x)
        walk(subject)
        generated, producer_receipts = generate(subject, json.loads(args.core.read_text()), store)
        seeds=list(subject['functions'].values())+[obj['payload'][field]for obj in generated.values()for field in ['from','to']]
        roots = discover_roots(store, sorts.values(), seeds)
        if __package__:
            from .definition_context import applicable_roots
        else:
            from definition_context import applicable_roots
        core_program = json.loads(args.core.read_text())['program']
        fetched={}
        roots = applicable_roots(store, roots, core_program, subject, fetched)
        discovery = discovery_objects(store, roots, fetched)
        del fetched
        original_snapshot_id = store.snapshot_id
        if generated:
            # An immutable derived catalogue transports proposals and exact
            # dependencies. Membership remains distinct from mathematical admission.
            discovery.update(generated)
            from ink_knowledge.proposal_views import ProposalViews
            store = ProposalViews(store, discovery, tmp / 'generated-knowledge')
            roots = sorted(set(roots) | set(generated))
        def law(e):return dict(schema=1,semantics=e['semantics'],dependencies=e['dependencies'],**e['payload'])
        certificates=[]
        if args.properties:
            for packet in json.loads(args.properties.read_bytes()):
                path=tmp/'property.json';path.write_text(json.dumps(packet))
                admitted=json.loads(subprocess.check_output([compiler,'check-properties',args.core.resolve(),path,'--core'],text=True))
                for fact,goal in zip(packet['facts'],admitted['equations']):certificates.append(dict(goal=goal,proof=fact['proof']))
                discovery.update(packet['knowledge']['objects'])
            from ink_knowledge.proposal_views import ProposalViews
            store=ProposalViews(store if not isinstance(store,ProposalViews)else store.source,discovery,tmp/'with-properties')
        catalogue=dict(roots=sorted(roots),objects={i:law(e) for i,e in discovery.items() if e['kind']=='rewrite'},certificates=certificates)
        package = dict(schema=1, semantics='ink-evidence-v1', input_core_sha256=subject['input_core_sha256'],
                       knowledge=None, applications=[])
        # Discovery is untrusted: do not spend one aggregate kernel budget on
        # every unused candidate. Each proposal is checked with its exact law
        # and premise dependency closure, against this same input module.
        checker = SelectionChecker(compiler, args.core.resolve(), store, tmp)
        check = checker.check
        try:
            if not check([]): raise ValueError('compiler rejected the unchanged input')
            alternatives=[]
            applications, receipt = search(subject, catalogue, check, args.budget,
                operation_costs=operation_costs, rounds=args.rounds, core_program=core_program, width=args.width, strategy=args.strategy, memory_bytes=args.memory_bytes, time_seconds=args.time_seconds, alternatives=alternatives)
            receipt['round_limit'] = args.rounds
            receipt['checker_sessions'] = dict(attempts=checker.session_attempts, admission_batches=checker.admission_batches, newly_admitted=checker.newly_admitted, rejected_closures=checker.rejected_closures, checked_roots=sorted(checker.checked_roots))
            receipt['admission'] = 'each proposed application list and final output checked with its exact selected-law/premise dependency closure; unused discovery objects are not mathematical authority'
            package['applications'] = applications
            if not check(applications): raise ValueError('compiler rejected final selection')
        finally:
            checker.close()
        # Even non-compact formatting must not resupply unused discovery roots
        # to a later replay's single fixed proof-work budget.
        package['knowledge'] = store.bundle(selection_roots(applications))
        if args.alternatives:
            args.alternatives.parent.mkdir(parents=True,exist_ok=True)
            with args.alternatives.open('w') as output:
                for alternative in alternatives:
                    output.write(json.dumps(dict(package,applications=alternative,knowledge=store.bundle(selection_roots(alternative))),separators=(',',':'))+'\n')
        args.o.parent.mkdir(parents=True, exist_ok=True); args.o.write_text(json.dumps(package, separators=(',',':')) + '\n' if args.compact_output else json.dumps(package, indent=2) + '\n')
    if hashlib.sha256(compiler.read_bytes()).hexdigest() != original_hash: raise ValueError('compiler changed during search')
    if cache_key:
        if plan_cache.fingerprint(compiler,args.core,snapshot,args.cost_model,args.budget,args.rounds,args.cache_context,search_options)!=cache_key:raise ValueError('compilation inputs changed during search')
        plan_cache.save(args.cache_dir,cache_key,json.dumps(dict(schema=1,key=cache_key,source_snapshot=source_snapshot,package=package),separators=(',',':')).encode()+b'\n')
    print(json.dumps(dict(compiler_sha256=original_hash, database=dict(snapshot_sha256=__import__('ink_knowledge').identity(package['knowledge']['snapshot']),source_snapshot_sha256=original_snapshot_id,entries=len(roots),loaded_objects=len(discovery),producers=producer_receipts), cost_model=cost_model_receipt, **receipt)))
if __name__ == '__main__': main()
