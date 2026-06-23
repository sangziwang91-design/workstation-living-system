# WLS Agent Engineering Contract

This repository is developed through evidence-gated rounds. An agent is an execution worker, not the authority that decides whether a capability exists.

## Mandatory workflow

1. Read `PROJECT_CHARTER.md`, `CLAIM_CEILING.md`, and `CURRENT_STATE.yaml` before editing.
2. Work only on the active round branch and linked issue.
3. Reproduce a failure or establish a numeric baseline before modifying behavior.
4. Make the smallest coherent change that closes the identified gap.
5. Add tests that fail on the previous implementation and pass on the new implementation.
6. Add at least one adversarial or misuse case for every safety invariant.
7. Run the complete repository validation suite; targeted tests are not sufficient.
8. Update claims only when the evidence supports them.
9. Keep failed experiments visible. Do not rewrite history or delete contrary evidence.
10. Open a pull request; never push a capability change directly to `main`.

## Evidence rules

- A module name is not a capability.
- A database row is not learning.
- Retrieval is not memory influence.
- A generated candidate is not a new skill.
- A passing unit test is not an external outcome.
- Repeated execution of the same test is not multiple validation scenarios.
- Model narration is never accepted as proof of completion.
- Current state must be read from code, runtime output, or committed evidence; never infer it from old reports.

Every claim must be marked one of:

- `VERIFIED`: reproduced by committed tests or a recorded external experiment.
- `PARTIAL`: a bounded component works, but the complete capability is not established.
- `UNSUPPORTED`: current evidence does not justify the claim.
- `UNKNOWN`: not tested or not currently measurable.

## Safety boundaries

- No real patient information, credentials, private Notion content, browser cookies, or workstation databases may enter the repository.
- Tests and CI use synthetic data only.
- Core runtime, permission, identity, evidence, and promotion-policy changes require human review.
- Irreversible actions and external publication remain human-gated.
- A model cannot lower the deterministic risk classification of an action.
- Interrupted actions with possible side effects must not be replayed automatically.
- Memory can affect planning only through explicit, evidence-marked schemas and bounded effects.

## Definition of a completed round

A round ends only with:

- linked issue and branch;
- baseline failure or metric;
- code diff;
- committed tests;
- adversarial case;
- benchmark delta where relevant;
- `CLAIM_CEILING.md` and `CURRENT_STATE.yaml` updates;
- pull request;
- result: `PASS`, `PARTIAL`, `FAIL`, or `INVALIDATED`.

Do not use vague completion language such as “basically complete,” “production-ready,” or “has consciousness.”
