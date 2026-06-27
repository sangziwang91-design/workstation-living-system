# Jules Task 20 · v0.6 Shadow Review

Start only after Jules task 19 is complete.

Repository: `sangziwang91-design/workstation-living-system-private`

Branch: `feature/spacetime-causal-shadow-v06`

All required files are already on this branch. No external upload is needed. Do not use GitHub Actions because hosted quota is unavailable.

Read the shadow source, plugin, focused tests, verifier, preregistration, pilot README, and Manus summary. Then inspect the current canonical runtime, database, evidence, stores, temporal world, cognition, and memory attribution files.

Run in the Jules environment:

```bash
python -m compileall -q source/src/wls source/tests scripts
python -m pytest source/tests/test_shadow_causal.py -q
python scripts/verify_shadow_causal_local.py --output .jules-shadow-verification
python -m pytest -q
```

Also run ruff, mypy, bandit, and build when those tools are available. Record unavailable tools as unavailable, not passed.

Keep the branch and pull request in draft state. Do not merge, enable on the owner host, or expand into v0.7 research. Make only narrow fixes that have a failing test first.

Final result must report exact head SHA, commands, results, files changed, remaining limits, and one bounded next action.
