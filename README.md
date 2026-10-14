# Ink planner

Untrusted search, cost selection and routing for [Ink](https://github.com/siliconjungle/ink-lang). This repository can propose programs and execution plans; only the semantic core can admit their proof evidence. It neither defines language semantics nor emits target code.

Use an optimized compiler (`cargo build --release --bin ink`) for planning-time
measurements. Checker deadlines include request transfer and proof replay; a
debug build can exhaust them on a proof that an optimized build checks promptly.
Deadline-limited candidates remain unchecked, not mathematically rejected.
Compiled search reports `unchecked_checks` and stops with `checker-time` when
these are the only remaining incomplete work. Such results never enter the
selected-plan cache, and other proposals may still be checked and selected.

Install `ink-knowledge` first, then `python3 -m pip install -e .`. The distribution pins both repositories; its source entry points `plan.py` and `route.py` use the installed package or explicitly configured/pinned knowledge checkout. `INK_KNOWLEDGE_ROOT` selects a separate installation.

```sh
ink-plan --compiler /path/to/ink --core program.core.json \
  --knowledge-root /path/to/ink-knowledge \
  --snapshot /path/to/ink-knowledge/store/snapshots/SNAPSHOT_SHA256.json \
  --budget 128 -o selection.json
/path/to/ink check-selection program.core.json selection.json
```

The knowledge API discovers laws by typed result sorts present in the program. Search matches below binders, composes rewrites, discharges conditions with supporting proofs, and checks every candidate in a bounded checker session. Rules and combinations come from data. Candidate cost is a structural estimate, explicitly separate from measured physical performance. Unsupported candidates or exhausted search retain the unchanged program.

A selection uses `ink-evidence-v1`: the exact input core ID, an authenticated pinned knowledge view and ordered applications carrying their substitutions and premise proofs. Replay rechecks all evidence against that input; it requires no live database, filenames or latest snapshot. The old catalogue selection wire is not accepted. `schemas/selection.json` describes this envelope; individual semantic proof terms are versioned by the core.

`ink-route` creates literal pure source graphs, with explicit placements or an unchanged CPU baseline. `ink-select-route` checks every candidate, then consults `Store.observations` only when `--knowledge-root`, `--target TARGET.json` and `--workload WORKLOAD.json` supply matching provenance. No comparable baseline evidence means baseline selection. A cheap invalid candidate still rejects. Proved graph alternatives use the same pinned knowledge view and semantic proof calculus.

`ink_planner.search` is the bounded search API; `ink_planner.source_routing.produce` is the graph API. Target packages and execution domains are opaque names to the proof checker. Physical scheduling, profiling, GPU/device operations and fallback belong to `ink-runtime` and device adapters.

`research/` contains the historical scalar/collection search experiment. It is outside production discovery and is retained to reproduce regression evidence.


Semantic search marks a resulting term state as seen only after the compiler
accepts its proof package. A rejected conditional law cannot suppress another
valid proof reaching the same result. `tests/test_semantic_search.py` covers this
case and preservation of the baseline on rejection/budget exhaustion. Matching,
costs and deduplication remain untrusted; every selected step is replayed.

The CLI exposes `--rounds 0..64` (default 12) independently of the unchanged
`--budget 0..4096` proof-check bound. Larger modules may need more than twelve
independent rewrites; `--rounds 32` permits those while retaining checked
admission and the baseline fallback. The receipt records `round_limit`.

## Measured physical representation selection

`ink-select-representation` (or `select_representation.py`) checks a baseline and
a bounded candidate index with the actual Ink compiler, then ranks comparable
measurements of a supplied lifecycle profile. Candidate indices contain request
paths and optional bridge paths, resolved relative to the index file. Maximum
candidates is 32; per-check timeout is bounded to 60 seconds. Checker inputs are
staged from the exact bytes later written to the selection output.

```sh
python3 planner/select_representation.py --compiler target/debug/ink \
  --source examples/point-query-delta.ink \
  --baseline knowledge/research/point-delta/refresh.json \
  --candidates reports/incremental-maintenance-selection/candidates.json \
  --knowledge-root knowledge \
  --target reports/incremental-maintenance-selection/target.json \
  --workload reports/incremental-maintenance-selection/workload.json \
  --profile reports/incremental-maintenance-selection/profile.json \
  --migration-count 1 -o build/selected-representation.json
```

Profile keys are initialise, teardown, check, compile and action:NAME, with
nonnegative integer counts. Missing required phases exclude alternatives; missing
comparable baseline phases retain the checked baseline. Exact rational pooled
medians determine weighted costs, with ties retaining the baseline. Migration
requires a checked bridge and charges its measured cost. Phase observations retain
the supplied workload shape/distribution/concurrency/residency and exact target
provenance. Distribution becomes `{workload: ORIGINAL, phase: PHASE}`.

The measurement plan ID is SHA256 of the exact request file, binding witness
and closure costs. Migration phases use `migrate:BASELINE_REQUEST_SHA:BRIDGE_SHA`.
`--memory-limit BYTES` constrains the maximum measured peak across required
regions; unknown memory is not zero. An infeasible baseline memory constraint
fails explicitly. Measurements do not guarantee a future process memory limit.

The output is the exact frozen checked request, plus a checked bridge when switching
and a `.selection.json` receipt containing hashes, evidence IDs and weighted costs.
Emit/execute commands replay through the checker. This is explicit external
selection; there is no live adaptation or performance authority in the core.

Discovery objects are untrusted search inputs. The planner validates each proposal
and final result with the exact selected rewrite/premise dependency closure,
using at most eight cached checker sessions. Unused discovery laws do not consume
a shared admission budget and are omitted from final proof authority. Every
chosen law is still checked; an invalid or over-budget closure rejects the
proposal. Discovery counts and checked roots are recorded separately.
`--compact-output` controls JSON formatting; both output forms contain only the
selected closure.

Proposal costs are ranked before bounded checker work. Search checks only enough admitted alternatives to fill the next beam; rejected proofs do not suppress other proofs of the same body. Generated proposals and checked attempts are reported separately.

Type-indexed discovery pages through snapshot candidates independently of the rewrite-check budget. The budget bounds proof attempts, not the subset of law IDs that can be discovered.

Optional target component models and their source/physical boundaries are documented
in [docs-target-components.md](docs-target-components.md). Schema1 pricing and default
structural ranking remain unchanged.

Offline cost profiles use `python -m ink_planner.cost_profiles --knowledge-root
KNOWLEDGE --rows ROWS.json --target-key TARGET -o PROFILE.json`. Each row names an
immutable observation and its exact compiled scope. The fitter uses explicit
workload features, nonnegative prices and intersected per-feature observed domains.
New schema-3 profiles additionally carry each backend's exact convex hull of
observed feature combinations, including affine equalities for correlated data.
The shared Rust/Wasm consumer rejects unsupported combinations using bounded
integer constraints, without training probes or geometric search at execution.
Construction limits fail authoring rather than widen the domain. Convex support
limits extrapolation; it does not establish prediction accuracy, confidence or
profitability. Older schema-1/2 profiles retain only per-feature range checks.
Fitted models retain their observation
IDs in knowledge's `store/cost-models`, outside mathematical admission. Structural
search defaults come from knowledge's `structural-costs.json` and are unmeasured
hypotheses, never evidence of speed or correctness.

Optional offline emitted-work ranking over already checked alternatives is described in [docs-physical-features.md](docs-physical-features.md). It leaves the default semantic search unchanged and retains the baseline for missing/incomparable target evidence.

## Database implementation candidates

`ink-select-implementation` (`select_implementation.py`) compares supplied
canonical implementation entry IDs through the existing selection and physical
representation proof protocols. An ordinary program can be published as an empty
selection package and has the same checks as other candidates. The compiler
contains no source-versus-cache selector. All seven lanes bind separate measurement
identities to the candidate and exact compiler; lifecycle frequencies, setup,
portable migration and measured memory enter external ranking. Missing comparable
evidence retains the supplied baseline, which can itself be any admitted candidate.

`--replay selected.json` authenticates the recorded database snapshot/package,
rechecks proofs and invokes existing lowering commands. Catalogue entries cannot
provide scripts or executable commands. See the distribution's
`docs/state-choice.md` and knowledge's `research/implementation-candidates`
publication for usage and trust limits. Representation-only selection retains
its explicit checked direct-bridge interface.

See the distribution’s `docs/storage-bulk-incremental.md` for the checked chunk/index
and Boolean maintenance producers, bulk collection laws, overlapping proof admission,
optional search-hint cache and incremental discovery index. Optimisation evidence
remains untrusted; source-bound proof replay and measured selection are separate.

Without an explicit `--candidates` file, implementation selection now discovers
source-bound database packages and automatically proposes checked indexed and
grouped alternatives through knowledge's generic producer. `--no-proposals`
limits selection to published candidates. Explicit pools retain their previous
behavior. Producer subprocesses are bounded; failed proposals are reported and
leave the baseline available. Recipe caching binds source, exact compiler,
producer code/data and budgets; checker admission is repeated on every selection.
The existing lifecycle ranker and backend/workload observations decide whether
batching is profitable. Missing evidence retains the baseline. Reproduce cold
proposal, grouped/indexed/baseline selection and exact replay with the distribution
integration test `tests/automatic_candidates_integration.py` and its fixture.
Scalable table profiles use separate `action:batch`, `action:scan`,
`action:intersection`, `action:erase` and `action:abort` phases alongside setup,
proof checking, compilation, checkpoint and restore. The caller supplies bounded
counts and exact workload cohorts. A batch sample is not a scalar-update sample
multiplied by input length: duplicates, overlap and rollback can change its cost.
These existing generic lifecycle phases need no compiler-specific pricing rule.

## Optional probabilistic search and compiled kernels

`ink-plan` retains ordinary deterministic search by default. Opt-in
`--search-strategy stochastic --search-seed N --search-restarts 4`
shares one bounded candidate-check budget across restarts and retains an ordinary
first lane. Fixed structural scores and seeded randomness order proposals.
`--search-trace TRACE.json` records checker outcomes and effort for diagnostics.
The learned ranking model, its training command and `--search-model` are removed.
All proposals and cached recipes still replay through
the independent source-bound checker; failed proposals retain the baseline.

`ink-synthesize --compiler INK --core CORE --function NAME --output-store STORE
-o SELECTION.json` proposes bounded pure u32 arithmetic expressions. Affine probes,
random candidates, exact sample vectors and counterexamples reject bad proposals;
ordinary universal word-ring proofs admit replacements. This is not general
program synthesis, float equivalence or a transactional state proof.

`ink-plan-parallel` runs independent planner/checker processes with a shared total
proof budget and separately rechecks the selected package before publication.
Worker startup and proof costs count in complete planning timings. More workers
are not automatically faster.

`ink-autotune` provides bounded offline Gaussian-process configuration exploration
over supplied complete-call observations. Correctness, device fallback, explicit
setup cost, expected call count and independent heldout evidence gate alternatives.
The CLI without heldout evidence retains the baseline. The Python API accepts a
heldout callback. Receipts do not install runtime profiles or grant eligibility;
existing proof and target-context admission still apply.

`--discovery-accelerator` enables knowledge's authenticated in-memory exact index.
`--ann-hints` optionally uses HNSW structural neighbourhoods to order proposals.
Install the optional `ann` extra for hnswlib 0.8.0; neither NumPy nor HNSW is a
default dependency. The 64-dimensional deterministic structural sketch is not a
learned semantic embedding. Full typed discovery and exact matching remain;
missing libraries or index failures retain ordinary ordering. Approximate recall
can affect search quality at a fixed budget, so multi-restart search keeps an
ordinary lane. These disposable indexes confer no proof authority.

Packaged `kernels/planner.ink` and `hosts/planner-kernels.mjs` execute bounded word
feature scoring and candidate evaluation through ordinary compiled Ink backends.
`--selfhost-endpoint http://127.0.0.1:PORT` connects stochastic search to an
optional loopback host; scores are independently compared and failures use local
ranking. Synthesis supports the same host for one-register word evaluation. Python
still coordinates graph search and the independent checker remains separate.
No runtime profitability trials are added. See the distribution's
`docs/probabilistic-planning.md` and `bench/probabilistic-planner` for commands,
actual hardware GPU receipts, retained losses and setup costs.

`ink-plan-adaptive --compiler INK --core CORE --snapshot SNAPSHOT --knowledge-root
STORE --target-context TARGET.json --profile PROFILE.json -o SELECTION.json`
chooses a search approach from saved offline evidence. Observed cohorts bind
catalogue size, source node count, budgets, exact source/snapshot, planner and
producer code/data, compiler, Python and caller-supplied target context. There is
no extrapolation to an unseen size or catalogue pin. Alternatives need more than
10% gain in complete-call training and each independent holdout, with no loss
in checked selected structural cost. Setup already enters child timings. Missing
or stale profiles retain ordinary search. Wrapper overhead is measured separately;
these receipts guide compilation and never change mathematical admission.

Source contract refinement uses the ordinary knowledge recipe registry and
`ink-evidence-v1` package checker. `contract-witnesses` proposes postcondition
witnesses and the core rechecks every postcondition, invariant and caller.
Both compiled planning and implementation selection accept `--contract-proofs`
for source input; checked core input already carries its evidence. Candidate
publication includes the dependency closure of embedded pure and state proofs,
including inside composed plans. Missing comparable performance observations
retains the baseline; a proof of correctness is not a speed estimate.

## Streaming planning and effort

Compiled planning now admits proposals while producers continue running. Two
bounded recipe processes share the budget, and semantic matching reuses exact
unchanged subtrees. Quick, balanced and thorough settings make the exploration
budget explicit; individual flags override the preset. See
[search scheduling](docs/search-scheduling.md) for limits, cache behaviour and
research references. Wider discovery still requires independent proof replay and
comparable performance evidence before selection.

## Conservative offline calibration

Fresh pure JavaScript calibration runs three independent processes by default.
Every candidate must preserve inputs and match the reference in every process.
An alternative is chosen only when its largest observed weighted phase cost
beats the baseline's smallest by at least 10%; these sample extrema are an
empirical guard, not statistical confidence or an extrapolation model.
`--measure-holdout` supplies disjoint typed inputs with identical objective
weights. It may veto the frozen choice to baseline and never chooses another
candidate. `--measure-processes`, `--measure-timeout` and
`--minimum-measured-gain-ppm` make the acquisition budget and margin explicit.
Module import and validation costs are reported separately from warm calls.
This explicit multi-process and typed-holdout path uses pure JavaScript calls.
The automatic path below also supports Rust JSON calls. Neither path measures
state or migration lifecycles or exact peak memory. General lifecycle ranking
can still consume externally supplied observations. No runtime probes or learned
search are introduced.

## Automatic implementation choice

Ordinary compiled planning refreshes installed database proposals on admitted
intermediate syntax and tries bounded alternative support certificates. Every
application and complete plan still passes the independent checker. Structural
costs only order discovery; they do not establish correctness or profitability.

Without supplied lifecycle evidence, JavaScript and Rust compilation can acquire
bounded synthetic typed probes from knowledge's `automatic-selection.json`.
Source-diverse checked candidates undergo interleaved complete-output measurements
and independent reference/input checks. Setup enters the lifecycle estimate; a
comparison-adjusted paired uncertainty guard requires at least 5% improvement.
Synthetic calls do not describe actual user usage, and some input regimes can
regress. Unsupported types, stateful lifecycles, targets, memory constraints or
migration retain the baseline unless comparable supplied evidence supports a
choice. `--measurement-mode off` disables synthetic acquisition;
`--measure-workload` supplies explicit development calls. No selection packet is
required. Pure JavaScript calls and Rust's JSON-call ABI are distinct boundaries.

The bounded compiled-plan cache binds source, exact snapshot, compiler, code,
policy, host, options and supplied evidence. Every hit freshly replays the full
selected chain and target before emission. Historical search/measurement costs
are marked separately in diagnostics. `--cache off` requests fresh planning.
The distribution forwards this as `--planning-cache off`.

Development checks: `tests/automatic_measurement_integration.py`,
`tests/composed_templates_integration.py`, support-certificate/matcher/selection
unit tests and the distribution's `automatic_compilation` cross-target suite.
The frozen sixteen-program experiment, raw regressions and reproduction tooling
are published in the distribution's `reports/automatic-planning-2026-10-13`.

## License

Project code and documentation are [MIT licensed](LICENSE). Third-party dependencies,
vendored projects and research data retain their own licenses and notices.

## State lifecycle acquisition

JavaScript source-state candidates can use explicit schema-2 workloads with
`--measure-workload` and an independent `--measure-holdout`. The installed
`producers.maintained_folds` recipe proposes proof-checked maintained maps and
bounded dense prefixes. Every proposal is replayed before measurement.

```json
{
  "schema": 2,
  "setup": [{"call":"put","args":[0,7,true,false]}],
  "phases": {
    "load":{"kind":"module_load"},
    "initialise":{"kind":"initialise"},
    "update":{"kind":"history","calls":[
      {"call":"put","args":[0,9,true,false]},
      {"call":"total_query","args":[]}
    ]},
    "checkpoint":{"kind":"checkpoint"},
    "restore":{"kind":"restore"}
  },
  "profile":{"load":1,"initialise":1,"update":10000,"checkpoint":1,"restore":1},
  "workload":{"shape":{"rows":1},"distribution":"explicit","concurrency":1,"residency":"restored"}
}
```

Each history resets to the same portable seed outside its timed window. The
independent source evaluator supplies all results, errors, versions, events and
exact snapshots. These must match before and after timing, and captured inputs
must remain owned. Each candidate is measured in separate fresh processes; peak
RSS includes the host and adapter. Setup, module load, checkpoint and restore costs
have explicit weights. Missing comparable evidence retains the baseline.

Holdout histories must differ from training transition histories, although setup
and parameterless reads can recur. Holdout accepts or rejects the frozen winner.
There are no application-time profitability probes. Current limits are 4,096 setup
calls, 512 history calls, 64 phases, 1–8 processes and a 16 MB input workload.

### Encoded call usage profiles

Offline workload schema 3 adds a boundary field to each function/args call:
trusted, portable or compact. Encoded inputs are prepared outside the timed call;
timing includes validation, execution, complete reply encoding and consumption
of every reply byte. Independent source execution supplies expected values.
The adapter checks exact replies and unchanged input bytes before and after
measurement, and rejects unsupported transfer data or targets.

The offline JavaScript adapter uses tagged measurement packets to preserve every
integer bit and negative zero without colliding with application record keys.
Checked portable/compact probes include inhabited recursive records, options,
results, fixed numeric vectors, exact words/Int, enums and identifiers. Recursion,
value counts and the first nested sequence expansion are bounded. Unsupported
trusted-call types remain excluded from that lane. These are cost inputs, not
proof assumptions or observed customer usage.

JavaScript automatic probes obtain their boundary mix from knowledge's
automatic-selection.json. This is a bounded general usage model, not observed
customer behaviour. Discovered observed profiles retain precedence. The
database's boundary-readers recipe supplies implementation choices; no recipe
dispatch or JSON recognition is added to the planner. Holdouts must contain new
semantic inputs; switching the same value to another transport is insufficient.

`python -m ink_planner.lifecycle_analysis replay.json -o analysis.json` performs
bounded offline hindsight analysis of comparable state-history windows. It
compares fixed choices and migration-charged schedules, optionally enforcing
measured memory/pause budgets. Counterfactual windows require the same logical
prefix and at least three measurements per variant. Hindsight, choice gaps and
execution residuals are diagnostics, never a runtime policy or proof premise.


### Resuming corpus evaluations

`python -m ink_planner.corpus` saves the compiler, catalogue, recipe, source and
manifest identities alongside its execution environment. Resume and report-only
operations require the same recorded context. Older reports without an environment
record remain historical evidence; they cannot be resumed into the new format.

Environment capture runs in a fresh interpreter using the same environment
construction as corpus workers. It records Python/ABI/SQLite identity, selected
import locations and actual files for the proof and optional discovery dependencies
(`python-sat`, `six`, `numpy`, `hnswlib`, including the separate `pysolvers` module).
Package version strings and RECORD entries alone are insufficient: edits to the
installed files also change the context. JavaScript binds Node bytes/version
response; Rust binds rustc and Cargo bytes/version responses, including rustup
proxy responses. Host identity and a small explicit set of execution controls
are recorded; control values are hashed, and unrelated environment variables
are omitted. Package discovery does not import or certify optional native solvers.

This records selected execution components, not a hermetic machine snapshot.
It does not bind every system library, Cargo configuration file, hardware driver
or arbitrary future plugin dependency, and it does not remove load, scheduling,
random-seed or timing-budget variability. Extend the component list when adding
execution dependencies. A missing package is recorded as missing rather than
silently equated with an installed package; proof admission remains independent.
