# Task19 rebuild preregistration

- Base SHA: `4c77401deb159e8ee2a2daf3c6e2702ce48876e3`
- Branch: `repair/task19-main-convergence-20260628`
- Candidate SHA: resolved dynamically by the verifier from `git rev-parse HEAD`; no tracked report may hard-code a pre-report head.
- Integration method: main-based rebuild; historical PR #22 used only as source material.
- GitHub-hosted Actions: unavailable; no missing check is interpreted as PASS.
- Required execution: `scripts/run_task19_windows.ps1` from a clean exact-head checkout.
- Merge, Task20, provider attachment, and owner-host deployment remain unauthorized.

## Mandatory gates

1. compileall
2. repository layout
3. full pytest
4. ET001–ET004
5. Ruff
6. Mypy
7. Bandit
8. root Wheel build
9. outside-repository clean install
10. 100-cycle bounded non-idle workload with runtime re-instantiation after cycles 25, 50, and 75

## Stop conditions

Any failed, timed-out, or unavailable mandatory gate keeps the PR Draft. Generated evidence belongs under ignored `artifacts/task19/` and must identify the exact checkout head measured at runtime.
