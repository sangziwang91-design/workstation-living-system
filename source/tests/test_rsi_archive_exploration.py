"""Ablate frozen-champion-only vs bounded archive-parent exploration.

Synthetic unit evidence confirms ancestry selection and acceptance boundaries;
it is NOT held-out evidence of improved real coding capability.
"""
from __future__ import annotations

import pytest

from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.experiment_decision import ExperimentPolicy, MetricResult
from wls.rsi_evolution import RsiEvolutionPilot

DIGEST = "f" * 64


def experiment(tmp_path, name: str, exploration_branches: int):
    db = Database(tmp_path / (name + ".sqlite"))
    pilot = RsiEvolutionPilot(
        db, EvidenceLedger(db, tmp_path / (name + ".key"))
    )
    policy = ExperimentPolicy(
        direction="maximize", minimum_gain=0.01,
        hard_gates={"regressions": 0}, max_rounds=2,
        max_failures=1, evaluator_digest=DIGEST,
    )
    pilot.start(
        run_id=name, policy=policy, baseline_id="base",
        baseline=MetricResult(.50, {"regressions": 0}, DIGEST),
        branches=2, exploration_branches=exploration_branches,
    )
    parents = []

    def propose(parent: str, generation: int, branch: int) -> str:
        parents.append((generation, branch, parent))
        return f"g{generation}b{branch}p{parent}"

    def evaluate(candidate: str) -> MetricResult:
        if candidate.startswith("g1b0"):
            score = .55
        elif candidate.startswith("g1b1"):
            score = .45
        elif candidate.startswith("g2b1") and "pg1b1pbase" in candidate:
            score = .70
        else:
            score = .56
        return MetricResult(score, {"regressions": 0}, DIGEST)

    result = pilot.run_bounded(
        name, policy=policy, propose=propose, evaluate=evaluate
    )
    assert result["generation"] == 2 and result["status"] == "COMPLETE"
    assert pilot.read(name) == result
    db.close_all()
    return result, parents


def test_open_archive_finds_path_not_available_to_frozen_champion(tmp_path):
    baseline, parents_fixed = experiment(tmp_path, "fixed", 0)
    exploratory, parents_open = experiment(tmp_path, "open", 1)
    assert parents_fixed[3][2] == "g1b0pbase"
    assert parents_open[3][2] == "g1b1pbase"
    assert baseline["champion_metric"]["primary"] == .56
    assert exploratory["champion_metric"]["primary"] == .70
    assert baseline["exploration_branches"] == 0
    assert exploratory["exploration_branches"] == 1
    # All accepted children beat the same *global* champion, not a weak parent.
    assert exploratory["history"][1]["proposal_parents"]["g2b1pg1b1pbase"] == "g1b1pbase"
    assert any(
        item["candidate_id"] == "g1b1pbase" and item["decision"] == "REVERT"
        for item in exploratory["archive"]
    )


@pytest.mark.parametrize("num", [-1, 3])
def test_exploration_cannot_exceed_fixed_branch_budget(tmp_path, num):
    db = Database(tmp_path / (str(num) + ".sqlite"))
    pilot = RsiEvolutionPilot(db, EvidenceLedger(db, tmp_path / (str(num) + ".key")))
    pol = ExperimentPolicy("maximize", .01, {"regressions": 0}, 2, 1, DIGEST)
    with pytest.raises(ValueError, match="exploration_branches"):
        pilot.start(
            run_id="bad", policy=pol, baseline_id="base",
            baseline=MetricResult(.5, {"regressions": 0}, DIGEST),
            branches=2, exploration_branches=num,
        )
    db.close_all()


def test_unsafe_high_scoring_candidate_never_enters_archive(tmp_path):
    db = Database(tmp_path / "unsafe.sqlite")
    pilot = RsiEvolutionPilot(db, EvidenceLedger(db, tmp_path / "unsafe.key"))
    pol = ExperimentPolicy("maximize", .01, {"regressions": 0}, 2, 1, DIGEST)
    pilot.start(
        run_id="unsafe", policy=pol, baseline_id="base",
        baseline=MetricResult(.50, {"regressions": 0}, DIGEST),
        branches=2, exploration_branches=1,
    )
    def score(candidate):
        return MetricResult(
            .99 if candidate.endswith("b1") else .55,
            {"regressions": 1 if candidate.endswith("b1") else 0}, DIGEST,
        )
    pilot.advance(
        "unsafe", policy=pol,
        propose=lambda _, g, b: f"g{g}b{b}", evaluate=score,
    )
    first = pilot.read("unsafe")
    assert first is not None
    assert first["champion_id"] == "g1b0"
    assert "g1b1" not in {a["candidate_id"] for a in first["archive"]}
    parents = []
    pilot.advance(
        "unsafe", policy=pol,
        propose=lambda parent, g, b: (parents.append(parent) or f"g{g}b{b}"),
        evaluate=lambda _: MetricResult(.56, {"regressions": 0}, DIGEST),
    )
    assert "g1b1" not in parents
    db.close_all()
