# Opt-in target component hypotheses

`ink-plan --cost-model path.json` accepts the existing schema1 operation-price
format unchanged. Schema2 prices a bounded vector from the **current candidate**
semantic bodies and the actual core function parameter signatures. It neither
admits a rewrite nor establishes target eligibility. Every candidate still goes
through the unchanged selection checker and exact dependency-closure replay.
No model is enabled by default.

Schema2 requires these fields:

```json
{
  "schema": 2,
  "target": "javascript",
  "boundary": "owned",
  "cardinality": {"default": 1024, "by_parameter": {"xs": 1024}},
  "weights": {
    "invocations": 0,
    "traversal_elements": 0,
    "allocation_events": 0,
    "allocation_bytes": 0,
    "copy_bytes": 0,
    "scalar_ops": 0,
    "div_rem": 0,
    "branches": 0,
    "loop_body_pressure": 0,
    "gpu_dispatches": 0,
    "upload_bytes": 0,
    "readback_bytes": 0,
    "unknown_work": 1
  },
  "limits": {"nodes": 8192, "depth": 48, "helper_depth": 16}
}
```

The example is a schema illustration, **not calibrated prices**. All components
require explicit coefficients. Coefficients are finite, nonnegative ns numbers
(up to 1e9); floats are permitted, booleans are rejected. `unknown_work` must be
positive. Cardinalities are integer hypotheses from zero to 1e9, looked up by
actual public parameter name. Limits are positive integers capped at the
illustrated values; omitting `limits` uses those caps. Counts saturate at 1e18.
Unknown fields, components and malformed values fail validation.

`TargetCostModel.estimate_function(name, bodies, core_program)` returns one
entry's vector and score. `estimate_module` sums independent entry estimates for
all current named functions. Helpers expand from that same candidate's body map,
using their declared parameter names bound to actual argument values. A helper
rewrite therefore immediately changes its callers' vectors and consumed
body/signature digests. Digests disclose provenance and confer no authority.
Lambda applications evaluate their argument once; bound reads share that value.
Callbacks retain lexical captures and shadowing. Two-input zip uses the shorter
estimated extent; concat sums extents and preserves empty inputs. Filter output
uses its input upper bound rather than invented selectivity. Unknown branches
use a componentwise maximum of their arms; known boolean branches and boolean
short-circuiting avoid the inactive arm. The componentwise maximum is an upper
shape estimate and may combine costs from different mutually exclusive arms.

Each entry counts one `invocations` event, regardless of how many source helpers
are expanded. Collection loops count traversal and expanded callback work.
`loop_body_pressure` is extent times the square of callback scalar/div-rem/branch
and unknown work. It is an untrusted scheduling/optimization-shape proxy, **not a
measurement of registers, vectorization or throughput**. Separate staged loops
can have a smaller pressure vector than a single fused loop. Zero coefficients
retain that feature as unidentified rather than creating a default preference.

Allocation and copy bytes describe flat logical carriers (32-bit words/floats,
64-bit words, or booleans). JavaScript object representations, spare capacity,
physical allocator events, fresh receiver reuse and destination lowering may
differ. Producers conservatively retain their logical allocation events; the
model does not claim to mirror the source-facts emitter's reuse decisions.
A borrowed alias returned at an owned boundary adds an output allocation/copy;
at an into boundary it adds a copy only. Into capacity and disjointness are ABI
preconditions; the planner introduces no runtime caller checks. Cardinality
hypotheses are not target capacity validation.

Boundaries are `owned`, `into`, `resident_owned`, `resident_into`,
`gpu_upload_readback`, `gpu_resident_readback`, and `gpu_resident`. Residency
labels keep incompatible measurements separate; they do not infer runtime
residency. GPU labels `gpu`, `webgpu` and `wgsl` enable semantic stage hypotheses.
Only `gpu_upload_readback` charges all list-argument uploads; the two readback
boundaries charge the logical result readback. `gpu_resident` charges neither.
This is a declared boundary model; parameters are transported independently,
without assuming alias deduplication.

Every GPU estimate explicitly reports physical lowering unknown. A semantic loop
is not necessarily one device dispatch: literal filter rank/compaction, serial
scan and ordered reduction, transfers, graph staging and fallback can differ.
Filter and ordered-work stages add unknown-work fallback counts. No vector
asserts GPU eligibility, workgroup counts or measured parallel kernel cost.

Recursive helpers, unsupported operations, unresolved values/callbacks, unknown
layouts and exhausted analysis budgets retain positive fallback work and report
reasons. An iterative preflight bounds nested helper bodies before recursive
analysis or digest serialization. Receipts expose `complete`, unknown reasons,
assumptions, consumed current definitions and an explicit completeness scope.
Complete means supported bounded source analysis, not exact physical lowering.
Search emits both `initial_components` and `selected_components`, alongside
unchanged proof admission receipts and the model file's hash.

Calibration belongs in knowledge-owned data. Frozen training cells, unidentified
columns and heldout errors must accompany profitability claims. Neither a price
file nor a source-component estimate is measured performance evidence.
