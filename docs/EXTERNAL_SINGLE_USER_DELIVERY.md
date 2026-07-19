# WLS External Single-User Delivery

This runbook defines the external-user delivery shape for WLS when the product
is sold or handed to one workstation owner at a time.

## Scope

- One local Windows workstation owner.
- Local SQLite state under the configured WLS home.
- Local Owner Console served on loopback only.
- Read-only default operation.
- Owner approval for cleanup, rollback, and other material writes.

Out of scope:

- Multi-user tenancy.
- Regional/province account partitioning.
- Hosted SaaS operations.
- Third-party compliance certification.

## Release Artifact

Build the wheel from the repository root:

```powershell
.\.venv\Scripts\python.exe -m build --wheel
```

Expected artifact:

```text
dist\workstation_living_system-<version>-py3-none-any.whl
```

Before delivery, run:

```powershell
wls health
wls commercial-readiness-audit --rc-min-qualified-measurements 8
wls external-product-audit
```

The delivery candidate is acceptable only when `external-product-audit` returns
`EXTERNAL_SINGLE_USER_READY`.

## Installation

Install into the target user's Python environment:

```powershell
python -m pip install --upgrade .\dist\workstation_living_system-<version>-py3-none-any.whl
wls init --home "$env:LOCALAPPDATA\WLS"
wls health
```

Launch the Owner Console:

```powershell
wls ui --app-mode
```

## Acceptance

The first-run acceptance checklist is:

- `wls health` returns `OK`.
- No runtime or daemon lock is held.
- `wls garbage-audit` returns `CLEAN` or owner-reviewable candidates.
- `wls performance-audit --include-write-workflows --include-rollback-workflow`
  returns `PERFORMANCE_BUDGET_PASSED`.
- `wls upgrade-drill --wheel <wheel> --disposable-clone` returns
  `UPGRADE_DRILL_PASSED`.

