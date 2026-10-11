"""Untrusted offline emitted-work observations; never semantic proof authority.

These are literal executed sites for exact fixtures, not a source-language cost
estimate or an online prediction. Missing required work retains the baseline.
"""
import hashlib
import json
import math
from pathlib import Path

COMPONENTS = ('invocations', 'loop_iterations', 'allocation_events', 'copy_bytes',
              'gpu_dispatches', 'upload_bytes', 'readback_bytes')
BINDINGS = ('original_core', 'selected_core', 'selection', 'artifact', 'fixture',
            'instrumenter', 'instrumented', 'instrumentation', 'runner', 'trace')
BOUNDARY_BINDINGS = ('boundary_runner', 'boundary_extractor', 'boundary_adapter', 'boundary_instrumented', 'boundary_instrumentation', 'boundary_receipt')
MAX_COUNT = 10**15


def read_json(path, limit=64_000_000):
    with Path(path).open('rb') as f:
        raw = f.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('physical metadata byte budget')
    return json.loads(raw)


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(65536), b''):
            digest.update(block)
    return digest.hexdigest()


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


def cohort(value):
    if not isinstance(value, dict) or set(value) != {'target', 'host', 'boundary', 'emitter_sha256', 'entry_abi'}:
        raise ValueError('physical cohort fields')
    if not _digest(value['emitter_sha256']):
        raise ValueError('physical emitter binding')
    for key in ('target', 'boundary', 'entry_abi'):
        if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 256:
            raise ValueError('physical cohort identifier')
    host = value['host']
    if not isinstance(host, dict) or not 1 <= len(host) <= 16 or any(not isinstance(k, str) or not isinstance(v, str) or not 1 <= len(k) <= 128 or not 1 <= len(v) <= 1024 for k, v in host.items()):
        raise ValueError('physical host provenance')
    return value


class PhysicalPrices:
    def __init__(self, data):
        if not isinstance(data, dict) or set(data) != {'schema', 'kind', 'cohort', 'weights', 'training'} or type(data['schema']) is not int or data['schema'] != 1 or data['kind'] != 'ink-target-work-prices-v1':
            raise ValueError('physical price schema')
        self.cohort = cohort(data['cohort'])
        weights = data['weights']
        if not isinstance(weights, dict) or not 1 <= len(weights) <= len(COMPONENTS) or set(weights) - set(COMPONENTS):
            raise ValueError('physical price components')
        if any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 10**9 for v in weights.values()):
            raise ValueError('physical prices must be finite nonnegative ns')
        training = data['training']
        if not isinstance(training, dict) or set(training) != {'protocol_sha256', 'observations_sha256', 'scope'} or not _digest(training['protocol_sha256']) or not _digest(training['observations_sha256']) or not isinstance(training['scope'], str) or len(training['scope']) > 4096:
            raise ValueError('physical training provenance')
        self.weights = weights
        self.data = data

    def score(self, observation):
        if observation['cohort'] != self.cohort:
            return None, 'target/host/boundary/emitter/ABI cohort mismatch'
        missing = [k for k in self.weights if not observation['known'][k]]
        if missing:
            return None, 'unknown required emitted work: ' + ','.join(sorted(missing))
        return sum(self.weights[k] * observation['components'][k] for k in self.weights), None


def load_observation(path, expected_bindings=None):
    """Reauthenticate source/trace bytes, never grant equality from these hashes."""
    path = Path(path)
    data = read_json(path)
    fields = {'schema', 'kind', 'cohort', 'bindings', 'entry', 'fixture_id', 'components', 'known', 'unknown', 'scope'}
    if not isinstance(data, dict) or set(data) != fields or type(data['schema']) is not int or data['schema'] != 1 or data['kind'] != 'ink-target-work-v1':
        raise ValueError('physical observation schema')
    cohort(data['cohort'])
    bindings = data['bindings']
    if not isinstance(bindings, dict) or not set(BINDINGS).issubset(bindings) or set(bindings)-set(BINDINGS)-set(BOUNDARY_BINDINGS)-{'parser'}:
        raise ValueError('physical exact-byte bindings')
    resolved = {}
    for name, record in bindings.items():
        if not isinstance(record, dict) or set(record) != {'path', 'sha256'} or not isinstance(record['path'], str) or len(record['path']) > 4096 or not _digest(record['sha256']):
            raise ValueError('physical binding format')
        bound = (path.parent / record['path']).resolve()
        if file_hash(bound) != record['sha256']:
            raise ValueError('physical binding changed: ' + name)
        resolved[name] = bound
    for name, expected in (expected_bindings or {}).items():
        if name not in bindings or bindings[name]['sha256'] != file_hash(expected):
            raise ValueError('candidate physical binding mismatch: ' + name)
    if not isinstance(data['entry'], str) or not 1 <= len(data['entry']) <= 256 or not isinstance(data['fixture_id'], str) or not 1 <= len(data['fixture_id']) <= 512:
        raise ValueError('physical entry/fixture identifier')
    if not isinstance(data['components'], dict) or set(data['components']) != set(COMPONENTS) or any(type(v) is not int or not 0 <= v <= MAX_COUNT for v in data['components'].values()):
        raise ValueError('physical component counts')
    if not isinstance(data['known'], dict) or set(data['known']) != set(COMPONENTS) or any(type(v) is not bool for v in data['known'].values()):
        raise ValueError('physical completeness flags')
    if not isinstance(data['unknown'], list) or len(data['unknown']) > 8192 or not isinstance(data['scope'], str) or len(data['scope']) > 8192:
        raise ValueError('physical scope budget')
    # Cross-check the actual untimed replay bytes, rather than accepting a
    # sidecar's invented vector or fixture identity in isolation.
    trace = read_json(resolved['trace'])
    if trace.get('status') != 'untimed-complete-output-replayed' or trace.get('artifact_sha256') != bindings['artifact']['sha256'] or trace.get('fixture_sha256') != bindings['fixture']['sha256'] or trace.get('instrumentation_sha256') != bindings['instrumentation']['sha256'] or trace.get('instrumented_sha256') != bindings['instrumented']['sha256'] or trace.get('runner_sha256') != bindings['runner']['sha256']:
        raise ValueError('physical replay bindings')
    rows = trace.get('rows')
    if not isinstance(rows, list) or len(rows) > 4096:
        raise ValueError('physical trace budget')
    matches = [r for r in rows if r.get('id') == data['fixture_id'] and r.get('entry') == data['entry'] and r.get('boundary') == data['cohort']['boundary']]
    if len(matches) != 1 or any(matches[0].get(k) != data[k] for k in ('components', 'known', 'unknown')):
        raise ValueError('physical trace vector mismatch')
    instrumentation = read_json(resolved['instrumentation'])
    if instrumentation.get('artifact_sha256') != bindings['artifact']['sha256'] or instrumentation.get('instrumented_sha256') != bindings['instrumented']['sha256'] or instrumentation.get('instrumenter_sha256') != bindings['instrumenter']['sha256']:
        raise ValueError('physical instrumenter binding')
    if 'parser' in bindings and instrumentation.get('parser_sha256') != bindings['parser']['sha256']:
        raise ValueError('physical parser binding')
    if 'boundary' in trace:
        if not set(BOUNDARY_BINDINGS).issubset(bindings): raise ValueError('physical boundary bindings missing')
        for name, key in [('boundary_runner','runner_sha256'),('boundary_extractor','extractor_sha256'),('boundary_adapter','adapter_sha256'),('boundary_instrumented','instrumented_sha256'),('boundary_instrumentation','instrumentation_sha256'),('boundary_receipt','extraction_sha256')]:
            if trace['boundary'].get(key) != bindings[name]['sha256']: raise ValueError('physical boundary binding: '+name)
        boundary = read_json(resolved['boundary_receipt'])
        if boundary.get('runner_sha256') != bindings['boundary_runner']['sha256'] or boundary.get('producer_sha256') != bindings['boundary_extractor']['sha256'] or boundary.get('adapter_sha256') != bindings['boundary_adapter']['sha256']: raise ValueError('physical boundary extraction mismatch')
        boundary_instrumentation = read_json(resolved['boundary_instrumentation'])
        if boundary_instrumentation.get('artifact_sha256') != bindings['boundary_adapter']['sha256'] or boundary_instrumentation.get('instrumented_sha256') != bindings['boundary_instrumented']['sha256'] or boundary_instrumentation.get('instrumenter_sha256') != bindings['instrumenter']['sha256']: raise ValueError('physical boundary instrumentation mismatch')
    return data


def rank_observations(observations, baseline, model):
    """Exact comparable offline fixture only; incomplete baseline cannot switch."""
    if baseline not in observations:
        raise ValueError('physical baseline missing')
    original = observations[baseline]
    scores, excluded = {}, {}
    fixture = original['bindings']['fixture']['sha256']
    for ident, observation in observations.items():
        if observation['entry'] != original['entry'] or observation['fixture_id'] != original['fixture_id'] or observation['bindings']['fixture']['sha256'] != fixture or observation['bindings']['original_core']['sha256'] != original['bindings']['original_core']['sha256']:
            excluded[ident] = 'source/entry/exact fixture mismatch'; continue
        price, reason = model.score(observation)
        if price is None: excluded[ident] = reason
        else: scores[ident] = price
    selected = baseline
    if baseline in scores:
        for ident in observations:
            if ident in scores and scores[ident] < scores[selected]: selected = ident
    return selected, dict(predicted_ns=scores, excluded=excluded, baseline_comparable=baseline in scores,
                         scope='Opt-in offline exact-fixture hypothesis; acquisition/setup excluded; no online prediction, profitability proof or physical exact timing claim')
