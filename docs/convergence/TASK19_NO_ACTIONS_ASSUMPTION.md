# No hosted-Actions assumption

This branch is designed to be verifiable without GitHub-hosted execution. Existing self-hosted workflows are preserved from `main`, but the acceptance path does not depend on their availability. `scripts/run_task19_windows.ps1` is the source of execution evidence while the Actions control plane is unavailable.
