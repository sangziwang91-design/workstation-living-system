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

This implementation supports executable handlers for R01-R13. R14 is the first
LEVEL_2 boundary and remains owner-gated because it admits an endogenous
read-only Goal. R15 also requires real elapsed time for 24-hour validation.
R16-R30 are fully specified in the campaign spec and normally remain blocked
behind the earlier gates.

If the Owner explicitly stops R15 before 24 hours, the runner preserves the
partial heartbeat evidence and marks R15 `OWNER_REVIEW`, not `PASS`. A separate
Owner authorization can then open an `R15_PARTIAL_OWNER_AUTHORIZED` continuation
gate. That gate allows R16+ to be evaluated on a new continuation path without
claiming R15 `PASS` or promoting the campaign to `LEVEL_3`.

R05 passing updates campaign state to `LEVEL_1`; this is only a campaign-clone
permission signal and does not modify the live instance or authorize R06.

R13 passing does not update the campaign to `LEVEL_2`; explicit Owner
authorization is still required before R14.

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

Passing R01-R13 supports only:

```text
Disposable campaign clone is prepared, bounded read-only cycles can run,
process-real restart continuity is checked, bounded survival is checked,
pause/resume/kill/reset/lease behavior is evidenced in the clone, LEVEL_1
bounded cycles are measured, campaign backup/restore is checked, and memory,
planner, and goal profiles are compared without promoting LEVEL_2.
```

It does not prove 24-hour survival, endogenous goals, skill evolution,
longitudinal self-evolution, production deployment, or any live-instance
configuration change.
