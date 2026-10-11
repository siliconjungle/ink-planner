# Ink planner

Untrusted search, cost selection and routing for [Ink](https://github.com/siliconjungle/ink-lang). This repository can propose programs and execution plans; only the semantic core can admit their proof evidence. It neither defines language semantics nor emits target code.

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
workload features, nonnegative prices and intersected observed domains; hosts
never extrapolate or run training probes. Fitted models retain their observation
IDs in knowledge's `store/cost-models`, outside mathematical admission. Structural
search defaults come from knowledge's `structural-costs.json` and are unmeasured
hypotheses, never evidence of speed or correctness.
