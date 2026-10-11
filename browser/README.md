# Browser pipeline cost search

`pipeline-selection.mjs` is the independently versioned planner asset installed by
Ink distribution assembly into packed browser bundles. It imports the generated
runtime's `pipeline-contract.mjs` for physical scopes/features; it has no semantic
checker or device operations. The distribution's runtime integration tests copy
both assets to the same bundle directory before loading them.

`createPipelineSelector` performs bounded beam search over saved contiguous
intervals. It compares main/GPU/worker critical paths with measured cold setup,
expected lifetime, availability and surviving worker capacity. Missing full CPU
comparisons, unknown domains, unmeasured cold alternatives and inadequate saving
retain baseline. At most 256 models, 40,960 expansions, beam width 32 and 32 cached
decisions bound work. Profiles and receipts never grant proof authority.

`fitPlacementProfile` is an explicit offline nonnegative affine upper-envelope
fit with a conservative margin. Runtime training measures full captured-segment
calls and fresh-engine startup; complete heldout requests independently gate
release. See Ink's `docs/adaptive-execution.md` and
`bench/adaptive-placement/browser.mjs`. The historical broad losing models remain
separate; this asset does not enable them.
