# Local Commands After Jules Task 19

Run from the repository root on the Windows workstation:

```powershell
$ErrorActionPreference = "Stop"
$branch = "feature/spacetime-causal-shadow-v06"
$report = Join-Path $PWD ".shadow-verification"

git fetch origin
git switch $branch
git pull --ff-only origin $branch

python -m compileall -q source/src/wls source/tests scripts
python -m pytest source/tests/test_shadow_causal.py -q
python scripts/verify_shadow_causal_local.py --output $report
python -m pytest -q

if (Get-Command ruff -ErrorAction SilentlyContinue) { ruff check . }
if (Get-Command mypy -ErrorAction SilentlyContinue) { mypy source/src/wls }
if (Get-Command bandit -ErrorAction SilentlyContinue) { bandit -q -r source/src/wls }
python -m build

git status --short
```

Do not add the plugin to the owner-host canonical config during this verification run.
