# Reliability execution while GitHub-hosted capacity is unavailable

Task19 does not treat an absent hosted check as a pass. The authoritative fallback is the isolated Windows/local command:

```powershell
.\scripts\run_task19_windows.ps1
```

It installs the root project in a new virtual environment and runs compilation, repository-layout checks, the complete test suite, ET001–ET004, Ruff, Mypy, Bandit, root package build, clean Wheel installation outside the repository, and the bounded 100-cycle workload.

Reports are written to the ignored `artifacts/task19/` directory and bind all results to the current Git head. They must be attached or quoted in review before merge. The existing self-hosted workflows remain available when the Actions control plane and runner service are functioning, but their absence is never converted into a synthetic green result.
