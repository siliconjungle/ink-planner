"""Recorded corpus execution components, not proof or hermetic-build authority.

Include the Python proof dependency and optional discovery dependencies, their
actual installed bytes, and the selected measurement tool. Do not serialize the
whole process environment: it may contain credentials and unrelated settings.
"""
import hashlib
import importlib.metadata
import importlib.util
import json
import os
import platform
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import sysconfig

# Runtime dependencies of knowledge and the planner's optional ANN acquisition.
DISTRIBUTIONS = ('python-sat', 'six', 'numpy', 'hnswlib')
MODULES = ('pysat', 'pysolvers', 'six', 'numpy', 'hnswlib')


def file_digest(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def file_set_digest(files):
    """Hash actual bytes, including missing files, rather than trusting RECORD."""
    digest = hashlib.sha256()
    count = 0
    for label, path in sorted(files, key=lambda item: item[0]):
        if '__pycache__' in Path(label).parts or Path(label).suffix in {'.pyc', '.pyo'}:
            continue
        record = [label, file_digest(path) if path.is_file() else None]
        digest.update(json.dumps(record, separators=(',', ':')).encode())
        digest.update(b'\n')
        count += 1
    return dict(files=count, sha256=digest.hexdigest())


def distribution_identity(name):
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return dict(available=False)
    files = distribution.files
    return dict(available=True, version=distribution.version,
                files_recorded=files is not None,
                content=file_set_digest((str(p), Path(distribution.locate_file(p))) for p in files or ()))


def module_identity(name):
    # Top-level find_spec does not import these optional native packages. Record
    # their selected import locations as well as distribution metadata: a local
    # module may shadow an installed distribution without changing its version.
    specification = importlib.util.find_spec(name)
    if specification is None:
        return dict(available=False)
    locations = list(specification.submodule_search_locations or ())
    files = []
    for index, location in enumerate(locations):
        root = Path(location)
        files.extend((str(index) + '/' + str(p.relative_to(root)), p)
                     for p in root.rglob('*') if p.is_file())
    origin = specification.origin
    if not locations and origin not in (None, 'built-in', 'frozen'):
        files.append(('module', Path(origin)))
    return dict(available=True, origin=origin, locations=locations,
                content=file_set_digest(files))


def tool_identity(name, arguments):
    executable = shutil.which(name)
    if executable is None:
        return dict(available=False)
    # rustc/cargo may be rustup proxies. Include their version response as well
    # as proxy bytes so selecting a different installed toolchain changes this.
    result = subprocess.run([executable, *arguments], capture_output=True,
                            timeout=15, check=False)
    return dict(available=True, executable=executable,
                resolved=str(Path(executable).resolve()), sha256=file_digest(executable),
                version_status=result.returncode,
                stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                stderr_sha256=hashlib.sha256(result.stderr).hexdigest())


def execution_environment(target):
    executable = Path(sys.executable)
    python = dict(executable=str(executable), resolved=str(executable.resolve()),
                  sha256=file_digest(executable), version=sys.version,
                  implementation=sys.implementation.name,
                  cache_tag=sys.implementation.cache_tag,
                  prefix=sys.prefix, base_prefix=sys.base_prefix,
                  soabi=sysconfig.get_config_var('SOABI'), sqlite=sqlite3.sqlite_version,
                  distributions={name: distribution_identity(name) for name in DISTRIBUTIONS},
                  modules={name: module_identity(name) for name in MODULES})
    tools = {}
    if target == 'javascript':
        tools['node'] = tool_identity('node', ['--version'])
    elif target == 'rust':
        tools['rustc'] = tool_identity('rustc', ['--version', '--verbose'])
        tools['cargo'] = tool_identity('cargo', ['--version'])
    # Record only known execution controls; hash values instead of publishing
    # paths or any future sensitive value accidentally placed in these settings.
    controls = {name: hashlib.sha256(os.environ[name].encode()).hexdigest()
                for name in ('PYTHONHASHSEED', 'PYTHONOPTIMIZE', 'PYTHONNOUSERSITE',
                             'NODE_OPTIONS', 'RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS',
                             'RUSTUP_TOOLCHAIN', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER',
                             'CARGO_HOME', 'RUSTUP_HOME', 'OMP_NUM_THREADS',
                             'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS') if name in os.environ}
    return dict(schema=1, python=python, tools=tools, controls=controls,
                host=dict(system=platform.system(), release=platform.release(),
                          machine=platform.machine(), processor=platform.processor(),
                          node=platform.node()))


def capture_environment(target, environment):
    # Inspect a fresh interpreter with the same invocation and environment as a
    # corpus worker, not the parent's already imported modules or command flags.
    result = subprocess.run([sys.executable, '-m', 'ink_planner.corpus_environment', target],
                            env=environment, capture_output=True, text=True,
                            check=True, timeout=120)
    return json.loads(result.stdout)


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('usage: python -m ink_planner.corpus_environment TARGET')
    print(json.dumps(execution_environment(sys.argv[1]), sort_keys=True))
