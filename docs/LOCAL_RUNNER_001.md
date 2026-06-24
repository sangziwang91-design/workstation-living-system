# LOCAL-RUNNER-001

## Status

`SCOPED -> READ -> MODIFIED`

The Windows self-hosted runner is a bounded verification node. GitHub remains the control plane and evidence ledger. This phase does not claim live five-system autonomy or safe self-modification.

## Host configuration

- Root: `D:\actions-runner`
- Work directory: `D:\actions-runner\_work`
- Mode: Windows service with delayed automatic start
- Account: `NT AUTHORITY\NETWORK SERVICE`
- Scope: `sangziwang91-design/workstation-living-system-private`

## Permitted in phase 1

- Inspect runner identity and service context
- Prepare D-drive cache and temporary directories
- Check Git, Python, disk capacity, and GitHub connectivity

## Forbidden in phase 1

- No writes to live five-system directories
- No live database or canonical-state mutation
- No loading of workstation secrets
- No automatic merge, release, deployment, or runtime promotion
- No unreviewed branch execution

## Workflows

- `Local Runner Smoke`: identity, tools, and D-drive path check
- `Local Runner Preflight`: storage, connectivity, and path-boundary check

Both workflows use manual `workflow_dispatch` only.

## Acceptance

The phase reaches `TESTED` only after both workflows pass and show Windows X64, the configured service identity, callable Git and Python, GitHub TCP 443 connectivity, and D-drive temporary/cache paths.

## Stop condition

After evidence is collected, stop at owner gate. Repository checkout and WLS source execution belong to the next phase.
