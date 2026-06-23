# ROUND-001 Baseline Result

**Result:** `PASS` for reproducible baseline and claim reset.  
**Product state:** remains `experimental_alpha`.

## Reproduced defects

1. The release repository omitted its test suite even though `pyproject.toml` pointed to `tests/`.
2. Episodic memories recursively embedded earlier memory payloads.
3. Large tool outputs could be copied into every later episode.
4. Internal memories reduced real-event attention to one event per cycle under load.
5. Event acknowledgement did not verify worker ownership or transition count before emitting evidence.
6. Approval digests omitted acceptance criteria, goal, skill, and idempotency semantics.
7. Retrieved memories were listed in plans even when they had not changed a decision.
8. The `1.0.0` version and “software-life runtime” framing exceeded the evidence.

## Corrective changes

- Restored 39 committed tests, including adversarial invariants.
- Demoted the version to `0.1.0a1` and rewrote the claim ceiling.
- Added bounded episodic projection and large-output hashing.
- Added a 50% minimum external-event workspace quota.
- Added strict worker-bound atomic acknowledgement.
- Bound approvals to complete action semantics.
- Added an explicit validated-memory guidance schema with constrained effects.
- Added clean wheel build/install and repository CI.

## Measured delta in the same sandbox class

The pre-fix stress reproduction with 50 events produced:

- 30 cycles;
- about 2.81 events/second;
- about 74 MiB database size;
- about 988 ms retrieval time after 50 additional memories;
- episodic rows growing to roughly 328 KiB each.

After the bounded-memory and event-quota correction, the committed benchmark with 200 events and 200 additional memories produced:

- 49 cycles;
- 65.41 events/second;
- 2,756,608-byte database;
- 18.501 ms memory retrieval;
- 17.107 ms verification across 697 evidence rows.

These values are sandbox measurements, not Windows or production claims. See `benchmarks/round001_baseline.json`.

## Validation

- 39/39 tests passed.
- Branch coverage: 67.67%; the enforced alpha floor is 65%, not a claim of comprehensive coverage.
- Ruff passed.
- Mypy passed.
- Bandit passed.
- Wheel build passed.
- Clean virtual-environment installation passed.
- Release verification ran without `.git` or hidden sandbox files.

## Remaining high-risk unknowns

- Windows installer execution on the actual Workstation.
- Multi-day process and system-sensor behavior.
- Provider planning quality and hostile-provider outputs.
- Database migrations and disaster restore.
- General learning advantage beyond one constrained decision-memory path.
