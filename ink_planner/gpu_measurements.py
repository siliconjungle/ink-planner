"""Explicit source-checked GPU calibration, separate from automatic selection.

Every plan replays before literal emission; every reply is checked against the
original evaluator. Browser/renderer/GPU peak memory stays unknown. These costs
do not enter the installed CPU-baseline selector or its cache.
"""
import argparse
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess
import time
from types import SimpleNamespace

from ink_knowledge import canonical, identity, Store
from .representation_selection import phase_workload
from .measurement_context import execution_context, node_command
from .offline_measurements import independent_workloads, read_workload, has_refinement, reference_receipt, artifact_digest
from .process_measurements import combine
from .value_transfer import transfer

RUNNER = Path(__file__).with_name('measure_gpu_calls.mjs')
SOURCES = [RUNNER, *[RUNNER.with_name(n) for n in ('browser_host.mjs', 'gpu_device.mjs', 'gpu_measurement_values.mjs', 'value_transfer.mjs')]]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _stable_browser_context(reports):
    if not reports or any(not isinstance(r.get('browserVersion'), str) or not r['browserVersion'] for r in reports):
        raise ValueError('observed browser version required')
    context = dict(device=reports[0]['device'], browserVersion=reports[0]['browserVersion'])
    if any(dict(device=r['device'], browserVersion=r['browserVersion']) != context for r in reports):
        raise ValueError('browser/device identity changed across fresh launches or workload splits')
    return context


def acquire(compiler, source, plans, training, holdout, browser, scratch, *, processes=3, timeout=900):
    if type(processes) is not int or not 1 <= processes <= 8:
        raise ValueError('1..8 fresh browser processes required')
    if not 0 < timeout <= 3600:
        raise ValueError('bounded acquisition timeout required')
    compiler, source, browser = [Path(p).resolve() for p in (compiler, source, browser)]
    plans = [Path(p).resolve() for p in plans]
    if not 2 <= len(plans) <= 32:
        raise ValueError('baseline and 1..31 alternatives required')
    independent_workloads(Path(training), Path(holdout))
    specs = {name: read_workload(Path(p))[0] for name, p in [('training', training), ('holdout', holdout)]}
    if any(spec['schema'] != 1 for spec in specs.values()):
        raise ValueError('device complete-call calibration requires workload schema 1')
    if any(set(spec['workload']) != {'shape','distribution','concurrency','residency'} or spec['workload']['concurrency'] != 1 for spec in specs.values()):
        raise ValueError('serial workload provenance required')
    packages = [json.loads(p.read_bytes()) for p in plans]
    if packages[0].get('steps') != [] or any(has_refinement(p) for p in packages):
        raise ValueError('unchanged baseline and exact-equality plans required')
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    (scratch/'storage-retention.json').write_text(json.dumps(dict(schema=1, keep=[dict(path='.', reason='Active required explicit device calibration; retain source, checked plans, oracles, artifacts and live workers.')], safe_to_remove=[]), indent=2)+'\n')
    deadline = time.monotonic() + timeout

    def run(command, label, env=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError('device acquisition time budget')
        result = subprocess.run(list(map(str, command)), capture_output=True, text=True, timeout=remaining, env=env)
        (scratch/(label+'.stdout')).write_text(result.stdout)
        (scratch/(label+'.stderr')).write_text(result.stderr)
        if result.returncode:
            raise ValueError(label+': '+result.stderr[-3000:])
        return result.stdout

    tracked = [compiler, source, browser, *plans, Path(training).resolve(), Path(holdout).resolve(), *SOURCES, Path(__file__).resolve()]
    pins = {str(p): digest(p) for p in tracked}
    context = dict(schema=1, node=execution_context(SimpleNamespace(target='javascript', memory_bytes=512000000)),
                   rust=execution_context(SimpleNamespace(target='rust')), browser_sha256=digest(browser))
    shutil.copyfile(source, scratch/'source.core.json')
    artifacts, builds = [], []
    cargo = shutil.which('cargo')
    if not cargo:
        raise ValueError('local Cargo entry point required')
    for i, (path, package) in enumerate(zip(plans, packages)):
        staged = scratch/f'plan-{i}.json'
        staged.write_bytes(canonical(package))
        artifact = scratch/f'artifact-{i}'
        checked = json.loads(run([compiler, 'emit-plan', source, staged, '--core', '--target', 'webgpu', '-o', artifact], f'emit-{i}'))
        if checked.get('status') != 'checked' or checked.get('input') != package.get('input_core_sha256'):
            raise ValueError('checked source/plan binding mismatch')
        manifest = json.loads((artifact/'manifest.json').read_bytes())
        if manifest.get('runtime_abi') != 'ink-packed-pipeline-v2' or manifest.get('core_sha256') != checked['selected']:
            raise ValueError('packed supplied-device artifact required')
        started = time.monotonic()
        run([cargo, 'build', '--release', '--offline', '--lib', '--target', 'wasm32-unknown-unknown',
             '--manifest-path', artifact/'cpu/Cargo.toml', '--target-dir', scratch/'cpu-target'], f'build-{i}',
            env=dict(os.environ, CARGO_BUILD_JOBS='2'))
        shutil.copyfile(scratch/'cpu-target/wasm32-unknown-unknown/release/ink_packed_cpu.wasm', artifact/'cpu.wasm')
        builds.append(dict(plan=identity(package), seconds=time.monotonic()-started,
                           lock_sha256=digest(artifact/'cpu/Cargo.lock'), wasm_sha256=digest(artifact/'cpu.wasm')))
        artifacts.append(dict(measurement=identity(package), path=str(artifact), selected_core_sha256=checked['selected'], artifact_sha256=artifact_digest(artifact)))
    phases, fingerprints = {}, {}
    for name, spec in specs.items():
        shutil.copyfile(training if name == 'training' else holdout, scratch/(name+'.workload.json'))
        phases[name] = {}
        for phase, calls in spec['phases'].items():
            phases[name][phase] = []
            for index, call in enumerate(calls):
                args = scratch/'oracle-args.json'
                args.write_text(json.dumps(call['args'], allow_nan=False))
                receipt = json.loads(run([compiler, 'run', source, call['function'], args, '--core', '--input-receipt'], f'oracle-{name}-{phase}-{index}'))
                expected, fingerprint = reference_receipt(receipt, packages[0]['input_core_sha256'], call['function'])
                phases[name][phase].append(dict(call, expected=expected, input_sha256=fingerprint))
        fingerprints[name] = sorted({c['input_sha256'] for calls in phases[name].values() for c in calls})
    if set(fingerprints['training']) & set(fingerprints['holdout']):
        raise ValueError('normalized typed holdout inputs overlap training')
    receipts = {}
    for name in ['training', 'holdout']:
        reports = []
        for process in range(processes):
            # Reserve normal owned-browser shutdown outside the whole-host budget.
            # Do not let the parent timeout kill Node before it can close Chrome.
            remaining = deadline - time.monotonic()
            if remaining <= 35:
                raise ValueError('device acquisition lacks browser cleanup time budget')
            request = scratch/f'{name}-{process}.request.json'
            request.write_text(json.dumps(dict(schema=1, artifacts=artifacts, phases=transfer(phases[name]),
                measurement=dict(rounds=11, minimum_batch_ms=5, maximum_calls_per_sample=256), process_index=process,
                timeout_ms=min(600000, max(1, int((remaining-35)*1000)))), allow_nan=False))
            output = run(node_command(SimpleNamespace(memory_bytes=512000000), RUNNER, browser, request, digest(request)), f'{name}-{process}')
            report = json.loads(output)
            if report['request_sha256'] != digest(request) or report['input_fingerprints'] != fingerprints[name]:
                raise ValueError('device response/request/input binding mismatch')
            reports.append(report)
        _stable_browser_context(reports)
        receipts[name] = dict(combine(reports, artifacts, fingerprints=True), device=reports[0]['device'],
                              reports=reports, profile=specs[name]['profile'], peak_rss_bytes=None)
    _stable_browser_context([report for receipt in receipts.values() for report in receipt['reports']])
    if any(digest(p) != pins[str(p)] for p in tracked):
        raise ValueError('source, compiler, plans, browser or adapter changed during acquisition')
    if context['node'] != execution_context(SimpleNamespace(target='javascript', memory_bytes=512000000)) or context['rust'] != execution_context(SimpleNamespace(target='rust')):
        raise ValueError('execution controls changed during acquisition')
    if any(artifact_digest(Path(a['path'])) != a['artifact_sha256'] for a in artifacts):
        raise ValueError('emitted artifact changed during acquisition')
    accepted = set.intersection(*[{r['measurement'] for r in receipt['rows']} for receipt in receipts.values()])
    target = dict(hardware=platform.machine()+':'+platform.node()+';webgpu:'+identity(receipts['training']['device']),
                  driver=platform.system()+':'+platform.release()+';'+receipts['training']['reports'][0]['browserVersion'],
                  toolchain='compiler:'+digest(compiler)+';context:'+identity(context)+';adapter:'+identity(pins),
                  backend='webgpu', bridge='explicit-gpu-required;packed-pipeline-v2;provided-device-observed;full-complete-call;memory-unknown-v1')
    observations = []
    for name, receipt in receipts.items():
        workload = dict(specs[name]['workload'])
        workload['shape'] = dict(inputs_sha256=digest(training if name=='training' else holdout), declared=workload['shape'])
        for row in receipt['rows']:
            if row['measurement'] not in accepted:
                continue
            observation = dict(schema=1, program=packages[0]['input_core_sha256'], entries=[], plan=row['measurement'], target=target,
                workload=phase_workload(workload, row['phase']), metrics=dict(complete_call_ns=row['complete_call_ns'], peak_bytes=None))
            Store.validate_observation(observation)
            observations.append(observation)
    result = dict(schema=1, semantics='ink-explicit-device-measurements-v1', input_core_sha256=packages[0]['input_core_sha256'],
        context=context, input_hashes=pins, artifacts=artifacts, builds=builds, phases=receipts,
        target=target, observation_proposals=observations, validated_measurements=sorted(accepted),
        scope='Explicit same-device GPU-required equality-plan comparison with disjoint normalized source-oracle holdout. Full async call and result consumption; setup/build separate. Unknown whole-browser peak memory. No CPU-placement gain, automatic choice, cached selection or backend correctness proof.')
    (scratch/'receipt.json').write_text(json.dumps(result, indent=2)+'\n')
    (scratch/'terminal.json').write_text(json.dumps(dict(schema=1, exit_status=0, processes=processes, normalized_holdout_disjoint=True), indent=2)+'\n')
    (scratch/'storage-retention.json').write_text(json.dumps(dict(schema=1, keep=[dict(path='.', reason='Selected checked source/plans, complete-call inputs/oracles, immutable artifacts, raw process receipts and exact contexts; generated target explicitly retires separately.')], safe_to_remove=[dict(path='cpu-target', reason='Completed generated Wasm build output; exact selected CPU Wasm, source, lockfiles and build logs retained in artifacts.')]), indent=2)+'\n')
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ['compiler', 'source', 'training', 'holdout', 'browser', 'scratch']:
        p.add_argument('--'+name, type=Path, required=True)
    p.add_argument('--plan', type=Path, action='append', required=True)
    p.add_argument('--processes', type=int, default=3)
    p.add_argument('--timeout', type=float, default=900)
    args = p.parse_args()
    result = acquire(args.compiler, args.source, args.plan, args.training, args.holdout, args.browser,
                     args.scratch, processes=args.processes, timeout=args.timeout)
    print(json.dumps(dict(status='measured', receipt=str(args.scratch/'receipt.json'), source=result['input_core_sha256'])))


if __name__ == '__main__':
    main()
