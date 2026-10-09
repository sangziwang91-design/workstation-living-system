"""Private, external risk-strategy holdout evaluator (candidate-only evidence).

The evaluator runs from a TRUSTED WLS checkout, and the only contestant input
is a bounded data-only risk_terms JSON. Nuomi retains all private cases and
answers outside Git; this program never prints or uploads them. No model calls,
network, git writes, or runtime promotion occur here.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
from math import comb, sqrt
from pathlib import Path
import re
import sys

from wls.schemas import RiskLevel
from wls.task_admission import (
    _validate_rsi_risk_strategy,
    admit_with_rsi_risk_strategy,
)

SCHEMA = "wls.risk_holdout.v1"
VERSION = "wls.private_holdout_result.v1"
MAX_BYTES = 512_000
MAX_CASES = 2_000
MAX_STRATEGY_BYTES = 4_096
MIN_CASES = 60
HEX_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def load_json(path: Path, maximum: int):
    data = path.read_bytes()
    if not data or len(data) > maximum:
        raise ValueError("missing or oversized input")

    def no_duplicate_keys(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError("duplicate JSON key")
            obj[key] = value
        return obj

    return json.loads(
        data.decode("utf-8"), object_pairs_hook=no_duplicate_keys,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError("non-finite JSON")),
    )


def evaluator_digest(repo: Path) -> str:
    """Evaluator identity is computed by the operator, never by the candidate."""
    paths = [
        "source/src/wls/task_admission.py",
        "source/src/wls/schemas.py",
        "source/scripts/run_external_risk_holdout.py",
    ]
    digest = sha256()
    for path in paths:
        file = repo / path
        if not file.is_file():
            raise ValueError("trusted evaluator source unavailable")
        digest.update(path.encode("utf-8") + b"\0")
        digest.update(sha256(file.read_bytes()).digest())
    return digest.hexdigest()


def parse_cases(value) -> list[tuple[str, RiskLevel]]:
    if not isinstance(value, dict) or set(value) != {"schema", "cases"}:
        raise ValueError("incorrect case document structure")
    if value["schema"] != SCHEMA:
        raise ValueError("unknown private case schema")
    rows = value["cases"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CASES:
        raise ValueError("invalid case count")
    seen = set()
    cases = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"id", "request", "expected_risk"}:
            raise ValueError("invalid case fields")
        case_id, request, risk = row["id"], row["request"], row["expected_risk"]
        if (
            not isinstance(case_id, str)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,80}", case_id)
            or case_id in seen
            or not isinstance(request, str)
            or not 1 <= len(request.encode("utf-8")) <= 1024
            or not isinstance(risk, str)
            or risk not in RiskLevel.__members__
        ):
            raise ValueError("invalid case identity, input or expected label")
        seen.add(case_id)
        cases.append((request, RiskLevel[risk]))
    return cases


def load_strategy(path: Path) -> dict[str, list[str]]:
    value = load_json(path, MAX_STRATEGY_BYTES)
    if not isinstance(value, dict):
        raise ValueError("strategy must be an object")
    rules = value.get("risk_terms")
    return _validate_rsi_risk_strategy({"risk_terms": rules})


def wilson(successes: int, size: int) -> list[float]:
    z = 1.959963984540054
    p = successes / size
    d = 1 + z*z/size
    center = (p + z*z/(2*size)) / d
    radius = z * sqrt(p*(1-p)/size + z*z/(4*size*size)) / d
    return [round(max(0, center-radius), 6), round(min(1, center+radius), 6)]


def paired_pvalue(corrected: int, regressed: int) -> float:
    """Exact one-sided McNemar sign-test tail over discordant paired cases."""
    changed = corrected + regressed
    if changed == 0:
        return 1.0
    return sum(comb(changed, k) for k in range(corrected, changed+1)) / (2**changed)


def measure(cases: list[tuple[str, RiskLevel]], baseline: dict, candidate: dict):
    base_pass = cand_pass = corrected = regressed = 0
    for task, expected in cases:
        a = admit_with_rsi_risk_strategy(task, baseline).risk_floor == expected
        b = admit_with_rsi_risk_strategy(task, candidate).risk_floor == expected
        base_pass += a
        cand_pass += b
        corrected += (not a and b)
        regressed += (a and not b)
    n = len(cases)
    return {
        "n": n,
        "baseline_passed": base_pass,
        "candidate_passed": cand_pass,
        "baseline_score": round(base_pass/n, 8),
        "candidate_score": round(cand_pass/n, 8),
        "baseline_wilson_95": wilson(base_pass, n),
        "candidate_wilson_95": wilson(cand_pass, n),
        "paired_corrected": corrected,
        "paired_regressed": regressed,
        "paired_p_one_sided": round(paired_pvalue(corrected, regressed), 10),
        "delta": round((cand_pass-base_pass)/n, 8),
    }


def run(args) -> tuple[int, dict]:
    repo = args.repo.resolve()
    cases_path = args.cases.resolve()
    try:
        # A candidate may observe the repository. The private questions must
        # not be inside it even temporarily.
        cases_path.relative_to(repo)
    except ValueError:
        pass
    else:
        raise ValueError("private cases must be outside the repository checkout")

    case_digest = sha256(cases_path.read_bytes()).hexdigest()
    source_digest = evaluator_digest(repo)
    if args.manifest_only:
        return 0, {
            "schema": VERSION, "status": "MANIFEST_ONLY",
            "case_sha256": case_digest, "evaluator_sha256": source_digest,
            "private_cases_in_git": False,
        }
    if not args.expected_case_sha256 or not args.expected_evaluator_sha256:
        return 2, {"schema": VERSION, "status": "UNMEASURED",
                   "reason": "frozen manifest not supplied"}
    if not HEX_SHA256.fullmatch(args.expected_case_sha256) or not HEX_SHA256.fullmatch(args.expected_evaluator_sha256):
        return 2, {"schema": VERSION, "status": "UNMEASURED",
                   "reason": "invalid frozen manifest digest"}
    if case_digest != args.expected_case_sha256 or source_digest != args.expected_evaluator_sha256:
        return 3, {
            "schema": VERSION, "status": "STANDARD_MOVED",
            "case_sha256": case_digest, "evaluator_sha256": source_digest,
            "eligible": False, "claim": "no cross-standard improvement allowed",
        }
    cases = parse_cases(load_json(cases_path, MAX_BYTES))
    if len(cases) < args.min_n:
        return 2, {"schema": VERSION, "status": "UNMEASURED",
                   "n": len(cases), "minimum_n": args.min_n,
                   "reason": "insufficient independent cases"}
    baseline = load_strategy(args.baseline)
    candidate = load_strategy(args.candidate)
    result = measure(cases, baseline, candidate)
    # This is a conservative promotion *recommendation*, not an owner action.
    qualified = (result["delta"] > 0 and result["paired_regressed"] == 0
                 and result["paired_p_one_sided"] < 0.05)
    return (0 if qualified else 1), {
        "schema": VERSION,
        "status": "QUALIFIED_CANDIDATE_ONLY" if qualified else "NO_MEASURED_GAIN",
        "eligible": qualified, "metric": result,
        "case_sha256": case_digest, "evaluator_sha256": source_digest,
        "source_type": "private_external_holdout",
        "authority": "Nuomi_review_required_no_live_promotion",
        "case_content_in_report": False,
        "warning": "Repeated peeking at holdout invalidates independent evidence",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--candidate", type=Path)
    parser.add_argument("--expected-case-sha256")
    parser.add_argument("--expected-evaluator-sha256")
    parser.add_argument("--min-n", type=int, default=MIN_CASES)
    parser.add_argument("--manifest-only", action="store_true")
    args = parser.parse_args()
    try:
        if args.min_n < 1 or args.min_n > MAX_CASES:
            raise ValueError("invalid minimum sample size")
        if not args.manifest_only and (args.baseline is None or args.candidate is None):
            raise ValueError("baseline and candidate strategies required")
        code, response = run(args)
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError):
        # Fail closed without printing the secret case payload or exception text.
        code, response = 2, {
            "schema": VERSION, "status": "UNMEASURED", "eligible": False,
            "reason": "invalid input or missing external evaluator evidence",
        }
    print(json.dumps(response, sort_keys=True, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
