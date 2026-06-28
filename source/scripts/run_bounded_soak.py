import argparse
import sys
import os
import time
import json
import platform
import tracemalloc
from pathlib import Path
from datetime import datetime
from wls.config import default_config
from wls.runtime import LivingSystem

def run_soak(max_cycles=1000, home_path=None):
    if not home_path:
        home_path = Path("soak_home")
    home_path.mkdir(parents=True, exist_ok=True)

    config = default_config(home_path)
    # Ensure keys are 32 bytes
    (home_path / "secrets").mkdir(parents=True, exist_ok=True)
    (home_path / "secrets/evidence.key").write_bytes(b"a" * 32)
    (home_path / "secrets/approval.key").write_bytes(b"b" * 32)
    config.read_only = False

    tracemalloc.start()
    start_time = time.time()

    system = LivingSystem(config)

    results = {
        "completed_cycles": 0,
        "failed_cycles": 0,
        "exceptions": [],
        "database_size_bytes": 0,
        "evidence_count": 0,
        "memory_peak": 0,
        "elapsed_time": 0
    }

    try:
        for i in range(max_cycles):
            try:
                if i % 10 == 0:
                    system.ledger.append("soak_event", {"cycle": i})

                # Correct method is run_cycle
                system.run_cycle()

                results["completed_cycles"] += 1

                # Periodic restart to test continuity
                if i > 0 and i % 100 == 0:
                    system = LivingSystem(config)

            except Exception as e:
                results["failed_cycles"] += 1
                results["exceptions"].append(str(e))
                if results["failed_cycles"] > 10:
                    break

    finally:
        results["elapsed_time"] = time.time() - start_time
        results["memory_peak"] = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()

        if config.db_path.exists():
            results["database_size_bytes"] = config.db_path.stat().st_size

        ok, details = system.ledger.verify()
        results["integrity_pass"] = ok
        results["evidence_count"] = details.get("records", 0)

    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("cycles", type=int, nargs='?', default=10)
    args = parser.parse_args()

    res = run_soak(args.cycles)
    print(json.dumps(res, indent=2))

    # Fail if more than 10% cycles failed or integrity check failed
    if res["failed_cycles"] > (args.cycles * 0.1) or not res.get("integrity_pass"):
        sys.exit(1)
    sys.exit(0)
