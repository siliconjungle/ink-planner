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
