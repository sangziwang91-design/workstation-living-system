"""GitHub-hosted Windows-only WLS generations, with verified cross-run state.

This driver is an external verifier for the *one* canonical LivingSystem.
It never imports private owner data or lets model-generated code execute.
Public Actions artifacts contain deliberately disposable, synthetic state and
a PUBLIC synthetic ledger key: provenance is the trusted GitHub run identity,
NOT secrecy of the included key.
"""
from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import zipfile

from verify_hosted_living_loop import cli, validate_cycle

SCHEMA = "wls.g1.github_windows_generation.v1"
ARTIFACT = "wls-g1-windows-generation-state"
WORKFLOW = "wls-g1-windows-generation.yml"
FILES = ("config.json", "state/wls.db", "secrets/evidence.key")
SHA = re.compile(r"[0-9a-f]{40}\Z")
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_CAPTURE = 128 * 1024


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def gh_json(*args: str) -> object:
    result = subprocess.run(
        ["gh", *args], stdin=subprocess.DEVNULL, capture_output=True,
        timeout=45, check=False,
    )
    if result.returncode or len(result.stdout) > MAX_CAPTURE:
        raise ValueError("trusted GitHub workflow metadata unavailable")
    try:
        return json.loads(result.stdout)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid GitHub workflow metadata") from exc


def previous_verified_run(repo: str, run_id: int) -> dict | None:
    """Find the latest successful *persisted* generation, not a failed attempt.

    At most three consecutive failed runs can be bypassed, and every bypass
    is explicitly recorded in the next receipt. No state is sourced from a
    failed run (the workflow uploads checkpoint only on success).
    """
    raw = gh_json(
        "run", "list", "--repo", repo, "--workflow", WORKFLOW,
        "--branch", "main", "--limit", "50",
        "--json", "databaseId,status,conclusion,event,headSha,createdAt",
    )
    if not isinstance(raw, list):
        raise ValueError("untrusted GitHub run list")
    candidates = []
    for row in raw:
        if not isinstance(row, dict) or row.get("status") != "completed":
            continue
        if row.get("event") not in {"push", "schedule", "workflow_dispatch"}:
            continue
        ident = row.get("databaseId")
        # Replaying an older run must not adopt any future state.
        if type(ident) is not int or ident <= 0 or ident >= run_id:
            continue
        candidates.append(row)
    if not candidates:
        return None
    skipped: list[int] = []
    for row in sorted(candidates, key=lambda x: x["databaseId"], reverse=True):
        ident = row["databaseId"]
        conclusion = row.get("conclusion")
        if conclusion == "success":
            prior_sha = row.get("headSha")
            if not isinstance(prior_sha, str) or not SHA.fullmatch(prior_sha):
                raise ValueError("previous generation has invalid source provenance")
            return {
                "run_id": ident,
                "head_sha": prior_sha,
                "skipped_failed_run_ids": skipped,
            }
        if conclusion not in {"failure", "cancelled"}:
            raise ValueError("previous complete generation has unrecognized status")
        skipped.append(ident)
        if len(skipped) > 3:
            raise ValueError("too many failed generation attempts: owner review required")
    raise ValueError("previous complete generation failed: no silent reset")


def prior_successful_run(repo: str, run_id: int, head: str) -> int | None:
    """Strict compatibility check; never hides a failed prior generation."""
    prior = previous_verified_run(repo, run_id)
    if prior is None:
        return None
    if prior["skipped_failed_run_ids"]:
        raise ValueError("previous complete generation failed: no silent reset")
    if prior["head_sha"] != head:
        raise ValueError("source SHA changed: frozen lineage needs fresh admission")
    return int(prior["run_id"])


def download_checkpoint(repo: str, prior_run: int, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=False)
    command = [
        "gh", "run", "download", str(prior_run), "--repo", repo,
        "--name", ARTIFACT, "--dir", str(directory),
    ]
    result = subprocess.run(command, stdin=subprocess.DEVNULL,
                            capture_output=True, timeout=90, check=False)
    if result.returncode:
        raise ValueError("previous successful generation artifact is missing")
    archive = directory / "checkpoint.zip"
    if not archive.is_file() or archive.is_symlink():
        raise ValueError("missing or unsafe generation checkpoint")
    if archive.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("generation artifact exceeded byte budget")
    return archive


def consistent_sqlite_snapshot(path: Path) -> bytes:
    """Take a committed SQLite backup, including changes in an active WAL.

    A raw copy of wls.db with journal_mode=WAL is not portable to the
    :memory: deserializer and may omit committed pages in wls.db-wal.
    """
    with tempfile.TemporaryDirectory(prefix="wls-g1-db-backup-") as base:
        target = Path(base) / "portable.sqlite3"
        source = sqlite3.connect(path, timeout=10)
        try:
            backup = sqlite3.connect(target)
            try:
                source.backup(backup)
                if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("WLS snapshot integrity failed")
            finally:
                backup.close()
        finally:
            source.close()
        data = target.read_bytes()
    if not data.startswith(b"SQLite format 3\x00") or len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError("invalid or oversized portable SQLite snapshot")
    return data


def validated_portable_sqlite(raw: bytes) -> bytes:
    """Validate a historical WAL-mode DB on disk, then convert to DELETE mode.

    The original digest is checked first by restore_checkpoint(). Everything
    here occurs in an isolated temporary directory, before writing WLS_HOME.
    """
    if not raw.startswith(b"SQLite format 3\x00") or len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("checkpoint database is not a bounded SQLite file")
    with tempfile.TemporaryDirectory(prefix="wls-g1-db-restore-") as base:
        path = Path(base) / "restored.sqlite3"
        path.write_bytes(raw)
        connection = sqlite3.connect(path, timeout=10)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("checkpoint database integrity failed")
            # A legacy checkpoint may have been copied directly from WAL mode.
            # Opening it as a normal disk database works; changing to DELETE
            # makes the normalized copy independent of sidecar files.
            if connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError("checkpoint database could not normalize WAL")
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("normalized database integrity failed")
        finally:
            connection.close()
        clean = path.read_bytes()
    if len(clean) > MAX_ARTIFACT_BYTES or clean[18:20] != b"\x01\x01":
        raise ValueError("checkpoint database not portable after normalization")
    return clean


def restore_checkpoint(
    archive: Path, home: Path, *, expected_head: str, expected_run: int,
) -> tuple[dict, bytes]:
    if home.exists() and any(home.iterdir()):
        raise ValueError("restore may not overwrite an existing WLS home")
    raw = archive.read_bytes()
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError("oversized checkpoint")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        infos = zf.infolist()
        names = [z.filename for z in infos]
        if len(names) != len(FILES) + 1 or set(names) != {"manifest.json", *FILES}:
            raise ValueError("unexpected checkpoint contents")
        if any(
            item.is_dir() or item.file_size > MAX_ARTIFACT_BYTES
            or item.compress_size > MAX_ARTIFACT_BYTES
            for item in infos
        ):
            raise ValueError("checkpoint archive size or path invalid")
        if sum(item.file_size for item in infos) > MAX_ARTIFACT_BYTES:
            raise ValueError("decompressed checkpoint exceeds budget")
        payload = {name: zf.read(name) for name in names}
    raw_manifest = payload.pop("manifest.json")
    try:
        manifest = json.loads(raw_manifest)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("malformed generation manifest") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA
            or manifest.get("run_id") != expected_run
            or manifest.get("head_sha") != expected_head
            or type(manifest.get("generation")) is not int
            or not 0 <= manifest["generation"] <= 1000
            or type(manifest.get("cycle_count")) is not int
            or manifest["cycle_count"] < 1
            or manifest.get("synthetic_only") is not True
            or manifest.get("files_sha256") != {
                name: sha(payload[name]) for name in FILES
            }):
        raise ValueError("checkpoint provenance/digest mismatch")
    if len(payload["secrets/evidence.key"]) != 32:
        raise ValueError("invalid synthetic ledger key")
    try:
        config = json.loads(payload["config.json"])
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("invalid serialized WLS configuration") from exc
    if not isinstance(config, dict) or config.get("home") != str(home):
        raise ValueError("checkpoint Windows home does not match current runner")
    # The upstream archive digest authenticates the *original* database;
    # only then do we validate and normalize it on an isolated temp disk.
    # Legacy WAL-mode blobs cannot be deserialized into an in-memory DB.
    payload["state/wls.db"] = validated_portable_sqlite(payload["state/wls.db"])
    # Mutate only after every file and manifest passed validation.
    for name in FILES:
        dest = home / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload[name])
    return manifest, raw_manifest


def checkpoint(
    home: Path, directory: Path, *,
    head: str, run_id: int, parent: dict | None,
    prior_manifest_raw: bytes | None, count: int,
) -> dict:
    files: dict[str, bytes] = {}
    for name in FILES:
        path = home / name
        if not path.is_file() or path.is_symlink():
            raise ValueError("missing/unsafe public synthetic checkpoint source")
        files[name] = (
            consistent_sqlite_snapshot(path)
            if name == "state/wls.db" else path.read_bytes()
        )
    if any(len(data) > MAX_ARTIFACT_BYTES for data in files.values()):
        raise ValueError("checkpoint file too large")
    now = datetime.now(UTC).isoformat()
    generation = parent["generation"] + 1 if parent is not None else 0
    first_at = parent["first_at"] if parent is not None else now
    manifest = {
        "schema": SCHEMA, "head_sha": head, "run_id": run_id,
        "parent_run_id": parent["run_id"] if parent is not None else None,
        "previous_manifest_sha256":
            sha(prior_manifest_raw) if prior_manifest_raw is not None else None,
        "generation": generation, "cycle_count": count,
        "first_at": first_at, "created_at": now,
        "parent_head_sha": parent["head_sha"] if parent is not None else None,
        "source_transition": parent is not None and parent["head_sha"] != head,
        "synthetic_only": True,
        "files_sha256": {name: sha(data) for name, data in files.items()},
        "model_calls": 0, "autonomous_skill_gain_proven": False,
    }
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "checkpoint.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
    return manifest


def run(repo: str, run_id: int, head: str, workspace: Path) -> dict:
    if (not SHA.fullmatch(head) or run_id <= 0
            or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)):
        raise ValueError("invalid GitHub source/run identity")
    home = workspace / ".g1-windows" / "home"
    if home.exists() and any(home.iterdir()):
        raise ValueError("refuse to overwrite previously used generation home")
    # Public synthetic history may cross a mainline source update only by
    # verifying the old checkpoint against its *own* run/source identity,
    # then verifying the restored state with the current canonical WLS.
    # The strict prior_successful_run() contract remains fail-closed.
    previous_record = previous_verified_run(repo, run_id)
    prior_id = previous_record["run_id"] if previous_record is not None else None
    prior_head = previous_record["head_sha"] if previous_record is not None else None
    skipped_attempts = (
        previous_record["skipped_failed_run_ids"]
        if previous_record is not None else []
    )
    parent = None
    parent_raw = None
    if prior_id is None:
        initialized = cli(home, "init")["status"]
        if initialized.get("cycle_count") != 0 or initialized.get("read_only") is not True:
            raise ValueError("invalid initial canonical state")
    else:
        archive = download_checkpoint(repo, prior_id, workspace / "g1-generation-prior")
        parent, parent_raw = restore_checkpoint(
            archive, home, expected_head=str(prior_head), expected_run=prior_id
        )
        # Cross-source transitions are admitted only for bounded, synthetic
        # checkpoints with independently validated GitHub run provenance.
        # The new executable must verify the old SQLite/evidence before any
        # new cycle is allowed. No silent restart or private owner migration.
        if prior_head != head and cli(home, "verify").get("ok") is not True:
            raise ValueError("restored synthetic state incompatible with new source")
    before = cli(home, "status")
    previous = int(before["cycle_count"])
    if parent is not None and previous != parent["cycle_count"]:
        raise ValueError("restored canonical cycle count mismatch")
    ids: list[str] = []
    for index in range(2):
        cycle = cli(home, "once")
        status = cli(home, "status")
        cycle_id = validate_cycle(cycle, status, previous + index)
        if cycle_id in ids:
            raise ValueError("duplicate life-cycle identity")
        ids.append(cycle_id)
    if cli(home, "verify").get("ok") is not True:
        raise ValueError("canonical evidence chain verification failed")
    cli(home, "sleep")
    if cli(home, "verify").get("ok") is not True:
        raise ValueError("post-sleep evidence verification failed")
    final_count = previous + 2
    if cli(home, "status").get("cycle_count") != final_count:
        raise ValueError("sleep changed generation state")
    manifest = checkpoint(
        home, workspace / "g1-generation-checkpoint", head=head, run_id=run_id,
        parent=parent, prior_manifest_raw=parent_raw, count=final_count,
    )
    age_hours = (
        (datetime.fromisoformat(manifest["created_at"])
         - datetime.fromisoformat(manifest["first_at"])).total_seconds() / 3600
    )
    return {
        "schema": SCHEMA, "status": "HOSTED_WINDOWS_GENERATION_PASS",
        "run_id": run_id, "head_sha": head, "generation": manifest["generation"],
        "parent_run_id": prior_id, "cycle_count": final_count,
        "restored_from_distinct_run": prior_id is not None,
        "failed_attempts_not_in_lineage": skipped_attempts,
        "source_transition": manifest["source_transition"],
        "parent_head_sha": manifest["parent_head_sha"],
        "transition_scope": "verified_public_synthetic_checkpoint_only",
        "elapsed_generation_hours": round(age_hours, 4),
        "synthetic_only": True, "local_owner_deployed": False,
        "model_calls": 0, "autonomous_skill_gain_proven": False,
        "longitudinal_72h_proven": False,
        "claim_ceiling": (
            "canonical_Windows_cross_run_cycles_only; "
            "no_owner_deployment_or_autonomous_skill_improvement"
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--repo", required=True)
    p.add_argument("--run-id", type=int, required=True)
    p.add_argument("--head", required=True)
    p.add_argument("--workspace", type=Path, required=True)
    args = p.parse_args()
    try:
        result = run(args.repo, args.run_id, args.head, args.workspace.resolve())
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error,
            zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
        result = {
            "schema": SCHEMA, "status": "BLOCKED",
            "error_type": type(exc).__name__,
            "claim_ceiling": "no_generation_claim_without_a_verified_receipt",
        }
    path = args.workspace / "wls-g1-windows-generation-receipt.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "HOSTED_WINDOWS_GENERATION_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
