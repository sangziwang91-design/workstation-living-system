## Owner context influence: evidence before claims

The WLS owner context bridge now exposes a bounded `owner_context_effect` summary in canonical `wls status` and `wls life-state`. It reads existing `memory_decision_attributions` and validates memory IDs against the canonical `memories` table. It never exports the owner's text, source references, private file bytes, or retrieved-memory summaries in this public report.

### Metrics that can actually be observed

- `active_owner_memories`: how many imported owner-context memories exist locally
- `owner_linked_decisions`: decisions whose stored memory ID set includes a real owner-context memory
- `owner_only_memory_decisions` versus `mixed_memory_decisions`: confounding from non-owner memories
- `all_memory_counterfactual_changed`: stored difference compared with the **all-memories-disabled** counterfactual
- `observed_task_success`, `observed_task_failure` and `unmeasured_outcomes`: available outcome labels, not proof of causation

All are local, observational signals; at most the latest 500 decision records are scanned in normal status output. If no private bundle or attributable use is present, the report explicitly says so.

### What **must not** be inferred

Even when `owner_only_memory_decisions > 0` and `all_memory_counterfactual_changed > 0`, this audit does not prove that the owner's knowledge improved WLS task completion. A changed decision is not necessarily a better decision; all-memory ablation is not an owner-specific ablation; test fixtures are not held-out deployment tasks. The receipt fixes `owner_specific_ablation_performed=false` and `transfer_advantage_proven=false`.

Next acceptance is a **separately frozen equal-model/equal-budget experimental pair**, with identical goals and task inputs and only the owner-memory condition differing. It must report real task outcomes and controls for trivial personalization, leakage and benchmark selection. No promotion of executable skills follows from a memory-use count.

This is an instrument for that experiment, not an RSI claim.
