from __future__ import annotations

from pathlib import Path
from typing import Any
import json
import subprocess  # nosec B404
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = REPO_ROOT / "source" / "src"
OUTPUT = REPO_ROOT / ".evolution" / "capabilities" / "current.json"

if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from wls.capabilities import CANONICAL_AUTHORITIES, baseline_registry  # noqa: E402
from wls.schemas import digest_json  # noqa: E402


def _git_value(args: list[str]) -> str:
    completed = subprocess.run(  # nosec
        ["git", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        shell=False,  # nosec
    )
    if completed.returncode != 0:
        return "UNKNOWN"
    return completed.stdout.strip()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def build_inventory() -> dict[str, Any]:
    registry = baseline_registry()
    worker_registry_path = REPO_ROOT / ".evolution" / "worker_registry.json"
    worker_registry = (
        _load_json(worker_registry_path) if worker_registry_path.exists() else {}
    )
    tests = sorted(path.name for path in (REPO_ROOT / "source" / "tests").glob("test_*.py"))
    scripts = sorted(path.name for path in (REPO_ROOT / "source" / "scripts").glob("*.py"))
    campaign_spec = _load_json(REPO_ROOT / "source" / "verification" / "life_campaign_30.json")
    round_ids = [
        item["round_id"]
        for item in campaign_spec.get("rounds", [])
        if isinstance(item, dict) and "round_id" in item
    ]
    architecture_passes = [f"P{index:02d}" for index in range(1, 71)]
    payload: dict[str, Any] = {
        "schema_version": 1,
        "inventory_id": "WLS-CAPABILITY-INVENTORY-DL001",
        "source": {
            "repo": str(REPO_ROOT),
            "branch": _git_value(["rev-parse", "--abbrev-ref", "HEAD"]),
            "head_before_inventory_write": _git_value(["rev-parse", "HEAD"]),
            "head_note": (
                "This is the commit used as input when regenerating the inventory; "
                "the commit containing current.json will necessarily differ."
            ),
        },
        "authority_model": {
            "single_subject": "wls.runtime.LivingSystem",
            "canonical_authorities": CANONICAL_AUTHORITIES,
            "duplicate_authority_allowed": False,
        },
        "capability_registry": {
            "summary": registry.summary(),
            "capabilities": registry.list(),
        },
        "worker_registry": {
            "path": ".evolution/worker_registry.json",
            "primary_code_worker_id": worker_registry.get("primary_code_worker_id"),
            "workers": worker_registry.get("workers", []),
            "admission_gate": worker_registry.get("admission_gate", {}),
        },
        "validation_surfaces": {
            "tests": tests,
            "scripts": scripts,
            "architecture_passes": architecture_passes,
            "campaign_rounds": round_ids,
            "base_campaign_rounds": round_ids[:30],
            "longitudinal_extension_rounds": round_ids[30:],
        },
        "claim_ceiling": (
            "repository inventory only; no live deployment, worker execution, "
            "Skill promotion, or longitudinal evidence claim"
        ),
    }
    payload["inventory_digest"] = digest_json(
        {
            "authority_model": payload["authority_model"],
            "capability_registry": payload["capability_registry"],
            "worker_registry": payload["worker_registry"],
            "validation_surfaces": payload["validation_surfaces"],
        }
    )
    return payload


def write_inventory(path: Path = OUTPUT) -> dict[str, Any]:
    payload = build_inventory()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def main() -> int:
    payload = write_inventory()
    print(json.dumps({"output": str(OUTPUT), "digest": payload["inventory_digest"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
