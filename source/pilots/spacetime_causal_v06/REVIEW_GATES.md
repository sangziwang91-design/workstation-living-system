# Review Gates

Jules task 20 must independently check:

- no duplicate WLS authority
- plugin absent means no shadow load
- proposal frozen before outcome
- canonical data unchanged by shadow analysis
- shadow candidates cannot reach verified status
- frozen proposal and candidates are immutable
- shadow failures do not alter canonical results
- pending runs recover once after restart
- freeze and resolution are idempotent
- normal events skip PAST; failures may activate it
- analysis graph is acyclic
- shadow code performs no tool, shell, network, or sensor action

Only narrow test-backed fixes are allowed. Do not redesign the canonical runtime or begin v0.7 work.
