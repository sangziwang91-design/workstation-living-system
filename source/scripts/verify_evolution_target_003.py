from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json

from wls.memory_ablation import run_evolution_target_003
from wls.schemas import utc_now


CLAIM_BOUNDARY = (
    "This verifies local, controlled causal-memory retrieval, explicit "
    "memory-to-decision attribution, contradiction-driven memory suppression, "
    "a frozen memory-disabled ablation advantage, and restart continuity. "
    "It does not prove real-host longitudinal learning, multi-month autonomous "
    "adaptation, production readiness, AGI, consciousness, or subjective emotion."
)


def run_probe() -> dict:
    with TemporaryDirectory(prefix="wls-et003-") as raw:
        result = run_evolution_target_003(Path(raw))
        return {
            "target": "EVOLUTION-TARGET-003",
            "generated_at": utc_now(),
            "passed": bool(result["passed"]),
            "memory_enabled": result["memory_enabled"],
            "memory_disabled_baseline": result["memory_disabled_baseline"],
            "decision_differences": result["decision_differences"],
            "attributed_memory_ids": result["attributed_memory_ids"],
            "refuted_memory_ids": result["refuted_memory_ids"],
            "restart_continuity": result["restart_continuity"],
            "memory_refutation": result["refutation"],
            "event_stream_equivalent": result["event_stream_equivalent"],
            "regressions": result["regressions"],
            "claim_boundary": CLAIM_BOUNDARY,
            "evidence_classification": {
                "VERIFIED": [
                    "controlled memory-enabled versus memory-disabled ablation",
                    "structured project/entity/failure/applicability retrieval",
                    "decision trace attribution to concrete memory identifiers",
                    "repeated failure weakens then refutes a memory",
                    "refuted-memory suppression persists across restart",
                    "valid-memory advantage persists across restart",
                ],
                "INFERENCE": [
                    "the mechanism may support longer-term adaptation when validated on genuine host tasks"
                ],
                "UNKNOWN": [
                    "owner production-host advantage",
                    "multi-month longitudinal advantage",
                    "real-world statistical generalization",
                ],
            },
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run_probe()
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
