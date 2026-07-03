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
    validate_external_memory_projection,
    validate_mcp_a2a_candidates,
    validate_phase2_coding_candidate_readonly_execution,
    validate_phase2_browser_readonly_runtime_execution,
    validate_phase2_external_handoff_runtime_receipts,
    validate_phase2_multimodal_asset_readonly_execution,
    validate_phase2_capability_epoch_audit_receipts,
    validate_phase2_owner_surface_and_readonly_organs,
    validate_phase2_preflighted_readonly_execution,
    validate_phase2_projection_review_and_rollback,
    validate_phase2_provider_route_runtime_receipts,
    validate_phase2_readonly_result_projection,
    validate_phase2_runtime_readonly_task_preview,
    validate_phase2_readonly_planner_admission,
    validate_phase2_readonly_execution_preflight,
    validate_phase2_research_composite_readonly_execution,
    validate_phase2_scheduler_due_event_runtime_intake,
    validate_phase2_skill_candidate_extraction_receipts,
    validate_phase2_learning_epoch_review_receipts,
    validate_phase2_typed_readonly_organ_profiles,
    validate_phase2_wechat_approval_channel_receipts,
    validate_phase2_voice_transcript_ingress_receipts,
    validate_phase2_local_notification_draft_receipts,
    validate_phase2_screen_snapshot_ingress_receipts,
    validate_phase2_browser_form_draft_receipts,
    validate_phase2_download_quarantine_draft_receipts,
    validate_phase2_document_ingress_receipts,
    validate_phase2_document_retrieval_preview,
    validate_phase2_document_readonly_execution,
    validate_phase2_document_projection_review,
    validate_phase2_document_skill_candidate_receipts,
    validate_phase2_document_skill_sandbox_receipts,
    validate_phase2_agentic_acceptance_trace,
    validate_phase2_agentic_task_harness,
    validate_p01_registry,
    validate_runtime_approval_receipts,
    validate_runtime_event_ingress,
    validate_runtime_provider_route,
    validate_workbench_templates,
)

PASS_CONTRACTS: dict[str, str] = {}
SELECTIVE_VALIDATION_PASSES = {"P01", "P42", "P43"}


def _selected(selected: set[str] | None, pass_id: str) -> bool:
    return selected is None or pass_id in selected


def _run_selected_validation(selected: set[str]) -> dict[str, object]:
    unknown = selected - SELECTIVE_VALIDATION_PASSES
    if unknown:
        raise ValueError(
            "selective architecture validation currently supports only "
            f"{sorted(SELECTIVE_VALIDATION_PASSES)}, got {sorted(unknown)}"
        )
    results: list[ArchitecturePassResult] = []
    with tempfile.TemporaryDirectory(prefix="wls-architecture-validation-") as temp:
        temp_path = Path(temp)
        if _selected(selected, "P01"):
            results.append(validate_p01_registry())
        if _selected(selected, "P42"):
            results.append(
                validate_phase2_agentic_task_harness(
                    temp_path / "phase2-agentic-task-harness-home"
                )
            )
        if _selected(selected, "P43"):
            results.append(
                validate_phase2_agentic_acceptance_trace(
                    temp_path / "phase2-agentic-acceptance-trace-home"
                )
            )
    return {
        "task_id": "WLS-LIVING-AGENT-OS-CAPABILITIES-001",
        "claim_ceiling": "repository-level contracts and tests only; no live/external operation proven",
        "results": [item.to_dict() for item in results],
    }


def run_validation(selected: set[str] | None = None) -> dict[str, object]:
    if selected is not None:
        return _run_selected_validation(selected)
    results: list[ArchitecturePassResult] = [validate_p01_registry()]
    with tempfile.TemporaryDirectory(prefix="wls-architecture-validation-") as temp:
        temp_path = Path(temp)
        results.extend(validate_runtime_event_ingress(temp_path / "event-home"))
        results.append(validate_runtime_provider_route(temp_path / "provider-home"))
        results.append(validate_runtime_approval_receipts(temp_path / "approval-home"))
        results.append(validate_browser_computer_organs())
        results.append(validate_coding_worktree_candidate(temp_path / "coding-worktree"))
        results.append(validate_mcp_a2a_candidates())
        results.append(validate_workbench_templates())
        results.append(validate_external_memory_projection())
        results.extend(
            validate_phase2_owner_surface_and_readonly_organs(
                temp_path / "phase2-owner-surface-home"
            )
        )
        results.append(validate_phase2_typed_readonly_organ_profiles())
        results.append(
            validate_phase2_runtime_readonly_task_preview(
                temp_path / "phase2-preview-home"
            )
        )
        results.append(
            validate_phase2_readonly_planner_admission(
                temp_path / "phase2-admission-home"
            )
        )
        results.append(
            validate_phase2_readonly_execution_preflight(
                temp_path / "phase2-preflight-home"
            )
        )
        results.append(
            validate_phase2_preflighted_readonly_execution(
                temp_path / "phase2-execution-home"
            )
        )
        results.append(
            validate_phase2_readonly_result_projection(
                temp_path / "phase2-projection-home"
            )
        )
        results.append(
            validate_phase2_projection_review_and_rollback(
                temp_path / "phase2-review-home"
            )
        )
        results.append(
            validate_phase2_browser_readonly_runtime_execution(
                temp_path / "phase2-browser-runtime-home"
            )
        )
        results.append(
            validate_phase2_research_composite_readonly_execution(
                temp_path / "phase2-research-composite-home"
            )
        )
        results.append(
            validate_phase2_multimodal_asset_readonly_execution(
                temp_path / "phase2-multimodal-asset-home"
            )
        )
        results.append(
            validate_phase2_coding_candidate_readonly_execution(
                temp_path / "phase2-coding-candidate-home"
            )
        )
        results.append(
            validate_phase2_scheduler_due_event_runtime_intake(
                temp_path / "phase2-scheduler-due-home"
            )
        )
        results.append(
            validate_phase2_external_handoff_runtime_receipts(
                temp_path / "phase2-external-handoff-home"
            )
        )
        results.append(
            validate_phase2_wechat_approval_channel_receipts(
                temp_path / "phase2-wechat-approval-home"
            )
        )
        results.append(
            validate_phase2_provider_route_runtime_receipts(
                temp_path / "phase2-provider-route-home"
            )
        )
        results.append(
            validate_phase2_skill_candidate_extraction_receipts(
                temp_path / "phase2-skill-candidate-home"
            )
        )
        results.append(
            validate_phase2_learning_epoch_review_receipts(
                temp_path / "phase2-learning-epoch-home"
            )
        )
        results.append(
            validate_phase2_capability_epoch_audit_receipts(
                temp_path / "phase2-capability-epoch-home"
            )
        )
        results.append(
            validate_phase2_voice_transcript_ingress_receipts(
                temp_path / "phase2-voice-transcript-home"
            )
        )
        results.append(
            validate_phase2_local_notification_draft_receipts(
                temp_path / "phase2-local-notification-home"
            )
        )
        results.append(
            validate_phase2_screen_snapshot_ingress_receipts(
                temp_path / "phase2-screen-snapshot-home"
            )
        )
        results.append(
            validate_phase2_browser_form_draft_receipts(
                temp_path / "phase2-browser-form-home"
            )
        )
        results.append(
            validate_phase2_download_quarantine_draft_receipts(
                temp_path / "phase2-download-quarantine-home"
            )
        )
        results.append(
            validate_phase2_document_ingress_receipts(
                temp_path / "phase2-document-ingress-home"
            )
        )
        results.append(
            validate_phase2_document_retrieval_preview(
                temp_path / "phase2-document-retrieval-preview-home"
            )
        )
        results.append(
            validate_phase2_document_readonly_execution(
                temp_path / "phase2-document-readonly-execution-home"
            )
        )
        results.append(
            validate_phase2_document_projection_review(
                temp_path / "phase2-document-projection-review-home"
            )
        )
        results.append(
            validate_phase2_document_skill_candidate_receipts(
                temp_path / "phase2-document-skill-candidate-home"
            )
        )
        results.append(
            validate_phase2_document_skill_sandbox_receipts(
                temp_path / "phase2-document-skill-sandbox-home"
            )
        )
        results.append(
            validate_phase2_agentic_task_harness(
                temp_path / "phase2-agentic-task-harness-home"
            )
        )
        results.append(
            validate_phase2_agentic_acceptance_trace(
                temp_path / "phase2-agentic-acceptance-trace-home"
            )
        )
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
    parser.add_argument(
        "--only",
        action="append",
        dest="only",
        help="Run one validation pass only. Currently supports P01, P42, and P43.",
    )
    args = parser.parse_args(argv)
    selected = {item.upper() for item in args.only} if args.only else None
    result = run_validation(selected)
    payload = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
