# WLS Shadow Causal v0.6

This branch adds an optional read-only shadow plugin. The canonical WLS runtime, event store, planner, memory, skills, goals, world model, and tool registry remain unchanged.

Enable only in a copied test configuration by adding `wls.shadow_causal_plugin` to `plugin_modules`.

Current status: branch deployed; focused offline checks passed; full repository tests, Jules review, and owner-host evidence remain pending. GitHub Actions are not used because hosted quota is unavailable.
