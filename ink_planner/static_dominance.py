"""Selection without measurements for plans that never cost asymptotically more.

This is the one exception to evidence-gated selection. A checked maintained
group fold (`ink-maintained-fold-v1`) adds a constant amount of work to each
cell write of its table, and turns every read of its keep from a scan of all
rows into a constant-time read. A checked word extremum view adds one ordered
multiset update to each write, logarithmic like the ordered table write itself,
and turns every read into a logarithmic one. No workload makes such a plan
asymptotically worse than the baseline, so ordinary compilation selects it when
no workload profile, measurement or validated work model has decided. Every
other step kind, and any plan mixing one in, remains evidence-gated.
"""

DOMINANT_KINDS = frozenset({'maintained'})
REASON = 'static dominance: checked maintained folds (no asymptotic cost per write, keep reads without table scans)'


def dominant(package):
    steps = package.get('steps', [])
    return bool(steps) and all(step.get('kind') in DOMINANT_KINDS for step in steps)


def coverage(package):
    """Keeps maintained by the plan."""
    # A maintained step replaces the active package; it does not accumulate views.
    last = next((step for step in reversed(package['steps']) if step.get('kind') == 'maintained'), None)
    return len(last['package'].get('views', [])) if last else 0


def choose(packages, states):
    """Index of the emittable dominant plan maintaining the most keeps (fewest
    steps on ties, then first found), or None."""
    best = None
    for index, (package, state) in enumerate(zip(packages, states)):
        if not dominant(package) or not state.get('capability', {}).get('emittable', True):
            continue
        key = (coverage(package), -len(package['steps']))
        if best is None or key > best[0]:
            best = (key, index)
    return None if best is None else best[1]
