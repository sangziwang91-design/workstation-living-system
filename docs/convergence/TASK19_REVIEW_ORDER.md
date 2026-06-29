# Task19 review order

1. Confirm the branch is based on current `main` and contains no historical PR merge.
2. Review `config.py`, `task19_stabilization.py`, and focused tests.
3. Review Provider Hub secret, SSRF, and lock boundaries.
4. Run the diagnostic Windows gate with `-SkipSoak`.
5. Resolve all deterministic failures.
6. Run the full Windows gate including the 100-cycle workload.
7. Independently inspect generated JSON/Markdown evidence.
8. Only the owner may decide whether to merge.
