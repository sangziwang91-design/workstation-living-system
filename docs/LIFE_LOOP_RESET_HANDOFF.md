# WLS Life Loop Reset Handoff

Updated: 2026-07-19

Start here for the current WLS mainline. The full 2026-07-15 process log was
moved to:

```text
docs/history/LIFE_LOOP_RESET_HANDOFF_20260715_FULL.md
```

## Direction

WLS is being pulled back from evidence-led commercial hardening to the original
local long-term action-body line:

```text
sense -> remember -> judge -> act -> observe outcome -> learn -> self-model -> sleep
```

Health, evidence, approval, rollback, retention, readiness, and audits remain as
immune-system protection. They should not expand as the foreground product unless
a real action chain is blocked without them.

## Current Mainline

Read `docs/WLS_MAINLINE_CONTEXT.md` first. It is the compact anatomy and merge
rule for the system.

Current canonical trunk:

```text
D:\WLS-Dev\workstation-living-system-private
source/src/wls/runtime.py::LivingSystem
```

Current installed owner-host:

```text
D:\WLS\wls-0.9.0.dev1-ui-20260706
```

## Completed Reset Links

- `life-state` exposes the living loop as a bounded CLI/API/UI state.
- Perception classifies observations and suppresses noise.
- Goal pressure ranks live goals and proposes one small next step.
- Memory influence and owner feedback affect action choice.
- Action candidates classify risk and preserve owner approval.
- Outcome feedback can update goal progress with evidence and suppress repeated
  bad suggestions.
- Self-model calibration can defer action classes after owner feedback.
- Patch Mission is the active representative action chain in source: local repo
  inspection, owner-gated tests, patch draft, owner-gated apply, verification,
  PR summary, git prep, commit draft, remote/CI evidence, PR update loop, and
  repair skill candidate lifecycle.

## Current Boundary

The source tree contains the mainline Patch Mission work. The installed WLS may
lag until the source package is verified, built, and reinstalled.

Do not claim the installed owner-host has a capability until the installed CLI/API
shows the entry and a bounded owner-host verification has passed.

## Next Single Action

Continue Task A: GitHub Patch Mission.

```text
approve recovered commit-draft
-> create the local commit
-> resume-next to refresh post-commit git metadata
-> prepare remote-readiness summary
-> verify push/PR remain separate owner-approved actions
```

Stop if the next change would only add a dashboard, label, readiness gate, report,
or abstract architecture note without strengthening that action chain.
