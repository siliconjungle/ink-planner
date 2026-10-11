"""Select database implementations using existing checking/lowering protocols.

No algorithm names, source-pattern recognisers or executable catalogue commands.
The baseline is a supplied database ID, with the same checks as every candidate.
"""
import argparse, hashlib, json, subprocess, tempfile
from pathlib import Path
from ink_knowledge import Store, canonical, identity
from ink_knowledge.store import hash_id, MAX_VIEW_BYTES
from ink_knowledge.implementation_candidates import SEMANTICS
from .route_selection import read
from .representation_selection import rank, phase_workload

TARGETS = ('rust', 'c', 'javascript', 'rust-wasm', 'c-wasm', 'wgpu', 'webgpu')

def measurement_id(candidate, compiler_sha256, target):
    hash_id(candidate); hash_id(compiler_sha256)
    if target not in TARGETS: raise ValueError('unknown emission lane')
    return identity(dict(schema=1, semantics='ink-implementation-measurement-v1',
                         candidate=candidate, compiler_sha256=compiler_sha256,
                         emission_target=target))

def invoke(compiler, args, timeout=30):
    r = subprocess.run([str(compiler), *map(str, args)], capture_output=True,
                       text=True, timeout=timeout)
    if r.returncode: raise ValueError(r.stderr[:1000])
    return r.stdout

def candidate_package(store, candidate):
    # Authenticate the candidate first; shared proof roots are exported once below.
    # Expanding the candidate closure here duplicates the large checked view.
    obj = store.object(candidate)
    if (obj['kind'] != 'implementation' or obj['semantics'] != SEMANTICS
            or set(obj['payload']) != {'package'}):
        raise ValueError('not an implementation package entry')
    package = obj['payload']['package']
    if not isinstance(package, dict): raise ValueError('invalid package')
    if 'knowledge_ref' in package:
        ref=package['knowledge_ref']
        if ('knowledge' in package or not isinstance(ref,dict) or set(ref)!={'schema','roots'}
                or type(ref['schema']) is not int or ref['schema']!=1
                or not isinstance(ref['roots'],list) or any(not isinstance(r,str)for r in ref['roots']) or ref['roots']!=sorted(set(ref['roots']))):
            raise ValueError('invalid shared knowledge reference')
        for root in ref['roots']:hash_id(root)
        if obj['dependencies']!=ref['roots']:raise ValueError('candidate dependency roots mismatch')
        package={k:v for k,v in package.items()if k!='knowledge_ref'}
        package['knowledge']=store.bundle(ref['roots'])
    elif obj['dependencies'] != sorted(set(package['knowledge']['roots'])):
        raise ValueError('candidate dependency roots mismatch')
    return package

def check_package(compiler, source, core, package, staged, timeout):
    staged.write_bytes(canonical(package)+b'\n')
    protocol = package.get('semantics')
    if protocol == 'ink-evidence-v1':
        receipt = json.loads(invoke(compiler, ['check-selection', core, staged], timeout))
        if receipt.get('input_core_sha256') != package.get('input_core_sha256'):
            raise ValueError('selection checker receipt mismatch')
    elif protocol == 'ink-source-representation-v1':
        receipt = json.loads(invoke(compiler, ['check-representation', source, staged], timeout))
        if (receipt.get('status') != 'checked' or
                receipt.get('representation') != package.get('representation') or
                receipt.get('source') != package.get('input_core_sha256')):
            raise ValueError('representation checker receipt mismatch')
    else: raise ValueError('unsupported proof package protocol')

def emission_args(protocol, source, package, target, output):
    """Fixed CLI adapters for proof formats and lanes, never for algorithms."""
    if target not in TARGETS: raise ValueError('unknown emission lane')
    if protocol == 'ink-source-representation-v1':
        return ['emit-representation', source, package, '--target', target, '-o', output]
    if protocol != 'ink-evidence-v1': raise ValueError('unsupported proof package protocol')
    if target == 'rust': args = ['emit-rust', source]
    elif target in ('c', 'c-wasm'): args = ['emit-c-project', source]
    elif target == 'rust-wasm': args = ['build', source, '--target', 'rust']
    else: args = ['build', source, '--target', 'gpu' if target == 'wgpu' else target]
    return [*args, '--selection', package, '-o', output]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--compiler', type=Path, required=True)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--knowledge-root', type=Path, required=True)
    p.add_argument('--emit-target', choices=TARGETS, required=True)
    p.add_argument('-o', '--output', type=Path, required=True)
    p.add_argument('--replay', type=Path, help='recheck a selected package and its pinned database receipt before emission')
    p.add_argument('--baseline', help='database entry ID')
    for name in ('candidates', 'target', 'workload', 'profile'):
        p.add_argument('--'+name, type=Path)
    p.add_argument('--max-candidates', type=int, default=16)
    p.add_argument('--check-timeout-seconds', type=int, default=30)
    p.add_argument('--migration-count', type=int, default=0)
    p.add_argument('--memory-limit', type=int)
    p.add_argument('--zig', type=Path)
    a = p.parse_args(); compiler = a.compiler.resolve(); source_path = a.source.resolve()
    if not 0 <= a.max_candidates <= 32 or not 1 <= a.check_timeout_seconds <= 300:
        raise ValueError('selection budget')
    compiler_hash = hashlib.sha256(compiler.read_bytes()).hexdigest()
    with tempfile.TemporaryDirectory(prefix='ink-implementation-selection-') as temporary:
        scratch = Path(temporary); core = scratch/'core.json'
        source = json.loads(invoke(compiler, ['emit-core', source_path, '-o', core], a.check_timeout_seconds))['core_sha256']
        if a.replay:
            raw = read(a.replay, MAX_VIEW_BYTES)
            receipt = json.loads(read(Path(str(a.replay)+'.selection.json'), 1_000_000))
            if (receipt.get('schema') != 1 or receipt.get('semantics') != 'ink-implementation-selection-v1'
                    or receipt.get('source') != source or receipt.get('compiler_sha256') != compiler_hash
                    or receipt.get('emission_target') != a.emit_target
                    or receipt.get('package_sha256') != hashlib.sha256(raw).hexdigest()):
                raise ValueError('stale or modified implementation selection')
            snapshot = hash_id(receipt['snapshot'])
            store = Store(a.knowledge_root, a.knowledge_root/'store/snapshots'/f'{snapshot}.json')
            if store.snapshot_id != snapshot: raise ValueError('snapshot identity mismatch')
            package = candidate_package(store, receipt['selected'])
            if raw != canonical(package)+b'\n': raise ValueError('selected package differs from pinned candidate')
            staged = scratch/'package.json'
            check_package(compiler, source_path, core, package, staged, a.check_timeout_seconds)
            args = emission_args(package['semantics'], source_path, staged, a.emit_target, a.output)
            if a.zig and a.emit_target in ('wgpu', 'webgpu'): args += ['--zig', a.zig]
            invoke(compiler, args, 600)
            print(json.dumps(dict(status='checked', selected=receipt['selected'], emission_target=a.emit_target)))
            return
        if not a.baseline or any(getattr(a, name) is None for name in ('candidates', 'target', 'workload', 'profile')):
            p.error('selection requires baseline, candidates, target, workload and profile')
        store = Store(a.knowledge_root); hash_id(a.baseline)
        candidates = json.loads(read(a.candidates, 1_000_000))
        if not isinstance(candidates, list) or len(candidates) > 32:
            raise ValueError('candidate index budget')
        for candidate in candidates: hash_id(candidate)
        checked = []; rejected = []
        for i, candidate in enumerate([a.baseline, *candidates[:a.max_candidates]]):
            try:
                package = candidate_package(store, candidate)
                check_package(compiler, source_path, core, package, scratch/f'{i}.json', a.check_timeout_seconds)
                checked.append(dict(candidate=candidate, package=package,
                                    measurement=measurement_id(candidate, compiler_hash, a.emit_target)))
            except (ValueError, OSError, KeyError, subprocess.TimeoutExpired) as e:
                if i == 0: raise ValueError('baseline checking failed: '+str(e)) from e
                rejected.append(dict(candidate=candidate, reason=str(e)))
        target = json.loads(read(a.target, 64_000)); workload = json.loads(read(a.workload, 64_000))
        profile = json.loads(read(a.profile, 64_000))
        if target.get('backend') != a.emit_target: raise ValueError('measurement backend must match emission lane')
        Store.validate_observation(dict(schema=1, program=source, entries=[], plan=checked[0]['measurement'],
            target=target, workload=workload, metrics=dict(complete_call_ns=['0'], peak_bytes=None)))
        migration_phases = {c['measurement']: 'migrate:'+checked[0]['measurement']+':portable:'+c['measurement'] for c in checked[1:]}
        selected, costs = rank(store, source, list(dict.fromkeys(c['measurement'] for c in checked)),
            target, workload, profile, checked[0]['measurement'], a.migration_count, a.memory_limit, migration_phases)
        chosen = next(c for c in checked if c['measurement'] == selected)
        raw = canonical(chosen['package'])+b'\n'
        receipt = dict(schema=1, semantics='ink-implementation-selection-v1', status='checked',
            source=source, selected=chosen['candidate'], baseline=a.baseline, compiler_sha256=compiler_hash,
            emission_target=a.emit_target, snapshot=store.snapshot_id, package_sha256=hashlib.sha256(raw).hexdigest(),
            measurement=selected, baseline_measurement=checked[0]['measurement'], profile=profile, target=target,
            workload=workload, migration_count=a.migration_count, memory_limit=a.memory_limit, costs=costs,
            checked=len(checked), rejected=rejected, remaining=max(0, len(candidates)-a.max_candidates),
            scope='external measured selection of database packages; portable checkpoint/restore migration')
        a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_bytes(raw)
        Path(str(a.output)+'.selection.json').write_text(json.dumps(receipt, indent=2)+'\n')
        print(json.dumps(receipt))

if __name__ == '__main__': main()
