# Offline measurement context

Offline performance evidence is comparable only within its recorded execution
context. `measurement_context.py` binds known controls to both the observation
target and the untrusted selected-plan cache. This is cost provenance, not rewrite
authority. Every cached selected plan still needs fresh complete compiler replay.
Runtime execution does not probe profitability or adapt its strategy.

The observation target retains the five-field knowledge storage contract. Its
`toolchain` field includes a versioned digest of the execution context; pure and
state acquisition receipts retain the structured context. Old observations are
left intact and do not match the new target. Do not relabel historical costs.

For JavaScript, the context records the selected Node executable's actual bytes,
resolved location and version response; hashed `NODE_OPTIONS`, `NODE_PATH`,
`UV_THREADPOOL_SIZE` and worker controls; and the exact heap/GC arguments. One
helper constructs both the recorded arguments and actual measurement commands.
State description retains its separate argument list. A 64 MB heap cannot reuse
observations gathered with a 512 MB heap. Normal Node and `--jitless` execution
cannot reuse one another's costs or selected-plan cache entries.

For native Rust, the context records Cargo and selected rustc identities, known
flags/toolchain/compiler/wrapper selectors, release profile overrides and target
linker/Rust flag overrides. Explicitly selected compiler, wrapper and linker
executable bytes are hashed. Cargo/rustup configuration files are hashed from the
actual current directory and its ancestors, plus Cargo's home configuration.
Absent files are included so creating a configuration file changes the context.
Reads are bounded to 64 ancestor directories and 1 MB per configuration file;
unreadable or oversized configuration declines measurement. Cargo's adapter
forces release/offline mode and two build jobs, so inherited build-job and target
directory overrides are not recorded as effective knobs.

Known settings are hashed, and arbitrary environment values are not copied into
receipts. Tool locations and configuration filenames are recorded. Cheap controls
also enter the selected-cache key if host inspection fails or times out. One frozen
execution binding follows lookup through output-snapshot publication; changed
contexts during cached replay or planning are rejected, and publishing source-bound
candidates cannot re-key old measured choices with newly read host settings. Tool
inspection uses the existing corpus identity helper, including its 15-second
version query timeout. Both acquisition paths recheck their context before
publishing observations; a detected change publishes no measurements.

This is not hermetic-build attestation. It does not bind arbitrary Node preloads'
transitive files, compiler-selected libraries/linkers hidden in Cargo config,
shared libraries, kernel scheduling, thermal state or other processes. Exact
adapter executable/dependency hashes remain in native acquisition receipts.
Shared-machine timings still require an explicit contention qualification.
Context separation establishes comparability boundaries, not an improvement in
generated-program speed.

Validation includes real Node argument inspection, actual knowledge observation
queries, cache misses across flags, native configuration/wrapper byte changes,
missing/timed-out host inspection, and refusal to publish after a context change.
The automatic compilation integration exercises normal → jitless → jitless →
normal, checks fresh acquisition then separate warm caches, corrupts a cached
proof and verifies recovery, and checks 24 independent boundary inputs.

Configuration lookup follows the [Cargo configuration reference](https://doc.rust-lang.org/cargo/reference/config.html);
known native controls follow the [Cargo environment reference](https://doc.rust-lang.org/cargo/reference/environment-variables.html).
Node controls are documented in the [Node 22 command-line reference](https://nodejs.org/download/release/v22.22.1/docs/api/cli.html).
