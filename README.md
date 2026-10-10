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
