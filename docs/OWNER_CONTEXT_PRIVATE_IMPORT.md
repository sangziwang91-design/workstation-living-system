# WLS owner context: private handoff and automatic inheritance

This is a bounded **local-first, owner-reviewed knowledge bridge**, not a bulk ChatGPT history export, model fine-tuning, an executable skill importer, or a claim of full autonomy. It extends the single `LivingSystem`, `MemoryStore` and `GoalStore`; it does **not** create a second memory/planner/identity authority.

## Runtime behavior

Place `owner_context.json` in `<WLS_HOME>/secrets/`. On every real `wls once` / daemon cycle, the canonical runtime checks this private location. If a reviewed bundle is present, it imports memories and explicitly activated **read-only** goals into WLS SQLite; skills and methods remain contextual knowledge, never directly promoted executable actions. The same bytes are not re-imported on later cycles or restarts. Results are available at `runtime.db.get_runtime("owner_context_last_import")` with counts and SHA-256 only.

The public GitHub repository and hosted Actions never receive personal memory text. No GitHub synchronization, remote ChatGPT account access, private holdout ingestion, or direct API key is required or implemented. The importer itself does not call models, networks or shell commands. The OpenAI-compatible **remote planner denies any canonical owner_context data by default** at its outbound boundary; it can fall back to the existing local cognitive/deterministic planner. An explicit local provider configuration flag `allow_owner_context_remote: true` permits remote disclosure for that planner. Loopback endpoints are treated as local, but a local proxy could still forward data, so the operator controls the endpoint. **This is not a full cross-tool DLP boundary**: independently configured external workers, tools, connectors or other outbound channels require their own restrictions.

## Private file contract

```json
{
  "schema": "wls.owner_context.v1",
  "consent": "owner_reviewed_local_import",
  "entries": [
    {
      "id": "wls-growth-20261010",
      "kind": "goal",
      "text": "Find and verify transferable improvement in real WLS tasks",
      "source_ref": "owner:direct:2026-10-10",
      "origin": "owner_direct",
      "activate_readonly": true
    },
    {
      "id": "method-evidence-20261010",
      "kind": "method",
      "text": "Prefer source-verified real task improvements over iteration counts",
      "source_ref": "owner:direct:2026-10-10",
      "origin": "owner_direct",
      "activate_readonly": false
    }
  ]
}
```

Allowed kinds: `preference`, `method`, `skill`, `goal`, `history`, `hypothesis`. Origins: `owner_direct`, `owner_retrospective`, `assistant_summary`. Only direct owner goals may be activated, and then only as a bounded read-only goal under existing policy. Assistant summaries receive lower confidence; no imported statement becomes a verified external fact or policy override.

Maximum 512,000 input bytes, 128 entries per file, 1,500 characters per entry. IDs are immutable and must change for substantive revisions; duplicate IDs, symlinks, invalid JSON and unreviewed bundles fail closed. Never place actual user conversations, names, credentials or other sensitive exports in this repository. Protect local WLS state and backups using OS access control and disk encryption.

## What is not yet delivered

- There is no authorized API that returns **all ChatGPT conversation histories and all editable memories** in this session. A later raw user export is needed for full coverage.
- There is no safe automatic promotion of prose skills into executable `SkillLibrary` entries. Verified task outcomes and existing promotion gates are still mandatory.
- Import into a local WLS instance is not equivalent to running a personalized agent inside ephemeral GitHub Actions. Hosted CI can prove code behavior using synthetic fixtures; real personal knowledge is local only.
- The next longitudinal acceptance is **equal-model/compute A-B with and without inherited owner methods** on withheld tasks, spanning real restarted sessions. Until then, this is a durable ingestion capability, not verified skill growth or RSI.
