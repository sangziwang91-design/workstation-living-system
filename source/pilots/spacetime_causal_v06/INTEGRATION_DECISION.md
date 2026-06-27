# Integration Decision

The standalone v0.6 package is not vendored into WLS. Only its transferable read-only contracts are implemented through the existing plugin mechanism.

Decision:

- reuse canonical WLS database and evidence ledger
- keep the plugin disabled by default
- freeze shadow proposals before canonical outcomes
- resolve them after canonical outcomes
- write only disposable shadow-prefixed records
- preserve all existing runtime, planner, memory, skill, goal, world, and action authorities
- require Jules review and local Windows gates before any owner-host trial

This branch is an integration experiment, not production deployment.
