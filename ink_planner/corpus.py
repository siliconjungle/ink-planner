"""Evaluate installed knowledge on a fixed program corpus with default planning.

Each program is compiled by the ordinary compiled planner against a private,
hard-linked copy of the knowledge root, because compilation publishes admitted
candidates into the store it is given. The report records where each program
stopped (matched laws, rejected premises, admitted states, selection gate), which
knowledge entries were admitted for which programs, and where planning time went.

Everything here is an observation of untrusted search and recorded selection.
It grants no proof, applicability or profitability authority, and a law that is
not observed here may still apply to programs outside the corpus.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

SEMANTICS = 'ink-planning-corpus-report-v1'
MANIFEST = 'ink-planning-corpus-v1'


def clone_knowledge(source, destination):
    """Private publication with shared immutable files and a private index.

    Producers may read research fixtures, libraries and custom root metadata.
    Keep those inputs; omit only development caches and checkout metadata.
    SQLite is mutable, so a hard link would let candidate publication modify
    the installed index. Its backup API also avoids copying a partial WAL state.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    omitted = {'.git', 'build', 'compiled-cache', '__pycache__', 'store',
               'index.sqlite', 'index.sqlite-wal', 'index.sqlite-shm'}
    def ignore(path, names):
        return set(names) & (omitted if Path(path) == source else {'__pycache__'})
    shutil.copytree(source, destination, copy_function=private_file_copy, ignore=ignore, symlinks=True)
    if (source / 'store/snapshot.json').exists():
        from ink_knowledge import Store, identity
        publication = Store(source)
        members = publication.members()  # Authenticate the pinned canonical tree.
        trees = {publication.snapshot['root']}
        seen_witnesses = set()
        for witness in members.values():
            # Every entry in a leaf shares the same authenticated ancestry.
            # Hash that path once, preserving distinct paths for equal leaves.
            key = (tuple(witness['entries']), tuple((edge['sibling'], edge['left']) for edge in witness['path']))
            if key in seen_witnesses:
                continue
            seen_witnesses.add(key)
            node = identity(dict(schema=1, entries=witness['entries']))
            trees.add(node)
            for edge in witness['path']:
                sibling = edge['sibling']
                trees.add(sibling)
                node = identity(dict(schema=1, left=sibling if edge['left'] else node,
                                     right=node if edge['left'] else sibling))
                trees.add(node)
        data = destination / 'store'
        shutil.copytree(publication.data, data, copy_function=private_file_copy, symlinks=True,
                        ignore=lambda _, names: set(names) & {'objects', 'trees', 'snapshots'})
        for directory, ids in [('objects', members), ('trees', trees), ('snapshots', [publication.snapshot_id])]:
            (data / directory).mkdir()
            for ident in ids:
                os.link(publication.data / directory / (ident + '.json'), data / directory / (ident + '.json'))
        if Store(source).snapshot_id != publication.snapshot_id:
            raise ValueError('knowledge publication changed during corpus isolation')
    elif (source / 'store').exists():
        shutil.copytree(source / 'store', destination / 'store', copy_function=private_file_copy, symlinks=True)
    index = source / 'index.sqlite'
    if index.exists():
        with closing(sqlite3.connect(index.as_uri() + '?mode=ro', uri=True)) as original:
            with closing(sqlite3.connect(destination / 'index.sqlite')) as private, private:
                original.backup(private)


def load_manifest(path, content=None):
    manifest = json.loads(Path(path).read_bytes() if content is None else content)
    if manifest.get('schema') != 1 or manifest.get('semantics') != MANIFEST:
        raise ValueError('corpus manifest schema')
    names = set()
    for program in manifest['programs']:
        if not {'name', 'source', 'sha256', 'split'} <= set(program) or program['name'] in names:
            raise ValueError('corpus program fields or duplicate name')
        names.add(program['name'])
        source = Path(path).parent / program['source']
        if hashlib.sha256(source.read_bytes()).hexdigest() != program['sha256']:
            raise ValueError(f"corpus source changed: {program['name']}")
    return manifest


def step_references(package):
    """Laws applied and representations chosen by a plan's own steps.

    Proof dependencies inside embedded knowledge views are not counted: they
    support an application but were not selected by it.
    """
    laws, representations, kinds = set(), set(), Counter()
    for step in package.get('steps', []) if package.get('semantics') == 'ink-compiled-plan-v1' else []:
        kinds[step.get('kind')] += 1
        payload = step.get('package') or {}
        for application in payload.get('applications', []) or []:
            if isinstance(application.get('law'), str):
                laws.add(application['law'])
        representation = payload.get('representation')
        if isinstance(representation, str) and len(representation) == 64:
            representations.add(representation)
    return laws, representations, kinds


def candidate_references(root, receipt):
    """Entries named by admitted candidate packages in a planning receipt."""
    sys.path.insert(0, str(root))
    try:
        from ink_knowledge import Store
    finally:
        sys.path.pop(0)
    store = Store(root)
    references = []
    for candidate in receipt.get('candidates', [])[1:]:
        try:
            package = store.object(candidate['candidate'])['payload']['package']
        except (OSError, ValueError, KeyError):
            continue
        laws, representations, kinds = step_references(package)
        references.append(dict(plan=candidate.get('plan'), laws=sorted(laws), representations=sorted(representations),
                               step_kinds=dict(kinds), emittable=(candidate.get('capability') or {}).get('emittable', True)))
    return references


MUTABLE_SUFFIXES = ('.sqlite', '.sqlite-wal', '.sqlite-shm', '.sqlite-journal')


def private_file_copy(source, destination):
    name = Path(source).name
    if name.endswith(MUTABLE_SUFFIXES) or name == 'index-published.json':
        return shutil.copy2(source, destination)
    return os.link(source, destination)


def private_copy(root, scratch):
    """Keep the upstream API, with a pinned publication and private indexes."""
    return clone_knowledge(root, scratch)


def worker_environment(knowledge_root):
    return dict(os.environ, PYTHONPATH=os.pathsep.join([str(Path(__file__).parents[1]), str(knowledge_root)]))


def run_program(program, prepared, args, out):
    record = dict(name=program['name'], split=program['split'])
    with tempfile.TemporaryDirectory(prefix='ink-corpus-', dir=args.scratch) as temporary:
        core = Path(temporary) / 'source.core.json'
        core.write_bytes(prepared['core'])
        scratch = Path(temporary) / 'knowledge'
        private_copy(args.knowledge_root, scratch)
        plan = out / 'plans' / f"{program['name']}.json"
        plan.parent.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, '-m', 'ink_planner.compiled_planning', '--compiler', str(args.compiler),
                   '--source', str(core), '--core', '--knowledge-root', str(scratch),
                   '--target', args.target, '--planning-effort', args.planning_effort, '-o', str(plan)]
        if getattr(args,'expansion_limit',None) is not None:command += ['--expansion-limit',str(args.expansion_limit)]
        environment = worker_environment(scratch)
        started = time.monotonic()
        result = subprocess.run(command, capture_output=True, text=True, env=environment, timeout=args.timeout)
        record['planning_seconds'] = round(time.monotonic() - started, 3)
        record['source_capture_seconds'] = prepared['seconds']
        record['seconds'] = round(record['planning_seconds'] + prepared['seconds'], 3)
        record['core_sha256'] = prepared['core_sha256']
        record['status'] = result.returncode
        if result.returncode:
            record['error'] = result.stderr[-3000:]
            return record
        receipt_path = Path(str(plan) + '.receipt.json')
        receipt = json.loads(receipt_path.read_text())
        record['references'] = candidate_references(scratch, receipt)
        names = json.loads((scratch / 'store' / 'names.json').read_text()) if (scratch / 'store' / 'names.json').exists() else {}
        wanted = {r for ref in record['references'] for r in ref['laws'] + ref['representations']}
        wanted |= set(((receipt.get('diagnostics') or {}).get('semantic') or {}).get('laws', {}))
        record['names'] = {ident: name for name, ident in names.items() if isinstance(ident, str) and ident in wanted}
    record['receipt'] = str(receipt_path.relative_to(out))
    return record


def summarise(records, out):
    programs = []
    attribution = defaultdict(lambda: dict(programs=set(), kind=None))
    names = {}
    shapes = Counter(); shape_programs = defaultdict(set)
    instances = Counter(); instance_programs = defaultdict(set)
    reasons = Counter(); rejected_laws = defaultdict(lambda: dict(rejected=0, programs=set(), premises=Counter()))
    seconds = Counter()
    for record in records:
        names.update(record.get('names', {}))
        if record.get('status') != 0:
            programs.append(dict(name=record['name'], status=record.get('status'), error=record.get('error', '')[-400:]))
            continue
        receipt = json.loads((out / record['receipt']).read_text())
        diagnostics = receipt.get('diagnostics') or {}
        funnel = diagnostics.get('funnel') or {}
        semantic = diagnostics.get('semantic') or {}
        for ref in record.get('references', []):
            for ident in ref['laws']:
                attribution[ident]['programs'].add(record['name']); attribution[ident]['kind'] = 'law'
            for ident in ref['representations']:
                attribution[ident]['programs'].add(record['name']); attribution[ident]['kind'] = 'representation'
        for shape, count in (semantic.get('premise_shapes') or {}).items():
            shapes[shape] += count; shape_programs[shape].add(record['name'])
        for shape, count in (semantic.get('instance_shapes') or {}).items():
            instances[shape] += count; instance_programs[shape].add(record['name'])
        reasons.update(semantic.get('reasons') or {})
        for ident, entry in (semantic.get('laws') or {}).items():
            if entry.get('rejected'):
                merged = rejected_laws[ident]
                merged['rejected'] += entry['rejected']; merged['programs'].add(record['name'])
                merged['premises'].update(entry.get('premises') or {})
        for recipe, value in (diagnostics.get('recipe_seconds') or {}).items():
            seconds[recipe] += value
        alternatives = [ref for ref in record.get('references', []) if ref['laws'] or ref['representations']]
        programs.append(dict(
            name=record['name'], split=record['split'], status=0, seconds=record['seconds'],
            stop_reason=receipt.get('stop_reason'), admitted_states=receipt.get('admitted_states'),
            candidates=len(receipt.get('candidates', [])), admitted_edges=funnel.get('admitted_edges', {}),
            semantic=dict((k, funnel.get('semantic_' + k, 0)) for k in ('matched', 'checked', 'admitted', 'rejected', 'unchecked')),
            alternatives_with_knowledge=len(alternatives),
            rewrite_alternatives=sum(1 for ref in alternatives if ref['laws']),
            selected_alternative=bool(funnel.get('selected_alternative')), last_gate=funnel.get('last_gate'),
            selection_reason=(receipt.get('selection') or {}).get('reason')))
    used = {ident: dict(kind=v['kind'], name=names.get(ident), programs=sorted(v['programs']))
            for ident, v in attribution.items()}
    ok = [p for p in programs if p.get('status') == 0]
    law_entries = [v for v in used.values() if v['kind'] == 'law']
    headline = dict(
        programs=len(programs), compiled=len(ok),
        programs_with_rewrite_alternative=sum(1 for p in ok if p['rewrite_alternatives']),
        programs_with_any_alternative=sum(1 for p in ok if p['alternatives_with_knowledge']),
        programs_selecting_alternative=sum(1 for p in ok if p['selected_alternative']),
        median_seconds=statistics.median([p['seconds'] for p in ok]) if ok else None,
        stopped_by_time=sum(1 for p in ok if p['stop_reason'] == 'time'),
        laws_admitted=len(law_entries),
        laws_admitted_in_multiple_programs=sum(1 for v in law_entries if len(v['programs']) > 1),
        representations_admitted=sum(1 for v in used.values() if v['kind'] == 'representation'),
        last_gates=dict(Counter(p['last_gate'] for p in ok)))
    near = dict(reasons=dict(reasons),
                premise_shapes=[dict(shape=s, rejections=c, programs=sorted(shape_programs[s])) for s, c in shapes.most_common(40)],
                instance_shapes=[dict(shape=s, rejections=c, programs=sorted(instance_programs[s])) for s, c in instances.most_common(40)],
                laws=[dict(law=ident, name=names.get(ident), rejected=v['rejected'], programs=sorted(v['programs']),
                           premises=dict(v['premises'].most_common(3)))
                      for ident, v in sorted(rejected_laws.items(), key=lambda kv: -kv[1]['rejected'])[:40]])
    total = sum(seconds.values()) or 1
    time_split = [dict(recipe=r, seconds=round(s, 2), share=round(s / total, 3)) for r, s in seconds.most_common(25)]
    return dict(schema=1, semantics=SEMANTICS,
                scope='Observed default planning on a fixed corpus; no proof, applicability or profitability authority',
                headline=headline, programs=programs, attribution=used, near_misses=near, recipe_seconds=time_split)


def markdown(report, context):
    h = report['headline']
    lines = [f"# Planning corpus report", '',
             f"Target `{context['target']}`, effort `{context['planning_effort']}`, compiler `{context['compiler_sha256'][:12]}`, "
             f"knowledge snapshot `{context['snapshot'][:12]}`.", '',
             '| Measure | Value |', '|---|---:|',
             f"| Programs compiled | {h['compiled']} / {h['programs']} |",
             f"| With an admitted rewrite alternative | {h['programs_with_rewrite_alternative']} |",
             f"| With any knowledge-backed alternative | {h['programs_with_any_alternative']} |",
             f"| Selecting an alternative under defaults | {h['programs_selecting_alternative']} |",
             f"| Stopped by the time budget | {h['stopped_by_time']} |",
             f"| Median planning seconds | {h['median_seconds']} |",
             f"| Laws admitted (in 2+ programs) | {h['laws_admitted']} ({h['laws_admitted_in_multiple_programs']}) |",
             f"| Representations admitted | {h['representations_admitted']} |", '',
             '## Programs', '',
             '| Program | Seconds | Stop | Matched | Admitted | Rejected | Unchecked | Rewrite alts | Last gate |',
             '|---|---:|---|---:|---:|---:|---:|---:|---|']
    for p in report['programs']:
        if p.get('status') != 0:
            lines.append(f"| {p['name']} | failed | | | | | | | {p.get('error','')[:60]} |"); continue
        s = p['semantic']
        lines.append(f"| {p['name']} | {p['seconds']:.0f} | {p['stop_reason']} | {s['matched']} | {s['admitted']} | "
                     f"{s['rejected']} | {s['unchecked']} | {p['rewrite_alternatives']} | {p['last_gate']} |")
    lines += ['', '## Near misses', '', 'Checker rejection gates: ' +
              (', '.join(f"{k} {v}" for k, v in sorted(report['near_misses']['reasons'].items(), key=lambda kv: -kv[1])) or 'none'), '',
              '| Failed premise shape | Rejections | Programs |', '|---|---:|---|']
    for row in report['near_misses']['premise_shapes'][:20]:
        lines.append(f"| `{row['shape'][:90]}` | {row['rejections']} | {', '.join(row['programs'])} |")
    lines += ['', '| Failed premise as instantiated in the program | Rejections | Programs |', '|---|---:|---|']
    for row in report['near_misses'].get('instance_shapes', [])[:20]:
        lines.append(f"| `{row['shape'][:110]}` | {row['rejections']} | {', '.join(row['programs'])} |")
    lines += ['', '## Where planning time went', '', '| Recipe | Seconds | Share |', '|---|---:|---:|']
    for row in report['recipe_seconds'][:15]:
        lines.append(f"| {row['recipe']} | {row['seconds']} | {row['share']:.0%} |")
    lines += ['', '## Admitted knowledge by program', '', '| Entry | Kind | Programs |', '|---|---|---|']
    for ident, v in sorted(report['attribution'].items(), key=lambda kv: (-len(kv[1]['programs']), kv[0]))[:40]:
        lines.append(f"| {v['name'] or ident[:16]} | {v['kind']} | {', '.join(v['programs'])} |")
    lines += ['', report['scope'] + '.', '']
    return '\n'.join(lines)


def execution_context(args, inputs=None):
    """Bind resumable diagnostics to the exact compiler, publication and code."""
    from ink_knowledge import Store
    from ink_knowledge.recipes import load
    from .corpus_environment import capture_environment
    store = Store(args.knowledge_root)
    _, registry = load(store=store)
    sources = hashlib.sha256()
    for directory in ['producers', 'ink_knowledge', 'libraries']:
        for path in sorted((args.knowledge_root / directory).rglob('*')):
            if path.is_file() and not path.is_symlink() and path.suffix in {'.py', '.json', '.gz', '.mjs', '.rs', '.txt', '.ink', '.lean'} and '__pycache__' not in path.parts:
                sources.update(str(path.relative_to(args.knowledge_root)).encode())
                sources.update(b'\0')
                sources.update(hashlib.sha256(path.read_bytes()).digest())
    planner_sources = hashlib.sha256()
    planner_root = Path(__file__).parent
    for path in sorted(planner_root.rglob('*.py')):
        if not path.is_symlink() and '__pycache__' not in path.parts:
            planner_sources.update(str(path.relative_to(planner_root)).encode())
            planner_sources.update(b'\0')
            planner_sources.update(hashlib.sha256(path.read_bytes()).digest())
    return dict(target=args.target, planning_effort=args.planning_effort, expansion_limit=getattr(args,'expansion_limit',None),
                environment=capture_environment(args.target, worker_environment(args.knowledge_root)), timeout=args.timeout, planner_source_sha256=planner_sources.hexdigest(),
                compiler_sha256=hashlib.sha256(args.compiler.read_bytes()).hexdigest(),
                snapshot=store.snapshot_id, registry_sha256=registry,
                producer_source_sha256=sources.hexdigest(),
                manifest_sha256=hashlib.sha256(args.manifest.read_bytes() if inputs is None else inputs.manifest_bytes).hexdigest(),
                corpus_inputs=None if inputs is None else inputs.context())


def write_checkpoint(path, value):
    """A terminated run must leave either the old or the complete new record."""
    fd, temporary = tempfile.mkstemp(prefix='.corpus-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2)
            output.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def bind_context(path, current, resume):
    if resume:
        if not path.exists() or json.loads(path.read_text()) != current:
            raise ValueError('corpus resume requires the exact original execution context')
    else:
        write_checkpoint(path, current)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--manifest', type=Path, required=True)
    p.add_argument('--compiler', type=Path, required=True)
    p.add_argument('--knowledge-root', type=Path, required=True)
    p.add_argument('--target', default='javascript')
    p.add_argument('--planning-effort', default='balanced', choices=['quick', 'balanced', 'thorough'])
    p.add_argument('--expansion-limit',type=int,help='override per-state proof attempt budget; 0 reproduces exhaustive parent enumeration')
    p.add_argument('--programs', help='comma-separated subset of program names')
    p.add_argument('--timeout', type=int, default=1800)
    p.add_argument('--scratch', type=Path, help='directory for private knowledge copies (same filesystem as the root)')
    p.add_argument('--resume', action='store_true', help='retain successful records only under the exact original execution context')
    p.add_argument('--report-only', action='store_true', help='re-aggregate existing records in the output directory')
    p.add_argument('-o', '--output', type=Path, required=True)
    args = p.parse_args()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    args.compiler = args.compiler.resolve(); args.knowledge_root = args.knowledge_root.resolve()
    args.scratch = (args.scratch or out / 'scratch').resolve(); args.scratch.mkdir(parents=True, exist_ok=True)
    wanted = set(args.programs.split(',')) if args.programs else None
    records_path = out / 'records.json'
    from .corpus_inputs import CapturedInputs
    inputs = CapturedInputs(args)
    manifest = inputs.manifest
    context = execution_context(args, inputs)
    context_path = out / 'context.json'
    bind_context(context_path, context, args.resume or args.report_only)
    inputs.save(out / 'inputs', verify=args.resume or args.report_only)
    records = json.loads(records_path.read_text()) if (args.resume or args.report_only) and records_path.exists() else []
    if not args.report_only:
        done = {r['name'] for r in records if r.get('status') == 0}
        for program in manifest['programs']:
            if args.resume and program['name'] in done:
                continue
            if execution_context(args, inputs) != context:
                raise ValueError('corpus execution context changed during evaluation')
            if wanted is not None and program['name'] not in wanted:
                continue
            records = [r for r in records if r['name'] != program['name']]
            print(f"planning {program['name']} ...", file=sys.stderr, flush=True)
            try:
                record = run_program(program, inputs.programs[program['name']], args, out)
            except subprocess.TimeoutExpired:
                record = dict(name=program['name'], split=program['split'], status='timeout')
            records.append(record)
            write_checkpoint(records_path, records)
            print(f"  {record.get('status')} in {record.get('seconds')}s", file=sys.stderr, flush=True)
    if execution_context(args, inputs) != context:
        raise ValueError('corpus execution context changed during evaluation')
    report = dict(summarise(records, out), context=context)
    (out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    (out / 'report.md').write_text(markdown(report, context))
    print(json.dumps(report['headline']))


if __name__ == '__main__':
    main()
