# Selecting without timings: the work model

Measured selection needs a paired timing run for every admitted candidate. A
work model is the alternative: predict each candidate's speedup from a
deterministic count of work, and select from the prediction alone. It is
only as good as the count's relation to real time. That relation is fitted
and then tested on held-out programs and inputs before the planner will use
it.

## Work features

`ink_planner/work_model.py` acquires two features for every candidate on the
workload's calls. Both are deterministic and machine independent.

| Feature | What is counted | How |
|---|---|---|
| `reference_fuel` | Reference-interpreter fuel of the plan's checked selected module | `ink plan-work CORE PLAN CALLS.json --core` |
| `target_iterations` | Loop iterations, function entries and sort work in the emitted JavaScript | The planner instruments a never-shipped copy of each artifact and runs it once per call (`count_work.mjs`) |

Both acquisitions check that every candidate returns the baseline's outputs
and leaves its inputs unchanged.

## Model and gate

A model is
`log(speedup) = slope × log(baseline work / candidate work)`, together with
the residual spread `sigma` of that fit.
- **Selection.** A candidate is chosen only if its lower bound,
  `exp(slope × log ratio − z × sigma)`, exceeds `1 + min_gain`
  (`z = 2`, `min_gain = 0.05`).
- **Ties.** The simplest plan with the largest predicted gain wins.

The planner refuses a model file unless its `validation.status` is
`validated`.

It uses a model only under all of these conditions:
- `--work-model` and `--work-workload` are both given;
- no timings or usage profiles are available;
- the default would otherwise keep the baseline.

Real timings always take precedence.

## Validation protocol

`bench/planning-corpus/work_model.py` reads a `measured.py --work` run over
the frozen corpus. For each program it does the following:
1. fit on the other nine programs' development pairs only;
2. let the planner's own `select` pick;
3. time the pick against the baseline on the program's 24 held-out inputs.

A model passes if all of these hold:
- it picks at least one program;
- no picked program loses on held-out geomean;
- robust held-out losses are at most 5% of the evaluated cells.

## Results (2026-10-13 corpus run)

| Feature | Fit on all programs | Leave-one-out picks | Held-out | Status |
|---|---|---|---|---|
| `reference_fuel` | slope ≈ 0, spread 0.55 (344 pairs) | none | – | **rejected** |
| `target_iterations` | slope 0.73, spread 0.26 (251 pairs) | spatial-collision | 1.53×, 18 gains / 0 losses | **validated** |

**Reference fuel does not predict JavaScript time.** The fastest drawing-hit
plan (3.3× on development inputs) does about 57× *more* interpreter work than
the baseline. The interpreter prices closures and fused steps very differently
from the target. This confirms the repository's warning that work counts
alone do not establish speed, and the gate rejected the model.

**Counted JavaScript iterations do predict time.** Development speedup
follows `ratio^0.73`; the best drawing-hit candidate cuts iterations 3.98× and
runs 3.51× faster.

**The validation is narrow.**
- The conservative bound picked one held-out program.
- drawing-hit and compact-set gains were left unclaimed: leave-one-out fits
  without those programs have a lower slope.
- More corpus programs with semantic wins are what would tighten the model.

## Use

```sh
python3 bench/planning-corpus/measured.py --compiler target/release/ink --work -o build/measured
python3 bench/planning-corpus/work_model.py build/measured -o build/work-model
python3 -m ink_planner.compiled_planning ... --target javascript \
  --work-workload calls.json --work-model build/work-model/work-model-target_iterations.json
```
