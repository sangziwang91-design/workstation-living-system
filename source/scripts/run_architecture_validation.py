from __future__ import annotations

from pathlib import Path
import argparse
import json
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "source" / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from wls.architecture_validation import (  # noqa: E402
    ArchitecturePassResult,
    validate_browser_computer_organs,
    validate_coding_worktree_candidate,
    validate_p01_registry,
    validate_runtime_approval_receipts,
    validate_runtime_event_ingress,
    validate_runtime_provider_route,
)


PASS_CONTRACTS = {
    "P06": "external memory remains shadow-only by registry rule",
    "P07": "MCP/A2A outputs are validated candidates, not canonical truth",
    "P10": "workbench templates bind to existing planning/evolution/skills authorities",
}


def run_validation() -> dict[str, object]:
    results: list[ArchitecturePassResult] = [validate_p01_registry()]
    with tempfile.TemporaryDirectory(prefix="wls-architecture-validation-") as temp:
        temp_path = Path(temp)
        results.extend(validate_runtime_event_ingress(temp_path / "event-home"))
        results.append(validate_runtime_provider_route(temp_path / "provider-home"))
        results.append(validate_runtime_approval_receipts(temp_path / "approval-home"))
        results.append(validate_browser_computer_organs())
        results.append(validate_coding_worktree_candidate(temp_path / "coding-worktree"))
    for pass_id, note in PASS_CONTRACTS.items():
        results.append(
            ArchitecturePassResult(
                pass_id,
                "ADMIT_DESIGN_ONLY",
                ["source/tests/test_living_agent_os_capabilities.py"],
                [note],
            )
        )
    return {
        "task_id": "WLS-LIVING-AGENT-OS-CAPABILITIES-001",
        "claim_ceiling": "repository-level contracts and tests only; no live/external operation proven",
        "results": [item.to_dict() for item in results],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run WLS architecture validation passes")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    result = run_validation()
    payload = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
