# Reliability CI Deferred

Due to GitHub Actions capacity being unavailable, automated reliability gating is currently deferred.

## Local Verification
To run the full reliability gate locally, execute:
```bash
export PYTHONPATH=$PWD/source/src
python3 source/scripts/verify_task19_local.py
```

## Future CI Integration
Once capacity is restored, the following commands should be integrated into the CI workflow:
- `python -m compileall -q source/src source/tests source/scripts`
- `python -m pytest source/tests`
- `python source/scripts/verify_evolution_target_001.py`
- `python source/scripts/verify_evolution_target_002.py`
- `python source/scripts/verify_evolution_target_003.py`
- `python source/scripts/verify_evolution_target_004.py`
- `python source/scripts/verify_task19_local.py`
