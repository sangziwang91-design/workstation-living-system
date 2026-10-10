## Owner-only frozen ablation switch

WLS now accepts `provider.owner_context_mode` with **enabled** (default) or **disabled**. The latter excludes `owner_context` memories *before* the canonical causal-memory candidate-window/top-k selection in real `LivingSystem.run_cycle` retrieval. It leaves all ordinary memories, goals (including imported explicit owner goals), skills, tools, and base planner configuration unchanged.

The legacy `provider.memory_mode=disabled` still disables **all** memories; it is NOT an owner-specific control. The new switch exists precisely because A/B comparing all memories against none cannot attribute gains to imported owner information.

Example, applied to two separate disposable WLS home directories with identical copied owner goals, ordinary memories, model settings, tools and task inputs:

```json
{"provider":{"type":"cognitive","fallback":"deterministic","owner_context_mode":"disabled"}}
```

This example only shows the provider field; preserve all other standard config fields in actual WLS configuration. Never disable owner permission checks, safety policy or holdout segregation to run an ablation.

### Interpretation

A matching pair of modes is **necessary but not sufficient** for a scientific comparison. The *actual* paired run must freeze source SHA, task IDs, independent outcome criteria, owner goal set, model ID, budgets, environment and random seeds (where available), and ensure neither arm receives hidden test cases or learns from the other. Publish only synthetic or aggregate anonymized receipts. In particular:

- Different decisions are not necessarily better decisions.
- Tool/NOOP acceptance is not verified real task completion.
- A model that receives private owner content may still disclose it through other explicitly enabled tools, so local privacy gates remain mandatory.
- This patch provides a tested control and does **not** claim a completed owner-specific holdout experiment or transferable autonomous learning.
