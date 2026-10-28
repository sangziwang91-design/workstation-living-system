"""Score canonical task admission + routing + readiness on a public task cohort.

This taskpack is human-authored public development material. Accuracy on it is
a real deterministic test result, NOT private-task transfer or model intelligence.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any

from wls.schemas import RiskLevel
from wls.task_classifier import SideEffectClass, VerificationOracle
from wls.task_judgment import TaskJudgmentEngine, TaskJudgmentInput

SCHEMA = "wls.task_judgment.public_development.v1"
MAX_CASES = 200


def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, val in pairs:
        if key in result:
            raise ValueError("duplicate JSON key in taskpack")
        result[key] = val
    return result


def read_taskpack(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not 0 < path.stat().st_size <= 500_000:
        raise ValueError("missing, symlinked or oversized taskpack")
    data = json.loads(path.read_bytes(), object_pairs_hook=strict_object)
    if (not isinstance(data, dict) or data.get("schema") != SCHEMA
            or not isinstance(data.get("cases"), list)
            or not 10 <= len(data["cases"]) <= MAX_CASES):
        raise ValueError("malformed taskpack schema or sample count")
    return data


def to_input(item: dict[str, Any]) -> TaskJudgmentInput:
    allowed = set(TaskJudgmentInput.__dataclass_fields__) - {"task_id", "request"}
    if (not isinstance(item, dict) or not {"task_id", "request"} <= set(item)
            or set(item) - allowed - {"task_id", "request"}):
        raise ValueError("unapproved task fields")
    data = dict(item)
    for key in ("tags", "required_capabilities", "available_capabilities",
                "required_evidence", "available_evidence"):
        if key in data:
            if not isinstance(data[key], list) or not all(isinstance(v, str) for v in data[key]):
                raise ValueError("capability and evidence fields must be string lists")
            data[key] = frozenset(data[key])
    if "verification_oracles" in data:
        values = data["verification_oracles"]
        if not isinstance(values, list):
            raise ValueError("oracles must be a list")
        data["verification_oracles"] = tuple(VerificationOracle(v) for v in values)
    for key, enum in (("declared_risk", RiskLevel), ("side_effect_class", SideEffectClass)):
        if key in data:
            data[key] = enum(data[key])
    return TaskJudgmentInput(**data)


def evaluate(data: dict[str, Any]) -> dict[str, Any]:
    if data.get("schema") != SCHEMA or not isinstance(data.get("cases"), list):
        raise ValueError("unknown taskpack")
    observed: list[dict[str, Any]] = []
    seen: set[str] = set()
    family: Counter[str] = Counter()
    correct = 0
    for entry in data["cases"]:
        if not isinstance(entry, dict) or set(entry) != {"input", "expected"}:
            raise ValueError("untrusted test case format")
        task = to_input(entry["input"])
        if task.task_id in seen:
            raise ValueError("duplicate task ID")
        seen.add(task.task_id)
        expected = entry["expected"]
        if (not isinstance(expected, dict) or "status" not in expected
                or set(expected) - {"status", "risk", "kind", "mode"}):
            raise ValueError("invalid expected task decision")
        got = TaskJudgmentEngine().judge(task).to_dict()
        fields = {"status": got["status"], "risk": got["effective_risk"],
                  "kind": got["task_kind"], "mode": got["route_mode"]}
        correct_fields = {key: fields[key] == value for key, value in expected.items()}
        passed = all(correct_fields.values())
        correct += passed
        family[expected["status"]] += 1
        observed.append({
            "task_id": task.task_id, "expected": expected,
            "observed": {k: fields[k] for k in expected},
            "passed": passed, "field_passes": correct_fields,
            "blockers": got["blockers"],
        })
    total = len(observed)
    return {
        "schema": "wls.task_judgment.public_development_result.v1",
        "status": "PUBLIC_CALIBRATION_PASS" if correct == total else "PUBLIC_CALIBRATION_FAIL",
        "total": total, "correct": correct,
        "exact_accuracy": round(correct / total, 4),
        "expected_family_counts": dict(sorted(family.items())),
        "cases": observed,
        "model_calls": 0, "private_holdout_gain": "UNMEASURED",
        "recursive_improvement": "UNPROVEN",
        "claim_ceiling": "DETERMINISTIC_POLICY_JUDGMENT_ON_PUBLIC_AUTHOR_LABELED_CASES",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = evaluate(read_taskpack(args.dataset))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        report = {"schema": "wls.task_judgment.public_development_result.v1",
                  "status": "BLOCKED", "error_type": type(exc).__name__}
    args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "cases"}, sort_keys=True))
    if report.get("status") == "PUBLIC_CALIBRATION_FAIL":
        print(json.dumps({"failed_cases": [
            {"id": row["task_id"], "expected": row["expected"],
             "observed": row["observed"], "blockers": row["blockers"]}
            for row in report["cases"] if not row["passed"]
        ]}, sort_keys=True))
    return 0 if report["status"] == "PUBLIC_CALIBRATION_PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
