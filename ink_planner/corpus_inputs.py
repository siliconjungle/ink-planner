"""Capture the exact source graph and checked core used by a corpus evaluation.

The compiler owns import resolution. Its single-load capture includes embedded
standard modules, absolute imports and symlink targets. These are observations,
not proof authority; compiled planning checks the captured core normally.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time


def digest(data):
    return hashlib.sha256(data).hexdigest()


class CapturedInputs:
    def __init__(self, args):
        from .corpus import load_manifest
        self.manifest_bytes = args.manifest.read_bytes()
        self.manifest = load_manifest(args.manifest, self.manifest_bytes)
        self.programs = {}
        # Capture every manifest member so subset/resume runs share one context.
        # A failed acquisition aborts before any context or record is replaced.
        with tempfile.TemporaryDirectory(prefix='ink-corpus-inputs-', dir=args.scratch) as temporary:
            directory = Path(temporary)
            for index, program in enumerate(self.manifest['programs']):
                core_path = directory / f'{index}.core.json'
                capture_path = directory / f'{index}.sources.json'
                source = args.manifest.resolve().parent / program['source']
                started = time.monotonic()
                result = subprocess.run([str(args.compiler), 'emit-core', str(source),
                                         '-o', str(core_path), '--capture-sources', str(capture_path)],
                                        capture_output=True, text=True, timeout=args.timeout)
                seconds = round(time.monotonic() - started, 3)
                if result.returncode:
                    raise ValueError(f"corpus input capture failed: {program['name']}: {result.stderr[-3000:]}")
                core = core_path.read_bytes()
                captured = capture_path.read_bytes()
                packet = json.loads(captured)
                if packet.get('schema') != 1 or packet.get('semantics') != 'ink-source-capture-v1':
                    raise ValueError('corpus compiler did not return a supported source capture')
                files = packet.get('files')
                if not isinstance(files, list) or not files or packet.get('core_sha256') != digest(core):
                    raise ValueError('corpus captured core identity mismatch')
                for entry in files:
                    if digest(entry['source'].encode('utf-8')) != entry['sha256']:
                        raise ValueError('corpus captured source identity mismatch')
                if files[0]['sha256'] != program['sha256']:
                    raise ValueError(f"corpus source changed during capture: {program['name']}")
                self.programs[program['name']] = dict(
                    core=core, capture=captured, core_sha256=digest(core),
                    capture_sha256=digest(captured), seconds=seconds)

    def context(self):
        return {name: dict(core_sha256=item['core_sha256'], capture_sha256=item['capture_sha256'])
                for name, item in self.programs.items()}

    def save(self, directory, verify=False):
        """Keep inspectable inputs; resume never replaces missing/corrupt evidence."""
        artifacts = {'manifest.json': self.manifest_bytes}
        for item in self.programs.values():
            artifacts[item['core_sha256'] + '.core.json'] = item['core']
            artifacts[item['capture_sha256'] + '.sources.json'] = item['capture']
        if verify:
            for name, data in artifacts.items():
                path = directory / name
                if not path.is_file() or path.read_bytes() != data:
                    raise ValueError(f'corpus saved input missing or changed: {name}')
            return
        directory.mkdir(parents=True, exist_ok=True)
        for name, data in artifacts.items():
            path = directory / name
            # Write a private file then replace: never mutate a hard-linked archive.
            with tempfile.NamedTemporaryFile(dir=directory, delete=False) as output:
                temporary = Path(output.name)
                try:
                    output.write(data)
                    output.close()
                    temporary.replace(path)
                finally:
                    temporary.unlink(missing_ok=True)
