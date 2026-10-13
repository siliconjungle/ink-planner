"""Equality saturation as an untrusted search strategy.

An e-graph holds many equal programs at once. It is grown by applying every
unconditional catalogue law at every match until a node or round budget is
reached, then the cheapest program is extracted under the same structural
cost model as the beam search. The e-graph proves nothing: the extracted
program is only a target. A guided search then rebuilds an ordinary
application chain towards it, using only laws that took part in saturation,
and the checker admits every step as usual. When the target cannot be
reached, the cheapest admitted state on the way is kept, and the caller
compares it with the beam search's result.

Pattern variables bind to classes. An argument that must be closed (a variable
under a pattern lambda) is taken from the class's current cheapest term, as
the ordinary matcher would extract it; instances made this way are still
equalities, so unions stay sound with respect to the laws.
"""
import json
import time

from .semantic_search import children, cost, extract_hole, key, match, positions, replace, substitute

LEAF = 'leaf'


class EGraph:
    def __init__(self, cost_of, node_limit=4000):
        self.parent = []
        self.table = {}        # canonical e-node -> class
        self.members = {}      # class -> set of e-nodes
        self.cost_of = cost_of
        self.node_limit = node_limit
        self.best = {}         # class -> (cost, term)
        self.used = set()      # laws whose instances caused a union

    def find(self, c):
        while self.parent[c] != c:
            self.parent[c] = self.parent[self.parent[c]]
            c = self.parent[c]
        return c

    def canonical(self, enode):
        sort, kind, label, kids = enode
        return (sort, kind, label, tuple(self.find(k) for k in kids))

    def _new(self, enode):
        c = len(self.parent)
        self.parent.append(c)
        self.table[enode] = c
        self.members[c] = {enode}
        return c

    def add(self, term):
        n = term['node']
        sort = key(term['sort'])
        if isinstance(n, dict) and 'Op' in n:
            enode = (sort, 'Op', n['Op'][0], tuple(self.add(c) for c in n['Op'][1]))
        elif isinstance(n, dict) and 'Lambda' in n:
            enode = (sort, 'Lambda', '', (self.add(n['Lambda']),))
        elif isinstance(n, dict) and 'Cons' in n:
            enode = (sort, 'Cons', '', tuple(self.add(c) for c in n['Cons']))
        else:
            enode = (sort, LEAF, key(n), ())
        enode = self.canonical(enode)
        if enode in self.table:
            return self.find(self.table[enode])
        return self._new(enode)

    def union(self, a, b):
        a, b = self.find(a), self.find(b)
        if a == b:
            return False
        if len(self.members[a]) < len(self.members[b]):
            a, b = b, a
        self.parent[b] = a
        self.members[a] |= self.members.pop(b)
        return True

    def rebuild(self):
        """Restore congruence: equal canonical e-nodes share one class."""
        while True:
            merged = False
            table = {}
            for enode, c in list(self.table.items()):
                canon = self.canonical(enode)
                c = self.find(c)
                if canon in table and self.find(table[canon]) != c:
                    self.union(table[canon], c)
                    merged = True
                table[canon] = self.find(c)
            self.table = table
            members = {}
            for enode, c in table.items():
                members.setdefault(self.find(c), set()).add(enode)
            self.members = members
            if not merged:
                return

    def size(self):
        return len(self.table)

    def term(self, enode, kid_terms):
        sort, kind, label, _ = enode
        sort = json.loads(sort)
        if kind == 'Op':
            return dict(sort=sort, node={'Op': [label, kid_terms]})
        if kind == 'Lambda':
            return dict(sort=sort, node={'Lambda': kid_terms[0]})
        if kind == 'Cons':
            return dict(sort=sort, node={'Cons': kid_terms})
        return dict(sort=sort, node=json.loads(label))

    def extract(self, passes=12):
        """Cheapest term per class under the structural cost model."""
        best = {}
        for _ in range(passes):
            changed = False
            for enode, c in self.table.items():
                c = self.find(c)
                kids = [best.get(self.find(k)) for k in enode[3]]
                if any(k is None for k in kids):
                    continue
                t = self.term(enode, [k[1] for k in kids])
                value = self.cost_of(t)
                if c not in best or value < best[c][0]:
                    best[c] = (value, t)
                    changed = True
            if not changed:
                break
        self.best = best
        return best

    def ematch(self, pattern, params, c, binding, depth=0):
        """Bindings of pattern variables to classes (or closed terms under binders)."""
        n = pattern['node']
        c = self.find(c)
        if isinstance(n, dict) and 'Free' in n and n['Free'] in params:
            name = n['Free']
            if depth:
                best = self.best.get(c)
                if best is None:
                    return
                try:
                    value = ('term', key(extract_hole(best[1], depth)))
                except ValueError:
                    return
            else:
                value = ('class', c)
            if name in binding:
                if binding[name] == value:
                    yield binding
                return
            yield dict(binding, **{name: value})
            return
        sort = key(pattern['sort'])
        for enode in list(self.members.get(c, ())):
            esort, kind, label, kids = enode
            if esort != sort:
                continue
            if isinstance(n, dict) and 'Op' in n:
                if kind != 'Op' or label != n['Op'][0] or len(kids) != len(n['Op'][1]):
                    continue
                subpatterns, inner = n['Op'][1], depth
            elif isinstance(n, dict) and 'Lambda' in n:
                if kind != 'Lambda':
                    continue
                subpatterns, inner = [n['Lambda']], depth + 1
            elif isinstance(n, dict) and 'Cons' in n:
                if kind != 'Cons':
                    continue
                subpatterns, inner = n['Cons'], depth
            else:
                if kind == LEAF and label == key(n):
                    yield binding
                continue

            def walk(i, b):
                if i == len(subpatterns):
                    yield b
                    return
                for nb in self.ematch(subpatterns[i], params, kids[i], b, inner):
                    yield from walk(i + 1, nb)
            yield from walk(0, binding)

    def arguments(self, binding):
        out = {}
        for name, (kind, value) in binding.items():
            if kind == 'class':
                best = self.best.get(self.find(value))
                if best is None:
                    return None
                out[name] = best[1]
            else:
                out[name] = json.loads(value)
        return out


def first_order(law):
    """Unconditional laws whose variables are never applied as functions."""
    if law.get('conditions') or law.get('types') or law.get('assumptions'):
        return False
    applied = []

    def scan(t):
        n = t['node']
        if isinstance(n, dict) and n.get('Op', [None])[0] == 'apply':
            head = n['Op'][1][0]['node']
            if isinstance(head, dict) and head.get('Free') in law['params']:
                applied.append(head['Free'])
        for c in children(t):
            scan(c)
    scan(law['from'])
    return not applied


def saturate(body, laws, cost_of, node_limit=4000, rounds=8, deadline=None):
    graph = EGraph(cost_of, node_limit)
    root = graph.add(body)
    graph.extract()
    stats = dict(rounds=0, unions=0, applications=0, stopped='saturated')
    for _ in range(rounds):
        stats['rounds'] += 1
        pending = []
        for ident, law in laws.items():
            for c in list(graph.members):
                for binding in graph.ematch(law['from'], law['params'], c, {}):
                    if set(binding) != set(law['params']):
                        continue
                    pending.append((ident, law, c, binding))
                    if len(pending) > 4 * node_limit:
                        break
        changed = False
        for ident, law, c, binding in pending:
            if graph.size() > node_limit:
                stats['stopped'] = 'node limit'
                break
            if deadline is not None and time.monotonic() > deadline:
                stats['stopped'] = 'time'
                break
            args = graph.arguments(binding)
            if args is None:
                continue
            try:
                new = graph.add(substitute(law['to'], args))
            except (ValueError, KeyError):
                continue
            stats['applications'] += 1
            if graph.union(c, new):
                graph.used.add(ident)
                stats['unions'] += 1
                changed = True
        graph.rebuild()
        graph.extract()
        if not changed or stats['stopped'] != 'saturated':
            break
    best = graph.best.get(graph.find(root))
    return graph, best, stats


def distance(a, b):
    """Structural distance: shared prefix nodes count as matched."""
    if a == b:
        return 0
    ca, cb = children(a), children(b)
    same = (a['sort'] == b['sort'] and type(a['node']) == type(b['node']) and len(ca) == len(cb)
            and (not isinstance(a['node'], dict) or set(a['node']) == set(b['node']))
            and (not isinstance(a['node'], dict) or 'Op' not in a['node'] or a['node']['Op'][0] == b['node']['Op'][0]))
    if same and ca:
        return sum(distance(x, y) for x, y in zip(ca, cb))
    return sum(1 for _ in positions(a)) + sum(1 for _ in positions(b))


def guided(name, body, target, laws, catalogue, check, base_applications, score, budget, width=4, steps=48, deadline=None, alternatives=None):
    """Kernel-checked chain from `body` towards `target` using `laws` only."""
    from .semantic_search import condition_proof
    frontier = [(distance(body, target), score(body), body, [])]
    seen = {key(body)}
    best = (score(body), body, [])
    attempts = 0
    for _ in range(steps):
        proposals = []
        for _, _, current, applications in frontier:
            for path, site in positions(current):
                for ident, law in laws.items():
                    args = {}
                    if not match(law['from'], site, law['params'], args) or set(args) != set(law['params']):
                        continue
                    new = replace(current, path, substitute(law['to'], args))
                    k = key(new)
                    if k in seen:
                        continue
                    seen.add(k)
                    premises = [condition_proof({'from': substitute(c['from'], args), 'to': substitute(c['to'], args)}, catalogue)
                                for c in law['conditions']]
                    step = dict(function=name, path=list(path), law=ident, arguments=dict(sorted(args.items())), premises=premises)
                    proposals.append((distance(new, target), score(new), new, applications + [step]))
        proposals.sort(key=lambda x: (x[0], x[1]))
        frontier = []
        for proposal in proposals:
            if len(frontier) >= width or attempts >= budget or (deadline is not None and time.monotonic() > deadline):
                break
            attempts += 1
            if not check(base_applications + proposal[3]):
                continue
            if alternatives is not None:
                alternatives.append(base_applications + proposal[3])
            frontier.append(proposal)
            if proposal[1] < best[0]:
                best = (proposal[1], proposal[2], proposal[3])
            if proposal[0] == 0:
                return best, attempts, True
        if not frontier:
            break
    return best, attempts, False


def search(subject, catalogue, check, budget=4096, time_seconds=600, operation_costs=None, node_limit=4000, rounds=8, alternatives=None):
    """Saturate each function, extract, then rebuild a checked chain."""
    started = time.monotonic()
    deadline = started + time_seconds
    objects = catalogue['objects']
    roots = set(catalogue['roots'])
    laws = {i: o for i, o in objects.items() if i in roots and first_order(o)}
    cost_cache = {}

    def cost_of(t):
        k = key(t)
        if k not in cost_cache:
            cost_cache[k] = cost(t, operation_costs)
        return cost_cache[k]
    bodies = dict(subject['functions'])
    applications = []
    attempts = 0
    report = {}
    for name, body in sorted(bodies.items()):
        if time.monotonic() > deadline or attempts >= budget:
            break
        graph, best, stats = saturate(body, laws, cost_of, node_limit, rounds, deadline)
        initial = cost_of(body)
        entry = dict(stats, nodes=graph.size(), used_laws=len(graph.used), initial_cost=initial,
                     extracted_cost=best[0] if best else initial)
        if best is None or best[0] >= initial:
            entry['chain'] = 'no cheaper target'
            report[name] = entry
            continue
        used = {i: laws[i] for i in graph.used}
        (value, new_body, chain), spent, reached = guided(name, body, best[1], used, catalogue, check, applications,
                                                          cost_of, budget - attempts, deadline=deadline, alternatives=alternatives)
        attempts += spent
        entry.update(chain_steps=len(chain), reached_target=reached, chain_cost=value, attempts=spent)
        report[name] = entry
        if chain and value < initial:
            applications += chain
            bodies[name] = new_body
    initial = sum(cost_of(b) for b in subject['functions'].values())
    selected = sum(cost_of(b) for b in bodies.values())
    receipt = dict(initial_cost=initial, selected_cost=selected, attempts=attempts, rejected=0, generated_proposals=0,
                   exhausted=attempts >= budget, estimate='structural; not measured runtime',
                   egraph=dict(functions=report, node_limit=node_limit, rounds=rounds,
                               scope='Untrusted saturation; every chain step checked by the compiler'))
    return applications, receipt
