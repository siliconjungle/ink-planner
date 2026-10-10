"""Untrusted discovery and deterministic AST filtering of immutable entries.

No rule names or optimisation laws live here. Shape filtering only reduces search
work; the unchanged Ink checker must validate every selected application.
"""
import copy
import hashlib
import json
from pathlib import Path

SEMANTICS = 'first-order-inductive-equality-v1'
MAX_FILES = 8192
MAX_BYTES = 64_000_000


def identity(raw):
    return hashlib.sha256(raw).hexdigest()


def scalar_endpoint(term, objects, budget, bindings=None, active=(), depth=0):
    if depth > 64 or budget[0] <= 0:
        raise ValueError('scalar discovery expansion limit')
    budget[0] -= 1
    bindings = bindings or {}
    kind, value = next(iter(term.items()))
    if kind == 'Var':
        if value in bindings:
            # Charge every substituted node, not merely the variable occurrence.
            return scalar_endpoint(bindings[value], objects, budget, {}, active, depth)
        return {'Var': value}
    if kind in ('U64', 'Bool'):
        return copy.deepcopy(term)
    if kind == 'Binary':
        return {'Binary': dict(op=value['op'],
            left=scalar_endpoint(value['left'], objects, budget, bindings, active, depth + 1),
            right=scalar_endpoint(value['right'], objects, budget, bindings, active, depth + 1))}
    if kind == 'Call':
        id_ = value['function']
        function = objects[id_]['declaration']['Function']
        if id_ in active or function['recursive'] is not None or len(function['params']) != len(value['arguments']):
            raise ValueError('non-acyclic scalar definition')
        local = {n: scalar_endpoint(a, objects, budget, bindings, active, depth + 1)
            for (n, _), a in zip(function['params'], value['arguments'])}
        return scalar_endpoint(function['body'], objects, budget, local, (*active, id_), depth + 1)
    raise ValueError('non-scalar theorem endpoint')


def variables(term):
    pending, result = [term], set()
    while pending:
        t = pending.pop()
        k, v = next(iter(t.items()))
        if k == 'Var':
            result.add(v)
        elif k == 'Binary':
            pending.extend([v['left'], v['right']])
    return result


def pattern_key(term):
    k, v = next(iter(term.items()))
    if k == 'Var':
        return ('any',)
    if k == 'Binary':
        return ('Binary', v['op'])
    return (k, v)


def source_key(expr):
    k, v = next(iter(expr.items()))
    if k == 'Binary':
        return ('Binary', v[0])
    if k == 'Num':
        return ('U64', v)
    if k == 'Bool':
        return (k, v)
    return (k,)


class RuleQuery:
    """Fixed hash order, root-shape buckets, no scheduling based on wall time."""
    def __init__(self, rules):
        self.rules = sorted(rules, key=lambda rule: rule[0])
        self.buckets = {}
        self.queries = self.candidates_returned = 0
        for rule in self.rules:
            self.buckets.setdefault(pattern_key(rule[1]['from']), []).append(rule)

    def candidates(self, expr):
        self.queries += 1
        candidates = sorted([*self.buckets.get(('any',), []),
            *self.buckets.get(source_key(expr), [])], key=lambda rule: rule[0])
        self.candidates_returned += len(candidates)
        return candidates

    def __iter__(self):
        return iter(self.rules)


class Database:
    def __init__(self, root):
        root = Path(root).resolve()
        if not root.is_dir():
            raise ValueError('database root must be a directory')
        self.raw, self.objects = {}, {}
        total, files, ignored = 0, 0, 0
        paths = []
        for path in root.glob('**/objects/*.json'):
            if len(paths) >= MAX_FILES:
                raise ValueError('database discovery file count limit')
            paths.append(path)
        for path in sorted(paths):
            files += 1
            if files > MAX_FILES:
                raise ValueError('database discovery file count limit')
            if not path.resolve().is_relative_to(root):
                raise ValueError('database entry escapes the configured root')
            with path.open('rb') as f:
                raw = f.read(4_000_001)
            total += len(raw)
            if len(raw) > 4_000_000 or total > MAX_BYTES:
                raise ValueError('database discovery byte limit')
            id_ = identity(raw)
            object_ = json.loads(raw)
            if object_.get('semantics') != SEMANTICS:
                ignored += 1
                continue
            if path.stem != id_:
                raise ValueError('database entry content hash mismatch')
            if object_.get('schema') != 1 or not isinstance(object_.get('dependencies'), list):
                raise ValueError('invalid database entry schema')
            self.raw[id_] = raw
            self.objects[id_] = object_
        rules, skipped = [], []
        for id_, object_ in sorted(self.objects.items()):
            theorem = object_.get('declaration', {}).get('Theorem')
            if theorem is None:
                continue
            try:
                if any(t not in ('U64', 'Bool') for _, t in theorem['params']):
                    raise ValueError('non-scalar parameters')
                budget = [10000]
                t = copy.deepcopy(theorem)
                t['from'] = scalar_endpoint(t['from'], self.objects, budget)
                t['to'] = scalar_endpoint(t['to'], self.objects, budget)
                t['conditions'] = [{k: scalar_endpoint(v, self.objects, budget)
                    for k, v in c.items()} for c in t['conditions']]
                names = {n for n, _ in t['params']}
                # Application cannot guess an argument absent from its pattern.
                if variables(t['from']) != names or not variables(t['to']) <= names:
                    raise ValueError('parameters not bound by the original expression')
                rules.append((id_, t))
            except (ValueError, KeyError, TypeError, StopIteration, RecursionError) as e:
                skipped.append(dict(entry=id_, reason=str(e)))
        self.query = RuleQuery(rules)
        # Paths, directory enumeration order and duplicate copies are immaterial.
        self.manifest = dict(schema=1, semantics='ink-discovered-entries-v1', entries=sorted(self.raw))
        self.manifest_bytes = (json.dumps(self.manifest, separators=(',', ':')) + '\n').encode()
        self.snapshot = identity(self.manifest_bytes)
        self.discovery = dict(semantics='ink-ast-database-query-v1',
            snapshot_sha256=self.snapshot, entries=len(self.raw), scalar_rules=len(rules),
            skipped_theorems=skipped, ignored_incompatible_files=ignored)

    def selected(self, roots, generated):
        """Freeze precisely the selected entries and dependency closure."""
        raw = dict(self.raw)
        raw.update(generated)
        selected, active = {}, set()

        def visit(id_, depth=0):
            if id_ in selected:
                return
            if id_ in active or depth > 64:
                raise ValueError('cyclic or too deep database dependency')
            if len(active) + len(selected) >= 128:
                raise ValueError('selected database closure count limit')
            data = raw[id_]
            if identity(data) != id_:
                raise ValueError('selected database entry changed')
            active.add(id_)
            object_ = json.loads(data)
            if object_['schema'] != 1 or object_['semantics'] != SEMANTICS:
                raise ValueError('incompatible selected database entry')
            for dependency in object_['dependencies']:
                visit(dependency, depth + 1)
            active.remove(id_)
            selected[id_] = data

        for root in sorted(set(roots)):
            visit(root)
        if sum(map(len, selected.values())) > 16_000_000:
            raise ValueError('selected database closure byte limit')
        return selected
