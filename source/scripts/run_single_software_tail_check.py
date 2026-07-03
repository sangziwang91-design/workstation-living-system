from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess  # nosec B404
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _contained(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _status_smoke(
    *,
    python_exe: Path,
    config_path: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    command = [
        str(python_exe),
        "-m",
        "wls",
        "--config",
        str(config_path),
        "status",
    ]
    try:
        result = subprocess.run(  # nosec B603
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except Exception as exc:
        return {
            "executed": True,
            "ok": False,
            "command": command,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "executed": True,
        "ok": result.returncode == 0,
        "command": command,
        "returncode": result.returncode,
        "stdout_tail": result.stdout[-4000:],
        "stderr_tail": result.stderr[-4000:],
    }


def run_tail_check(
    *,
    install_root: Path,
    campaign_home: Path,
    live_home: Path | None = None,
    run_status_smoke: bool = False,
    timeout_seconds: int = 30,
) -> dict[str, Any]:
    install_root = install_root.expanduser().resolve()
    campaign_home = campaign_home.expanduser().resolve()
    resolved_live_home = (
        live_home.expanduser().resolve()
        if live_home is not None
        else install_root / "home"
    )
    live_config = resolved_live_home / "config.json"
    live_db = resolved_live_home / "state" / "wls.db"
    campaign_config = campaign_home / "config.json"
    campaign_r30 = campaign_home / "campaign_evidence" / "R30" / "epoch_audit.json"
    before = {
        "live_config_sha256": _sha256(live_config),
        "live_db_sha256": _sha256(live_db),
    }
    checks = {
        "install_root_exists": install_root.exists(),
        "campaign_home_exists": campaign_home.exists(),
        "live_home_exists": resolved_live_home.exists(),
        "campaign_not_live_home": install_root != campaign_home,
        "campaign_not_runtime_live_home": resolved_live_home != campaign_home,
        "campaign_not_inside_live_home": not _contained(campaign_home, install_root),
        "campaign_not_inside_runtime_live_home": not _contained(
            campaign_home, resolved_live_home
        ),
        "campaign_config_exists": campaign_config.exists(),
        "campaign_r30_epoch_audit_exists": campaign_r30.exists(),
        "live_config_present": live_config.exists(),
        "live_db_present": live_db.exists(),
    }
    campaign_config_payload = _load_json(campaign_config) or {}
    checks["campaign_config_points_to_campaign_home"] = (
        Path(str(campaign_config_payload.get("home", ""))).resolve()
        == campaign_home
        if campaign_config_payload
        else False
    )
    smoke: dict[str, Any] = {"executed": False, "ok": None}
    if run_status_smoke:
        python_exe = install_root / "venv" / "Scripts" / "python.exe"
        checks["installed_python_exists"] = python_exe.exists()
        if python_exe.exists() and campaign_config.exists():
            smoke = _status_smoke(
                python_exe=python_exe,
                config_path=campaign_config,
                timeout_seconds=timeout_seconds,
            )
        else:
            smoke = {
                "executed": False,
                "ok": False,
                "reason": "installed python or campaign config missing",
            }
    after = {
        "live_config_sha256": _sha256(live_config),
        "live_db_sha256": _sha256(live_db),
    }
    checks["live_config_unchanged"] = (
        before["live_config_sha256"] == after["live_config_sha256"]
    )
    checks["live_db_unchanged"] = before["live_db_sha256"] == after["live_db_sha256"]
    required_checks = [
        "install_root_exists",
        "live_home_exists",
        "campaign_home_exists",
        "campaign_not_live_home",
        "campaign_not_runtime_live_home",
        "campaign_not_inside_live_home",
        "campaign_not_inside_runtime_live_home",
        "campaign_config_exists",
        "campaign_config_points_to_campaign_home",
        "live_config_present",
        "live_db_present",
        "live_config_unchanged",
        "live_db_unchanged",
    ]
    if run_status_smoke:
        required_checks.append("installed_python_exists")
    ok = all(bool(checks.get(name)) for name in required_checks) and (
        smoke["ok"] is not False
    )
    return {
        "receipt_type": "SINGLE_SOFTWARE_TAIL_CHECK",
        "status": "PASS_WITH_LIMITS" if ok else "FAIL",
        "install_root": str(install_root),
        "live_home": str(resolved_live_home),
        "campaign_home": str(campaign_home),
        "checks": checks,
        "live_hashes_before": before,
        "live_hashes_after": after,
        "status_smoke": smoke,
        "required_checks": required_checks,
        "claim_ceiling": (
            "disposable installed-package tail check only; does not merge, deploy, "
            "promote skills, mutate live config/database, or prove final product completeness"
        ),
    }


def record_tail_check_manifest(
    *,
    campaign_home: Path,
    output: Path,
    round_id: str = "R30",
    label: str = "single_software_tail_check",
) -> dict[str, Any]:
    campaign_home = campaign_home.expanduser().resolve()
    output = output.expanduser().resolve()
    if not output.exists() or not output.is_file():
        raise FileNotFoundError(f"tail-check output not found: {output}")
    manifest_path = campaign_home / "campaign_evidence" / "manifest.json"
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    else:
        manifest = {
            "schema_version": 1,
            "created_at": _utc_now(),
            "records": [],
        }
    records = manifest.setdefault("records", [])
    output_sha256 = _sha256(output)
    for record in records:
        if (
            record.get("kind") == "file"
            and record.get("round_id") == round_id
            and record.get("label") == label
            and Path(str(record.get("path", ""))).resolve() == output
            and record.get("sha256") == output_sha256
        ):
            return {
                "recorded": False,
                "record_id": record.get("id"),
                "manifest_path": str(manifest_path),
            }

    record_id = f"ev-{len(records) + 1:04d}"
    receipt = _load_json(output) or {}
    records.append(
        {
            "id": record_id,
            "round_id": round_id,
            "kind": "file",
            "label": label,
            "path": str(output),
            "sha256": output_sha256,
            "bytes": output.stat().st_size,
            "recorded_at": _utc_now(),
            "metadata": {
                "receipt_type": receipt.get("receipt_type"),
                "status": receipt.get("status"),
                "status_smoke_executed": (
                    receipt.get("status_smoke", {}).get("executed")
                    if isinstance(receipt.get("status_smoke"), dict)
                    else None
                ),
            },
        }
    )
    _atomic_write_json(manifest_path, manifest)
    return {
        "recorded": True,
        "record_id": record_id,
        "manifest_path": str(manifest_path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run WLS single-software disposable tail checks"
    )
    parser.add_argument("--install-root", required=True, type=Path)
    parser.add_argument("--live-home", type=Path)
    parser.add_argument("--campaign-home", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--record-manifest", action="store_true")
    parser.add_argument("--manifest-round-id", default="R30")
    parser.add_argument("--manifest-label", default="single_software_tail_check")
    parser.add_argument("--run-status-smoke", action="store_true")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    receipt = run_tail_check(
        install_root=args.install_root,
        live_home=args.live_home,
        campaign_home=args.campaign_home,
        run_status_smoke=args.run_status_smoke,
        timeout_seconds=args.timeout_seconds,
    )
    payload = json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
        if args.record_manifest:
            manifest_receipt = record_tail_check_manifest(
                campaign_home=args.campaign_home,
                output=args.output,
                round_id=args.manifest_round_id,
                label=args.manifest_label,
            )
            print(json.dumps(manifest_receipt, ensure_ascii=False, sort_keys=True))
    elif args.record_manifest:
        parser.error("--record-manifest requires --output")
    else:
        print(payload)
    return 0 if receipt["status"] == "PASS_WITH_LIMITS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
