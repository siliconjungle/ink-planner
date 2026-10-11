#!/usr/bin/env python3
"""Bounded, untrusted search over arbitrary typed semantic term patterns.
No operation recognisers determine applicability. Every proposed step and every
premise is replayed by the independent compiler checker. Cost guides search only.
"""
import argparse
import copy
import hashlib
import json
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

def condition_proof(goal, catalogue, depth=0):
    """General bounded proof production: instantiate a checked lemma, or lift
    child proofs through an unchanged operator. Normalization is the fallback.
    The checker, including actual branch facts, remains the only authority.
    """
    if depth >= 3: return 'Normalize'
    # Supporting lemmas need not be enabled as rewrite roots. The compiler
    # admits only the dependency closure, so unreachable objects cannot confer authority.
    for ident, law in catalogue['objects'].items():
        args = {}
        if match(law['from'], goal['from'], law['params'], args) and set(args) == set(law['params']):
            if substitute(law['to'], args) == goal['to']:
                premises = [condition_proof({'from': substitute(c['from'], args), 'to': substitute(c['to'], args)},
                                            catalogue, depth + 1) for c in law['conditions']]
                return {'Use': dict(law=ident, arguments=dict(sorted(args.items())), premises=premises)}
    left, right = goal['from'], goal['to']; ln, rn = left['node'], right['node']
    if left['sort'] == right['sort'] and isinstance(ln, dict) and isinstance(rn, dict) and 'Op' in ln and 'Op' in rn:
        if ln['Op'][0] == rn['Op'][0] and len(children(left)) == len(children(right)):
            proofs = [condition_proof({'from': a, 'to': b}, catalogue, depth + 1)
                      for a, b in zip(children(left), children(right))]
            if any(p != 'Normalize' for p in proofs): return {'Congruence': proofs}
    return 'Normalize'


def search(subject, catalogue, check, budget=128, width=4, rounds=12, operation_costs=None, core_program=None):
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
    start = subject['functions']; initial = score_bodies(start)
    frontier = [(initial, start, [])]; best = frontier[0]; seen = {key(start)}
    rejected = 0; checked = 0; exhausted = False
    generated = 0
    for _ in range(rounds):
        pending = []
        pending_seen = set()
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
                        pending_seen.add(proposal_key)
                        score = score_bodies(updated)
                        pending.append((score, updated, proposal, fingerprint, law, args))
        # Rank untrusted proposals before spending bounded checker work. Only
        # the best `width` admitted bodies can enter the next beam; checking
        # lower-ranked alternatives after filling it cannot improve that beam.
        # A rejection never marks a body seen or hides another proof of it.
        generated += len(pending)
        pending.sort(key=lambda x: (x[0], len(x[2]), key(x[1])))
        candidates = []
        for score, updated, proposal, fingerprint, law, args in pending:
            if fingerprint in seen: continue
            if checked >= budget: exhausted = True; break
            checked += 1
            if not check(proposal):
                rejected += 1
                step = proposal[-1]
                alternatives = [condition_proof({'from': substitute(c['from'], args),
                                                 'to': substitute(c['to'], args)}, catalogue)
                                for c in law['conditions']]
                if alternatives == step['premises']: continue
                if checked >= budget: exhausted = True; break
                proposal = proposal[:-1] + [dict(step, premises=alternatives)]
                checked += 1
                if not check(proposal): rejected += 1; continue
            seen.add(fingerprint)
            state = (score, updated, proposal)
            candidates.append(state)
            if score < best[0]: best = state
            if len(candidates) >= width: break
        if not candidates or exhausted: break
        frontier = candidates
    receipt = dict(initial_cost=initial, selected_cost=best[0], attempts=checked,
                   rejected=rejected, generated_proposals=generated, exhausted=exhausted, estimate='structural; not measured runtime')
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


def discovery_objects(store, roots):
    """Authenticate discovery bytes individually; no aggregate proof admission."""
    objects = {}
    pending = list(sorted(roots))
    while pending:
        ident = pending.pop()
        if ident in objects: continue
        obj = store.object(ident)
        objects[ident] = obj
        pending.extend(obj['dependencies'])
    return objects


class SelectionChecker:
    """Bounded checker processes; only exact proposals become proof authority."""
    def __init__(self, compiler, core, store, directory):
        self.compiler, self.core, self.store, self.directory = compiler, core, store, directory
        self.sessions = {}
        self.checked_roots = set()
        self.session_attempts = 0
        self.rejected_closures = 0

    @staticmethod
    def close_session(session):
        session.stdin.close()
        try: session.wait(timeout=10)
        except subprocess.TimeoutExpired: session.kill(); session.wait()
        session.stdout.close(); session.stderr.close()

    def check(self, applications):
        roots = tuple(selection_roots(applications))
        if roots not in self.sessions:
            if len(self.sessions) >= 8:
                first = next(iter(self.sessions))
                self.close_session(self.sessions.pop(first))
            path = self.directory / ('closure-' + hashlib.sha256(key(roots).encode()).hexdigest() + '.json')
            try: path.write_text(json.dumps(self.store.bundle(roots)))
            except ValueError:
                self.rejected_closures += 1
                return False
            self.session_attempts += 1
            session = subprocess.Popen([self.compiler, 'selection-session', self.core, path],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            ready = session.stdout.readline()
            if not ready or not json.loads(ready).get('ready'):
                self.close_session(session)
                self.rejected_closures += 1
                return False
            self.sessions[roots] = session
        session = self.sessions[roots]
        session.stdin.write(json.dumps(applications) + '\n'); session.stdin.flush()
        response = session.stdout.readline()
        checked = bool(response and json.loads(response).get('checked'))
        if checked: self.checked_roots.update(roots)
        return checked

    def close(self):
        for session in self.sessions.values(): self.close_session(session)
        self.sessions.clear()


def discover_roots(store, sorts):
    """Page type-indexed discovery independently of proof-check attempts."""
    roots = set()
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
    a.add_argument('-o', type=Path, required=True); a.add_argument('--budget', type=int, default=128)
    a.add_argument('--cost-model',type=Path,help='Untrusted JSON operation prices; default structural estimate when omitted')
    a.add_argument('--compact-output',action='store_true',help='Write the selected proof closure without JSON indentation')
    a.add_argument('--rounds',type=int,default=12,help='maximum beam expansion rounds (0..64)')
    args = a.parse_args()
    if not 0 <= args.rounds <= 64: a.error('rounds must be between 0 and 64')
    operation_costs=load_cost_model(args.cost_model) if args.cost_model else None
    cost_model_receipt=None if args.cost_model is None else dict(path=str(args.cost_model.resolve()),sha256=hashlib.sha256(args.cost_model.read_bytes()).hexdigest(),scope='Untrusted compile-time search hypothesis, not measured runtime')
    if cost_model_receipt is not None:
        cost_model_receipt['model' if isinstance(operation_costs, TargetCostModel) else 'operation_costs'] = operation_costs.describe() if isinstance(operation_costs, TargetCostModel) else operation_costs
    if not 0 <= args.budget <= 4096: a.error('budget must be between 0 and 4096')
    compiler = args.compiler.resolve(); original_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
    from ink_knowledge import Store
    from ink_knowledge.proposals import generate
    snapshot = args.snapshot.resolve()
    storage = snapshot.parent.parent if snapshot.parent.name == 'snapshots' else snapshot.parent
    store = Store(args.knowledge_root or storage.parent, snapshot)
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
        roots = discover_roots(store, sorts.values())
        if __package__:
            from .definition_context import applicable_roots
        else:
            from definition_context import applicable_roots
        core_program = json.loads(args.core.read_text())['program']
        roots = applicable_roots(store, roots, core_program, subject)
        discovery = discovery_objects(store, roots)
        generated, producer_receipts = generate(subject, json.loads(args.core.read_text()))
        original_snapshot_id = store.snapshot_id
        if generated:
            # An immutable derived catalogue transports proposals and exact
            # dependencies. Membership remains distinct from mathematical admission.
            discovery.update(generated)
            store = Store.publish(tmp / 'generated-knowledge', discovery.values())
            roots = sorted(set(roots) | set(generated))
        def law(e):return dict(schema=1,semantics=e['semantics'],dependencies=e['dependencies'],**e['payload'])
        catalogue=dict(roots=sorted(roots),objects={i:law(e) for i,e in discovery.items()})
        package = dict(schema=1, semantics='ink-evidence-v1', input_core_sha256=subject['input_core_sha256'],
                       knowledge=None, applications=[])
        # Discovery is untrusted: do not spend one aggregate kernel budget on
        # every unused candidate. Each proposal is checked with its exact law
        # and premise dependency closure, against this same input module.
        checker = SelectionChecker(compiler, args.core.resolve(), store, tmp)
        check = checker.check
        try:
            if not check([]): raise ValueError('compiler rejected the unchanged input')
            applications, receipt = search(subject, catalogue, check, args.budget,
                operation_costs=operation_costs, rounds=args.rounds, core_program=core_program)
            receipt['round_limit'] = args.rounds
            receipt['checker_sessions'] = dict(attempts=checker.session_attempts, rejected_closures=checker.rejected_closures, checked_roots=sorted(checker.checked_roots))
            receipt['admission'] = 'each proposed application list and final output checked with its exact selected-law/premise dependency closure; unused discovery objects are not mathematical authority'
            package['applications'] = applications
            if not check(applications): raise ValueError('compiler rejected final selection')
        finally:
            checker.close()
        # Even non-compact formatting must not resupply unused discovery roots
        # to a later replay's single fixed proof-work budget.
        package['knowledge'] = store.bundle(selection_roots(applications))
        args.o.parent.mkdir(parents=True, exist_ok=True); args.o.write_text(json.dumps(package, separators=(',',':')) + '\n' if args.compact_output else json.dumps(package, indent=2) + '\n')
    if hashlib.sha256(compiler.read_bytes()).hexdigest() != original_hash: raise ValueError('compiler changed during search')
    print(json.dumps(dict(compiler_sha256=original_hash, database=dict(snapshot_sha256=store.snapshot_id,source_snapshot_sha256=original_snapshot_id,entries=len(roots),loaded_objects=len(discovery),producers=producer_receipts), cost_model=cost_model_receipt, **receipt)))
if __name__ == '__main__': main()
