"""Exact offline authoring source snapshots; provenance grants no proof."""
import hashlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path


def source_files(roots=None):
    if roots is None:
        import ink_knowledge
        import producers
        roots = {'planner': Path(__file__).parent,
                 'knowledge': Path(ink_knowledge.__file__).parent,
                 'producers': Path(producers.__file__).parent}
    result = {}
    for label, root in sorted(roots.items()):
        root = Path(root)
        for path in sorted(root.rglob('*.py')):
            if '__pycache__' not in path.parts:
                result[label + '/' + str(path.relative_to(root))] = path.read_bytes()
    return result


def fingerprints(files):
    return {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()}


def runtime():
    try:
        solver_version = importlib.metadata.version('python-sat')
    except importlib.metadata.PackageNotFoundError:
        solver_version = None
    return dict(python=sys.version, implementation=platform.python_implementation(),
                executable_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
                python_sat_version=solver_version)


def capture(directory, roots=None):
    """Save actual source bytes before generating suggestions or certificates."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    files = source_files(roots)
    for name, raw in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    snapshot = dict(schema=1, sources=fingerprints(files), runtime=runtime(),
                    scope='Ink authoring Python sources and runtime identity; not a full OS/dependency image or correctness authority')
    (directory / 'manifest.json').write_text(json.dumps(snapshot, indent=2) + '\n')
    return snapshot


def verify(snapshot, roots=None):
    if snapshot['sources'] != fingerprints(source_files(roots)) or snapshot['runtime'] != runtime():
        raise ValueError('Authoring tool sources or runtime changed during acquisition')
