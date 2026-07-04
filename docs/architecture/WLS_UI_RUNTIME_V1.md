# WLS UI Runtime V1

## Status

Repository-integrated loopback Owner Console.

This UI is not a second runtime. It owns no database, planner, goal store,
approval store, evidence store, or task scheduler.

## Authority Map

| UI concept | Canonical source |
|---|---|
| Project | top-level `Goal` |
| Task | child `Goal` |
| Run | `Cycle` |
| Plan | `Plan` |
| Step | `Action` |
| Inbox | waiting approval, approved action, unknown side effect, blocked goal |
| Library | `EvidenceLedger` and `SkillLibrary` tables |
| Owner decision | `ApprovalManager` and `resolve_unknown_action` |

## Security

- IPv4 loopback only.
- One-time bootstrap nonce.
- Ephemeral in-memory bearer token.
- No auth cookie and no persistent UI token file.
- Strict Host header validation.
- Write requests require bearer auth plus `X-WLS-UI: 1`.
- Cross-site write attempts are rejected by Fetch Metadata / Origin checks.
- Static assets contain no owner data.
- CSP forbids inline script and style.

## Claim Ceiling

The UI supports local owner-console interaction with the existing runtime. It
does not prove remote access, multi-user operation, deployment readiness, or
automatic execution safety beyond the existing runtime authorities and tests.
