# WLS Claim Ceiling

## Current release status

`0.1.0a1 — experimental, evidence-gated alpha`

## VERIFIED

- The Python package builds as an offline wheel and installs in a clean virtual environment.
- SQLite-backed events, plans, actions, goals, world facts, memories, skills, and evidence survive restart.
- Configured filesystem, inbox, Git, HTTP-health, clock, process, and host-resource sensors have implementation paths; selected sensors have synthetic end-to-end tests.
- A finite workspace gives real external events a reserved attention quota and releases unselected events instead of acknowledging them.
- Event acknowledgement is atomic with plan persistence and rejects duplicate IDs, stale reservations, and ownership mismatches.
- Risk classification is deterministic; a model declaration cannot lower tool risk.
- Approval is bound to the complete persisted action semantics, expires, and is consumed once.
- Interrupted actions with possible side effects enter `UNKNOWN_SIDE_EFFECT` and are not replayed automatically.
- Episodic memory is bounded and does not recursively embed prior memory payloads or unbounded tool output.
- Only high-confidence, explicitly validated decision-guidance memories broadcast into the workspace may suppress a grounded action or alter bounded resource arguments.
- Functional regulation changes the runtime action budget under tested high-pressure state.
- HMAC-chained evidence detects mutation of retained records.
- Pause and kill-switch state survive restart.

## PARTIAL

- **Perception:** the runtime can poll configured sensors; it does not understand the unrestricted outside world.
- **World model:** facts, contradictions, predictions, and refutations are represented; causal understanding is not established.
- **Memory:** storage, retrieval, bounded episodic projection, and one constrained influence path are verified; broad continual learning is not.
- **Learning:** outcome episodes and repair candidates are created; autonomous root-cause discovery and general strategy improvement are not established.
- **Skill growth:** declarative action sequences have a gated lifecycle; automatic creation of genuinely new executable capability is not established.
- **Functional affect:** numeric control state changes tested runtime policy; human-like emotion is not claimed.
- **Autonomy:** a few narrowly defined maintenance goals can be generated; open-ended independent agency is not established.
- **Evidence:** retained-row mutation is detected; protection against an attacker who controls the database, key files, and all anchors is not claimed.
- **Windows installer:** scripts are present and source/artifact verification is reproducible in the sandbox; current Windows Workstation execution remains unverified.

## UNSUPPORTED

- WLS is a complete software lifeform.
- WLS possesses subjective consciousness, genuine emotion, pain, attachment, or desire.
- WLS is AGI or human-equivalent.
- WLS can safely rewrite and promote its own core without external validation.
- WLS currently observes the user's real five-system Workstation environment.
- WLS has demonstrated social, scientific, clinical, or economic value.
- WLS improves long-term task performance across domains.

## UNKNOWN

- Multi-week stability on the user's Windows Workstation.
- Real provider behavior, cost, rate limits, and planning quality.
- Performance under production event volume.
- Whether future memory and skill mechanisms produce statistically significant external improvement.
