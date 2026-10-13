# Finding where automatic optimisation stops

Ordinary compiled planning writes a `diagnostics` object in the plan receipt.
It identifies each recorded candidate's last gate: backend capability,
measurement failure, measurement budget, missing comparable evidence, measured
memory, profitability uncertainty, or selection. Search limits and incomplete
recipe results are separate from those candidate gates.

For an existing receipt:

```sh
python -m ink_planner.planning_diagnostics plan.json.receipt.json -o diagnosis.json
```

An optional `--expect opportunities.json` accepts a list of `{ "plan": "<sha256>",
"label": "..." }` objects from a separately checked capability experiment.
This reports whether those exact plans were reached. A different proof chain can
implement the same algorithm, so an absent plan identity does not establish that
an equivalent implementation was missed. Expected identities do not enter
search, proof admission, measurement, or selection.

These diagnostics describe recorded observations. They never grant proof
authority or claim exhaustive applicability. A time-limited search can retain
the baseline even when useful laws exist. Synthetic selection inputs also do not
establish profitability for arbitrary user workloads. Full receipts preserve
original rejection reasons; the command-line summary remains compact.

Cached receipts label search and cost observations as historical. `fresh_work`
distinguishes those observations from the selected packet's reported fresh proof
replay. Omitted candidate/edge/rejection lists remain unknown, rather than being
reported as zero newly examined alternatives. Baseline identity can be recovered
from the ordinary empty, source-bound initial packet even in a compact receipt.

## Near misses and the planning funnel

Semantic search records, per law, how many proposals matched a program position,
how many the checker examined, admitted or rejected, and how many were never
checked within the search budget (`ink_planner/near_misses.py`). For each
rejection it keeps the checker's gate: a failed premise (the core now names the
premise index and law in its rejection message), an endpoint mismatch, a closure
that could not be transported, or a checker budget. A failed premise is recorded
twice: as the law's condition with its parameters erased, and as instantiated in
the program, so repeated shapes show which program facts were missing.

`python -m ink_planner.semantic_search ... --diagnostics near-misses.json` writes
the receipt. The search flushes it after each round, so a search cut off by an
outer budget still leaves its observations. The `semantic-laws` recipe forwards
it as a `diagnostic` row, and compiled planning merges it into the receipt's
`diagnostics.semantic` and `diagnostics.funnel` sections, together with the
seconds each recipe spent (`diagnostics.recipe_seconds`).

These are observations of one bounded search. A law absent from them may still
apply, and a rejection does not show that the law can never apply.
`ink_planner.corpus` aggregates them over a fixed program corpus
(`bench/planning-corpus/`).
