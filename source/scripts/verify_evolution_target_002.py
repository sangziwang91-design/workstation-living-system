from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import argparse
import json

from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import EvidenceKind, Observation, VerificationStatus, utc_now


def run_probe() -> dict:
    with TemporaryDirectory(prefix="wls-et002-") as raw:
        root = Path(raw)
        config = default_config(root / "home")
        config.sensors = []
        config.sleep_after_idle_cycles = 100
        runtime = LivingSystem(config)
        try:
            target = config.sandbox_path / "world"
            target.mkdir(parents=True)
            (target / "evidence.txt").write_text("observed", encoding="utf-8")
            observation = Observation(
                source="et002-verifier",
                kind="external_event",
                subject="owner-request",
                predicate="request",
                value={"action": "inspect_path", "path": str(target)},
                confidence=1.0,
                evidence_kind=EvidenceKind.DIRECT,
                verification=VerificationStatus.VERIFIED,
            )
            runtime.events.add_observation(observation)
            runtime.world.assimilate(observation)
            result = runtime.run_cycle()
            runtime.close()
            restarted = LivingSystem(config)
            try:
                trace = restarted.cognition.recent(limit=1)[0]
                cognition_ok, cognition_details = restarted.cognition.integrity()
                integrity = restarted.verify_integrity(full=True)
                temporal = restarted.temporal_world.summary()
                checks = {
                    "cycle_succeeded": result["status"] == "SUCCEEDED",
                    "bounded_cognition_selected_request": trace["selected_key"].startswith(
                        "inspect_requested_path:"
                    ),
                    "prediction_confirmed": (
                        trace["outcome"]["predictions"][0]["status"] == "CONFIRMED"
                    ),
                    "causal_trial_persisted": temporal["causal_trials"] >= 1,
                    "trace_survived_restart": trace["status"] == "RESOLVED",
                    "cognition_integrity": cognition_ok,
                    "runtime_integrity": integrity["ok"],
                }
                return {
                    "target": "EVOLUTION-TARGET-002",
                    "generated_at": utc_now(),
                    "passed": all(checks.values()),
                    "checks": checks,
                    "cycle": {
                        "status": result["status"],
                        "plan_status": result["plan_status"],
                        "actions": result["actions"],
                        "outcomes": result["outcomes"],
                    },
                    "trace": trace,
                    "temporal_world": temporal,
                    "cognition_integrity": cognition_details,
                    "runtime_integrity": integrity,
                    "claim_boundary": (
                        "This verifies a bounded local cognition vertical slice with durable "
                        "hypothesis competition, prediction, action, calibration, and restart "
                        "continuity. It does not prove general intelligence, consciousness, or "
                        "long-term autonomous superiority."
                    ),
                }
            finally:
                restarted.close()
        finally:
            runtime.close()


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
