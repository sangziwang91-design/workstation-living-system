# Task19 local execution commands

From a clean checkout of `repair/task19-main-convergence-20260628` on Windows:

```powershell
.\scripts\run_task19_windows.ps1
```

To perform the shorter preflight without the 100-cycle workload:

```powershell
.\scripts\run_task19_windows.ps1 -SkipSoak
```

The full command is the acceptance gate. The shorter command is diagnostic only. Reports are created under `artifacts\task19\` and are ignored by Git. Do not copy generated virtual environments, Wheels, databases, keys, or soak homes into the repository.
