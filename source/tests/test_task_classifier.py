from __future__ import annotations

import pytest

from wls.schemas import RiskLevel
from wls.task_classifier import (
    OrchestrationMode,
    SideEffectClass,
    TaskClassifier,
    TaskKind,
    TaskRouteDescriptor,
    VerificationOracle,
)


def test_research_fans_out_without_claiming_execution_authority() -> None:
    task = TaskRouteDescriptor(
        task_id="t1",
        title="Research agents",
        goal="Compare architectures",
        tags=frozenset({"research", "sources"}),
        estimated_steps=12,
        allow_parallel=True,
        verification_oracles=(VerificationOracle.EXTERNAL_SOURCE,),
    )

    route = TaskClassifier().classify(task)

    assert route.task_kind is TaskKind.RESEARCH
    assert route.mode is OrchestrationMode.FAN_OUT_SYNTHESIZE
    assert route.max_workers >= 2
    assert route.authority == "route_candidate_only"
    assert route.canonical_owner == "planning"
    assert route.to_dict()["route_digest"]


def test_irreversible_task_is_human_gated() -> None:
    task = TaskRouteDescriptor(
        task_id="t2",
        title="Publish release",
        goal="Push production release",
        tags=frozenset({"publish"}),
        side_effect_class=SideEffectClass.IRREVERSIBLE,
    )

    route = TaskClassifier().classify(task)

    assert route.mode is OrchestrationMode.HUMAN_GATED
    assert route.requires_owner_gate
    assert route.effective_risk is RiskLevel.IRREVERSIBLE


def test_deterministic_small_task_uses_tools() -> None:
    task = TaskRouteDescriptor(
        task_id="t3",
        title="Verify digest",
        goal="Check the artifact hash",
        tags=frozenset({"hash"}),
        verification_oracles=(VerificationOracle.ARTIFACT_DIGEST,),
    )

    route = TaskClassifier().classify(task)

    assert route.mode is OrchestrationMode.TOOL_ONLY
    assert route.max_workers == 1
    assert route.uncertainty == "LOW"


def test_classifier_never_lowers_declared_risk() -> None:
    task = TaskRouteDescriptor(
        task_id="t4",
        title="Review live database",
        goal="Inspect live database state",
        tags=frozenset({"research"}),
        declared_risk=RiskLevel.HIGH,
        side_effect_class=SideEffectClass.NONE,
        allow_parallel=True,
        verification_oracles=(VerificationOracle.HUMAN_REVIEW,),
    )

    route = TaskClassifier().classify(task)

    assert route.effective_risk is RiskLevel.HIGH
    assert route.requires_owner_gate
    assert route.mode is OrchestrationMode.HUMAN_GATED


def test_unknown_side_effect_fails_closed() -> None:
    task = TaskRouteDescriptor(
        task_id="t5",
        title="Use unfamiliar desktop tool",
        goal="Operate an unknown GUI",
        side_effect_class=SideEffectClass.UNKNOWN,
    )

    route = TaskClassifier().classify(task)

    assert route.requires_owner_gate
    assert route.effective_risk is RiskLevel.HIGH


def test_invalid_descriptor_is_rejected() -> None:
    task = TaskRouteDescriptor(task_id="", title="x", goal="y")

    with pytest.raises(ValueError, match="task_id is required"):
        TaskClassifier().classify(task)
