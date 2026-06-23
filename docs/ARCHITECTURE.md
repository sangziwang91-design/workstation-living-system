# WLS Architecture — Evidence-Gated Alpha

## Runtime path

```text
configured sensors
      ↓
durable observations and events
      ↓
world-fact assimilation + prediction error
      ↓
retrieval + finite attention workspace
      ↓
deterministic/model planner
      ↓
policy classification + exact approval
      ↓
tool execution + acceptance evaluation
      ↓
bounded episode memory + self-model update
      ↓
repair/skill candidates + human promotion
```

## Trust boundaries

1. **Sensor boundary:** observation payloads are untrusted input with explicit provenance.
2. **Planner boundary:** model or deterministic planner proposes actions but has no execution authority.
3. **Policy boundary:** local deterministic code classifies risk and validates arguments.
4. **Approval boundary:** approval is HMAC-bound to full action semantics and consumed once.
5. **Tool boundary:** tools enforce path, host, command, size, timeout, and side-effect contracts.
6. **Learning boundary:** memories and candidates are evidence records, not automatic authority.
7. **Promotion boundary:** core changes and promoted skills remain human-gated.

## Round-001 architecture corrections

### External-event quota

The earlier workspace guaranteed only one external event. As episodic memories accumulated, internal memories occupied most slots and event throughput collapsed. The workspace now reserves at least half its capacity for external events while preserving up to 30% for goals, regulation, and memory context.

### Bounded episodic projection

The earlier episode recorder copied complete workspace memory payloads into the next episode. Each episode could therefore embed earlier episodes recursively. Tool outputs were also copied without a bounded projection. Episodes now:

- reference prior memories without copying their content;
- retain compact event observations needed by consolidation;
- replace large strings and bytes with preview, size, and SHA-256;
- enforce a 128 KiB episode ceiling with a minimal fallback projection.

### Decision-memory authority

Retrieval is not authority. Memory can influence the deterministic planner only when:

- it is broadcast into the finite workspace;
- its type is `procedural` or `failure`;
- confidence is at least 0.8;
- tags include `validated` and `decision-guidance`;
- content follows `wls.action_guidance.v1`.

Its effect is restricted to suppressing an already grounded action or changing allowlisted bounded resource arguments. It cannot introduce tools, paths, commands, URLs, payloads, risk classes, or writes.

### Atomic event acknowledgement

Every event named in a persisted plan must transition from `RESERVED` to `PROCESSED` under the same worker ID and transaction. Any mismatch aborts the plan transaction and emits no false processing evidence.
