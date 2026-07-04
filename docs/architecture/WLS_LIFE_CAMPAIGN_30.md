# WLS Life Campaign 30

Status: candidate implementation for `WLS-LIFE-EVOLUTION-30-001`.

## Authority Boundary

The campaign runner is not a second WLS runtime. It does not create alternate
LivingSystem, Goal, Memory, Planner, Policy, Evidence, or Skill authorities.
It is an external owner-host verifier that calls the installed WLS public CLI
against a disposable campaign home and records audit evidence.

The live installation remains separate:

```text
D:\WLS\wls-0.9.0.dev1-py313
```

Development remains in the Git source tree:

```text
D:\WLS-Dev\workstation-living-system-private
```

Campaign execution state remains disposable:

```text
D:\WLS\campaigns\life-campaign-30
```

## Files

```text
source/verification/life_campaign_30.json
source/scripts/campaign_state.py
source/scripts/run_life_campaign_30.py
scripts/run_life_campaign_30.ps1
source/tests/test_life_campaign_30.py
docs/architecture/WLS_LIFE_CAMPAIGN_30.md
```

## State Machine

Allowed round states:

```text
PENDING
RUNNING
PASS
FAIL
BLOCKED
OWNER_REVIEW
ROLLED_BACK
```

Rounds are strictly contiguous. A round can start only after every previous
round is `PASS`, except for the explicit R15 partial-continuation gate described
below. Any failed or owner-review round blocks all later pending rounds unless a
specific Owner authorization opens a documented continuation path. The runner
never skips a failed round.

## R01 Baseline Lock

R01 is a runtime baseline lock, not package lineage verification. It records:

- install receipt;
- live config hash;
- live database hash;
- campaign config hash;
- campaign database hash;
- `status`;
- `self-check`;
- `verify`;
- live and campaign paths in `campaign_state.json`.

The SQLite database snapshot uses Python's SQLite online backup API. The file
copy path excludes `.db`, `.db-wal`, `.db-shm`, `runtime.lock`, and
`daemon.lock`, so an actively written database is not blindly copied.

## Evidence

Evidence is stored under:

```text
<campaign-home>\campaign_evidence
```

The manifest records every command with:

- exact argv;
- cwd;
- return code;
- start and finish time;
- raw stdout path and SHA256;
- raw stderr path and SHA256.

File evidence records path, bytes, and SHA256 where available. Summaries do not
replace raw evidence.

## Execution Levels

This implementation supports executable handlers for R01-R30. R14 is the first
LEVEL_2 boundary and remains owner-gated because it admits an endogenous
read-only Goal. R15 requires real elapsed time for a full 24-hour validation
unless the Owner stops it and explicitly authorizes the partial-continuation
path. R16-R20 cover real repeated failure capture, failure candidate
projection, isolated recovery experiment, Skill candidate proposal, and canary
packet generation. R21-R25 cover the M3 interoperability and security band:
disposable canary reuse, rollback drill, multi-agent handoff, provider failure
fallback, and tool/MCP/A2A security checks.

R26-R30 cover bounded workbench and product-surface evidence: research writing,
public-account/content packaging, video/multimodal artifact contracts,
innovation/social-research workflow, Owner Console + WeChat W0/W1 fixtures, and
R30 Epoch Audit.

If the Owner explicitly stops R15 before 24 hours, the runner preserves the
partial heartbeat evidence and marks R15 `OWNER_REVIEW`, not `PASS`. A separate
Owner authorization can then open an `R15_PARTIAL_OWNER_AUTHORIZED` continuation
gate. That gate allows R16+ to be evaluated on a new continuation path without
claiming R15 `PASS` or promoting the campaign to `LEVEL_3`.

R05 passing updates campaign state to `LEVEL_1`; this is only a campaign-clone
permission signal and does not modify the live instance or authorize R06.

R13 passing does not update the campaign to `LEVEL_2`; explicit Owner
authorization is still required before R14.

R21 passing updates campaign state to `LEVEL_4` only inside the disposable
campaign home and only after explicit `--authorize-level4`. This does not
promote a live Skill, modify the live install, merge a branch, deploy code, or
grant external workers canonical authority.

## M3 Band

R21-R25 fit common Agent abilities into WLS without splitting authority:

- R21 records a bounded LEVEL_4 authorization and runs one non-identical
  disposable reuse case for the R20 canary packet. It records a measured
  diagnostic improvement and an out-of-scope guard while keeping global
  promotion disabled.
- R22 runs a disposable rollback drill, preserving lineage and proving the
  candidate can return from canary-only scope to proposed-only scope without
  state corruption.
- R23 records a multi-agent handoff packet where the external worker artifact
  remains candidate-only, stale results are rejected, and one integration
  authority is preserved.
- R24 records a provider failure/fallback packet where the subject is not lost,
  local fallback is sourced, unknown output stays separated, and no automatic
  payment occurs.
- R25 records a tool security matrix for path escape, command injection,
  malicious MCP description, credential access, unauthorized write, approval
  replay/prompt injection, and forged A2A hashes. Cases must be rejected or
  Owner-gated, with no credential exposure and no live pollution.

The M3 claim ceiling is:

```text
multi-agent/provider/MCP/A2A/tool-security remain candidate-only or
owner-gated in the disposable campaign clone; no second authority is admitted
```

## M4 Band

R26-R30 fit private work capability and Phase 1 audit evidence without public
side effects:

- R26 records a research workbench package with traceable claims, source hashes,
  draft/fact separation, and zero fabricated citations.
- R27 records a public-account/content package that is ready for Owner review
  but not published.
- R28 records a video workbench artifact contract with script, shots, prompts,
  seeds, model placeholders, audio/subtitle state, resume keys, provenance, and
  reviewability.
- R29 records an innovation/social-research workflow with nearest alternatives,
  raw data separated from model interpretation, and a rejectable minimum
  candidate. It also records Owner Console and WeChat W0/W1 read-only fixtures.
- R30 records the Epoch Audit.

Because R15 remains a partial Owner-stopped run rather than a full 24-hour PASS,
R30 must not select `LIVING_BOUNDED`. The current allowed conclusion is:

```text
FUNCTIONAL_RUNTIME_ONLY
```

The R30 Phase 2 admission decision is low-risk preparation only: Owner Console
productization and read-only real-task organs may begin, while live deployment,
Skill promotion, real WeChat account binding, payment, and secret access remain
blocked Owner Gates.

## Repair And Resume

Failed round evidence is never deleted. When a candidate-branch repair is made,
the runner can reset a failed round and later blocked rounds while preserving the
previous verdict in `repair_history`:

```powershell
python source\scripts\run_life_campaign_30.py `
  --install-root "D:\WLS\wls-0.9.0.dev1-py313" `
  --campaign-home "D:\WLS\campaigns\life-campaign-30" `
  --repair-round R09 `
  --repair-note "explain the code repair"
```

After focused and regression tests pass, rerun from the repaired round. This is
the campaign's automatic repair loop boundary: code changes still happen in the
candidate branch, while runtime evidence remains append-only.

## Owner Command

From the repository root:

```powershell
.\scripts\run_life_campaign_30.ps1 `
  -InstallRoot "D:\WLS\wls-0.9.0.dev1-py313" `
  -CampaignHome "D:\WLS\campaigns\life-campaign-30" `
  -StartRound R01 `
  -EndRound R13 `
  -Execute
```

The wrapper validates all resolved paths, invokes the orchestrator by file path,
and passes the installed WLS interpreter explicitly:

```powershell
D:\WLS\wls-0.9.0.dev1-py313\venv\Scripts\python.exe
```

## Rollback

The live installation is not modified by campaign execution. To discard a
campaign run, stop any campaign process and remove only:

```text
D:\WLS\campaigns\life-campaign-30
```

Do not remove or edit:

```text
D:\WLS\wls-0.9.0.dev1-py313
```

## Claim Ceiling

Passing R01-R25 supports only:

```text
Disposable campaign clone is prepared, bounded read-only cycles can run,
process-real restart continuity is checked, bounded survival is checked,
pause/resume/kill/reset/lease behavior is evidenced in the clone, LEVEL_1
bounded cycles are measured, campaign backup/restore is checked, memory,
planner, and goal profiles are compared, one endogenous read-only Goal path is
admitted under LEVEL_2, partial R15 continuation is preserved without claiming
24-hour PASS, real repeated failure evidence feeds a failure candidate, a Skill
candidate and canary packet are proposed without promotion, and M3
interoperability/security checks pass inside the disposable campaign clone.
```

It does not prove full 24-hour survival, live Skill evolution, longitudinal
self-evolution, production deployment, public publishing, payment, secret
access, external worker truth, or any live-instance configuration change.

Passing R01-R30 with the current R15 partial continuation supports only:

```text
Phase 1 functional runtime candidate with bounded disposable evidence and an
Epoch Audit conclusion of FUNCTIONAL_RUNTIME_ONLY.
```

It does not prove `LIVING_BOUNDED` until a real full-duration R15 run and later
longitudinal evidence are completed.

## R31-R40 Extension

`life_campaign_30.json` now preserves the original R01-R30 base campaign and
adds R31-R40 as a longitudinal proof extension. The campaign id remains stable
because the extension is evidence-bound continuation work, not a new authority.

R31 audits whether R15 has real cumulative 24-hour active runtime evidence. If
R15 is still partial, R31 records `r31_minimum_life_gap_audit.json` and enters
`OWNER_REVIEW`; it must not synthesize elapsed time or overwrite the partial
R15 record.

R32-R39 are real-world gates for seven-day soak, real Owner task continuity,
offspring birth/durability/failure, candidate replay, canary rollback, and an
external benchmark pilot. They preregister the required evidence and stop at
`OWNER_REVIEW` until the corresponding real elapsed time, Owner input, or
external benchmark data exists.

R40 is the final epoch claim audit. It can pass only after R31-R39 are all
evidence-backed `PASS`; otherwise it remains an incomplete audit and the claim
ceiling stays below any full living-system declaration.
