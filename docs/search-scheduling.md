# Search scheduling and effort

Planning should spend its budget finding and checking alternatives. It should
also make progress when one producer is slow or one rewrite family is prolific.

`ink compile --optimise KNOWLEDGE` uses balanced effort by default:

| Effort | Search time | Proof attempts | Retained states | Semantic attempts | Semantic time per producer |
| --- | ---: | ---: | ---: | ---: | ---: |
| quick | 8 s | 2,048 | 32 | 2,048 | 8 s |
| balanced | 60 s | 32,768 | 512 | 8,192 | 30 s |
| thorough | 300 s | 131,072 | 4,096 | 32,768 | 120 s |

```sh
ink compile example.ink --optimise knowledge --target javascript \
  --planning-effort thorough --measure-workload calls.json -o example.mjs
```

Explicit `--planning-seconds`, `--search-budget`, `--state-limit`,
`--semantic-budget` and `--semantic-seconds` override the respective preset.
Two recipe processes run simultaneously by default; `--recipe-workers 1` selects
serial production, and the maximum is eight. These are ordinary producer
processes. Search never downloads executable code from catalogue entries.

The old direct planner default allowed an hour of exploration. Named efforts
replace that ceiling with practical defaults; thorough provides more exploration
than the eight-second settings used in the generalisation experiment. Complete
compilation includes index preparation, reference execution, proof replay,
emission and optional offline measurement. Search time is not a deadline for
all those stages; checker and measurement calls have separate limits.

Recipes flush complete proposals as they author them. Semantic search exports
admitted alternatives immediately, while continuing to explore. The planner
consumes one available row per running family per pass and independently checks
each proposed edge. A slow family no longer holds already available output until
its process exits. Missing recipe inputs decline before a process is launched.
Workers and descendants are stopped when search closes or reaches a limit.
Only a complete, valid worker stream enters the per-compilation recipe cache.
Partial results still need ordinary proof replay and are not cached as completion.

Typed-head reachability uses one read connection and batches successor-head
queries. It includes heads introduced by earlier laws and wildcard roots.
Missing indexes retain conservative canonical discovery. Warm planning reuses
indexed immutable snapshots through `Store.ensure_index`; explicit refresh and
rebuild remain available for repair and importing externally copied observations.
Canonical bytes and membership witnesses authenticate during export, and
the checker replays selected mathematical dependencies.

The matcher compiles structural constraints below the root and reuses results
for exact unchanged subtrees. Filters skip parameter holes and retain binder-aware
matching. A disposable LRU is limited to 4,096 subtrees and one eighth of the
search memory estimate; its serialized bytes count towards the search budget.
Replacing a subtree copies ancestors and shares unchanged siblings. Search treats
terms as immutable. Cache identities, prices, indexes and scheduling do not
establish proof authority.

Receipts record effective budgets, per-family counts and time, checked states,
rejections, stop reasons and retained serialized bytes. Semantic receipts record
structural rejections, full matches, cache hits and evictions. Serialized sizes
are resource estimates, not exact Python heap measurements.

Correctness, backend eligibility and profitability remain separate gates. Wider
search can increase measurement cost. It does not make a candidate profitable;
missing comparable evidence retains the baseline. A small state limit may be
reached before a time limit, so increase both when seeking broader exploration.

## Research used

[Relational E-Matching](https://arxiv.org/abs/2108.02290) motivates using pattern
structure to narrow matching work. [Egg's scheduler](https://docs.rs/egg/latest/egg/struct.BackoffScheduler.html)
shows how to control resource use by prolific rules.
[Egglog](https://ztatlock.net/pubs/2023-pldi-egglog/2023-pldi-egglog.pdf) motivates
reusing work when a small part of search changes. Ink uses exact typed subtrees
and ordinary proof-producing rewrite steps here.
