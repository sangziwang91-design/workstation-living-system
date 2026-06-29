# Task19 rebuild preregistration

- Base SHA: `4c77401deb159e8ee2a2daf3c6e2702ce48876e3`
- Candidate SHA: `9406600009536c36d123a9f05c32dbfdbcb7a5d5`
- Branch: `repair/task19-main-convergence-20260628`
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

Any failed, timed-out, or unavailable mandatory gate keeps the PR Draft. Generated evidence belongs under ignored `artifacts/task19/` and must identify this exact candidate SHA.
