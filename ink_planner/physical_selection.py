"""Opt-in offline ranking of already checked/emitted DB alternatives.

Admission always replays the ordinary semantic checker using the explicitly
pinned snapshot. Target traces/prices can rank equality-admitted alternatives;
they cannot supply an application condition or prove native emission correct.
"""
import argparse
import json
import subprocess
import tempfile
from pathlib import Path
from .physical_features import PhysicalPrices, file_hash, load_observation, rank_observations, read_json
from .semantic_search import selection_roots


def checked_candidate(compiler, core, snapshot_store, entry, directory, timeout=30):
    """Bind the admitted selected core and literal JS emission to archived bytes.

    Other target acquisitions may supply sidecars, but this first selector only
    switches JavaScript whose exact emitted bytes are independently reproduced.
    """
    paths = {k: Path(entry[k]).resolve() for k in ('selection', 'selected_core', 'artifact', 'features')}
    package = read_json(paths['selection'], 16_000_000)
    if not isinstance(package, dict) or set(package) != {'schema', 'semantics', 'input_core_sha256', 'knowledge', 'applications'} or not isinstance(package['applications'], list) or len(package['applications']) > 4096:
        raise ValueError('physical candidate selection schema')
    # Fetch the exact selected closure from the supplied immutable snapshot;
    # neither sidecar hash nor embedded catalogue bytes grant admission.
    closure = snapshot_store.bundle(selection_roots(package['applications']))
    if package['knowledge'] != closure:
        raise ValueError('embedded selected proof closure differs from pinned snapshot')
    package['knowledge'] = closure
    replay = directory / 'snapshot.selection.json'
    replay.write_text(json.dumps(package, separators=(',', ':')) + '\n')
    def run(args):
        result = subprocess.run([str(compiler), *map(str, args)], capture_output=True, text=True, timeout=timeout)
        if result.returncode: raise ValueError('ordinary selection/emission replay failed: ' + result.stderr[:1000])
        return result
    admission = json.loads(run(['check-selection', core, replay]).stdout)
    selected_core = directory / 'replayed.core.json'
    run(['emit-core', core, '--core', '--selection', replay, '-o', selected_core])
    if selected_core.read_bytes() != paths['selected_core'].read_bytes():
        raise ValueError('selected core bytes do not match checked snapshot replay')
    receipt = dict(admission=admission, snapshot=snapshot_store.snapshot_id,
                   selection_sha256=file_hash(paths['selection']), snapshot_replay_sha256=file_hash(replay),
                   selected_core_sha256=file_hash(selected_core))
    try:
        observation = load_observation(paths['features'], dict(original_core=core, selection=paths['selection'], selected_core=paths['selected_core'], artifact=paths['artifact']))
        if observation['cohort']['emitter_sha256'] != file_hash(compiler):
            raise ValueError('emitter cohort differs from actual replay compiler')
        if observation['cohort']['target'] != 'javascript':
            raise ValueError('target artifact replay not implemented; retain baseline')
        artifact = directory / 'replayed.mjs'
        run(['build', selected_core, '--core', '--target', 'javascript', '-o', artifact])
        if artifact.read_bytes() != paths['artifact'].read_bytes():
            raise ValueError('target artifact bytes do not match checked selected-core emission')
        receipt['artifact_sha256'] = file_hash(artifact)
        return observation, receipt
    except (ValueError, OSError, KeyError) as error:
        receipt['physical_unavailable'] = str(error)
        return None, receipt


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('compiler', 'core', 'snapshot', 'knowledge-root', 'candidates', 'model', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--max-candidates', type=int, default=16)
    p.add_argument('--check-timeout-seconds', type=int, default=30)
    a = p.parse_args()
    if not 1 <= a.max_candidates <= 32 or not 1 <= a.check_timeout_seconds <= 60:
        raise ValueError('physical selection budgets')
    entries = read_json(a.candidates, 1_000_000)
    if not isinstance(entries, list) or not 1 <= len(entries) <= 32 or any(not isinstance(e, dict) or set(e) != {'id', 'selection', 'selected_core', 'artifact', 'features'} or any(not isinstance(v, str) or not 1 <= len(v) <= 4096 for v in e.values()) for e in entries):
        raise ValueError('physical candidate index')
    if len({e['id'] for e in entries}) != len(entries): raise ValueError('duplicate physical candidate id')
    entries = [{k: v if k == 'id' else str((a.candidates.parent / v).resolve()) for k, v in e.items()} for e in entries]
    from ink_knowledge import Store
    store = Store(a.knowledge_root, snapshot=a.snapshot)
    model = PhysicalPrices(read_json(a.model, 64000))
    compiler = a.compiler.resolve(); original_hash = file_hash(compiler)
    observations, receipts, rejected = {}, {}, {}
    with tempfile.TemporaryDirectory(prefix='ink-physical-selection-') as temporary:
        for i, entry in enumerate(entries[:a.max_candidates]):
            directory = Path(temporary) / str(i); directory.mkdir()
            try:
                observation, receipts[entry['id']] = checked_candidate(compiler, a.core.resolve(), store, entry, directory, a.check_timeout_seconds)
                if observation is not None: observations[entry['id']] = observation
            except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as error:
                if i == 0: raise ValueError('baseline physical replay failed: ' + str(error)) from error
                rejected[entry['id']] = str(error)
        if entries[0]['id'] in observations:
            selected, costs = rank_observations(observations, entries[0]['id'], model)
        else:
            selected, costs = entries[0]['id'], dict(predicted_ns={}, excluded={e['id']: receipts.get(e['id'], {}).get('physical_unavailable', 'missing comparable baseline work') for e in entries}, baseline_comparable=False, scope='Missing physical baseline retains ordinary checked baseline; no online prediction')
    if file_hash(compiler) != original_hash: raise ValueError('physical replay compiler changed')
    chosen = next(e for e in entries if e['id'] == selected)
    # Return the ordinary package, requiring ordinary replay wherever consumed.
    a.output.write_bytes(Path(chosen['selection']).read_bytes())
    receipt = dict(schema=1, selected=selected, baseline=entries[0]['id'], model_sha256=file_hash(a.model),
                   compiler_sha256=original_hash, snapshot=store.snapshot_id, checked=receipts,
                   rejected=rejected, remaining=max(0, len(entries)-a.max_candidates), costs=costs,
                   scope='Explicit opt-in offline exact-fixture target-work ranking; acquisition/setup excluded; hashes/models/traces are not semantic or lowering authority; ordinary checker replay remains required; default planner unchanged')
    Path(str(a.output)+'.physical.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))

if __name__ == '__main__': main()
