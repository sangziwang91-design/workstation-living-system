# WLS Support and Diagnostics

This guide is for single-user external support. It avoids collecting secrets,
raw private content, or uncontrolled filesystem dumps.

## First Response Checklist

Ask the owner to provide these command outputs:

```powershell
wls health
wls self-check
wls garbage-audit --max-candidates 200
wls retention-audit
wls commercial-readiness-audit --rc-min-qualified-measurements 8
wls external-product-audit
```

For performance issues:

```powershell
wls performance-audit --samples 3 --include-write-workflows --include-rollback-workflow
wls soak-audit --cycles 3 --max-cycle-seconds 60
```

For upgrade issues:

```powershell
wls upgrade-drill --wheel <wheel> --disposable-clone
```

## Data Handling

Support packets should include receipt IDs, status fields, timings, and failure
groups. They should not include:

- API keys or provider credentials.
- Raw personal documents.
- Full browser history.
- Unreviewed database exports.

## Triage

Use this order:

1. Health status and critical blockers.
2. Runtime and daemon locks.
3. Latest cycle status.
4. Database size and retention pressure.
5. Garbage audit candidates.
6. Performance budget failures.
7. Upgrade/rollback drill evidence.

## Escalation

Escalate to engineering only when a command returns a failing receipt with a
stable reproduction path, a stack trace, or a health critical entry.

