"""Paired real offline Qwen self-code experiment inside existing WLS RSI runtime.

Public development experiment, not sealed holdout and not recursive self-improvement.
No runner-token, hosted inference API, live code promotion or user PC.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

from measure_task_judgment_local_model import (
    LARGER_MODEL_ID, LARGER_MODEL_URL, LARGER_MODEL_SHA256,
    LARGER_MODEL_MIN_BYTES, LARGER_MODEL_MAX_BYTES,
    download_pinned_model, verify_model,
)
from wls.db import Database
from wls.evidence import EvidenceLedger
from wls.experiment_decision import ExperimentPolicy, MetricResult
from wls.isolation_runner import run_isolated_python
from wls.rsi_artifact_gate import RsiArtifactGate
from wls.rsi_evolution import RsiEvolutionPilot
from wls.rsi_model_port import (
    PinnedLocalLlamaProposalPort, RsiModelCandidateBuilder,
    RsiModelExperiment, RsiProposalResponse,
)

TARGET = "source/src/wls/skill_compiler.py"
FROZEN_BLOB_SHA = "d86bb4f7cab2202dbaabdb50e016856db51b7828"
SCHEMA = "wls.rsi.real_qwen_self_code_paired.v1"
ARMS = ("unguided", "guided")

# These probes are NEVER inserted into the model's task instruction.
# Untrusted candidate source is evaluated only inside WLS C1 networkless
# read-only Docker with a pinned Python image, no mounts and no credentials.
_PROBE_BOOT = """
import hashlib
import json
import sys
import types
from enum import Enum
class RiskLevel(Enum):
    READ = "READ"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
p = types.ModuleType("wls")
p.__path__ = []
s = types.ModuleType("wls.schemas")
s.RiskLevel = RiskLevel
s.digest_json = lambda v: hashlib.sha256(
    json.dumps(v, sort_keys=True).encode("utf-8")
).hexdigest()
s.new_id = lambda prefix: prefix + "-probe"
s.utc_now = lambda: "2026-11-08T00:00:00Z"
sys.modules["wls"] = p
sys.modules["wls.schemas"] = s
ns = {"__package__": "wls", "__name__": "wls.skill_compiler"}
exec(compile(SOURCE, "<candidate>", "exec"), ns)
compiler = ns["SkillCompiler"]()
package = compiler.propose("bounded", "probe", permissions=["read"])
compiled = compiler.compile(package, {"steps": ["read"]}, tests=["good", "bad"])
assert not compiled.is_validated(), "unvalidated source must remain unvalidated"
"""
_PROBE_BEHAVIOR = """
outcome = compiler.validate(compiled, {"good": True, "bad": False})
assert not outcome.is_validated(), "a failed test cannot validate a skill"
outcome = compiler.validate(compiled, {"good": False, "bad": False})
assert not outcome.is_validated(), "two failures cannot validate a skill"
for invalid in ({"good": True}, {"good": True, "bad": True, "extra": True}):
    try:
        claimed = compiler.validate(compiled, invalid)
    except (TypeError, ValueError):
        continue
    assert not claimed.is_validated(), "missing or extra tests cannot validate"
"""
_PROBE_REGRESSION = """
good = compiler.validate(compiled, {"good": True, "bad": True})
assert good.is_validated(), "complete successful checks must validate"
assert len(good.acceptance_evidence) == 2
assert compiled.is_validated() is False, "validation must not mutate parent"
assert package.status == "PROPOSED", "proposal status must not silently promote"
assert package.tool_permissions == ["read"]
assert compiler.reject(compiled, "rejected").status == "REJECTED"
"""

OBJECTIVES = {
    "unguided": (
        "Improve this existing WLS skill validation subsystem so that a skill "
        "is validated only if its test outcomes actually support that claim. "
        "Fix incorrect validation semantics without changing APIs or "
        "weakening the existing allowed-permission and rejection behavior."
    ),
    "guided": (
        "Observed WLS failure: SkillCompiler.validate records digests of "
        "passed=False tests as acceptance_evidence, while CompiledSkill."
        "is_validated only checks len(acceptance_evidence)>0. This makes a "
        "failing skill report validated. Repair this causal mechanism, handle "
        "missing or unexpected test outcomes, preserve true all-pass behavior "
        "and existing public method signatures. Do not change tests."
    ),
}


def _probe(source: str, kind: str) -> str:
    if kind not in {"behavior", "regression"}:
        raise ValueError("unknown independent probe")
    body = _PROBE_BEHAVIOR if kind == "behavior" else _PROBE_REGRESSION
    # SOURCE is quoted as one string literal; no candidate executes on host.
    return "SOURCE = " + repr(source) + "\n" + _PROBE_BOOT + body


def _grade_source(source: str, image: str) -> dict[str, Any]:
    if len(source.encode("utf-8")) > 8192:
        return {"source_sha256": sha256(source.encode()).hexdigest(),
                "behavior": "OVER_BUDGET", "regression": "OVER_BUDGET",
                "passes": False}
    results: dict[str, Any] = {"source_sha256": sha256(source.encode()).hexdigest()}
    for kind in ("behavior", "regression"):
        probe = _probe(source, kind)
        if len(probe.encode("utf-8")) > 8192:
            results[kind] = "PROBE_OVER_BUDGET"
        else:
            receipt = run_isolated_python(probe, image=image, timeout=20)
            results[kind] = receipt.status
            results[kind + "_elapsed_seconds"] = receipt.elapsed_seconds
            results[kind + "_probe_sha256"] = receipt.source_sha256
    results["passes"] = (
        results["behavior"] == "EXIT_0"
        and results["regression"] == "EXIT_0"
    )
    return results


class MeteredPort:
    """Real pinned Llama port; records observables, never fabricates success."""

    def __init__(self, underlying: PinnedLocalLlamaProposalPort) -> None:
        self.underlying = underlying
        self.receipts: list[dict[str, Any]] = []

    @property
    def model_id(self) -> str:
        return self.underlying.model_id

    @property
    def capabilities(self) -> Any:
        return self.underlying.capabilities

    def propose(self, instruction: str, *, max_output_bytes: int) -> RsiProposalResponse:
        started = time.monotonic()
        receipt: dict[str, Any] = {
            "prompt_sha256": sha256(instruction.encode()).hexdigest(),
            "max_output_bytes": max_output_bytes,
        }
        try:
            response = self.underlying.propose(
                instruction, max_output_bytes=max_output_bytes,
            )
            receipt.update({
                "response_sha256": sha256(response.text.encode()).hexdigest(),
                "response_bytes": len(response.text.encode()),
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            })
            return response
        except Exception as exc:
            receipt["error_type"] = type(exc).__name__
            raise
        finally:
            receipt["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.receipts.append(receipt)


def _pin_source() -> bytes:
    source = Path(TARGET)
    if source.is_symlink() or not source.is_file():
        raise ValueError("canonical self-code source missing or symlinked")
    blob = subprocess.run(
        ["git", "hash-object", "--", TARGET],
        check=True, capture_output=True, text=True, timeout=20,
    ).stdout.strip()
    if blob != FROZEN_BLOB_SHA:
        raise ValueError("frozen WLS self-code baseline changed; no experiment")
    return source.read_bytes()


def run(model_file: Path, image: str, artifact_root: Path) -> dict[str, Any]:
    if not image.startswith("python@sha256:") or len(image) != len("python@sha256:") + 64:
        raise ValueError("missing exact C1 Python image digest")
    base = _pin_source()
    baseline = _grade_source(base.decode("utf-8"), image)
    if baseline["behavior"] == "EXIT_0" or baseline["regression"] != "EXIT_0":
        raise ValueError("no verified unsolved defect on frozen baseline")
    artifact_root.mkdir(parents=True, exist_ok=True)
    artifact_root = artifact_root.resolve()
    if not model_file.is_file():
        if os.environ.get("GITHUB_ACTIONS") != "true":
            raise ValueError("model download permitted only on GitHub Actions")
        download_pinned_model(
            model_file, url=LARGER_MODEL_URL, expected_sha=LARGER_MODEL_SHA256,
            min_bytes=LARGER_MODEL_MIN_BYTES, max_bytes=LARGER_MODEL_MAX_BYTES,
        )
    model_digest = verify_model(
        model_file, expected_sha=LARGER_MODEL_SHA256,
        min_bytes=LARGER_MODEL_MIN_BYTES, max_bytes=LARGER_MODEL_MAX_BYTES,
    )
    wrapped = PinnedLocalLlamaProposalPort(
        model_id=LARGER_MODEL_ID, model_file=model_file,
        expected_sha256=LARGER_MODEL_SHA256,
        min_model_bytes=LARGER_MODEL_MIN_BYTES,
        max_model_bytes=LARGER_MODEL_MAX_BYTES,
        context_tokens=8192, max_output_tokens=2048, n_threads=2,
    )
    model = MeteredPort(wrapped)
    evaluator_sha = sha256(
        (TARGET + FROZEN_BLOB_SHA + _PROBE_BOOT
         + _PROBE_BEHAVIOR + _PROBE_REGRESSION).encode()
    ).hexdigest()
    report: dict[str, Any] = {
        "schema": SCHEMA, "status": "IN_PROGRESS",
        "base_git_blob": FROZEN_BLOB_SHA,
        "baseline": baseline, "model_id": LARGER_MODEL_ID,
        "model_sha256": model_digest,
        "image": image, "evaluator_digest": evaluator_sha,
        "model_calls_observed": 0, "arms": {},
        "heldout_gain": "UNMEASURED", "rsi": "UNPROVEN",
        "source_promotion": False,
    }
    for arm in ARMS:
        home = artifact_root / arm
        home.mkdir(parents=True, exist_ok=True)
        db = Database(home / "state.sqlite")
        ledger = EvidenceLedger(db, home / "evidence.key")
        gate = RsiArtifactGate(home / "candidates", ledger,
                               allowed_files=frozenset({TARGET}))
        policy = ExperimentPolicy(
            "maximize", 1.0, {"regressions": 0.0},
            1, 0, evaluator_sha,
        )
        gate.register(
            artifact_id="seed", parent_id=None, generation=0, branch=0,
            files={TARGET: base}, policy_digest=policy.digest(),
            evaluator_digest=evaluator_sha,
        )
        measured: list[dict[str, Any]] = []

        def independent_evaluator(candidate_id: str) -> MetricResult:
            gate.verify(candidate_id)
            text = (gate.root / candidate_id / TARGET).read_text(encoding="utf-8")
            grade = _grade_source(text, image)
            measured.append({"candidate": candidate_id, **grade})
            return MetricResult(
                primary=1.0 if grade["passes"] else 0.0,
                gates={"regressions": 0.0 if grade["regression"] == "EXIT_0" else 1.0},
                evaluator_digest=evaluator_sha,
            )

        builder = RsiModelCandidateBuilder(
            model, gate, policy_digest=policy.digest(),
            evaluator_digest=evaluator_sha,
        )
        experiment = RsiModelExperiment(
            RsiEvolutionPilot(db, ledger), builder, policy,
            objective=OBJECTIVES[arm], allowed_files=(TARGET,),
            independent_evaluator=independent_evaluator,
        )
        calls_before = len(model.receipts)
        try:
            experiment.start("single-self-code", "seed", branches=1)
            outcome = experiment.advance("single-self-code")
            status = outcome["status"]
        except Exception as exc:
            status = "BLOCKED"
            blocker = type(exc).__name__
        else:
            blocker = None
        state = experiment.pilot.read("single-self-code")
        created = [
            item for item in (state or {}).get("candidate_ids", []) if item != "seed"
        ]
        candidate = created[0] if created else None
        source_sha = None
        if candidate:
            source_sha = gate.verify(candidate)["files"][TARGET]["sha256"]
        report["arms"][arm] = {
            "status": status, "blocker": blocker,
            "model_receipts": model.receipts[calls_before:],
            "candidate_id": candidate,
            "candidate_source_sha256": source_sha,
            "source_changed": source_sha is not None
            and source_sha != baseline["source_sha256"],
            "measurements": measured,
            "ledger_verified": ledger.verify()[0],
            "pilot_champion": (state or {}).get("champion_id"),
            "pilot_generation": (state or {}).get("generation"),
        }
        db.close_all()
    report["model_calls_observed"] = len(model.receipts)
    hashes = {
        v["candidate_source_sha256"] for v in report["arms"].values()
        if v["candidate_source_sha256"] is not None
    }
    report["distinct_model_code_candidates"] = len(hashes)
    report["independently_passing_arms"] = sum(
        any(m["passes"] and m["source_sha256"] != baseline["source_sha256"]
            for m in x["measurements"])
        for x in report["arms"].values()
    )
    report["status"] = "MODEL_PAIRED_MEASURED" if len(model.receipts) == 2 else "INCOMPLETE"
    report["claim_ceiling"] = (
        "one frozen public WLS self-code defect; actual pinned CPU model and "
        "C1 behavior probes; no sealed transfer, full-suite promotion or RSI"
    )
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-file", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run(
            args.model_file, os.environ.get("WLS_C1_PINNED_IMAGE", ""),
            args.artifact_root,
        )
    except Exception as exc:
        report = {
            "schema": SCHEMA, "status": "BLOCKED",
            "blocker_type": type(exc).__name__,
            "model_calls_observed": 0,
            "heldout_gain": "UNMEASURED", "rsi": "UNPROVEN",
        }
    args.report.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    print(json.dumps({
        "status": report["status"],
        "model_calls_observed": report.get("model_calls_observed"),
        "independently_passing_arms": report.get("independently_passing_arms"),
        "blocker_type": report.get("blocker_type"),
    }, sort_keys=True))
    return 0 if report["status"] == "MODEL_PAIRED_MEASURED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
