# Search modes: equality saturation and modular planning

Both modes are untrusted search. Every application chain they produce is
checked by the compiler exactly like the beam search's.

## Equality saturation (`--egraph`)

`ink_planner/egraph.py` builds an e-graph per function from the subject.

**Saturation.** Every unconditional, first-order catalogue law (no applied
function variables) is applied at every match until the graph saturates or
reaches 4,000 nodes.
- Pattern variables bind to classes.
- A variable under a pattern lambda takes its class's cheapest term, extracted
  as the ordinary matcher would.
- Each instance is therefore a law instance on concrete terms, and unions are
  sound with respect to the laws.

**Extraction.** The cheapest program is extracted under the same structural
cost model the beam uses.

**Chain.** The extracted program is only a target. A guided search rebuilds an
ordinary chain towards it, using only the laws that caused unions, ranked by
tree distance to the target. Every step is checked. When the target cannot be
reached, the cheapest admitted state on the way is kept.

**Budget.** Saturation gets a third of the time and proof budget. The beam
runs with the rest, and the cheaper checked result is kept.

Results on the planning corpus and `examples/collection-pipelines.ink`, with
30 s per search (`bench/search-modes/compare.py`):

| Program | Initial | Beam | E-graph + beam | Kept |
|---|---:|---:|---:|---|
| 10 corpus programs | – | – | equal to beam on all 10 | beam |
| collection-pipelines (15 functions) | 1311 | 996 | **923** | e-graph |

The corpus programs are single small functions, where the beam already reaches
the best form. On the 15-function pipeline module, saturation finds cheaper
targets for 12 functions. For 9 of them, a one-step chain reaching the target
is checked. For 3 (`converted_length`, `mapped_length`, `pipeline_fold`), no
admitted step leads to the extracted target. Their targets depend on unions
that law instances alone cannot replay as one chain (for example, rewrites
under binders taken from a class representative).

## Modular planning (`--modular-cache DIR`)

`ink_planner/modular.py` searches each function on its own, with an equal
share of the budgets, and concatenates the chains.

**Cache key.** A function's key binds:
- the compiler, the knowledge snapshot and the search options;
- its semantic body;
- the core definitions of every function it can reach;
- the records and enums.

**Hints, not authority.** A cached chain is only a hint. The combined chain is
checked against the actual module. If it is rejected, every function is
searched again and the cache is rewritten.

On `collection-pipelines`:

| Run | Structural cost | Seconds | Cache hits / misses |
|---|---:|---:|---|
| Whole-module beam | 996 (from 1311) | 45.3 | – |
| Modular, cold cache | **606** | 11.7 | 0 / 15 |
| Modular, warm cache | 606 | 4.5 | 15 / 0 |
| After adding a function | 622 (from 1363) | 4.9 | 15 / 1 |

Per-function search is both faster and better here. In one module-wide beam,
the functions compete for the same width, so most never get explored. A warm
run spends its time on setup and the final combined check. Adding a function
re-plans only that function.

## Limits

- **Saturation scope.**
  - Saturation uses only unconditional, first-order laws.
  - Higher-order patterns (applied function variables) and conditional laws
    stay with the beam.
  - Explanations are reconstructed by guided search, not recorded proof
    forests, so some extracted targets are unreachable.
- **Modular alternatives.** Modular mode returns one combined chain. The
  compiled planner's layered alternatives still come from the module-wide
  search, so modular mode is a separate entry point for incremental re-planning
  rather than a replacement for the default recipe.
- **What is measured.** Costs here are the structural estimate, not runtime.
  Selection between the resulting plans is still made by measurement or a
  validated work model.
