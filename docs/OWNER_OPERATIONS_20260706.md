# WLS Owner Operations - 2026-07-06

## Installed runtime

Current owner-host UI install:

```text
D:\WLS\wls-0.9.0.dev1-ui-20260706
```

Launcher:

```powershell
. D:\WLS\Start-WLS-UI-20260706.ps1
WLS self-check
WLS status
```

## Owner Console

Use the managed UI entrypoint. It prevents stale acceptance servers from hiding the
real listening process and records the current ready file under the install root.

```powershell
D:\WLS\Manage-WLS-UI-20260706.ps1 -Action status
D:\WLS\Manage-WLS-UI-20260706.ps1 -Action start
D:\WLS\Manage-WLS-UI-20260706.ps1 -Action open
D:\WLS\Manage-WLS-UI-20260706.ps1 -Action stop
```

Repository copy:

```powershell
.\scripts\manage_installed_wls_ui.ps1 `
  -InstallRoot D:\WLS\wls-0.9.0.dev1-ui-20260706 `
  -Action status
```

`open` intentionally restarts the UI before opening a browser because the bootstrap
URL is a one-time URL. A URL shown by `status` may already have been consumed by a
browser or smoke test.

## Current campaign gate

`D:\WLS\campaigns\life-campaign-30` has R01-R30 evidence and is now extended to
R31-R40. The current gate is:

```text
R31: OWNER_REVIEW
R32-R40: BLOCKED pending R31
```

Latest R31 audit:

```text
D:\WLS\campaigns\life-campaign-30\campaign_evidence\R31\r31_minimum_life_gap_audit.json
```

Observed active elapsed evidence:

```text
6297 seconds observed
86400 seconds required
```

This is a real proof gap, not a runner failure. Do not mark R31 PASS until a real
24-hour active runtime record exists.

## Claim ceiling

The installed system is a usable read-only owner-console runtime with repository
and browser smoke evidence. It is not yet proven as a 24-hour durable life process
or production autonomous evolution system.

## Next evidence step

Run or wire a real 24-hour owner-host or isolated self-hosted-runner soak that
records active elapsed time, unique cycle ids, heartbeats, restart continuity,
integrity checks, and resource growth. Then rerun:

```powershell
.\scripts\run_life_campaign_30.ps1 `
  -InstallRoot D:\WLS\wls-0.9.0.dev1-ui-20260706 `
  -CampaignHome D:\WLS\campaigns\life-campaign-30 `
  -StartRound R31 `
  -EndRound R40 `
  -Execute
```
