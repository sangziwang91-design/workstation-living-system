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


def prior_successful_run(repo: str, run_id: int, head: str) -> int | None:
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
        if type(ident) is not int or ident <= 0 or ident == run_id:
            continue
        candidates.append(row)
    if not candidates:
        return None
    # gh returns newest-first, and IDs are monotonically assigned.
    prior = max(candidates, key=lambda x: x["databaseId"])
    if prior.get("conclusion") != "success":
        raise ValueError("previous complete generation failed: no silent reset")
    if prior.get("headSha") != head:
        raise ValueError("source SHA changed: frozen lineage needs fresh admission")
    return int(prior["databaseId"])


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
    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(payload["state/wls.db"])
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("checkpoint database integrity failed")
    finally:
        db.close()
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
        files[name] = path.read_bytes()
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
    prior_id = prior_successful_run(repo, run_id, head)
    parent = None
    parent_raw = None
    if prior_id is None:
        initialized = cli(home, "init")["status"]
        if initialized.get("cycle_count") != 0 or initialized.get("read_only") is not True:
            raise ValueError("invalid initial canonical state")
    else:
        archive = download_checkpoint(repo, prior_id, workspace / ".g1-prior")
        parent, parent_raw = restore_checkpoint(
            archive, home, expected_head=head, expected_run=prior_id
        )
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
        home, workspace / ".g1-checkpoint", head=head, run_id=run_id,
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
