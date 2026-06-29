from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import argparse
import json
import tempfile

from wls.examiner.anchors import AnchorBank, SealedAnchorManifest
from wls.examiner.integrity import current_implementation_digest
from wls.examiner.constitution import Constitution
from wls.examiner.fixtures import DEFECT_NAMES, RULE_SEQUENCE, hidden_holdout_cases, make_defect
from wls.examiner.models import ExaminerVersion, Verdict, digest_json
from wls.examiner.store import ExaminerStore, StandaloneDatabase
from wls.examiner.system import ExaminerSystem, IdFactory


class DeterministicClock:
    def __init__(self) -> None:
        self.tick = 0

    def __call__(self) -> str:
        value = f"2026-06-28T07:{self.tick // 60:02d}:{self.tick % 60:02d}+00:00"
        self.tick += 1
        return value


def run(output: Path, constitution_path: Path) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    constitution = Constitution.load(str(constitution_path))
    holdout = AnchorBank(
        cases=hidden_holdout_cases(),
        bank_id="WLS-EXAMINER-SANDBOX-HOLDOUT-V1",
        split="sandbox_holdout",
    )
    holdout.assert_unique()
    seal_secret = b"sandbox-only-not-production"
    manifest = SealedAnchorManifest.create(holdout, seal_secret)
    clock = DeterministicClock()

    db = StandaloneDatabase(output / "examiner-sandbox.db")
    store = ExaminerStore(db)
    incumbent = ExaminerVersion(
        version_id="examiner-v00",
        epoch_id="EPOCH-0000",
        constitution_digest=constitution.digest,
        anchor_manifest_digest=holdout.digest,
        implementation_digest=current_implementation_digest(),
        enabled_rules=(),
        parent_version_id=None,
        created_at=clock(),
    )
    system = ExaminerSystem(
        constitution,
        store,
        incumbent,
        clock=clock,
        id_factory=IdFactory(prefix="sandbox", deterministic=True),
    )

    rounds: list[dict] = []
    working = incumbent
    revealed_cases = []
    for round_number, rule_id in enumerate(RULE_SEQUENCE, 1):
        defect = make_defect(rule_id, 0)
        revealed_cases.append(defect)
        pre = system.evaluate(defect.candidate, version=working, record=False)
        if pre.verdict != Verdict.ALLOW:
            raise AssertionError(
                f"round {round_number} defect was already caught by another rule: "
                f"{pre.verdict.value}"
            )

        challenger = ExaminerVersion(
            version_id=f"examiner-v{round_number:02d}",
            epoch_id=f"CANDIDATE-{round_number:04d}",
            constitution_digest=constitution.digest,
            anchor_manifest_digest=holdout.digest,
            implementation_digest=current_implementation_digest(),
            enabled_rules=tuple([*working.enabled_rules, rule_id]),
            parent_version_id=working.version_id,
            created_at=clock(),
        )
        post = system.evaluate(defect.candidate, version=challenger, record=False)
        if post.verdict != defect.expected:
            raise AssertionError(
                f"round {round_number} post-fix verdict {post.verdict.value} != "
                f"{defect.expected.value}"
            )
        before_predictions = [
            system.evaluate(case.candidate, version=working, record=False).verdict
            for case in revealed_cases
        ]
        after_predictions = [
            system.evaluate(case.candidate, version=challenger, record=False).verdict
            for case in revealed_cases
        ]
        before_correct = sum(
            prediction == case.expected
            for case, prediction in zip(revealed_cases, before_predictions, strict=True)
        )
        after_correct = sum(
            prediction == case.expected
            for case, prediction in zip(revealed_cases, after_predictions, strict=True)
        )
        if after_correct != len(revealed_cases) or after_correct <= before_correct:
            raise AssertionError(
                f"round {round_number} did not produce deterministic validation gain"
            )
        round_report = {
            "round": round_number,
            "defect": DEFECT_NAMES[rule_id],
            "rule_added": rule_id,
            "incumbent_candidate_version": working.version_id,
            "challenger_candidate_version": challenger.version_id,
            "pre_fix_verdict": pre.verdict.value,
            "expected_verdict": defect.expected.value,
            "post_fix_verdict": post.verdict.value,
            "revealed_validation_cases": len(revealed_cases),
            "revealed_validation_accuracy_before": before_correct / len(revealed_cases),
            "revealed_validation_accuracy_after": after_correct / len(revealed_cases),
            "promotion_state": "CANDIDATE_ONLY_NOT_PROMOTED",
            "direction": (
                "retain the rule in the challenger bundle, keep the production epoch frozen, "
                "and expose the next uncaught defect class"
            ),
            "result": "PASS",
        }
        rounds.append(round_report)
        (output / f"round-{round_number:02d}.json").write_text(
            json.dumps(round_report, indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        working = challenger

    # Only the completed ten-rule challenger is compared against the sealed holdout.
    # Intermediate candidates are deliberately not promoted while critical false passes remain.
    for rule_id in RULE_SEQUENCE:
        system.evaluate(make_defect(rule_id, 99).candidate, record=True)
    final_version = replace(
        working,
        epoch_id="EPOCH-0010",
        version_id="examiner-v10-final",
        parent_version_id=incumbent.version_id,
        created_at=clock(),
    )
    final_decision = system.compare_versions(incumbent, final_version, holdout)
    if not final_decision.eligible:
        raise AssertionError(f"final challenger not eligible: {final_decision.reason_codes}")
    promotion_id = "sandbox-promotion-final"
    system.request_promotion(final_decision, promotion_id=promotion_id)
    stale_count = system.apply_promotion(
        final_version,
        final_decision,
        owner_actor="sandbox-owner",
        owner_approval_ref="sandbox-final-approval",
        promotion_id=promotion_id,
        holdout=holdout,
    )

    final_predictions = [
        system.evaluate(case.candidate, record=False).verdict for case in holdout.cases
    ]
    final_correct = sum(
        prediction == case.expected
        for case, prediction in zip(holdout.cases, final_predictions, strict=True)
    )
    integrity_ok, integrity_detail = store.integrity_check()
    report = {
        "result": "PASS"
        if final_correct == len(holdout.cases) and integrity_ok
        else "FAIL",
        "rounds_completed": len(rounds),
        "intermediate_promotions": 0,
        "final_promotion_eligible": final_decision.eligible,
        "final_paired_wins": final_decision.wins,
        "final_paired_losses": final_decision.losses,
        "final_one_sided_p": final_decision.one_sided_p,
        "selective_erasure_stale_count": stale_count,
        "final_version": system.active_version.to_dict(),
        "final_version_digest": system.active_version.digest,
        "holdout_manifest": manifest.to_dict(),
        "holdout_manifest_verified": manifest.verify(seal_secret),
        "holdout_cases": len(holdout.cases),
        "holdout_correct": final_correct,
        "holdout_accuracy": final_correct / len(holdout.cases),
        "sqlite_integrity": integrity_detail,
        "rounds": rounds,
        "claim_ceiling": (
            "DETERMINISTIC_SANDBOX_EVIDENCE_ONLY; not owner-host deployment proof, "
            "not external-value proof, and not permission to auto-promote in production"
        ),
    }
    report["report_digest"] = digest_json(report)
    (output / "sandbox-summary.json").write_text(
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
        with tempfile.TemporaryDirectory(prefix="wls-examiner-sandbox-") as directory:
            report = run(Path(directory), constitution)
    print(json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True))
    return 0 if report["result"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
