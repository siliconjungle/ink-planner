#!/usr/bin/env python3
"""Bounded, untrusted search over arbitrary typed semantic term patterns.
No operation recognisers determine applicability. Every proposed step and every
premise is replayed by the independent compiler checker. Cost guides search only.
"""
import argparse
import copy
import hashlib
import heapq
import itertools
import json
import time
import subprocess
import tempfile
from collections import deque
from pathlib import Path
if __package__:
    from .target_costs import TargetCostModel
else:
    from target_costs import TargetCostModel

SEMANTICS = ('ink-conditional-laws-v1', 'ink-general-laws-v1')


def interleave_premise_classes(pending):
    """Reserve checks for unconditional proposals under a finite search budget.

    Attractive conditional shortcuts can otherwise exhaust the checker budget
    before any larger helper expansion is tried. Retain ranking within each
    class, and alternate classes. Derived unconditional laws have their own
    queue so converting premises into guards does not erase this distinction.
    Every class still requires ordinary kernel admission.
    This is a search policy, not evidence that an unconditional law is valid.
    """
    plain=deque(p for p in pending if not p[4]['conditions'] and not p[4].get('dependencies'))
    derived=deque(p for p in pending if not p[4]['conditions'] and p[4].get('dependencies'))
    conditional=deque(p for p in pending if p[4]['conditions'])
    if sum(bool(q)for q in [plain,derived,conditional])<2:return pending
    ordered=[]
    while plain or derived or conditional:
        if plain:ordered.append(plain.popleft())
        if conditional:ordered.append(conditional.popleft())
        if derived:ordered.append(derived.popleft())
    return ordered


def children(t):
    n = t['node']
    if 'Op' in n: return n['Op'][1]
    if 'Lambda' in n: return [n['Lambda']]
    if 'Cons' in n: return n['Cons']
    return []


def set_child(t, i, child):
    n=t['node'];items=list(children(t));items[i]=child
    if 'Op'in n:node={'Op':[n['Op'][0],items]}
    elif 'Lambda'in n:node={'Lambda':child}
    elif 'Cons'in n:node={'Cons':items}
    else:raise ValueError('invalid child')
    return dict(t,node=node)


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

class ModuleKeys:
    """Exact JSON keys with bounded reuse of immutable function-body bytes.

    Search rebuilding shares unchanged bodies. Retain the actual object beside
    its ID to prevent ID reuse from aliasing a different body. This cache changes
    serialization work only; it supplies neither hashes nor proof authority.
    """
    def __init__(self,byte_limit):
        self.byte_limit=byte_limit;self.entries={};self.bytes=0;self.hits=0;self.misses=0
    def body(self,body):
        previous=self.entries.get(id(body))
        if previous is not None and previous[0]is body:
            self.hits+=1;return previous[1]
        self.misses+=1;encoded=key(body);size=len(encoded.encode())
        if size<=self.byte_limit:
            if self.bytes+size>self.byte_limit:self.entries.clear();self.bytes=0
            self.entries[id(body)]=(body,encoded);self.bytes+=size
        return encoded
    def module(self,bodies):
        return '{'+','.join(key(name)+':'+self.body(body)for name,body in sorted(bodies.items()))+'}'

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

def condition_candidates(goal,catalogue,limit=4,depth=0,active=None,work=None):
    """Bounded alternative certificates, never evidence that a premise is true.

    A first matching support lemma may have unprovable conditions. Do not let
    that untrusted proposal hide a later usable lemma or congruence proof.
    """
    active=set()if active is None else active;work=[4096]if work is None else work
    fingerprint=key(goal)
    if depth>=128 or fingerprint in active or work[0]<=0:return ['Normalize']
    active=active|{fingerprint};work[0]-=1;result=[];seen=set()
    def add(proof):
        k=key(proof)
        if k not in seen:result.append(proof);seen.add(k)
    add('Normalize')
    for fact in catalogue.get('certificates',[]):
        if fact['goal']==goal:add(fact['proof'])
        if len(result)>=limit:return result
    for ident,law in catalogue['objects'].items():
        if work[0]<=0 or len(result)>=limit:break
        work[0]-=1;args={}
        if not match(law['from'],goal['from'],law['params'],args)or set(args)!=set(law['params'])or substitute(law['to'],args)!=goal['to']:continue
        choices=[condition_candidates(dict(**{'from':substitute(c['from'],args),'to':substitute(c['to'],args)}),catalogue,limit,depth+1,active,work)for c in law['conditions']]
        for premises in itertools.islice(itertools.product(*choices),limit-len(result)):
            add({'Use':dict(law=ident,arguments=dict(sorted(args.items())),premises=list(premises))})
    left,right=goal['from'],goal['to'];ln,rn=left['node'],right['node']
    if len(result)<limit and left['sort']==right['sort']and isinstance(ln,dict)and isinstance(rn,dict)and 'Op'in ln and 'Op'in rn and ln['Op'][0]==rn['Op'][0]and len(children(left))==len(children(right)):
        choices=[condition_candidates(dict(**{'from':a,'to':b}),catalogue,limit,depth+1,active,work)for a,b in zip(children(left),children(right))]
        for proofs in itertools.islice(itertools.product(*choices),limit-len(result)):
            if any(p!='Normalize'for p in proofs):add({'Congruence':list(proofs)})
    return result


def guarded_variants(discovery, subject, limit=64):
    """Guarded variants of eligible conditional laws whose left side matches the subject."""
    from ink_knowledge.guarded_laws import eligible, guard
    from ink_knowledge.store import identity as entry_identity
    sites=[t for body in subject['functions'].values() for _,t in positions(body)]
    out={}
    for ident,entry in sorted(discovery.items()):
        if len(out)>=limit:break
        if not eligible(entry):continue
        law=entry['payload']
        if not any(match(law['from'],t,law['params'],{}) for t in sites):continue
        derived=guard(ident,entry)
        out[entry_identity(derived)]=derived
    return {i:e for i,e in out.items() if i not in discovery}


def guarded_again(body, path, condition):
    """Whether the site at `path` is, or lies under, a `choose` testing
    `condition` in the same binder scope, so a guard would only repeat it.
    Tests above the innermost enclosing lambda use different binders."""
    node=body;found=False
    for index in path:
        n=node['node']
        if isinstance(n,dict) and 'Lambda' in n:found=False
        elif isinstance(n,dict) and n.get('Op',[None])[0]=='call:choose' and n['Op'][1][0]==condition and index>0:found=True
        node=children(node)[index]
    n=node['node']
    return found or (isinstance(n,dict) and n.get('Op',[None])[0]=='call:choose' and n['Op'][1][0]==condition)


def search(subject, catalogue, check, budget=4096, width=16, rounds=None, operation_costs=None, core_program=None, diverse=True, strategy="memo", memory_bytes=256_000_000, time_seconds=600, alternatives=None, policy=None, neighbourhood=None, refresh=None, near_misses=None, reason=None, context_bodies=None):
    """`near_misses` optionally records matched-but-unadmitted proposals and
    `reason` returns the checker's last rejection message. Both are diagnostics
    only: they never change ordering, admission or the selected result."""
    def last_reason():
        if reason is None:return None
        try:return reason()
        except Exception as error:return 'reason unavailable: '+str(error)[:200]
    if strategy not in {"memo","beam"}:raise ValueError("unknown search strategy")
    if budget<0 or width<1 or memory_bytes<1 or time_seconds<0 or (rounds is not None and rounds<0):raise ValueError("search budgets")
    started=time.monotonic();deferred=[];memo_edges=[];iteration=0;memory_exhausted=False;timed_out=False;prefix_states={}
    objects = catalogue['objects']
    from ink_knowledge.guarded_laws import guard_condition
    def guarded(o):
        node=o['to']['node']
        return (not o.get('conditions') and len(o.get('dependencies',[]))==1 and isinstance(node,dict)
                and node.get('Op',[None])[0]=='call:choose' and o['from'] in node['Op'][1][1:])
    # A guard re-matching its own untouched branch would only repeat the test.
    guards={i:guard_condition(o) for i,o in objects.items() if guarded(o)}
    from ink_planner.term_matching import Matcher
    matcher=Matcher(objects,catalogue['roots'],key,children,match,substitute,memory_bytes)
    component_model = operation_costs if isinstance(operation_costs, TargetCostModel) else None
    if component_model is not None and core_program is None:
        raise ValueError('schema2 requires actual core parameter signatures')
    # One immutable cost snapshot per search, not one resource read per body.
    # A later search reads fresh defaults and applies its own caller overrides.
    if component_model is None:
        from ink_knowledge.cost_models import structural_prices
        prices = structural_prices()
        if operation_costs is not None: prices = dict(prices, **operation_costs)
    # Candidate states share every unchanged immutable function body. Avoid
    # repricing the whole module for each local proposal. Retain the actual
    # object alongside its identity so Python ID reuse cannot alias a cache row.
    # Helper-sensitive schema2 estimates keep their existing complete analysis.
    body_costs = {};body_profiles = {};analysis_hits = 0;analysis_misses = 0;analysis_bytes = 0
    def cached(cache,body,calculate):
        nonlocal analysis_hits,analysis_misses,analysis_bytes
        ident=id(body);previous=cache.get(ident)
        if previous is not None and previous[0]is body:
            analysis_hits+=1;return previous[1]
        analysis_misses+=1;result=calculate(body)
        size=len(key(body).encode())
        if size>memory_bytes//8:return result
        if len(cache)>=4096 or analysis_bytes+size>memory_bytes//8:
            body_costs.clear();body_profiles.clear();analysis_bytes=0
        cache[ident]=(body,result,size);analysis_bytes+=size
        return result
    def full_bodies(bodies):
        return dict(context_bodies or {}, **bodies)
    def score_bodies(bodies):
        if component_model is not None:
            return component_model.estimate_module(full_bodies(bodies), core_program)['score_ns']
        return sum(cached(body_costs,t,lambda body:_cost(body,prices))for t in bodies.values())
    from ink_knowledge.cost_models import structural_components
    dimensions = ('allocation_events', 'traversal_elements', 'copy_bytes')
    component_prices = structural_components()
    profile_cache = {}
    def body_profile(body):
        result=dict.fromkeys(dimensions,0)
        for _,t in positions(body):
            node=t['node']
            name=node['Op'][0]if isinstance(node,dict)and'Op'in node else next(iter(node))if isinstance(node,dict)else node
            for dimension in dimensions:result[dimension]+=component_prices.get(dimension,{}).get(name,0)
        return result
    def profile(bodies):
        if component_model is None:
            result=dict.fromkeys(dimensions,0)
            for body in bodies.values():
                values=cached(body_profiles,body,body_profile)
                for dimension in dimensions:result[dimension]+=values[dimension]
            return result
        fingerprint = key(bodies)
        if fingerprint not in profile_cache:
            if component_model is not None:
                profile_cache[fingerprint] = component_model.estimate_module(full_bodies(bodies), core_program)['components']
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
    module_keys=ModuleKeys(memory_bytes//8)
    start = subject['functions']; initial = score_bodies(start);initial_key=module_keys.module(start)
    frontier = [(initial, start, [])]; best = frontier[0]; seen = {initial_key}
    prefix_states[hashlib.sha256(key([]).encode()).hexdigest()]=initial_key
    rejected = 0; checked = 0; exhausted = False
    generated = 0;memo_bytes=len(initial_key.encode());dropped=0
    while rounds is None or iteration<rounds:
        if time.monotonic()-started>=time_seconds:timed_out=True;break
        iteration+=1
        pending = deferred;old_pending=len(deferred);deferred=[]
        pending_seen = {key(x[2])for x in pending}
        pending_bytes=sum(len(x[3].encode())+len(key(x[2]).encode())for x in pending)
        round_index=iteration-1
        hinted=set()
        for _, bodies, applications in frontier:
            if refresh is not None:
                # A proved rewrite can expose a template that did not match the
                # original syntax. Instances are proposals; check() below still
                # replays the complete chain against the original module.
                extra=refresh(bodies)
                objects.update(extra);matcher.add(extra)
                catalogue['roots']=sorted(set(catalogue['roots'])|set(extra))
            for name, body in bodies.items():
                for path, target in positions(body):
                    if time.monotonic()-started>=time_seconds:timed_out=True;break
                    for ident,args,new in matcher.matches(target):
                        if time.monotonic()-started>=time_seconds:timed_out=True;break
                        law=objects[ident]
                        if ident in guards and guarded_again(body,path,substitute(guards[ident],args)):continue
                        updated = dict(bodies); updated[name] = replace(body, path, new)
                        fingerprint = module_keys.module(updated)
                        if fingerprint in seen: continue
                        step = dict(function=name, path=list(path), law=ident, arguments=dict(sorted(args.items())),
                                    premises=['Normalize'] * len(law['conditions']))
                        proposal = applications + [step]
                        proposal_key = key(proposal)
                        if proposal_key in pending_seen: continue
                        size=len(fingerprint.encode())+len(proposal_key.encode())
                        if memo_bytes+pending_bytes+size+matcher.bytes>memory_bytes:
                            memory_exhausted=True;dropped+=1;continue
                        pending_bytes+=size;pending_seen.add(proposal_key)
                        score = score_bodies(updated)
                        pending.append((score, updated, proposal, fingerprint, law, args))
                        if near_misses is not None:near_misses.matched(ident, law)
        # Rank untrusted proposals before spending bounded checker work. Only
        # `width` admitted bodies enter the next beam. The work-profile and
        # proof-class policies below also reserve exploration opportunities.
        # A rejection never marks a body seen or hides another proof of it.
        generated += len(pending)-old_pending
        pending.sort(key=lambda x: (x[0], len(x[2]), x[3]))
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
        if policy is not None:
            pending = policy.order(pending, initial, round_index)
        if neighbourhood is not None:
            pending = neighbourhood.order(pending, hinted, round_index)
        pending = interleave_premise_classes(pending)
        candidates = []
        processed=0
        for processed, (score, updated, proposal, fingerprint, law, args) in enumerate(pending,1):
            if fingerprint in seen:
                if near_misses is not None:near_misses.duplicate(proposal[-1]['law'], law)
                continue
            if time.monotonic()-started>=time_seconds:timed_out=True;processed-=1;break
            if checked >= budget: exhausted = True; processed-=1; break
            checked += 1
            candidate = (score, updated, proposal, fingerprint, law, args)
            def admit(proof):
                return check(proof) if policy is None else policy.check(candidate, initial, check, proof)
            if not admit(proposal):
                rejected += 1
                step = proposal[-1]
                first_reason = last_reason()
                choices=[condition_candidates(dict(**{'from':substitute(c['from'],args),'to':substitute(c['to'],args)}),catalogue)for c in law['conditions']]
                accepted=False
                for premises in itertools.islice(itertools.product(*choices),16):
                    premises=list(premises)
                    if premises==step['premises']:continue
                    if checked>=budget:exhausted=True;break
                    if time.monotonic()-started>=time_seconds:timed_out=True;break
                    trial=proposal[:-1]+[dict(step,premises=premises)];checked+=1
                    if admit(trial):proposal=trial;accepted=True;break
                    rejected+=1
                if not accepted:
                    if near_misses is not None:near_misses.checked(step['law'], law, False, last_reason() or first_reason, args)
                    continue
            if near_misses is not None:near_misses.checked(proposal[-1]['law'], law, True)
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
                if memo_bytes+retained+size+matcher.bytes>memory_bytes:memory_exhausted=True;break
                deferred.append(item);retained+=size
        if near_misses is not None and hasattr(near_misses,'flush'):near_misses.flush(complete=False,rounds=iteration)
        if exhausted or memory_exhausted or timed_out:break
        if not candidates and not deferred:break
        frontier = candidates
    receipt = dict(initial_cost=initial, selected_cost=best[0], attempts=checked,
                   rejected=rejected, generated_proposals=generated, exhausted=exhausted, estimate='structural; not measured runtime')
    receipt['analysis_cache']=dict(hits=analysis_hits,misses=analysis_misses,body_costs=len(body_costs),body_profiles=len(body_profiles),retained_source_bytes=analysis_bytes,scope='Immutable source-body estimates only; bounded serialized source estimate; no applicability or proof authority')
    receipt['serialization_cache']=dict(hits=module_keys.hits,misses=module_keys.misses,retained_bytes=module_keys.bytes,byte_limit=module_keys.byte_limit,scope='Exact JSON function-body keys; immutable sharing only; no proof authority')
    receipt["memo"]=dict(strategy=strategy,states=len(seen),edges=len(memo_edges),deferred=len(deferred),rounds=iteration,memory_exhausted=memory_exhausted,timed_out=timed_out,retained_memo_bytes=memo_bytes,dropped_proposals=dropped,scope="Exact typed term states; admitted edges only; no hash grants proof authority")
    receipt['matching'] = matcher.receipt()
    receipt['beam_diversity'] = dict(enabled=diverse, dimensions=list(dimensions),
        initial=profile(start), selected=profile(best[1]), scope='Untrusted source-work hypotheses; no timing or proof authority')
    if component_model is not None:
        receipt['estimate'] = 'schema2 untrusted source component hypothesis in ns; not measured runtime or physical GPU cost'
        receipt['initial_components'] = component_model.estimate_module(full_bodies(start), core_program)
        receipt['selected_components'] = component_model.estimate_module(full_bodies(best[1]), core_program)
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
    MAX_SESSION_REQUESTS = 4098
    def __init__(self, compiler, core, store, directory):
        self.compiler, self.core, self.store, self.directory = compiler, core, store, directory
        self.sessions = {}
        self.incremental_sessions = set()
        self.session_requests = {}
        self.checked_roots = set()
        self.session_attempts = 0
        self.admission_batches = 0
        self.newly_admitted = 0
        self.rejected_closures = 0
        # Diagnostic text of the last rejection; never proof or selection authority.
        self.last_error = None

    @staticmethod
    def close_session(session):
        try: session.stdin.close()
        except BrokenPipeError: pass
        try: session.wait(timeout=10)
        except subprocess.TimeoutExpired: session.kill(); session.wait()
        session.stdout.close(); session.stderr.close()

    def check(self, applications):
        self.last_error = None
        # Admission and proof requests share the CLI's fixed session lease.
        # Reserve two requests for an extension followed by its proof check.
        for key_, process in list(self.sessions.items()):
            if self.session_requests[process] >= self.MAX_SESSION_REQUESTS - 2:
                del self.sessions[key_]
                self.incremental_sessions.discard(process)
                del self.session_requests[process]
                self.close_session(process)
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
                except ValueError as error:
                    self.rejected_closures += 1
                    self.last_error = 'closure: ' + str(error)[:400]
                    return False
                candidates = [k for k,s in self.sessions.items() if s in self.incremental_sessions]
                extended = False
                if candidates:
                    prior = max(candidates, key=lambda k: len(set(k) & set(roots)))
                    session = self.sessions[prior]
                    self.session_requests[session] += 1
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
                    elif result.get('error') not in (
                        'knowledge view schema/size limit', 'checker session snapshot mismatch'
                    ):
                        self.rejected_closures += 1
                        self.last_error = 'closure: admission rejected: ' + str(result.get('error'))[:400]
                        return False
                    # A full union or an older pinned transport snapshot does
                    # not invalidate the new closure. Open a fresh session and
                    # recheck its complete evidence; never weaken core admission.
                if not extended:
                    if len(self.sessions) >= 8:
                        first = next(iter(self.sessions))
                        discarded = self.sessions.pop(first)
                        self.incremental_sessions.discard(discarded)
                        del self.session_requests[discarded]
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
                        self.last_error = 'closure: checker session not ready'
                        return False
                    self.sessions[roots] = session
                    self.session_requests[session] = 0
                    if capability.get('incremental'): self.incremental_sessions.add(session)
        session = self.sessions.pop(roots_key)
        self.sessions[roots_key] = session
        self.session_requests[session] += 1
        session.stdin.write(json.dumps(applications) + '\n'); session.stdin.flush()
        response = session.stdout.readline()
        result = json.loads(response) if response else {}
        checked = bool(result.get('checked'))
        if checked: self.checked_roots.update(roots)
        else: self.last_error = str(result.get('error') or 'checker session returned no response')[:2000]
        return checked

    def close(self):
        for session in self.sessions.values(): self.close_session(session)
        self.sessions.clear()
        self.incremental_sessions.clear()
        self.session_requests.clear()


def discover_roots(store, sorts, terms=None):
    """Page type-indexed discovery independently of proof-check attempts."""
    roots = set()
    if terms is not None:
        from ink_knowledge.pattern_index import heads,Reader
        pending=set().union(*(heads(t)for t in terms));seen=set()
        agenda=list(pending);heapq.heapify(agenda)
        with Reader(store)as reader:
            while agenda:
                sort,head=heapq.heappop(agenda);pending.remove((sort,head))
                if (sort,head)in seen:continue
                seen.add((sort,head));after=None
                while True:
                    page=reader.candidates(json.loads(sort),head,after=after,limit=1024)
                    fresh=set(page)-roots;roots.update(page)
                    introduced=reader.targets(fresh)-seen-pending
                    pending.update(introduced)
                    for item in introduced:heapq.heappush(agenda,item)
                    if len(page)<1024:break
                    after=page[-1]
                if len(roots)>8192 or len(seen)>16384:raise ValueError('pattern discovery reachability budget')
        return roots
    for sort in sorts:
        for semantics in SEMANTICS:
            after = None
            while True:
                page = store.query(kind='rewrite', semantics=semantics, result=sort,
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
    a.add_argument('--stream-alternatives',action='store_true',help='flush admitted alternatives during search for layered planning')
    a.add_argument('--properties',type=Path,help='source-bound property packets; final checker replays every reused premise')
    a.add_argument('--cache-dir',type=Path,help='bounded search-hint cache; every hit is proof-checked again')
    a.add_argument('--cache-context',type=Path,help='additional exact target/toolchain/workload/profile bytes in the cache key')
    a.add_argument('--search-strategy',choices=['baseline','stochastic'],default='baseline')
    a.add_argument('--search-seed',type=int,default=0)
    a.add_argument('--search-restarts',type=int,default=4)
    a.add_argument('--search-trace',type=Path,help='offline admission observations; never proof authority')
    a.add_argument('--selfhost-endpoint',help='optional loopback compiled-Ink score service; errors retain local ranking')
    a.add_argument('--discovery-accelerator',action='store_true',help='opt-in authenticated in-memory Bloom/learned discovery index')
    a.add_argument('--ann-hints',action='store_true',help='opt-in HNSW structural neighbourhood ordering; requires optional ann extra')
    a.add_argument('--diagnostics',type=Path,help='write untrusted near-miss observations (matched laws the checker did not admit)')
    a.add_argument('--egraph',action='store_true',help='also run equality saturation and keep its checked chain when cheaper')
    a.add_argument('--egraph-nodes',type=int,default=4000)
    a.add_argument('--modular-cache',type=Path,help='plan each function separately, reusing cached per-function chains as hints')
    args = a.parse_args()
    def write_diagnostics(value):
        if args.diagnostics:
            args.diagnostics.parent.mkdir(parents=True,exist_ok=True)
            args.diagnostics.write_text(json.dumps(value,separators=(',',':'))+'\n')
    if args.stream_alternatives and not args.alternatives:a.error('streaming requires --alternatives')
    if not 1<=args.search_restarts<=16:a.error('search restarts must be between 1 and 16')
    from ink_planner.stochastic_search import search_restarts
    search_options=None if args.search_strategy=='baseline' else dict(strategy=args.search_strategy,seed=args.search_seed,restarts=args.search_restarts,selfhost_endpoint=args.selfhost_endpoint)
    if args.ann_hints:search_options={**(search_options or {}),'ann_hints':True,'ann_schema':'ink-structural-sketch-v1-64'}
    if args.selfhost_endpoint and args.search_strategy=='baseline':a.error('selfhost endpoint requires stochastic search')
    if args.selfhost_endpoint:
        from ink_planner.selfhost import Scorer
        scorer=Scorer(args.selfhost_endpoint)
    else:scorer=None
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
    # Cold/relocated installations have no disposable index. Build it once
    # before producers issue repeated queries; proof bytes still replay below.
    store.ensure_index()
    if args.discovery_accelerator:
        from ink_knowledge.discovery_accelerator import enable
        enable(store)
    search_options={**(search_options or {}),'engine':dict(width=args.width,strategy=args.strategy,memory_bytes=args.memory_bytes,time_seconds=args.time_seconds)}
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
                    if args.search_trace:
                        args.search_trace.parent.mkdir(parents=True,exist_ok=True)
                        args.search_trace.write_text(json.dumps(dict(schema=1,source=package['input_core_sha256'],compiler_sha256=original_hash,snapshot=source_snapshot,cache_hit=True,rows=[]),indent=2)+'\n')
                    from ink_planner.near_misses import NearMisses
                    write_diagnostics(NearMisses().receipt(cache_hit=True))
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
        generated, producer_receipts = generate(subject, json.loads(args.core.read_text()), store, compiler=compiler)
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
        catalogue_source=store
        if generated:
            # An immutable derived catalogue transports proposals and exact
            # dependencies. Membership remains distinct from mathematical admission.
            discovery.update(generated)
            # Generated instances may use published general laws; carry their
            # closures too. A missing dependency only fails that proposal's check.
            for dependency in sorted({d for o in generated.values() for d in o['dependencies']} - set(discovery)):
                try:discovery.update(discovery_objects(store, [dependency]))
                except ValueError:pass
            from ink_knowledge.proposal_views import ProposalViews
            store = ProposalViews(store, discovery, tmp / 'generated-knowledge')
            roots = sorted(set(roots) | set(generated))
        # Guarded variants turn data-dependent premises of conditional laws
        # that match this program into runtime tests (ink_knowledge.guarded_laws).
        # Each variant is an ordinary law checked with its dependency closure.
        guarded=guarded_variants(discovery,subject)
        if guarded:
            discovery.update(guarded)
            from ink_knowledge.proposal_views import ProposalViews
            store=ProposalViews(store if not isinstance(store,ProposalViews) else store.source,discovery,tmp/'guarded-knowledge')
            roots=sorted(set(roots)|set(guarded))
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
        objects={i:law(e) for i,e in discovery.items() if e['kind']=='rewrite'}
        # Laws with type parameters or assumptions apply only through checked
        # instances; they stay available as proof dependencies, not rewrite roots.
        roots=[r for r in roots if r not in objects or not (objects[r].get('types') or objects[r].get('assumptions'))]
        catalogue=dict(roots=sorted(roots),objects=objects,certificates=certificates)
        neighbourhood=None
        if args.ann_hints:
            from ink_planner.neighbourhood import Neighbourhood
            neighbourhood=Neighbourhood(catalogue)
        package = dict(schema=1, semantics='ink-evidence-v1', input_core_sha256=subject['input_core_sha256'],
                       knowledge=None, applications=[])
        # Discovery is untrusted: do not spend one aggregate kernel budget on
        # every unused candidate. Each proposal is checked with its exact law
        # and premise dependency closure, against this same input module.
        checker = SelectionChecker(compiler, args.core.resolve(), store, tmp)
        check = checker.check
        from ink_knowledge.proposal_views import ProposalViews
        refreshed=set();refresh_receipts=[]
        def refresh(bodies):
            nonlocal store
            body_key=key(bodies);body_id=hashlib.sha256(body_key.encode()).hexdigest()
            if body_id in refreshed:return {}
            refreshed.add(body_id)
            if len(refreshed)>4096:raise ValueError('semantic instantiation state budget')
            next_subject=dict(subject,functions=bodies)
            extra,notes=generate(next_subject,json.loads(args.core.read_text()),catalogue_source)
            extra={i:o for i,o in extra.items()if i not in discovery}
            if not extra:return {}
            if len(discovery)+len(extra)>16384 or sum(len(key(o))for o in extra.values())>args.memory_bytes:
                raise ValueError('incremental semantic discovery budget')
            # Use a new authenticated transport before updating discovery, so
            # ProposalViews can invalidate closures containing old membership.
            if not isinstance(store,ProposalViews):
                store=ProposalViews(catalogue_source,dict(discovery),tmp/'incremental-knowledge')
            store.update(extra);discovery.update(extra);checker.store=store
            refresh_receipts.append(dict(state_sha256=body_id,new_objects=len(extra),producers=notes))
            return {i:law(o)for i,o in extra.items()if o['kind']=='rewrite'}
        alternative_output=None
        if args.stream_alternatives:
            args.alternatives.parent.mkdir(parents=True,exist_ok=True)
            alternative_output=args.alternatives.open('w')
        class Alternatives(list):
            def append(self,alternative):
                if alternative_output is None:super().append(alternative)
                else:
                    value=dict(package,applications=alternative,knowledge=store.bundle(selection_roots(alternative)))
                    alternative_output.write(json.dumps(value,separators=(',',':'))+'\n');alternative_output.flush()
        from ink_planner.near_misses import NearMisses
        near_misses=NearMisses(args.diagnostics)
        try:
            if not check([]): raise ValueError('compiler rejected the unchanged input')
            alternatives=Alternatives()
            budget,time_seconds=args.budget,args.time_seconds
            saturation=None
            if args.egraph:
                # Saturation gets a third of the budgets; the beam keeps the rest.
                from ink_planner.egraph import search as egraph_search
                saturation=egraph_search(subject,catalogue,check,budget=budget//3,time_seconds=max(1,time_seconds//3),
                    operation_costs=operation_costs,node_limit=args.egraph_nodes,alternatives=alternatives)
                budget-=saturation[1]['attempts'];time_seconds=max(1,time_seconds-time_seconds//3)
            if args.modular_cache:
                from ink_planner.modular import plan as modular_plan
                context=dict(compiler=original_hash,snapshot=store.snapshot_id if hasattr(store,'snapshot_id') else None,
                    source_snapshot=source_snapshot,budget=args.budget,width=args.width,rounds=args.rounds,strategy=args.strategy,
                    cost_model=None if cost_model_receipt is None else cost_model_receipt['sha256'])
                applications, receipt = modular_plan(subject, catalogue, check, search, core_program, args.modular_cache, context,
                    budget=budget, time_seconds=time_seconds, operation_costs=operation_costs, rounds=args.rounds,
                    core_program=core_program, width=args.width, memory_bytes=args.memory_bytes, strategy=args.strategy,
                    near_misses=near_misses, reason=lambda:checker.last_error, refresh=refresh, alternatives=alternatives)
                from ink_planner.semantic_search import cost as structural
                def module_score(bodies):
                    if isinstance(operation_costs, TargetCostModel):
                        return operation_costs.estimate_module(bodies, core_program)['score_ns']
                    return sum(structural(b, operation_costs) for b in bodies.values())
                receipt.update(initial_cost=module_score(subject['functions']),
                    search_policy=dict(strategy='modular'),check_rows=[])
                bodies=dict(subject['functions'])
                for step in applications:
                    law=catalogue['objects'][step['law']]
                    bodies[step['function']]=replace(bodies[step['function']],step['path'],substitute(law['to'],step['arguments']))
                receipt['selected_cost']=module_score(bodies)
                if isinstance(operation_costs, TargetCostModel):
                    receipt['estimate']='schema2 untrusted source component hypothesis in ns; not measured runtime or physical GPU cost'
                    receipt['initial_components']=operation_costs.estimate_module(subject['functions'], core_program)
                    receipt['selected_components']=operation_costs.estimate_module(bodies, core_program)
            else:
                applications, receipt = search_restarts(search, subject, catalogue, check, budget=budget,
                    strategy=args.search_strategy, seed=args.search_seed, restarts=args.search_restarts, scorer=scorer,
                    operation_costs=operation_costs, rounds=args.rounds, core_program=core_program,neighbourhood=neighbourhood,engine_strategy=args.strategy,
                    width=args.width,memory_bytes=args.memory_bytes,time_seconds=time_seconds,alternatives=alternatives,refresh=refresh,
                    near_misses=near_misses,reason=lambda:checker.last_error)
                receipt['incremental_discovery']=dict(visited_states=len(refreshed),batches=refresh_receipts,
                    scope='Reinstantiate ordinary database authoring data on admitted intermediate syntax; each complete application chain replayed')
            if saturation is not None:
                receipt['egraph']=dict(saturation[1]['egraph'],selected_cost=saturation[1]['selected_cost'],
                    beam_selected_cost=receipt['selected_cost'],chosen='egraph' if saturation[1]['selected_cost']<receipt['selected_cost'] else 'beam')
                receipt['attempts']+=saturation[1]['attempts']
                if saturation[1]['selected_cost']<receipt['selected_cost'] and saturation[0]:
                    applications=saturation[0];receipt['selected_cost']=saturation[1]['selected_cost']
            diagnostics=near_misses.receipt(complete=True,discovered_roots=len(roots),catalogue_rewrites=len(catalogue['objects']),
                unavailable_functions=len(subject.get('unavailable',[])),attempts=receipt['attempts'],exhausted=receipt['exhausted'],
                rounds=receipt['memo']['rounds'],timed_out=receipt['memo']['timed_out'],memory_exhausted=receipt['memo']['memory_exhausted'])
            receipt['near_misses']={k:diagnostics[k] for k in ('totals','matched_laws','laws_with_admission','laws_only_rejected','reasons')}
            write_diagnostics(diagnostics)
            if neighbourhood is not None:receipt['neighbourhood']=neighbourhood.receipt
            receipt['round_limit'] = args.rounds
            receipt['checker_sessions'] = dict(attempts=checker.session_attempts, admission_batches=checker.admission_batches, newly_admitted=checker.newly_admitted, rejected_closures=checker.rejected_closures, checked_roots=sorted(checker.checked_roots))
            if scorer is not None:receipt['selfhost']=dict(endpoint=scorer.endpoint,weights=list(scorer.weights),receipts=scorer.receipts,scope='exact word scores used only as search hints')
            receipt['guarded_laws'] = dict(derived=len(guarded), applied=sum(1 for a in applications if a['law'] in guarded),
                scope='Unconditional variants of data-dependent conditional laws; checked like any law')
            receipt['admission'] = 'each proposed application list and final output checked with its exact selected-law/premise dependency closure; unused discovery objects are not mathematical authority'
            package['applications'] = applications
            if not check(applications): raise ValueError('compiler rejected final selection')
            if args.search_trace:
                args.search_trace.parent.mkdir(parents=True,exist_ok=True)
                args.search_trace.write_text(json.dumps(dict(schema=1,source=subject['input_core_sha256'],compiler_sha256=original_hash,
                    snapshot=original_snapshot_id,policy=receipt['search_policy'],rows=receipt['check_rows']),indent=2)+'\n')
        finally:
            checker.close()
            if alternative_output is not None:alternative_output.close()
        # Even non-compact formatting must not resupply unused discovery roots
        # to a later replay's single fixed proof-work budget.
        package['knowledge'] = store.bundle(selection_roots(applications))
        if args.alternatives and not args.stream_alternatives:
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
