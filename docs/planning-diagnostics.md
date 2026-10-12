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
