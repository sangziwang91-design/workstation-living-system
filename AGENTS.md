# WLS contributor continuation contract

> **This file is an entry-point pointer, NOT another source of truth.** For the owner's intended product and full handoff rules, read [README.md](README.md) first, then [LIVING_SYSTEM_GENOME.md](LIVING_SYSTEM_GENOME.md) and [CURRENT_STATE.yaml](CURRENT_STATE.yaml). The README's long-term objective is not a claim of implementation.

## Mission: do not silently narrow it

WLS is **one persistent, owner-controlled, local-first, multifunctional autonomous agent** intended to sense, remember, maintain goals, plan, act through governed tools, learn from real outcomes, survive process/model/session changes, and gradually improve its own tools, skills, strategies and eventually its improvement process. **RSI is one growth capability, not a replacement product**. A single code patch, scripted generations or CI success is not proof of longitudinal or recursive self-improvement.

## Before any edit

1. Inspect the **live** repository `main` SHA, open PRs (particularly RSI [#36](https://github.com/sangziwang91-design/workstation-living-system-private/pull/36)), the active branch HEAD, exact-head Actions results and current source; archived chat/Notion summaries and old `CURRENT_STATE.yaml` timestamps cannot supersede them.
2. Trace the change through `source/src/wls/runtime.py::LivingSystem`, its existing SQLite/evidence/permission contracts and real user task flow. Do not create a second runtime, new brain/graph authority, shadow `final/v2` product or duplicate design.
3. Ask **which measured ability improves** (independent action, durable continuity, multifunctionality, learning, safe recovery, cost efficiency or sustained RSI). Avoid meaningless versions, module growth, repeated fixture-only passes or new reporting layers.
4. Use **GitHub-first** modification and Linux/Windows hosted CI; read failures and re-run exact HEAD. Do not hand elementary tests to the owner's PC. Real unknown generated code needs a credential-free isolation boundary; owner-host Windows is for final device/installation/life-loop acceptance.
5. Preserve owner authority, stop/rollback, evidence provenance, frozen independent graders and permission budgets. A model's narrative never proves an execution result. Never equate Draft/CI/fixture/owner-reported/installed.
6. Leave a succinct PR/issue handoff: canonical base and HEAD, changed behavior, baseline vs new measured gain, exact CI links, remaining blockers, one executable next step and stop condition. **No new evergreen CURRENT_* file just for a handoff.**

**Technical scope:** `source/src/wls` is the only Python package authority; root `pyproject.toml` is canonical. Read `docs/WLS_MAINLINE_CONTEXT.md` for existing architecture but verify dated claims. Keep non-urgent experimental material in existing branches/archives, not in a parallel runtime.
