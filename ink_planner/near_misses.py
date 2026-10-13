"""Untrusted search observations: where a matched law was not admitted.

A near miss is a proposal whose law pattern matched a program position but whose
exact application the independent checker rejected, or which search never
checked within its budgets. Records are diagnostics only. A rejection reason
describes one exact proposal; it does not establish that the law can never
apply, and an absent record does not establish that no law applies. Nothing
here enters proof admission, ranking or selection.
"""
import re
from collections import Counter
from pathlib import Path

SEMANTICS = 'ink-search-near-misses-v1'
MAX_LAWS = 4096
MAX_REPORTED_LAWS = 512
MAX_SAMPLES = 4
MAX_TEXT = 400
MAX_SHAPES = 128
PREMISE = re.compile(r'premise (\d+) of (\S+): ')


def skeleton(term, params, depth=2):
    """A bounded, parameter-erased outline of a typed semantic term."""
    node = term.get('node') if isinstance(term, dict) else term
    if isinstance(node, dict):
        if 'Free' in node:
            return '_' if node['Free'] in params else 'free'
        if 'Bound' in node:
            return 'bound'
        if 'Op' in node:
            name, args = node['Op']
            if depth <= 0 or not args:
                return name if not args else name + '(..)'
            return name + '(' + ', '.join(skeleton(a, params, depth - 1) for a in args) + ')'
        if 'Lambda' in node:
            return 'fn' if depth <= 0 else 'fn.' + skeleton(node['Lambda'], params, depth - 1)
        if 'Cons' in node:
            return 'cons(..)'
        kind = next(iter(node))
        value = node[kind]
        if isinstance(value, (bool, int, str)) and len(str(value)) <= 24:
            return f'{kind}:{value}'
        return kind
    return str(node)


def premise_shape(condition, params):
    return skeleton(condition['from'], params) + ' = ' + skeleton(condition['to'], params)


def instantiated_shape(condition, arguments, depth=3):
    """The failed premise as the program presented it: law parameters replaced
    by their bound program terms, program variables shown as `free`."""
    try:
        from ink_planner.semantic_search import substitute
        left = substitute(condition['from'], arguments)
        right = substitute(condition['to'], arguments)
    except (KeyError, TypeError, ValueError):
        return None
    return skeleton(left, (), depth) + ' = ' + skeleton(right, (), depth)


def classify(reason):
    """Map a checker message to a coarse, documented gate; never authority."""
    if not reason:
        return 'unexplained', None
    text = str(reason)
    found = PREMISE.search(text)
    if found:
        return 'premise', int(found.group(1))
    if 'does not match exact selected expression' in text:
        return 'endpoint', None
    if 'not an enabled catalogue root' in text:
        return 'not_root', None
    if text.startswith('closure:'):
        return 'closure', None
    if 'general laws apply through checked instances' in text:
        return 'general_law', None
    if 'limit' in text or 'budget' in text:
        return 'checker_budget', None
    return 'other', None


class NearMisses:
    """Bounded per-law counters for one search invocation.

    With `path`, `flush` periodically replaces a partial receipt so a search cut
    off by an outer process budget still leaves its observations behind.
    """

    def __init__(self, path=None, interval=2.0):
        self.laws = {}
        self.totals = Counter()
        self.reasons = Counter()
        self.shapes = Counter()
        self.instances = Counter()
        self.path = path
        self.interval = interval
        self.flushed = None
        self.extra = {}

    def flush(self, force=False, **extra):
        if self.path is None:
            return
        import json, os, tempfile, time
        now = time.monotonic()
        if not force and self.flushed is not None and now - self.flushed < self.interval:
            return
        self.flushed = now
        self.extra.update(extra)
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.near-misses-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write(json.dumps(self.receipt(**self.extra), separators=(',', ':')) + '\n')
            os.replace(temporary, path)
        finally:
            if Path(temporary).exists():
                Path(temporary).unlink()

    def _entry(self, ident, law):
        entry = self.laws.get(ident)
        if entry is None:
            if len(self.laws) >= MAX_LAWS:
                self.totals['untracked'] += 1
                return None
            entry = self.laws[ident] = dict(
                conditions=len(law.get('conditions', [])), matched=0, checked=0, admitted=0,
                rejected=0, duplicate=0, reasons=Counter(), premises=Counter(), instances=Counter(), samples=[])
        return entry

    def matched(self, ident, law):
        self.totals['matched'] += 1
        entry = self._entry(ident, law)
        if entry is not None:
            entry['matched'] += 1

    def duplicate(self, ident, law):
        """The proposed body was already admitted through another route."""
        self.totals['duplicate'] += 1
        entry = self._entry(ident, law)
        if entry is not None:
            entry['duplicate'] += 1

    def checked(self, ident, law, admitted, reason=None, arguments=None):
        self.totals['checked'] += 1
        entry = self._entry(ident, law)
        if admitted:
            self.totals['admitted'] += 1
            if entry is not None:
                entry['checked'] += 1
                entry['admitted'] += 1
            return
        self.totals['rejected'] += 1
        gate, index = classify(reason)
        self.reasons[gate] += 1
        shape = None
        conditions = law.get('conditions', [])
        instance = None
        if gate == 'premise' and index is not None and index < len(conditions):
            shape = premise_shape(conditions[index], law.get('params', {}))
            self.shapes[shape] += 1
            if arguments:
                instance = instantiated_shape(conditions[index], arguments)
                if instance is not None:
                    self.instances[instance] += 1
        if entry is None:
            return
        entry['checked'] += 1
        entry['rejected'] += 1
        entry['reasons'][gate] += 1
        if shape is not None:
            entry['premises'][shape] += 1
        if instance is not None:
            entry['instances'][instance] += 1
        if reason and len(entry['samples']) < MAX_SAMPLES:
            text = str(reason)
            sample = text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + '...'
            if sample not in entry['samples']:
                entry['samples'].append(sample)

    def receipt(self, **extra):
        laws = {}
        ranked = sorted(self.laws.items(), key=lambda item: (
            -item[1]['rejected'], -(item[1]['matched'] - item[1]['checked'] - item[1]['duplicate']), item[0]))
        for ident, entry in ranked[:MAX_REPORTED_LAWS]:
            unchecked = max(0, entry['matched'] - entry['checked'] - entry['duplicate'])
            laws[ident] = dict(conditions=entry['conditions'], matched=entry['matched'],
                               checked=entry['checked'], admitted=entry['admitted'],
                               rejected=entry['rejected'], duplicate=entry['duplicate'],
                               unchecked=unchecked, reasons=dict(entry['reasons']),
                               premises=dict(entry['premises'].most_common(8)),
                               instances=dict(entry['instances'].most_common(8)), samples=entry['samples'])
        matched_laws = len(self.laws)
        totals = dict(self.totals)
        totals['unchecked'] = max(0, totals.get('matched', 0) - totals.get('checked', 0) - totals.get('duplicate', 0))
        return dict(schema=1, semantics=SEMANTICS,
                    scope='Untrusted search observations; no proof, applicability or profitability authority',
                    totals=totals, matched_laws=matched_laws,
                    laws_with_admission=sum(1 for e in self.laws.values() if e['admitted']),
                    laws_only_rejected=sum(1 for e in self.laws.values() if e['rejected'] and not e['admitted']),
                    reasons=dict(self.reasons), premise_shapes=dict(self.shapes.most_common(MAX_SHAPES)),
                    instance_shapes=dict(self.instances.most_common(MAX_SHAPES)),
                    reported_laws=len(laws), laws=laws, **extra)
