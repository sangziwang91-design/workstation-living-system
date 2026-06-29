# Task19 clean convergence preregistration

- Base SHA: `4c77401deb159e8ee2a2daf3c6e2702ce48876e3`
- Pull request: `#25`
- Branch: `task19-clean-squash`
- Candidate SHA: resolved at runtime with `git rev-parse HEAD`; no tracked report may hard-code a pre-report head.
- Integration method: one main-based candidate commit.
- Superseded PRs: `#22`, `#23`, and `#24`, all closed without merge.
- Hosted Actions: missing or queued runs are not interpreted as PASS.
- Required execution: `scripts/run_task19_windows.ps1` from a clean exact-head checkout.
- Merge, Task20, provider attachment, and deployment remain unauthorized.

## Mandatory gates

1. clean unchanged head and worktree
2. compileall and repository hygiene
3. full pytest
4. ET001–ET004
5. Ruff, Mypy, and Bandit
6. root Wheel build
7. Wheel metadata, entry-point, and contamination checks
8. outside-repository clean installation
9. 100-cycle bounded workload with runtime re-instantiation after cycles 25, 50, and 75

## Stop conditions

Any failed, timed-out, or unavailable mandatory gate keeps PR #25 Draft. Generated evidence belongs under ignored `artifacts/task19/` and must identify the exact checkout head measured at runtime.
