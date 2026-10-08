# Retired Windows owner-runner workflows (2026-10-08)

The following executable GitHub Actions files are removed from the current
public WLS default workflow surface. Their exact original bytes, run history,
and D-drive-specific recovery scripts remain available in Git history at
`f295ef70aa934a081484199ea67fda3549160a9a`:

- `.github/workflows/local-runner-24h-bidirectional.yml`
- `.github/workflows/local-runner-preflight.yml`
- `.github/workflows/local-runner-smoke.yml`

In addition, the dormant Windows `self-hosted` jobs were removed from
`ci.yml` and `evolution-chain.yml`. Both continue on GitHub-hosted Linux
and Windows. Public WLS may not request the owner's local runner.

**Separate account action still required:** unregister/deauthorize any
repository-level self-hosted runner and remove credentials from that host.
Deleting workflow YAML does NOT unregister a runner or revoke tokens.
WLS cannot claim the runner count is zero without the GitHub administrative
API receipt. Owner-machine campaigns belong outside public Actions.

This is a safety correction, not new WLS autonomy or measured RSI growth.
