"""Owner-started, bounded RSI candidate experiment inside WLS authority.

The supplied proposer and evaluator are TRUSTED integration callbacks. This
module never executes candidate code, accesses model credentials, or promotes
changes into the live WLS runtime. A disposable external worker and independent
evaluator are required before use on real software candidates.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from math import isfinite
from typing import Any
import re

from .db import Database
from .evidence import EvidenceLedger
from .experiment_decision import (
    ExperimentPolicy,
    ExperimentVerdict,
    MetricResult,
    decide_experiment,
)

Proposer = Callable[[str, int, int], str]
Evaluator = Callable[[str], MetricResult]


def _valid_artifact_id(value: str) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value) is not None


def _safe_metric_payload(value: MetricResult) -> dict[str, Any]:
    """Persist invalid evaluator results as JSON null, never NaN/Infinity."""
    result = asdict(value)
    score = result["primary"]
    if not isinstance(score, (int, float)) or not isfinite(score):
        result["primary"] = None
    result["gates"] = {
        key: number if isinstance(number, (int, float)) and isfinite(number) else None
        for key, number in result["gates"].items()
    }
    return result


class RsiEvolutionPilot:
    """Candidate-only generation runner; WLS Database and EvidenceLedger own state."""

    def __init__(self, db: Database, ledger: EvidenceLedger) -> None:
        self.db = db
        self.ledger = ledger

    @staticmethod
    def _key(run_id: str) -> str:
        if not run_id or not run_id.strip() or len(run_id) > 128:
            raise ValueError("run_id must be nonblank and at most 128 characters")
        return "rsi_pilot:" + run_id

    def read(self, run_id: str) -> dict[str, Any] | None:
        value = self.db.get_runtime(self._key(run_id))
        return value if isinstance(value, dict) else None

    def start(
        self,
        *,
        run_id: str,
        policy: ExperimentPolicy,
        baseline_id: str,
        baseline: MetricResult,
        branches: int = 2,
        max_no_gain_rounds: int = 3,
    ) -> dict[str, Any]:
        if not _valid_artifact_id(baseline_id):
            raise ValueError("baseline_id must be a safe artifact identifier")
        if branches < 1 or branches > 8:
            raise ValueError("branches must be in 1..8")
        if max_no_gain_rounds < 1:
            raise ValueError("max_no_gain_rounds must be positive")
        # Validate baseline metrics against itself, without declaring improvement.
        valid = decide_experiment(
            policy, baseline=baseline, candidate=baseline,
            completed_rounds=0, failures=0,
        )
        if valid.verdict in {ExperimentVerdict.INVALID, ExperimentVerdict.CRASH}:
            raise ValueError(f"invalid baseline: {valid.reasons}")
        if any(float(baseline.gates[name]) > limit for name, limit in policy.hard_gates.items()):
            raise ValueError("baseline violates a mandatory hard gate")
        state: dict[str, Any] = {
            "run_id": run_id, "policy_digest": policy.digest(),
            "evaluator_digest": policy.evaluator_digest,
            "status": "READY", "generation": 0,
            "champion_id": baseline_id, "champion_metric": asdict(baseline),
            "branches": branches, "max_no_gain_rounds": max_no_gain_rounds,
            "no_gain_rounds": 0, "failures": 0, "history": [],
            "candidate_ids": [baseline_id],
            "authority": "candidate_only", "live_promotion": False,
        }
        key = self._key(run_id)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT 1 FROM runtime_state WHERE key = ?", (key,)
            ).fetchone()
            if row is not None:
                raise ValueError("run_id already exists; cannot overwrite evidence")
            self.db.set_runtime(key, state, conn)
            self.ledger.append("rsi_pilot_started", {
                "run_id": run_id, "policy_digest": policy.digest(),
                "baseline_id": baseline_id, "branches": branches,
            }, conn)
        return state

    def advance(
        self, run_id: str, *, policy: ExperimentPolicy,
        propose: Proposer, evaluate: Evaluator,
    ) -> dict[str, Any]:
        """Execute one generation. In-flight interruptions require manual reconciliation."""
        key = self._key(run_id)
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT value_json FROM runtime_state WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                raise ValueError("unknown run_id")
            import json
            state = json.loads(row["value_json"])
            if state["policy_digest"] != policy.digest():
                raise ValueError("policy changed after start")
            if state["status"] != "READY":
                raise ValueError("run not ready; in-flight runs require manual reconciliation")
            if state["generation"] >= policy.max_rounds:
                raise ValueError("generation budget exhausted")
            state["status"] = "IN_FLIGHT"
            self.db.set_runtime(key, state, conn)
            self.ledger.append("rsi_generation_started", {
                "run_id": run_id, "generation": state["generation"] + 1,
                "parent": state["champion_id"],
            }, conn)

        gen = int(state["generation"]) + 1
        old_id = str(state["champion_id"])
        baseline = MetricResult(**state["champion_metric"])
        results: list[dict[str, Any]] = []
        seen: set[str] = set(state["candidate_ids"])
        try:
            for branch in range(int(state["branches"])):
                candidate_id = propose(old_id, gen, branch)
                if not _valid_artifact_id(candidate_id):
                    raise ValueError("candidate_id must be a safe artifact identifier")
                if candidate_id in seen:
                    raise ValueError("candidate_id reused in generation")
                seen.add(candidate_id)
                metrics = evaluate(candidate_id)
                if not isinstance(metrics, MetricResult):
                    raise TypeError("independent evaluator must return MetricResult")
                decision = decide_experiment(
                    policy, baseline=baseline, candidate=metrics,
                    completed_rounds=gen - 1, failures=int(state["failures"]),
                )
                results.append({
                    "candidate_id": candidate_id,
                    "metric": _safe_metric_payload(metrics),
                    "decision": decision.to_dict(),
                })
                # Each candidate is persisted before the next callback, so a crash
                # does not erase the evidence or invite an automatic replay.
                state["pending_candidates"] = results
                state["candidate_ids"].append(candidate_id)
                with self.db.transaction() as conn:
                    self.db.set_runtime(key, state, conn)
                    self.ledger.append("rsi_candidate_measured", {
                        "run_id": run_id, "generation": gen,
                        "candidate_id": candidate_id,
                        "decision": decision.to_dict(),
                        "metric": asdict(metrics),
                    }, conn)
        except Exception as exc:
            state["status"] = "BLOCKED"
            state["blocker"] = type(exc).__name__
            with self.db.transaction() as conn:
                self.db.set_runtime(key, state, conn)
                self.ledger.append("rsi_generation_blocked", {
                    "run_id": run_id, "generation": gen,
                    "blocker_type": type(exc).__name__,
                }, conn)
            raise

        candidates = [
            r for r in results if r["decision"]["verdict"] == ExperimentVerdict.KEEP.value
        ]
        if candidates:
            winner = max(candidates, key=lambda r: float(r["decision"]["gain"]))
            state["champion_id"] = winner["candidate_id"]
            state["champion_metric"] = winner["metric"]
            state["no_gain_rounds"] = 0
        else:
            state["no_gain_rounds"] += 1
        state["failures"] += sum(
            r["decision"]["verdict"] in {
                ExperimentVerdict.CRASH.value, ExperimentVerdict.INVALID.value
            }
            for r in results
        )
        state["generation"] = gen
        state["history"].append({
            "generation": gen, "parent": old_id,
            "champion": state["champion_id"],
            "candidates": [r["candidate_id"] for r in results],
        })
        state.pop("pending_candidates", None)
        if (gen >= policy.max_rounds
                or state["failures"] > policy.max_failures
                or state["no_gain_rounds"] >= state["max_no_gain_rounds"]):
            state["status"] = "COMPLETE"
        else:
            state["status"] = "READY"
        with self.db.transaction() as conn:
            self.db.set_runtime(key, state, conn)
            self.ledger.append("rsi_generation_completed", {
                "run_id": run_id, "generation": gen,
                "parent": old_id, "champion": state["champion_id"],
                "status": state["status"],
            }, conn)
        return state

    def run_bounded(
        self, run_id: str, *, policy: ExperimentPolicy,
        propose: Proposer, evaluate: Evaluator,
    ) -> dict[str, Any]:
        """Continue READY generations only; never re-run an interrupted callback."""
        state = self.read(run_id)
        if state is None:
            raise ValueError("unknown run_id")
        while state["status"] == "READY":
            state = self.advance(run_id, policy=policy, propose=propose, evaluate=evaluate)
        return state
