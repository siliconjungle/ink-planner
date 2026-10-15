"""Recorded cost context, never proof or hermetic-build attestation.

Bind known execution/build controls without serialising arbitrary environment
values. Cargo configuration is searched from the actual working directory, not
the generated manifest directory. Changes invalidate costs and selected caches.
"""
import hashlib
import os
from pathlib import Path
import re
import shutil

from ink_knowledge import identity
from .corpus_environment import file_digest, tool_identity

COMMON = ('INK_THREADS', 'OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS')
JAVASCRIPT = ('NODE_OPTIONS', 'NODE_PATH', 'UV_THREADPOOL_SIZE')
RUST = ('RUSTFLAGS', 'CARGO_ENCODED_RUSTFLAGS', 'RUSTUP_TOOLCHAIN', 'RUSTUP_HOME',
        'CARGO_HOME', 'RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER',
        'CARGO_BUILD_RUSTC', 'CARGO_BUILD_RUSTC_WRAPPER',
        'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER', 'CARGO_BUILD_RUSTFLAGS',
        'CARGO_BUILD_TARGET', 'RUSTC_BOOTSTRAP', 'CARGO_INCREMENTAL',
        'CARGO_BUILD_INCREMENTAL', 'MACOSX_DEPLOYMENT_TARGET', 'SDKROOT')


def controls(target):
    names = set(COMMON)
    if target == 'javascript':
        names.update(JAVASCRIPT)
    elif target == 'rust':
        names.update(RUST)
        # Profile/target overrides can change emitted machine code. These names
        # exclude registry credentials and unrelated environment variables.
        names.update(name for name in os.environ
                     if name.startswith('CARGO_PROFILE_RELEASE_') or
                     re.fullmatch(r'CARGO_TARGET_[A-Z0-9_]+_(LINKER|RUSTFLAGS)', name))
    return {name: hashlib.sha256(os.environ[name].encode()).hexdigest()
            for name in sorted(names) if name in os.environ}


def node_flags(options, *, state=False, describe=False):
    if describe:
        return []
    heap = max(32, min(4096, getattr(options, 'memory_bytes', 512000000) // 1000000))
    return (['--expose-gc'] if state else []) + ['--max-old-space-size=' + str(heap)]


def node_command(options, runner, *arguments, state=False, describe=False):
    executable = shutil.which('node')
    if executable is None:
        raise ValueError('offline measurement requires Node.js')
    return [executable, *node_flags(options, state=state, describe=describe),
            str(runner), *map(str, arguments)]


def configuration(options, *, state=False):
    """Cheap controls also bind keys when an installed host cannot be inspected."""
    target = getattr(options, 'target', None)
    value = dict(schema=1, target=target, controls=controls(target))
    if target == 'javascript':
        value['arguments'] = node_flags(options, state=state)
        if state:
            value['describe_arguments'] = node_flags(options, describe=True)
    elif target == 'rust':
        # The adapter forces jobs and output location. Inherited values of
        # CARGO_BUILD_JOBS/CARGO_TARGET_DIR are consequently not effective knobs.
        value['build'] = dict(profile='release', offline=True, jobs=2)
        value['configuration_files'] = config_files()
        selectors = ('RUSTC', 'CARGO_BUILD_RUSTC', 'RUSTC_WRAPPER', 'RUSTC_WORKSPACE_WRAPPER',
                     'CARGO_BUILD_RUSTC_WRAPPER', 'CARGO_BUILD_RUSTC_WORKSPACE_WRAPPER')
        selectors += tuple(name for name in os.environ
                           if re.fullmatch(r'CARGO_TARGET_[A-Z0-9_]+_LINKER', name))
        value['selected_executables'] = {
            name: selected_executable(os.environ[name]) for name in sorted(selectors)
            if os.environ.get(name)}
    return value


def config_files():
    """Bound actual Cargo/rustup lookup files, including currently absent files."""
    cwd = Path.cwd().resolve()
    ancestors = (cwd, *cwd.parents)
    if len(ancestors) > 64:
        raise ValueError('measurement configuration directory budget')
    paths = []
    for directory in ancestors:
        paths.extend(directory / '.cargo' / name for name in ('config', 'config.toml'))
        paths.extend(directory / name for name in ('rust-toolchain', 'rust-toolchain.toml'))
    home = Path(os.environ.get('CARGO_HOME', str(Path.home() / '.cargo'))).resolve()
    paths.extend(home / name for name in ('config', 'config.toml'))
    records = {}
    for path in paths:
        try:
            with path.open('rb') as source:
                raw = source.read(1_000_001)
        except FileNotFoundError:
            raw = None
        if raw is not None and len(raw) > 1_000_000:
            raise ValueError('measurement configuration file budget')
        records[str(path)] = hashlib.sha256(raw).hexdigest() if raw is not None else None
    return records


def selected_executable(name):
    path = shutil.which(name)
    if path is None:
        return dict(available=False)
    return dict(available=True, resolved=str(Path(path).resolve()), sha256=file_digest(path))


def execution_context(options, *, state=False):
    value = configuration(options, state=state)
    if options.target == 'javascript':
        tools = dict(node=tool_identity('node', ['--version']))
    elif options.target == 'rust':
        tools = dict(cargo=tool_identity('cargo', ['--version']),
                     rustc=tool_identity(os.environ.get('RUSTC') or
                                         os.environ.get('CARGO_BUILD_RUSTC') or 'rustc',
                                         ['--version', '--verbose']))
    else:
        raise ValueError('no installed offline measurement adapter for ' + options.target)
    if any(not tool['available'] or tool['version_status'] != 0 for tool in tools.values()):
        raise ValueError('offline measurement tool identity unavailable')
    value['tools'] = tools
    return value


def digest(context):
    return identity(context)
