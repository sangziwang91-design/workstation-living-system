# Spacetime Causal v0.6 WLS Shadow Pilot

Source artifact SHA-256: `0a5fec0b48a4f0ca0d74e9cb19bf87a60fd5392b581c917dc6ab931ce4e76fdb`.

Manus independently reproduced the standalone package and judged it eligible for a read-only shadow stage. This branch implements only the transferable shadow contract through the existing WLS plugin mechanism. It does not vendor the standalone runtime or create a second authority.

Current evidence: focused offline fixture passed. Full repository tests, Jules review, local Windows gates, and owner-host value remain pending. GitHub Actions are not used because hosted quota is unavailable.
