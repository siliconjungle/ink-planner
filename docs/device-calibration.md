# Explicit device calibration

`python -m ink_planner.gpu_measurements` compares already checked equality plans
on an explicitly requested browser GPU. It is separate from the installed
JavaScript/native automatic acquisition and selected-plan cache. It never runs
profitability probes inside an application.

Supply a checked core source, compiler, empty baseline plan, alternatives,
schema-1 training/holdout workloads, Chrome executable and fresh scratch directory:

```sh
python -m ink_planner.gpu_measurements \
  --compiler /path/to/ink --source source.core.json \
  --plan baseline.plan.json --plan alternative.plan.json \
  --training training.json --holdout holdout.json \
  --browser /path/to/chrome --scratch fresh-device-experiment
```

Node with built-in WebSocket support (22 or later), local Cargo and the Wasm Rust
target are required. No browser automation package is needed. All plans replay
through the compiler before literal WebGPU emission. CPU fallbacks build offline,
in release mode with two jobs, outside timing. Build durations, source, lockfile,
Wasm/artifact hashes and known Node/Rust controls are retained. Three fresh
browser processes per dataset are the default. Source, plans, compiler, browser,
adapter code, artifacts and execution controls are rechecked before success.
Every fresh process and both workload splits must expose the same observed
browser version and device context; an update or device change declines acquisition.
Do not edit those inputs during acquisition.

The original evaluator supplies every expected result and normalized typed input
fingerprint. Training and holdout retain one objective and must be disjoint both
as supplied calls and after source decoding. Each implementation owns a separate
input capture. Complete typed results and unchanged inputs are checked before
and after interleaved timing; every output is consumed inside the timed window.
Wide U64 JSON words and F32 raw bits are compared without floating rounding.

One observed hardware adapter creates the exact GPUDevice supplied to every
implementation. Fallback or unknown adapter status declines acquisition. The
adapter records browser-exposed identity, features, limits and required limits;
privacy-filtered model/description fields remain empty when unavailable. These
are cost provenance, not a unique device attestation. Independent hooks on this
device count actual compute dispatches, submissions and readbacks; reported
runtime counters must agree on dispatches. CPU fallback, fake GPU labels and
zero-device-work calls decline. The packed supplied-device ABI is required;
legacy scalar-pipeline/recursive/state/encoded-call adapters are outside this capability.

Samples measure full warm async calls: input capture/upload, GPU execution,
readback, result decoding, observation hooks and complete consumption. Browser
startup, device creation, module loading, first call (including lazy shader
setup) and validation are recorded separately. These do not establish cold-cache
or exclusive-machine performance, and the lifecycle objective must include setup
before choosing a deployment. A GPU rewrite comparison does not establish that
GPU placement beats the compiled CPU.

The receipt contains validated canonical observation proposals with a distinct
explicit-device bridge and canonical full-plan identities. Candidates must pass
every fresh process in training and holdout to receive proposals. They are not
relabelled as the automatic adapter's implementation measurement IDs. Whole
browser/renderer/GPU peak memory is **unknown** (`peak_bytes: null`), never zero
or an invented bound from buffer counters. A memory-constrained selection must
retain its baseline without suitable evidence. No automatic policy/cache changes
or backend correctness proof follow from calibration.

A terminal receipt declares the completed CPU build target safe to retire while
keeping selected artifacts, inputs/oracles, raw process samples and reproduction
sources. Observe experiment storage policy, exact Cargo source preflight and
existing timed compiler flags. Leave live workers and shared inputs intact.

The browser host applies one bounded budget across startup and the action. The
parent reserves 35 seconds outside that budget for normal shutdown of its owned
Chrome process. A timeout closes only the browser reached through its fresh
profile, observes process exit, then removes that profile. If shutdown cannot be
observed, the profile remains protected and the acquisition fails with its PID
and profile path. No unrelated workers or browser sessions are terminated.
