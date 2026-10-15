"""Actual state acquisition checks snapshots, events, rollback and context.

Timing samples establish functioning acquisition only, not a speed improvement.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace

from ink_knowledge.implementation_candidates import publish_candidates
from ink_planner.compiled_planning import Planner, code_fingerprint
from ink_planner.implementation_selection import measurement_id
from ink_planner.offline_measurements import acquire, target_context

compiler = Path(sys.argv[1]).resolve()
source_text = '''module ContextState;
enum Error { Failed, }
state Rows:Table<u64,u64> = Table.empty();
event seen:u64;
change put(key:u64,value:u64)->Result<Unit,Error> writes(Rows) {
 if Rows.contains(key) { Rows.replace(key,value); } else { Rows.insert(key,value); }
 return Ok(());
}
change replace_zero(value:u64,fail:Bool)->Result<Unit,Error> writes(Rows) emits(seen) {
 if Rows.contains(0) { Rows.replace(0,value); } else { return Err(Error.Failed); }
 emit seen(value); if fail { return Err(Error.Failed); } return Ok(());
}
'''
with tempfile.TemporaryDirectory(prefix='ink-context-state-') as temporary:
    root = Path(temporary); source = root/'source.ink'; source.write_text(source_text)
    original = root/'source.core.json'
    subprocess.run([str(compiler), 'emit-core', str(source), '-o', str(original)], check=True, capture_output=True)
    source_id = json.loads(subprocess.check_output([str(compiler), 'check-core', str(original)], text=True))['core_sha256']
    package = dict(schema=1, semantics='ink-compiled-plan-v1', input_core_sha256=source_id, steps=[])
    store, candidates = publish_candidates(root/'knowledge', [package])
    planner = SimpleNamespace(directory=root, original=original, compiler=compiler,
        compiler_hash=hashlib.sha256(compiler.read_bytes()).hexdigest(), code_hash=code_fingerprint(), root=store.root,
        options=SimpleNamespace(target='javascript', memory_bytes=64000000, measure_timeout=120,
                                check_timeout=30, measure_processes=1))
    planner.invoke = lambda *args, **kwargs: Planner.invoke(planner, *args, **kwargs)
    phases = dict(module=dict(kind='module_load'), initialise=dict(kind='initialise'),
        update=dict(kind='history', calls=[dict(call='replace_zero', args=[2**64-1, True]),
                                          dict(call='replace_zero', args=[9, False])]),
        checkpoint=dict(kind='checkpoint'), restore=dict(kind='restore'))
    spec = dict(schema=2, setup=[dict(call='put', args=[0, 3])], phases=phases,
        profile={name:1 for name in phases}, workload=dict(shape={}, distribution='state-context-regression',
                                                         concurrency=1, residency='restored'))
    workload = root/'workload.json'; workload.write_text(json.dumps(spec))
    measurement = measurement_id(candidates[0], planner.compiler_hash, 'javascript')
    target, _, profile, receipt = acquire(planner, store, [package], [{}], [measurement], workload)
    assert target == target_context(planner, state=True)
    assert receipt['execution_context']['arguments'] == ['--expose-gc', '--max-old-space-size=64']
    assert receipt['execution_context']['describe_arguments'] == []
    assert receipt['validated_measurements'] == [measurement] and len(receipt['observations']) == len(phases)
    assert receipt['acquisition']['pairing'] == 'interleaved-state-lifecycle-v1'
    assert {row['phase'] for row in receipt['acquisition']['rows']} == set(phases)
    assert not receipt['rejected'] and profile == spec['profile']
    if len(sys.argv)>2:
        output = Path(sys.argv[2]); output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(dict(schema=1, status='passed', source=source_text, package=package,
            target=target, workload=spec, receipt=receipt,
            scope='Exact independent oracle outcomes, rollback, events and portable snapshots in actual state acquisition; no speed claim'), indent=2)+'\n')
    print('state acquisition: five lifecycle phases, exact rollback/events/snapshots and recorded Node arguments passed')
