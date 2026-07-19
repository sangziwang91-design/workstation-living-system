# WLS Recovery Runbook

This runbook covers single-user recovery for local WLS installations.

## Health Stop

If `wls health` returns `BLOCKED`, do not run daemon cycles. Inspect:

```powershell
wls health
```

Resolve critical entries before restarting.

## Stale Locks

If health reports stale locks and no WLS process is running, remove only the
specific stale lock file reported by health. Do not delete the state directory.

## Database Backup

Before any upgrade:

```powershell
wls upgrade-drill --wheel <wheel> --disposable-clone
```

This creates a backup and verifies restore integrity in a disposable clone.

## Rollback

The standard rollback path is:

1. Stop WLS UI and daemon.
2. Preserve the current WLS home.
3. Restore from the verified backup created by `upgrade-drill`.
4. Run `wls health`.
5. Run `wls retention-audit`.

Live rollback must be owner-approved. Disposable rollback evidence is not the
same as a live rollback execution.

## Cleanup Recovery

Garbage cleanup quarantines candidates first. Clearing quarantine is a separate
owner-approved action:

```powershell
wls garbage-clear-quarantine --audit-id <audit_id> --approval-reference <owner_ref>
```

Never manually delete broad WLS home folders during support recovery.

