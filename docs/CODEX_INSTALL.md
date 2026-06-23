# Codex Installation Handoff

## Objective

Install the standalone Workstation Living System without modifying existing Workstation services. Integration is explicitly out of scope for this installation.

## Procedure

1. Extract the release ZIP to a temporary directory.
2. Open PowerShell in that directory.
3. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\INSTALL.ps1 -WorkstationRoot "D:\Workstation"
```

4. Verify:

```powershell
.\VERIFY.ps1 -WorkstationRoot "D:\Workstation"
```

5. Start the standalone daemon only after verification:

```powershell
.\START.ps1 -WorkstationRoot "D:\Workstation"
```

## Required boundaries

- Preserve existing `D:\Workstation` files and services.
- Do not enable filesystem/Git/process/HTTP sensors until their exact roots and endpoints are reviewed.
- Do not change `read_only=true` during installation.
- Do not register a Windows service or scheduled task.
- Do not add API keys to configuration or the repository.
- Treat future five-system integration as a separate READ → VERIFY → MODIFY → TEST task.

## Acceptance

```powershell
D:\Workstation\.wls\wls.cmd self-check
D:\Workstation\.wls\wls.cmd verify
D:\Workstation\.wls\wls.cmd status
```

All three commands must exit successfully, and status must report `read_only: true`.
