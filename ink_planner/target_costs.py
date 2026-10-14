"""Bounded source-component hypotheses. No estimate establishes proof or eligibility.

Vectors describe eager semantic producer work, not physical target instructions.
In particular GPU stage, transfer and allocation counts are hypotheses; literal
rank/compaction and serial scan/reduction lowering can differ substantially.
"""
from dataclasses import dataclass
import hashlib
import json
import math

COMPONENTS = ('invocations', 'traversal_elements', 'allocation_events',
              'allocation_bytes', 'copy_bytes', 'scalar_ops', 'div_rem',
              'branches', 'loop_body_pressure', 'gpu_dispatches',
              'upload_bytes', 'readback_bytes', 'unknown_work')
MAX_COUNT = 10**18
MAX_CARDINALITY = 10**9
MAX_PRICE = 10**9
MAX_FOLD_STEPS = 64
DEFAULT_LIMITS = {'nodes': 8192, 'depth': 48, 'helper_depth': 16}
BOUNDARIES = ('owned', 'into', 'resident_owned', 'resident_into',
              'gpu_upload_readback', 'gpu_resident_readback', 'gpu_resident')


def vector():
    return dict.fromkeys(COMPONENTS, 0)


def combine(*vectors):
    out = vector()
    for v in vectors:
        for k in COMPONENTS:
            out[k] = min(MAX_COUNT, out[k] + v[k])
    return out


def scaled(v, n):
    return {k: min(MAX_COUNT, v[k] * n) for k in COMPONENTS}


def list_type(sort):
    ty = sort.get('Value') if isinstance(sort, dict) else None
    return ty.get('List') if isinstance(ty, dict) else None


def size_of(ty):
    # Flat scalar carrier bytes only; JS boxing/array capacity is not inferred.
    return {'U32': 4, 'I32': 4, 'F32': 4, 'U64': 8, 'Bool': 1}.get(ty) if isinstance(ty, str) else None


@dataclass
class Value:
    sort: object
    length: object = None
    exact: bool = False
    closure: object = None
    literal: object = None
    fresh: bool = False


class TargetCostModel:
    """Schema2 prices in ns. All facts are bounded syntax-derived hypotheses."""
    def __init__(self, data):
        if not isinstance(data, dict) or set(data) - {'schema', 'target', 'boundary', 'cardinality', 'weights', 'limits'}:
            raise ValueError('invalid target component model fields')
        if type(data.get('schema')) is not int or data['schema'] != 2:
            raise ValueError('invalid target component schema')
        if not isinstance(data.get('target'), str) or not data['target'] or len(data['target']) > 128:
            raise ValueError('invalid target label')
        if data.get('boundary') not in BOUNDARIES:
            raise ValueError('invalid target boundary')
        cardinality = data.get('cardinality')
        if not isinstance(cardinality, dict) or set(cardinality) != {'default', 'by_parameter'}:
            raise ValueError('invalid cardinality hypothesis')
        self._integer(cardinality['default'], MAX_CARDINALITY)
        named = cardinality['by_parameter']
        if not isinstance(named, dict) or len(named) > 1024:
            raise ValueError('invalid named cardinalities')
        for name, n in named.items():
            if not isinstance(name, str) or not name or len(name) > 256:
                raise ValueError('invalid parameter name')
            self._integer(n, MAX_CARDINALITY)
        weights = data.get('weights')
        if not isinstance(weights, dict) or set(weights) != set(COMPONENTS):
            raise ValueError('all target components require explicit prices')
        for price in weights.values():
            if type(price) not in (int, float) or not math.isfinite(price) or not 0 <= price <= MAX_PRICE:
                raise ValueError('prices must be finite nonnegative ns numbers')
        if weights['unknown_work'] <= 0:
            raise ValueError('unknown work requires a positive fallback price')
        limits = data.get('limits', DEFAULT_LIMITS)
        if not isinstance(limits, dict) or set(limits) != set(DEFAULT_LIMITS):
            raise ValueError('invalid analysis limits')
        for name, maximum in DEFAULT_LIMITS.items():
            self._integer(limits[name], maximum)
            if not limits[name]:
                raise ValueError('analysis limits must be positive')
        # Copy external data: mutating caller dictionaries cannot change ranking.
        self.data = json.loads(json.dumps(data))
        self.weights = dict(weights)
        self.cardinality = dict(default=cardinality['default'], by_parameter=dict(named))
        self.limits = dict(limits)
        self.gpu = data['target'].lower() in ('gpu', 'webgpu', 'wgsl')

    @staticmethod
    def _integer(n, maximum):
        if type(n) is not int or not 0 <= n <= maximum:
            raise ValueError('cardinalities and budgets must be bounded integers')

    def describe(self):
        return dict(self.data, scope='Untrusted source/lowering hypothesis; no proof, timing, or GPU eligibility authority',
                    units='ns coefficients × source component counts')

    def estimate_function(self, name, bodies, core_program):
        return _Estimator(self, bodies, core_program).entry(name)

    def estimate_module(self, bodies, core_program):
        # Each named function is priced as an independent public entry. Callers
        # still expand current helper bodies, so changing a helper changes them.
        estimates = {name: self.estimate_function(name, bodies, core_program) for name in sorted(bodies)}
        counts = combine(*(r['components'] for r in estimates.values()))
        return self._receipt(counts, estimates=estimates,
                             complete=all(r['complete'] for r in estimates.values()),
                             unknown_reasons=sorted({s for r in estimates.values() for s in r['unknown_reasons']}),
                             definitions_consumed=sorted({s for r in estimates.values() for s in r['definitions_consumed']}),
                             assumptions=sorted({s for r in estimates.values() for s in r['assumptions']}))

    def _receipt(self, counts, **extra):
        counts = {k: min(MAX_COUNT, counts[k]) for k in COMPONENTS}
        return dict(score_ns=sum(counts[k] * self.weights[k] for k in COMPONENTS),
                    completeness_scope='bounded source-component analysis, never exact physical lowering',
                    components=counts, target=self.data['target'], boundary=self.data['boundary'], **extra)


class _Estimator:
    def __init__(self, model, bodies, program):
        self.model, self.bodies = model, bodies
        self.signatures = {f['name']: f for f in program.get('functions', [])}
        self.nodes = 0
        self.unknowns = set()
        self.consumed = set()
        self.assumptions = {'Input cardinalities are user-supplied hypotheses, not validated runtime facts',
                            'Allocation/copy bytes are logical flat-carrier estimates; target representation and reuse may differ',
                            'loop_body_pressure is extent × expanded callback work squared; not measured register pressure'}
        if model.gpu:
            self.assumptions.add('GPU dispatches describe semantic stages, not actual eligibility or physical parallel algorithms')
            self.unknowns.add('GPU physical lowering is unknown: filter rank/compaction, serial scan/ordered reduction and graph staging may differ')

    def unknown(self, sort, reason):
        self.unknowns.add(reason)
        counts = vector()
        counts['unknown_work'] = 1
        return Value(sort, self.model.cardinality['default'] if list_type(sort) is not None else None), counts

    def preflight(self, term):
        stack = [(term, 0)]
        n = 0
        while stack:
            t, depth = stack.pop()
            n += 1
            if n > self.model.limits['nodes'] or depth > self.model.limits['depth']:
                return False
            if not isinstance(t, dict) or 'sort' not in t or 'node' not in t:
                return False
            node = t['node']
            if isinstance(node, dict):
                children = node.get('Op', [None, []])[1] if 'Op' in node else ([node['Lambda']] if 'Lambda' in node else node.get('Cons', []))
                if not isinstance(children, list):
                    return False
                stack.extend((child, depth + 1) for child in children)
        return True

    def consume(self, name):
        f = self.signatures[name]
        body = self.bodies[name]
        # Hash actual current semantic body and declared ABI, never source body
        # metadata or a theorem ID. Digest is disclosure, not admission authority.
        data = dict(name=name, params=f['params'], result=f['result'], body=body)
        self.consumed.add(name + ':' + hashlib.sha256(json.dumps(data, sort_keys=True, separators=(',', ':')).encode()).hexdigest())

    def entry(self, name):
        signature = self.signatures.get(name)
        if signature is None or name not in self.bodies:
            _, counts = self.unknown(None, 'missing actual core signature: ' + name)
            counts['invocations'] = 1
            return self.receipt(counts)
        env = {}
        for parameter, ty in signature['params']:
            sort = {'Value': ty}
            n = self.model.cardinality['by_parameter'].get(parameter, self.model.cardinality['default'])
            env[parameter] = Value(sort, n if list_type(sort) is not None else None, False)
        value, counts = self.helper(name, list(env.values()), (), 0)
        counts['invocations'] += 1
        boundary = self.model.data['boundary']
        if boundary in ('owned', 'resident_owned') and value.length is not None:
            # Return alias requires an owned boundary copy; fresh producers are
            # already charged. Do not assume output-destination reuse here.
            if not value.fresh:
                counts['allocation_events'] += 1
                counts['allocation_bytes'] += self.bytes(value)
                counts['copy_bytes'] += self.bytes(value)
        if boundary in ('into', 'resident_into') and value.length is not None and not value.fresh:
            counts['copy_bytes'] += self.bytes(value)
        if self.model.gpu:
            counts['unknown_work'] += 1
            if boundary == 'gpu_upload_readback':
                for v in env.values():
                    if v.length is not None:
                        counts['upload_bytes'] += self.bytes(v)
            if boundary in ('gpu_upload_readback', 'gpu_resident_readback'):
                counts['readback_bytes'] += self.bytes(value) if value.length is not None else (size_of(signature['result']) or 0)
        return self.receipt(counts)

    def receipt(self, counts):
        if self.unknowns:
            counts['unknown_work'] = max(1, counts['unknown_work'])
        return self.model._receipt(counts, complete=not self.unknowns,
                                   unknown_reasons=sorted(self.unknowns),
                                   definitions_consumed=sorted(self.consumed),
                                   assumptions=sorted(self.assumptions))

    def bytes(self, value):
        size = size_of(list_type(value.sort))
        if size is None:
            self.unknowns.add('unknown flat collection element layout')
            return 0
        return min(MAX_COUNT, (value.length or 0) * size)

    def helper(self, name, args, active, depth):
        signature = self.signatures.get(name)
        if signature is None or name not in self.bodies or len(args) != len(signature['params']):
            return self.unknown({'Value': signature['result']} if signature else None, 'unresolved helper signature: ' + name)
        sort = {'Value': signature['result']}
        if name in active or len(active) >= self.model.limits['helper_depth'] or not self.preflight(self.bodies[name]):
            return self.unknown(sort, 'recursive, deep or oversized helper: ' + name)
        self.consume(name)
        env = dict(zip((p[0] for p in signature['params']), args))
        return self.eval(self.bodies[name], env, (), active + (name,), depth + 1)

    def invoke(self, value, args, active, depth):
        counts = vector()
        for argument in args:
            if value.closure is None:
                unknown, extra = self.unknown(None, 'unresolved callback closure')
                return unknown, combine(counts, extra)
            body, env, bound = value.closure
            value, extra = self.eval(body, env, (argument,) + bound, active, depth + 1)
            counts = combine(counts, extra)
        return value, counts

    def loop(self, counts, callback, n, stage=True):
        counts = combine(counts, scaled(callback, n))
        counts['traversal_elements'] = min(MAX_COUNT, counts['traversal_elements'] + n)
        work = callback['scalar_ops'] + callback['div_rem'] + callback['branches'] + callback['unknown_work']
        counts['loop_body_pressure'] = min(MAX_COUNT, counts['loop_body_pressure'] + n * work * work)
        if self.model.gpu and stage:
            counts['gpu_dispatches'] += 1
        return counts

    @staticmethod
    def stable_value(left, right):
        # Captured closure environments can change future work even when their
        # outer value descriptors look identical. Do not accelerate those.
        return left.closure is None and right.closure is None and (
            left.sort, left.length, left.exact, left.literal, left.fresh) == (
            right.sort, right.length, right.exact, right.literal, right.fresh)

    def fold(self, sort, values, counts, active, depth):
        source, accumulator, callback = values
        n = source.length
        if n is None:
            n = self.model.cardinality['default']
            counts['unknown_work'] += 1
            self.unknowns.add('unknown fold extent')
        if n == 0:
            return accumulator, counts
        element = Value({'Value': list_type(source.sort)})
        if self.model.gpu:
            counts['gpu_dispatches'] += 1
            counts['unknown_work'] += n
        self.assumptions.add('Fold analysis tracks abstract accumulator values; element values and order-dependent costs remain unknown')
        for step in range(min(n, MAX_FOLD_STEPS)):
            result, work = self.invoke(callback, [element, accumulator], active, depth)
            counts = self.loop(counts, work, 1, stage=False)
            remaining = n-step-1
            if not remaining:
                return result, counts
            if self.stable_value(accumulator, result):
                # This is an abstract fixed point, not an extrapolation from
                # two sampled runtimes or a guessed cardinality growth rate.
                return result, self.loop(counts, work, remaining, stage=False)
            accumulator = result
            if self.nodes >= self.model.limits['nodes']:
                break
        value, unknown = self.unknown(sort, 'fold accumulator did not stabilize within analysis budget')
        value.length = None  # Do not pretend its output has the input extent.
        return value, self.loop(counts, unknown, remaining, stage=False)

    def eval(self, term, env, bound, active, depth):
        self.nodes += 1
        sort = term['sort']
        if self.nodes > self.model.limits['nodes'] or depth > self.model.limits['depth']:
            return self.unknown(sort, 'analysis node/depth budget exhausted')
        node = term['node']
        if node == 'Nil':
            counts = vector(); counts['allocation_events'] = 1
            return Value(sort, 0, True, fresh=True), counts
        if not isinstance(node, dict):
            return self.unknown(sort, 'unsupported semantic constructor')
        if 'Free' in node:
            return (env[node['Free']], vector()) if node['Free'] in env else self.unknown(sort, 'unbound value: ' + node['Free'])
        if 'Bound' in node:
            return (bound[node['Bound']], vector()) if type(node['Bound']) is int and 0 <= node['Bound'] < len(bound) else self.unknown(sort, 'unresolved binder')
        if 'Lambda' in node:
            return Value(sort, closure=(node['Lambda'], dict(env), bound)), vector()
        for tag in ('Word', 'Nat', 'Bool', 'Float'):
            if tag in node:
                return Value(sort, literal=node[tag]), vector()
        if 'Cons' in node:
            _, a = self.eval(node['Cons'][0], env, bound, active, depth + 1)
            tail, b = self.eval(node['Cons'][1], env, bound, active, depth + 1)
            n = min(MAX_COUNT, (tail.length or 0) + 1)
            v = Value(sort, n, tail.exact, fresh=True)
            counts = combine(a, b); counts['allocation_events'] += 1
            counts['allocation_bytes'] += self.bytes(v); counts['copy_bytes'] += self.bytes(tail)
            return v, counts
        if 'Op' not in node:
            return self.unknown(sort, 'unsupported semantic constructor')
        name, terms = node['Op']
        if name in ('binary:&&', 'binary:||') and len(terms) == 2:
            left, counts = self.eval(terms[0], env, bound, active, depth + 1)
            required = True if name == 'binary:&&' else False
            if type(left.literal) is bool and left.literal != required:
                counts['branches'] += 1
                return Value(sort, literal=left.literal), counts
            right, extra = self.eval(terms[1], env, bound, active, depth + 1)
            counts = combine(counts, extra); counts['branches'] += 1
            if type(left.literal) is not bool:
                self.assumptions.add('Unknown short-circuit branch conservatively prices its right arm')
                # A neutral right operand preserves the unknown left value:
                # x && true and x || false cannot determine a later branch.
                # Only an absorbing constant determines the whole expression.
                if type(right.literal) is not bool or right.literal == required:
                    return Value(sort), counts
            return right, counts
        if name == 'call:choose' and len(terms) == 3:
            condition, a = self.eval(terms[0], env, bound, active, depth + 1)
            if type(condition.literal) is bool:
                v, b = self.eval(terms[1 if condition.literal else 2], env, bound, active, depth + 1)
                a = combine(a, b)
            else:
                left, b = self.eval(terms[1], env, bound, active, depth + 1)
                right, c = self.eval(terms[2], env, bound, active, depth + 1)
                a = combine(a, {k: max(b[k], c[k]) for k in COMPONENTS})
                lengths = [left.length, right.length]
                v = Value(sort, max(lengths) if all(n is not None for n in lengths) else None,
                          left.exact and right.exact and left.length == right.length, fresh=left.fresh and right.fresh)
                if left.closure is not None or right.closure is not None:
                    self.unknowns.add('branch-selected callback closure unresolved')
                self.assumptions.add('Unknown branches use componentwise maximum; no branch frequency or independence assumed')
            a['branches'] += 1
            return v, a
        values, counts = [], vector()
        for t in terms:
            v, c = self.eval(t, env, bound, active, depth + 1)
            values.append(v); counts = combine(counts, c)
        if name == 'apply' and len(values) == 2:
            v, extra = self.invoke(values[0], values[1:], active, depth)
            return v, combine(counts, extra)
        if name.startswith('call:') and name[5:] in self.signatures:
            v, extra = self.helper(name[5:], values, active, depth)
            return v, combine(counts, extra)
        maps = ('method:map', 'method:map_indexed', 'indexed.from', 'method:filter', 'method:zip')
        if name in maps and values:
            n = values[0].length
            if name == 'method:zip':
                n = min(n, values[1].length) if n is not None and values[1].length is not None else None
            if n is None:
                self.unknowns.add('unknown collection extent: ' + name); n = self.model.cardinality['default']
                counts['unknown_work'] += 1
            element = Value({'Value': list_type(values[0].sort)})
            arguments = [element]
            if name in ('method:map_indexed', 'indexed.from'):
                arguments = [Value({'Value': 'U32'}), element]
            elif name == 'method:zip':
                arguments = [element, Value({'Value': list_type(values[1].sort)})]
            # An empty producer never invokes its callback. Unsupported work
            # in that body must not make this known-empty path incomplete.
            callback = vector()
            if n:
                _, callback = self.invoke(values[-1], arguments, active, depth)
            counts = self.loop(counts, callback, n)
            v = Value(sort, n, values[0].exact and name != 'method:filter', fresh=True)
            counts['allocation_events'] += 1; counts['allocation_bytes'] += self.bytes(v)
            if name == 'method:filter':
                counts['copy_bytes'] += self.bytes(v)
                self.assumptions.add('Filter output uses its input upper bound; accepted cardinality is unknown')
                if self.model.gpu:
                    counts['unknown_work'] += n or 1
            return v, counts
        if name in ('method:concat',) and len(values) == 2:
            n = min(MAX_COUNT, sum(v.length if v.length is not None else self.model.cardinality['default'] for v in values))
            v = Value(sort, n, all(v.exact for v in values), fresh=True)
            counts['allocation_events'] += 1; counts['allocation_bytes'] += self.bytes(v)
            counts['copy_bytes'] += self.bytes(v); counts['traversal_elements'] += n
            if self.model.gpu: counts['gpu_dispatches'] += 1
            return v, counts
        if name in ('call:foldr', 'call:foldl') and len(values) == 3:
            return self.fold(sort, values, counts, active, depth)
        if name in ('method:scan', 'scan.acc', 'call:sum', 'sum.acc') and values:
            n = values[0].length
            if n is None:
                n = self.model.cardinality['default']; counts['unknown_work'] += 1
                self.unknowns.add('unknown reduction extent')
            callback = vector(); callback['scalar_ops'] = 1
            counts = self.loop(counts, callback, n)
            if self.model.gpu:
                counts['unknown_work'] += n or 1
            if list_type(sort) is not None:
                v = Value(sort, n, values[0].exact, fresh=True)
                counts['allocation_events'] += 1; counts['allocation_bytes'] += self.bytes(v)
            else: v = Value(sort)
            return v, counts
        if name in ('call:length', 'call:count', 'method:len', 'nat.word', 'nat.succ'):
            counts['scalar_ops'] += 1
            return Value(sort), counts
        if name == 'method:at_or':
            counts['scalar_ops'] += 1; counts['branches'] += 1
            return Value(sort), counts
        if name in ('call:quot_or', 'call:rem_or'):
            counts['div_rem'] += 1; counts['branches'] += 1
            return Value(sort), counts
        if name.startswith('binary:') or name == 'neg':
            counts['scalar_ops'] += 1
            return Value(sort), counts
        v, extra = self.unknown(sort, 'unsupported operation: ' + name)
        return v, combine(counts, extra)
