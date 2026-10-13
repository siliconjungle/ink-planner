"""Selection by a calibrated work model, without target timings.

Two deterministic, machine-independent work features are acquired per
candidate on supplied calls:

- `reference_fuel`: the reference interpreter's fuel for the candidate's
  checked selected module (`ink plan-work`). It sees semantic work only and
  prices constructs by interpreter cost, not target cost.
- `target_iterations`: loop iterations, function entries and sort work counted
  by instrumenting the candidate's emitted JavaScript (`count_work.mjs`). The
  instrumentation is a planner-side text transform of an artifact that is never
  shipped; it sees what the target code actually loops over.

A model names one feature and maps a candidate's work ratio to a predicted target speedup through a
slope and a residual spread fitted on paired development timings
(`bench/planning-corpus/work_model.py`). The planner uses a model only if it
carries a passed held-out validation record, and only when no comparable
timings exist. Work grants no rewrite authority: every candidate is already a
checked plan, and the selected one is replayed before emission as usual.
"""
import hashlib
import json
import math
import re
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

from ink_knowledge import canonical, identity

from .offline_measurements import read_workload

SEMANTICS = 'ink-work-model-v1'
WORK = 'ink-reference-work-v1'
FEATURES = ('reference_fuel', 'target_iterations')
MAX_MODULES = 256
RUNNER = Path(__file__).with_name('count_work.mjs')
LOOP = re.compile(r'(for\(let [^;{]*;[^;{]*;[^{]*\)\{|while\(true\)\{|function __fn\d+\([^)]*\)\{)')


def instrument(javascript):
    """Count loop iterations, function entries and sort work in an artifact."""
    counted = LOOP.sub(lambda m: m.group(1) + '__w++;', javascript)
    counted = counted.replace('const __sort = xs => Array.from(xs)',
                              'const __sort = xs => (__w+=xs.length*Math.ceil(Math.log2(xs.length+1)),Array.from(xs))')
    return 'let __w=0;export function __work(){const w=__w;__w=0;return w;}\n' + counted


def call_weights(spec):
    """Match timing's mean per phase before applying lifecycle frequencies."""
    return [Fraction(spec['profile'][phase], len(rows))
            for phase, rows in spec['phases'].items() for _ in rows]


def target_iterations(planner, packages, measurements, calls):
    node = shutil.which('node')
    if not node or planner.options.target != 'javascript':
        return {}, [dict(reason='target iteration counts need the JavaScript target and Node.js')]
    artifacts = []
    for package, measurement in zip(packages, measurements):
        staged = planner.directory / (measurement + '.count-plan.json')
        staged.write_bytes(canonical(package) + b'\n')
        artifact = planner.directory / (measurement + '.count.mjs')
        try:
            planner.invoke('emit-plan', planner.original, staged, '--core', '--target', 'javascript', '-o', artifact)
        except (ValueError, OSError, subprocess.TimeoutExpired):
            continue
        artifact.write_text(instrument(artifact.read_text()))
        artifacts.append(dict(measurement=measurement, path=str(artifact)))
    request = planner.directory / 'count-work.json'
    request.write_text(json.dumps(dict(artifacts=artifacts, calls=calls), allow_nan=False))
    result = subprocess.run([node, str(RUNNER), str(request)], capture_output=True, text=True,
                            timeout=planner.options.measure_timeout)
    if result.returncode:
        return {}, [dict(reason='target iteration counting failed: ' + result.stderr[-500:])]
    report = json.loads(result.stdout)
    return {r['measurement']: r['iterations'] for r in report['rows']}, report['rejected']


def acquire(planner, packages, states, measurements, path):
    """Reference work of every candidate on the workload's calls.

    Candidates sharing a selected module share one evaluation.
    """
    spec, digest = read_workload(path)
    calls, weights = [], call_weights(spec)
    for rows in spec['phases'].values():
        for row in rows:
            calls.append(dict(function=row['function'], args=row['args']))
    request = planner.directory / 'work-calls.json'
    request.write_text(json.dumps(calls, allow_nan=False))
    by_module, rows, rejected = {}, [], []
    for package, state, measurement in zip(packages, states, measurements):
        module = state.get('source')
        if module not in by_module:
            if len(by_module) >= MAX_MODULES:
                rejected.append(dict(measurement=measurement, reason='reference-work module budget'))
                continue
            staged = planner.directory / (measurement + '.work-plan.json')
            staged.write_bytes(canonical(package) + b'\n')
            try:
                out = planner.invoke('plan-work', planner.original, staged, request, '--core')
                if not isinstance(out, dict) or out.get('semantics') != WORK or len(out['fuel']) != len(calls):
                    raise ValueError('reference work response binding mismatch')
                by_module[module] = out
            except (ValueError, OSError, subprocess.TimeoutExpired) as error:
                by_module[module] = None
                rejected.append(dict(measurement=measurement, reason=str(error)[-500:]))
        out = by_module.get(module)
        if out is None:
            continue
        total = sum(int(f) * w for f, w in zip(out['fuel'], weights))
        rows.append(dict(measurement=measurement, selected=out['selected'], fuel=out['fuel'], weighted=str(total),
                         steps=[s['kind'] for s in package['steps']]))
    if not rows or rows[0]['measurement'] != measurements[0]:
        raise ValueError('baseline reference work unavailable')
    # Checked plans preserve results; a mismatch is a checker or harness fault,
    # so such a candidate is never given a work estimate.
    expected = by_module[states[0].get('source')]['values']
    differs = {m for m, out in by_module.items() if out is not None and out['values'] != expected}
    if differs:
        rejected += [dict(measurement=r['measurement'], reason='reference outputs differ from the baseline')
                     for r in rows if states[measurements.index(r['measurement'])].get('source') in differs]
        rows = [r for r in rows if states[measurements.index(r['measurement'])].get('source') not in differs]
    counts, count_rejections = target_iterations(planner, packages, measurements, calls)
    rejected += count_rejections
    for row in rows:
        iterations = counts.get(row['measurement'])
        if iterations is not None:
            row['iterations'] = iterations
            row['weighted_iterations'] = str(sum(int(c) * w for c, w in zip(iterations, weights)))
    return dict(schema=1, semantics=WORK, workload_sha256=digest, calls=len(calls), modules=len(by_module),
                rows=rows, rejected=rejected,
                scope='Reference interpreter fuel of checked selected modules; deterministic, machine independent, semantic work only')


def validate_model(model):
    if not isinstance(model, dict) or model.get('schema') != 1 or model.get('semantics') != SEMANTICS:
        raise ValueError('work model schema')
    for key in ('slope', 'sigma', 'z', 'min_gain'):
        if not isinstance(model.get(key), (int, float)) or not math.isfinite(model[key]) or model[key] < 0:
            raise ValueError(f'work model {key}')
    if not 0 < model['slope'] <= 4:
        raise ValueError('work model slope must be positive and bounded')
    if model.get('feature', 'reference_fuel') not in FEATURES:
        raise ValueError('work model feature')
    validation = model.get('validation')
    if not isinstance(validation, dict) or validation.get('status') != 'validated':
        raise ValueError('work model has no passed held-out validation')
    return model


def load(path):
    raw = path.read_bytes()
    if len(raw) > 1_000_000:
        raise ValueError('work model byte budget')
    return validate_model(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def predict(model, base, candidate):
    """(predicted speedup, conservative lower bound) for a work ratio."""
    if candidate <= 0 or base <= 0:
        return 1.0, 1.0
    log = model['slope'] * math.log(base / candidate)
    return math.exp(log), math.exp(log - model['z'] * model['sigma'])


def select(model, digest, work, measurements, eligible, baseline, *, memory_limit=None, migration_count=0):
    """Choose by predicted speedup; keep the baseline unless the bound clears the margin."""
    if memory_limit is not None or migration_count:
        return baseline, dict(reason='work model lacks memory or migration lifecycle evidence; baseline retained',
                              model_sha256=digest, memory_limit=memory_limit, migration_count=migration_count)
    field = 'weighted_iterations' if model.get('feature') == 'target_iterations' else 'weighted'
    rows = {r['measurement']: r for r in work['rows'] if field in r}
    if baseline not in rows:
        return baseline, dict(reason='baseline work unavailable for the model feature; baseline retained', model_sha256=digest)
    base = Fraction(rows[baseline][field])
    ranked = []
    for index, m in enumerate(measurements):
        if m not in eligible or m not in rows or m == baseline:
            continue
        speedup, lower = predict(model, base, Fraction(rows[m][field]))
        ranked.append((speedup, lower, -len(rows[m]['steps']), -index, m))
    ranked.sort(reverse=True)
    considered = [dict(measurement=m, predicted_speedup=round(s, 4), lower_bound=round(l, 4)) for s, l, _, _, m in ranked[:16]]
    for speedup, lower, _, _, m in ranked:
        if lower > 1 + model['min_gain']:
            return m, dict(reason='calibrated reference-work model', model_sha256=digest,
                           predicted_speedup=round(speedup, 4), lower_bound=round(lower, 4), considered=considered,
                           scope='Predicted from reference work with a held-out-validated calibration; no target timing of this program')
    return baseline, dict(reason='work model predicts no candidate clears its margin; baseline retained', model_sha256=digest,
                          considered=considered)
