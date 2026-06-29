from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import argparse
import json
import tempfile

from wls.examiner.anchors import AnchorBank
from wls.examiner.integrity import current_implementation_digest
from wls.examiner.constitution import Constitution
from wls.examiner.fixtures import RULE_SEQUENCE, hidden_holdout_cases, make_control
from wls.examiner.models import CandidateChange, ExaminerVersion, Verdict, digest_json
from wls.examiner.store import ExaminerStore, StandaloneDatabase
from wls.examiner.system import ExaminerSystem, IdFactory


class FixedClock:
    def __init__(self) -> None:
        self.index = 0

    def __call__(self) -> str:
        value = f"2026-06-28T08:00:{self.index:02d}+00:00"
        self.index += 1
        return value


def _build(
    db_path: Path,
    constitution: Constitution,
    holdout: AnchorBank,
    clock: FixedClock,
    *,
    version_id: str = "examiner-v10-final",
    epoch_id: str = "EPOCH-0010",
    deterministic_ids: bool = True,
) -> ExaminerSystem:
    version = ExaminerVersion(
        version_id=version_id,
        epoch_id=epoch_id,
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=RULE_SEQUENCE,
        created_at="2026-06-28T08:00:00+00:00",
    )
    store = ExaminerStore(StandaloneDatabase(db_path))
    return ExaminerSystem(
        constitution,
        store,
        version,
        clock=clock,
        id_factory=IdFactory(
            prefix="adversarial",
            deterministic=deterministic_ids,
        ),
    )


def run(output: Path, constitution_path: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    db_path = output / "adversarial.db"
    db_path.unlink(missing_ok=True)
    for suffix in ("-wal", "-shm"):
        Path(str(db_path) + suffix).unlink(missing_ok=True)

    constitution = Constitution.load(str(constitution_path))
    holdout = AnchorBank(
        cases=hidden_holdout_cases(),
        bank_id="WLS-EXAMINER-SANDBOX-HOLDOUT-V1",
        split="sandbox_holdout",
    )
    clock = FixedClock()
    system = _build(db_path, constitution, holdout, clock)
    trials: list[dict] = []

    # A1 — a path traversal that the pre-fix normalizer collapsed into an allowed path.
    traversal = CandidateChange(
        candidate_id="attack-path-traversal",
        title="path traversal",
        task_id="ADV-001",
        allowed_paths=("docs/**",),
        changed_paths=("../../docs/allowed-looking.md",),
        submission_nonce="adv-path-001",
    )
    result = system.evaluate(traversal, record=False)
    blocked = result.verdict == Verdict.BLOCK and any(
        finding.rule_id == "C003_CANONICAL_PATHS" for finding in result.findings
    )
    trials.append(
        {
            "trial": 1,
            "attack": "path_traversal_scope_bypass",
            "pre_fix_observation": "ALLOW",
            "real_defect": "leading ../ segments were stripped before scope matching",
            "repair": "reject absolute, traversing, backslash, NUL, and non-canonical paths before rule evaluation",
            "expected": "BLOCK",
            "actual": result.verdict.value,
            "detail": [item.to_dict() for item in result.findings],
            "result": "PASS" if blocked else "FAIL",
        }
    )

    # A2 — process restart previously reset the in-memory counter and reused verdict IDs.
    control_a = replace(
        make_control(700).candidate,
        candidate_id="restart-a",
        submission_nonce="restart-a-nonce",
    )
    first = system.evaluate(control_a)
    restarted = _build(
        db_path,
        constitution,
        holdout,
        clock,
        deterministic_ids=False,
    )
    control_b = replace(
        make_control(701).candidate,
        candidate_id="restart-b",
        submission_nonce="restart-b-nonce",
    )
    second = restarted.evaluate(control_b)
    collision_free = first.verdict_id != second.verdict_id
    trials.append(
        {
            "trial": 2,
            "attack": "restart_primary_key_collision",
            "pre_fix_observation": "UNIQUE constraint failed: examiner_verdicts.verdict_id",
            "real_defect": "default process-local counters restarted at one",
            "repair": "runtime IDs use UUID entropy; deterministic counters require explicit sandbox mode",
            "expected": "NO_COLLISION",
            "actual": "NO_COLLISION" if collision_free else "COLLISION",
            "detail": {
                "collision_free": collision_free,
                "runtime_id_mode": "UUID_ENTROPY",
            },
            "result": "PASS" if collision_free else "FAIL",
        }
    )

    # A3 — a different evaluator body reused an existing version ID and was silently ignored.
    collision = ExaminerVersion(
        version_id=system.active_version.version_id,
        epoch_id="EPOCH-COLLISION",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=("R001_SCOPE_INTEGRITY",),
        created_at="2026-06-28T08:09:00+00:00",
    )
    identity_blocked = False
    detail = ""
    try:
        system.store.register_version(collision)
    except RuntimeError as exc:
        identity_blocked = True
        detail = str(exc)
    trials.append(
        {
            "trial": 3,
            "attack": "evaluator_version_identity_collision",
            "pre_fix_observation": "ACCEPTED_BY_INSERT_OR_IGNORE",
            "real_defect": "version ID collisions could hide a different rule bundle",
            "repair": "version ID and digest form an immutable identity; epoch IDs cannot be replaced or reopened",
            "expected": "BLOCK",
            "actual": "BLOCK" if identity_blocked else "BYPASS",
            "detail": detail,
            "result": "PASS" if identity_blocked else "FAIL",
        }
    )

    # A4 — apply_promotion previously trusted a caller-constructed eligible decision.
    partial = ExaminerVersion(
        version_id="partial-challenger",
        epoch_id="EPOCH-PARTIAL",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=("R001_SCOPE_INTEGRITY",),
        parent_version_id=system.active_version.version_id,
        created_at="2026-06-28T08:10:00+00:00",
    )
    real_decision = system.compare_versions(system.active_version, partial, holdout)
    forged = replace(
        real_decision,
        eligible=True,
        status="AWAITING_OWNER",
        reason_codes=(),
        wins=max(1, real_decision.wins),
        losses=0,
        one_sided_p=0.0,
    )
    promotion_id = system.request_promotion(forged, promotion_id="forged-promotion")
    promotion_blocked = False
    detail = ""
    try:
        system.apply_promotion(
            partial,
            forged,
            owner_actor="owner",
            owner_approval_ref="adversarial-forged-decision",
            promotion_id=promotion_id,
            holdout=holdout,
        )
    except RuntimeError as exc:
        promotion_blocked = True
        detail = str(exc)
    trials.append(
        {
            "trial": 4,
            "attack": "forged_promotion_decision",
            "pre_fix_observation": "APPLIED",
            "real_defect": "promotion trusted caller-supplied eligibility without replaying the sealed comparison",
            "repair": "digest-bind the decision and atomically replay comparison against the same holdout before owner-approved application",
            "expected": "BLOCK",
            "actual": "BLOCK" if promotion_blocked else "BYPASS",
            "detail": detail,
            "result": "PASS" if promotion_blocked else "FAIL",
        }
    )

    # A5 — duplicate evidence IDs let the last entry redefine an earlier source;
    # a one-character digest was also accepted as a verified receipt.
    ambiguous = CandidateChange.from_dict(
        {
            "candidate_id": "attack-evidence-ambiguity",
            "title": "duplicate evidence",
            "task_id": "ADV-005",
            "allowed_paths": ["docs/**"],
            "changed_paths": ["docs/result.md"],
            "claims": [
                {
                    "text": "external value proven",
                    "level": "EXTERNAL_VERIFIED",
                    "claim_type": "external_effect",
                    "evidence_ids": ["same-id"],
                }
            ],
            "evidence": [
                {
                    "source_id": "same-id",
                    "source_kind": "model_output",
                    "verification": "UNKNOWN",
                    "independent": False,
                },
                {
                    "source_id": "same-id",
                    "source_kind": "external_usage",
                    "verification": "EXTERNAL_VERIFIED",
                    "digest": "x",
                    "independent": True,
                },
            ],
            "submission_nonce": "adv-evidence-005",
        }
    )
    evidence_result = system.evaluate(ambiguous, record=False)
    evidence_blocked = evidence_result.verdict == Verdict.BLOCK and {
        item.rule_id for item in evidence_result.findings
    }.issuperset({"C004_EVIDENCE_ID_UNIQUENESS", "C005_VERIFIED_DIGEST_FORMAT"})
    trials.append(
        {
            "trial": 5,
            "attack": "duplicate_evidence_id_and_malformed_digest",
            "pre_fix_observation": "ALLOW",
            "real_defect": "last-write evidence indexing and non-canonical digests could launder a claim",
            "repair": "reject duplicate evidence IDs and require SHA-256 shape for verified evidence",
            "expected": "BLOCK",
            "actual": evidence_result.verdict.value,
            "detail": [item.to_dict() for item in evidence_result.findings],
            "result": "PASS" if evidence_blocked else "FAIL",
        }
    )

    integrity_ok, integrity_detail = system.store.integrity_check()
    report = {
        "result": "PASS"
        if all(item["result"] == "PASS" for item in trials) and integrity_ok
        else "FAIL",
        "trials_completed": len(trials),
        "trials": trials,
        "sqlite_integrity": integrity_detail,
        "claim_ceiling": "DETERMINISTIC_ADVERSARIAL_SANDBOX_ONLY",
    }
    report["report_digest"] = digest_json(report)
    for item in trials:
        (output / f"adversarial-{item['trial']:02d}.json").write_text(
            json.dumps(item, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
    (output / "adversarial-summary.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--constitution", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    constitution = args.constitution or root / ".evolution/examiner/constitution.json"
    if args.output:
        report = run(args.output, constitution)
    else:
        with tempfile.TemporaryDirectory(prefix="wls-examiner-adversarial-") as directory:
            report = run(Path(directory), constitution)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
