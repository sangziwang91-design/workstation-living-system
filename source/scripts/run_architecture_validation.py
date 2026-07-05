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
    validate_phase2_delivery_readiness_audit,
    validate_phase2_final_delivery_audit,
    validate_phase2_holdout_epoch_immutability,
    validate_phase2_installed_tail_check_audit,
    validate_phase2_multimodal_asset_readonly_execution,
    validate_phase2_capability_epoch_audit_receipts,
    validate_phase2_owner_surface_and_readonly_organs,
    validate_phase2_packaging_layout_audit,
    validate_phase2_paired_baseline_candidate_experiment,
    validate_phase2_preflighted_readonly_execution,
    validate_phase2_promotion_bundle_gate,
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
    validate_phase2_transfer_efficiency_audit,
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
    validate_phase2_agentic_file_mailbox_handoff,
    validate_phase2_agentic_repair_candidate,
    validate_phase2_agentic_budget_gate,
    validate_phase2_agentic_benchmark_scorecard,
    validate_phase2_agentic_checkpoint_resume,
    validate_phase2_agentic_artifact_finalize_acceptance,
    validate_phase2_agentic_lease_fencing_reconciliation,
    validate_phase2_agentic_retry_gate,
    validate_phase2_agentic_replan_candidate,
    validate_phase2_agentic_role_context_packets,
    validate_phase2_agentic_context_epoch_checkpoint,
    validate_phase2_agentic_process_auditor,
    validate_phase2_agentic_result_replay_quarantine,
    validate_phase2_agentic_worker_trust_quarantine,
    validate_phase2_agentic_worker_capability_arbitration,
    validate_phase2_agentic_worker_lease_recovery,
    validate_phase2_agentic_worker_lifecycle,
    validate_phase2_agentic_task_harness,
    validate_phase2_offspring_birth_contract,
    validate_phase2_offspring_budget_no_gain_stop,
    validate_phase2_offspring_checkpoint_fork,
    validate_phase2_offspring_isolated_state_budget,
    validate_phase2_offspring_mailbox_envelope,
    validate_phase2_offspring_retirement_cleanup,
    validate_phase2_offspring_retirement_tombstone,
    validate_phase2_sandbox_adapter_contract,
    validate_p01_registry,
    validate_runtime_approval_receipts,
    validate_runtime_event_ingress,
    validate_runtime_provider_route,
    validate_workbench_templates,
)

PASS_CONTRACTS: dict[str, str] = {}
SELECTIVE_VALIDATION_PASSES = {
    "P01",
    "P42",
    "P43",
    "P44",
    "P45",
    "P46",
    "P47",
    "P48",
    "P49",
    "P50",
    "P51",
    "P52",
    "P53",
    "P54",
    "P55",
    "P56",
    "P57",
    "P58",
    "P59",
    "P60",
    "P61",
    "P62",
    "P63",
    "P64",
    "P65",
    "P66",
    "P67",
    "P68",
    "P69",
    "P70",
    "P71",
    "P72",
    "P73",
    "P74",
    "P75",
    "P76",
}


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
        if _selected(selected, "P44"):
            results.append(
                validate_phase2_agentic_file_mailbox_handoff(
                    temp_path / "phase2-agentic-file-mailbox-home"
                )
            )
        if _selected(selected, "P45"):
            results.append(
                validate_phase2_agentic_repair_candidate(
                    temp_path / "phase2-agentic-repair-candidate-home"
                )
            )
        if _selected(selected, "P46"):
            results.append(
                validate_phase2_agentic_budget_gate(
                    temp_path / "phase2-agentic-budget-gate-home"
                )
            )
        if _selected(selected, "P47"):
            results.append(
                validate_phase2_agentic_benchmark_scorecard(
                    temp_path / "phase2-agentic-benchmark-scorecard-home"
                )
            )
        if _selected(selected, "P48"):
            results.append(
                validate_phase2_agentic_checkpoint_resume(
                    temp_path / "phase2-agentic-checkpoint-resume-home"
                )
            )
        if _selected(selected, "P49"):
            results.append(
                validate_phase2_agentic_retry_gate(
                    temp_path / "phase2-agentic-retry-gate-home"
                )
            )
        if _selected(selected, "P50"):
            results.append(
                validate_phase2_agentic_replan_candidate(
                    temp_path / "phase2-agentic-replan-candidate-home"
                )
            )
        if _selected(selected, "P51"):
            results.append(
                validate_phase2_agentic_worker_lifecycle(
                    temp_path / "phase2-agentic-worker-lifecycle-home"
                )
            )
        if _selected(selected, "P52"):
            results.append(
                validate_phase2_agentic_result_replay_quarantine(
                    temp_path / "phase2-agentic-result-replay-home"
                )
            )
        if _selected(selected, "P53"):
            results.append(
                validate_phase2_agentic_worker_lease_recovery(
                    temp_path / "phase2-agentic-worker-lease-recovery-home"
                )
            )
        if _selected(selected, "P54"):
            results.append(
                validate_phase2_agentic_worker_capability_arbitration(
                    temp_path / "phase2-agentic-worker-capability-arbitration-home"
                )
            )
        if _selected(selected, "P55"):
            results.append(
                validate_phase2_agentic_lease_fencing_reconciliation(
                    temp_path / "phase2-agentic-lease-fencing-home"
                )
            )
        if _selected(selected, "P56"):
            results.append(
                validate_phase2_agentic_artifact_finalize_acceptance(
                    temp_path / "phase2-agentic-artifact-finalize-home"
                )
            )
        if _selected(selected, "P57"):
            results.append(
                validate_phase2_agentic_worker_trust_quarantine(
                    temp_path / "phase2-agentic-worker-trust-home"
                )
            )
        if _selected(selected, "P58"):
            results.append(
                validate_phase2_sandbox_adapter_contract(
                    temp_path / "phase2-sandbox-adapter-home"
                )
            )
        if _selected(selected, "P59"):
            results.append(
                validate_phase2_offspring_birth_contract(
                    temp_path / "phase2-offspring-birth-home"
                )
            )
        if _selected(selected, "P60"):
            results.append(
                validate_phase2_offspring_isolated_state_budget(
                    temp_path / "phase2-offspring-state-home"
                )
            )
        if _selected(selected, "P61"):
            results.append(
                validate_phase2_offspring_retirement_tombstone(
                    temp_path / "phase2-offspring-retirement-home"
                )
            )
        if _selected(selected, "P62"):
            results.append(
                validate_phase2_offspring_budget_no_gain_stop(
                    temp_path / "phase2-offspring-budget-home"
                )
            )
        if _selected(selected, "P63"):
            results.append(
                validate_phase2_offspring_checkpoint_fork(
                    temp_path / "phase2-offspring-checkpoint-home"
                )
            )
        if _selected(selected, "P64"):
            results.append(
                validate_phase2_offspring_mailbox_envelope(
                    temp_path / "phase2-offspring-mailbox-home"
                )
            )
        if _selected(selected, "P65"):
            results.append(
                validate_phase2_offspring_retirement_cleanup(
                    temp_path / "phase2-offspring-cleanup-home"
                )
            )
        if _selected(selected, "P66"):
            results.append(
                validate_phase2_paired_baseline_candidate_experiment(
                    temp_path / "phase2-paired-experiment-home"
                )
            )
        if _selected(selected, "P67"):
            results.append(
                validate_phase2_holdout_epoch_immutability(
                    temp_path / "phase2-holdout-epoch-home"
                )
            )
        if _selected(selected, "P68"):
            results.append(
                validate_phase2_promotion_bundle_gate(
                    temp_path / "phase2-promotion-bundle-home"
                )
            )
        if _selected(selected, "P69"):
            results.append(
                validate_phase2_transfer_efficiency_audit(
                    temp_path / "phase2-transfer-audit-home"
                )
            )
        if _selected(selected, "P70"):
            results.append(
                validate_phase2_final_delivery_audit(
                    temp_path / "phase2-final-delivery-home"
                )
            )
        if _selected(selected, "P71"):
            results.append(
                validate_phase2_agentic_role_context_packets(
                    temp_path / "phase2-agentic-role-context-home"
                )
            )
        if _selected(selected, "P72"):
            results.append(
                validate_phase2_agentic_context_epoch_checkpoint(
                    temp_path / "phase2-agentic-context-epoch-home"
                )
            )
        if _selected(selected, "P73"):
            results.append(
                validate_phase2_agentic_process_auditor(
                    temp_path / "phase2-agentic-process-auditor-home"
                )
            )
        if _selected(selected, "P74"):
            results.append(
                validate_phase2_delivery_readiness_audit(
                    temp_path / "phase2-delivery-readiness-home"
                )
            )
        if _selected(selected, "P75"):
            results.append(
                validate_phase2_packaging_layout_audit(
                    temp_path / "phase2-packaging-layout-home"
                )
            )
        if _selected(selected, "P76"):
            results.append(
                validate_phase2_installed_tail_check_audit(
                    temp_path / "phase2-installed-tail-check-home"
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
        results.append(
            validate_phase2_agentic_file_mailbox_handoff(
                temp_path / "phase2-agentic-file-mailbox-home"
            )
        )
        results.append(
            validate_phase2_agentic_repair_candidate(
                temp_path / "phase2-agentic-repair-candidate-home"
            )
        )
        results.append(
            validate_phase2_agentic_budget_gate(
                temp_path / "phase2-agentic-budget-gate-home"
            )
        )
        results.append(
            validate_phase2_agentic_benchmark_scorecard(
                temp_path / "phase2-agentic-benchmark-scorecard-home"
            )
        )
        results.append(
            validate_phase2_agentic_checkpoint_resume(
                temp_path / "phase2-agentic-checkpoint-resume-home"
            )
        )
        results.append(
            validate_phase2_agentic_retry_gate(
                temp_path / "phase2-agentic-retry-gate-home"
            )
        )
        results.append(
            validate_phase2_agentic_replan_candidate(
                temp_path / "phase2-agentic-replan-candidate-home"
            )
        )
        results.append(
            validate_phase2_agentic_worker_lifecycle(
                temp_path / "phase2-agentic-worker-lifecycle-home"
            )
        )
        results.append(
            validate_phase2_agentic_result_replay_quarantine(
                temp_path / "phase2-agentic-result-replay-home"
            )
        )
        results.append(
            validate_phase2_agentic_worker_lease_recovery(
                temp_path / "phase2-agentic-worker-lease-recovery-home"
            )
        )
        results.append(
            validate_phase2_agentic_worker_capability_arbitration(
                temp_path / "phase2-agentic-worker-capability-arbitration-home"
            )
        )
        results.append(
            validate_phase2_agentic_lease_fencing_reconciliation(
                temp_path / "phase2-agentic-lease-fencing-home"
            )
        )
        results.append(
            validate_phase2_agentic_artifact_finalize_acceptance(
                temp_path / "phase2-agentic-artifact-finalize-home"
            )
        )
        results.append(
            validate_phase2_agentic_worker_trust_quarantine(
                temp_path / "phase2-agentic-worker-trust-home"
            )
        )
        results.append(
            validate_phase2_sandbox_adapter_contract(
                temp_path / "phase2-sandbox-adapter-home"
            )
        )
        results.append(
            validate_phase2_offspring_birth_contract(
                temp_path / "phase2-offspring-birth-home"
            )
        )
        results.append(
            validate_phase2_offspring_isolated_state_budget(
                temp_path / "phase2-offspring-state-home"
            )
        )
        results.append(
            validate_phase2_offspring_retirement_tombstone(
                temp_path / "phase2-offspring-retirement-home"
            )
        )
        results.append(
            validate_phase2_offspring_budget_no_gain_stop(
                temp_path / "phase2-offspring-budget-home"
            )
        )
        results.append(
            validate_phase2_offspring_checkpoint_fork(
                temp_path / "phase2-offspring-checkpoint-home"
            )
        )
        results.append(
            validate_phase2_offspring_mailbox_envelope(
                temp_path / "phase2-offspring-mailbox-home"
            )
        )
        results.append(
            validate_phase2_offspring_retirement_cleanup(
                temp_path / "phase2-offspring-cleanup-home"
            )
        )
        results.append(
            validate_phase2_paired_baseline_candidate_experiment(
                temp_path / "phase2-paired-experiment-home"
            )
        )
        results.append(
            validate_phase2_holdout_epoch_immutability(
                temp_path / "phase2-holdout-epoch-home"
            )
        )
        results.append(
            validate_phase2_promotion_bundle_gate(
                temp_path / "phase2-promotion-bundle-home"
            )
        )
        results.append(
            validate_phase2_transfer_efficiency_audit(
                temp_path / "phase2-transfer-audit-home"
            )
        )
        results.append(
            validate_phase2_final_delivery_audit(
                temp_path / "phase2-final-delivery-home"
            )
        )
        results.append(
            validate_phase2_agentic_role_context_packets(
                temp_path / "phase2-agentic-role-context-home"
            )
        )
        results.append(
            validate_phase2_agentic_context_epoch_checkpoint(
                temp_path / "phase2-agentic-context-epoch-home"
            )
        )
        results.append(
            validate_phase2_delivery_readiness_audit(
                temp_path / "phase2-delivery-readiness-home"
            )
        )
        results.append(
            validate_phase2_packaging_layout_audit(
                temp_path / "phase2-packaging-layout-home"
            )
        )
        results.append(
            validate_phase2_installed_tail_check_audit(
                temp_path / "phase2-installed-tail-check-home"
            )
        )
        results.append(
            validate_phase2_agentic_process_auditor(
                temp_path / "phase2-agentic-process-auditor-home"
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
        help=(
            "Run one validation pass only. Currently supports P01 and "
            "P42 through P76."
        ),
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
