"""Explain recorded planning gates without granting rewrite or selection authority.

Receipts are observations, not proofs. An unfinished search is never described
as evidence that no applicable optimisation exists. Expected plan identities
may come from a separately checked capability experiment; they are diagnostics
only and are never passed to the planner as selections.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
from ink_knowledge import identity


def exclusion_gate(reason):
    text = str(reason).lower()
    if 'memory' in text:
        return 'memory_evidence'
    if any(word in text for word in ('uncertainty', 'minimum', 'gain guard', 'no measured improvement')):
        return 'profitability'
    if any(word in text for word in ('missing', 'unmeasured', 'comparable', 'phase')):
        return 'measurement_evidence'
    return 'selection_other'


def semantic_observations(notes, laws_limit=64, shapes_limit=32):
    """Merge per-state near-miss receipts from the semantic recipe.

    Counts may revisit the same law at several admitted states. They describe
    recorded proposals, not every applicable or latent rewrite.
    """
    totals = Counter(); reasons = Counter(); shapes = Counter(); instances = Counter(); laws = {}
    receipts = 0
    for note in notes:
        value = note.get('diagnostics') if note.get('status') == 'diagnostic' else None
        if not isinstance(value, dict) or value.get('semantics') != 'ink-search-near-misses-v1':
            continue
        receipts += 1
        totals.update({k: v for k, v in (value.get('totals') or {}).items() if isinstance(v, int)})
        reasons.update({k: v for k, v in (value.get('reasons') or {}).items() if isinstance(v, int)})
        shapes.update({k: v for k, v in (value.get('premise_shapes') or {}).items() if isinstance(v, int)})
        instances.update({k: v for k, v in (value.get('instance_shapes') or {}).items() if isinstance(v, int)})
        for ident, entry in (value.get('laws') or {}).items():
            merged = laws.setdefault(ident, dict(matched=0, checked=0, admitted=0, rejected=0, unchecked=0,
                                                 reasons=Counter(), premises=Counter(), samples=[]))
            for field in ('matched', 'checked', 'admitted', 'rejected', 'unchecked'):
                merged[field] += int(entry.get(field, 0))
            merged['reasons'].update(entry.get('reasons') or {})
            merged['premises'].update(entry.get('premises') or {})
            for sample in entry.get('samples') or []:
                if len(merged['samples']) < 2 and sample not in merged['samples']:
                    merged['samples'].append(sample)
    ranked = sorted(laws.items(), key=lambda item: (-item[1]['rejected'], -item[1]['unchecked'], item[0]))
    return dict(receipts=receipts, totals=dict(totals), reasons=dict(reasons),
                premise_shapes=dict(shapes.most_common(shapes_limit)),
                instance_shapes=dict(instances.most_common(shapes_limit)),
                laws_matched=len(laws), laws_admitted=sum(1 for e in laws.values() if e['admitted']),
                laws_only_rejected=sum(1 for e in laws.values() if e['rejected'] and not e['admitted']),
                laws={ident: dict(entry, reasons=dict(entry['reasons']), premises=dict(entry['premises'].most_common(4)))
                      for ident, entry in ranked[:laws_limit]},
                scope='Untrusted search observations merged across admitted states; not proof or applicability claims')


def recipe_seconds(notes):
    """Wall time each recipe reported, summed across expanded states."""
    seconds = Counter()
    for note in notes:
        try:
            seconds[note.get('recipe', 'unknown')] += float(note.get('elapsed_seconds') or 0)
        except (TypeError, ValueError):
            continue
    return {name: round(value, 3) for name, value in seconds.most_common()}


def diagnose(receipt, expected=()):
    if not isinstance(receipt, dict):
        raise ValueError('planning receipt must be an object')
    candidates = receipt.get('candidates', [])
    selection = receipt.get('selection') or {}
    acquisition = receipt.get('measurement_acquisition') or {}
    report = acquisition.get('acquisition') or {}
    guard = selection.get('uncertainty_guard') or {}
    measured = Counter(row['measurement'] for row in report.get('rows', []) if 'measurement' in row)
    evidence = selection.get('observations') or {}
    failures = {r['measurement']: str(r.get('reason', 'measurement rejected'))
                for r in acquisition.get('rejected', []) if 'measurement' in r}
    excluded = dict(selection.get('excluded') or {})
    excluded.update(guard.get('excluded') or {})
    shortlisted = set((acquisition.get('shortlist') or {}).get('measured_indices', []))
    has_shortlist = bool(acquisition.get('shortlist'))
    selected = receipt.get('selected')
    baseline = candidates[0].get('plan') if candidates else None
    cached = (receipt.get('cache') or {}).get('hit') is True
    # Compact cache receipts intentionally omit historical candidate packets.
    # The ordinary initial packet still has a deterministic source-bound identity.
    if baseline is None and receipt.get('semantics') == 'ink-compiled-plan-v1' and receipt.get('source'):
        baseline = identity(dict(schema=1,semantics='ink-compiled-plan-v1',
                                 input_core_sha256=receipt['source'],steps=[]))
    outcomes = []
    for index, candidate in enumerate(candidates):
        measurement = candidate.get('measurement')
        has_measurement = bool(measured[measurement] or evidence.get(measurement)
                               or measurement in (selection.get('total_ns') or {})
                               or measurement in (guard.get('estimates') or {}))
        capability = candidate.get('capability') or {}
        reason = None
        if candidate.get('plan') == selected:
            gate = 'selected_baseline' if index == 0 else 'selected_alternative'
            reason = selection.get('reason') or guard.get('reason')
        elif capability.get('emittable') is False:
            gate = 'backend_capability'
            reason = capability.get('reason') or capability.get('reasons') or 'target cannot emit this admitted state'
        elif measurement in failures:
            gate, reason = 'measurement_failed', failures[measurement]
        elif measurement in excluded:
            reason = str(excluded[measurement])
            gate = exclusion_gate(reason)
        elif has_shortlist and index not in shortlisted:
            gate = 'measurement_budget'
            reason = (acquisition['shortlist'].get('unmeasured_reason')
                      or 'outside the bounded measurement shortlist')
        elif has_measurement:
            gate = 'measured_not_selected'
            reason = 'comparable evidence recorded; another implementation selected'
        else:
            gate = 'measurement_evidence'
            reason = selection.get('reason') or (receipt.get('automatic_probe') or {}).get('reason') or 'no comparable measurement recorded'
        outcomes.append(dict(index=index, candidate=candidate.get('candidate'), plan=candidate.get('plan'),
                             measurement=measurement, gate=gate, reason=reason,
                             measured=has_measurement, sample_rows=measured[measurement]))
    recipes = {}
    for row in receipt.get('recipes', []):
        name = row.get('recipe', 'unknown')
        summary = recipes.setdefault(name, dict(statuses=Counter(), results=Counter(), reasons=Counter()))
        summary['statuses'][row.get('status', 'unknown')] += 1
        summary['results'].update(row.get('counts') or {})
        if row.get('reason'):
            summary['reasons'][str(row['reason'])] += 1
    recipes = {name: {k: dict(v) for k, v in summary.items()} for name, summary in sorted(recipes.items())}
    recorded = {r['plan']: r for r in outcomes}
    opportunities = []
    for item in expected:
        plan = item['plan']
        if not isinstance(plan, str) or len(plan) != 64 or any(c not in '0123456789abcdef' for c in plan):
            raise ValueError('expected plan identity must be a SHA-256 digest')
        outcome = recorded.get(plan)
        opportunities.append(dict(label=item.get('label'), plan=plan, reached=outcome is not None,
                                  gate=outcome['gate'] if outcome else 'not_reached',
                                  reason=outcome['reason'] if outcome else
                                  'expected exact plan was not recorded; this does not exclude equivalent proof chains'))
    stop = receipt.get('stop_reason', 'unknown')
    notes = receipt.get('recipes', [])
    semantic = semantic_observations(notes)
    edge_kinds = Counter(kind for edge in receipt.get('edges', []) for kind in edge.get('kinds', []))
    gates = Counter(r['gate'] for r in outcomes)
    funnel = dict(
        semantic_matched=semantic['totals'].get('matched', 0),
        semantic_checked=semantic['totals'].get('checked', 0),
        semantic_admitted=semantic['totals'].get('admitted', 0),
        semantic_rejected=semantic['totals'].get('rejected', 0),
        semantic_unchecked=semantic['totals'].get('unchecked', 0),
        plan_attempts=receipt.get('attempts'), plan_admitted_states=receipt.get('admitted_states'),
        plan_rejected=len(receipt.get('rejected', [])), admitted_edges=dict(edge_kinds),
        candidates=len(candidates), selected_alternative=gates.get('selected_alternative', 0) > 0,
        last_gate=('selected_alternative' if gates.get('selected_alternative') else
                   max((g for g in gates if g != 'selected_baseline'), key=lambda g: gates[g], default='no_alternative')),
        scope='Recorded stage counts for this compilation only')
    return dict(schema=1, semantics='ink-planning-diagnostics-v1',
                scope='Recorded gates only; no proof authority, no exhaustive applicability claim, no profitability claim on unseen inputs',
                provenance='historical cached search and costs; selected replay reported separately' if cached else 'recorded planning invocation',
                fresh_work=dict(search=not cached and 'attempts' in receipt, measurements=bool(acquisition) and not cached,
                                selected_replay_reported=receipt.get('status')=='checked'),
                binding={k: receipt.get(k) for k in ('source', 'compiler_sha256', 'producer_sha256', 'source_snapshot', 'selected')},
                search=dict(stop_reason=stop, exhausted=stop not in ('fixed-point', 'unknown'),
                            attempts=receipt.get('attempts'), admitted_states=receipt.get('admitted_states'),
                            rejected_proofs=len(receipt['rejected']) if 'rejected' in receipt else None,
                            checked_edges=len(receipt['edges']) if 'edges' in receipt else None,
                            remaining=receipt.get('remaining'), elapsed_seconds=receipt.get('elapsed_seconds'),
                            recipes=recipes),
                delivery=dict(baseline_retained=selected == baseline if baseline is not None else None,
                              reason=selection.get('reason') or guard.get('reason'),
                              admitted_candidates=len(candidates) if 'candidates' in receipt else None, gates=dict(Counter(r['gate'] for r in outcomes)),
                              cache=receipt.get('cache'), probe=receipt.get('automatic_probe')),
                candidates=outcomes, expected_opportunities=opportunities,
                rejected_proofs=receipt.get('rejected', []), funnel=funnel, semantic=semantic,
                recipe_seconds=recipe_seconds(notes))


def read(path):
    with path.open('rb') as stream:
        raw = stream.read(128_000_001)
    if len(raw) > 128_000_000:
        raise ValueError('planning diagnostics input byte budget')
    return json.loads(raw)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', type=Path)
    parser.add_argument('--expect', type=Path, help='diagnostic exact plan identities from an independent capability experiment')
    parser.add_argument('-o', '--output', type=Path)
    args = parser.parse_args()
    result = diagnose(read(args.receipt), read(args.expect) if args.expect else ())
    raw = json.dumps(result, indent=2, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(raw)
    else:
        print(raw, end='')


if __name__ == '__main__':
    main()
