# WLS Single-User Security Boundary

WLS is a local, owner-governed workstation system. The security model is
designed for a single trusted owner, not for hostile multi-user tenancy.

## Boundaries

- Owner Console binds to loopback hosts.
- The default runtime is read-only.
- Cleanup is audit-first and quarantine-first.
- Quarantine clear requires a second explicit approval reference.
- Rollback drills do not restore the live database unless an owner-approved live
  rollback workflow is intentionally executed.
- External product readiness excludes multi-user and province/region tenancy.

## Sensitive Data Rules

Do not put these into support tickets or handoff packages:

- API keys.
- Passwords.
- Browser cookies.
- Payment data.
- Raw private documents.
- Full unreviewed WLS database files.

## Owner Authority

The owner remains the only authority for:

- File cleanup.
- Quarantine clearing.
- Live rollback.
- Provider credential configuration.
- Publishing or exporting private evidence.

## Known Non-Goals

- Multi-user access control.
- Remote administrative console.
- SaaS tenant isolation.
- Compliance certification.

