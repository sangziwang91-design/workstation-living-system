"""GitHub-hosted C1 *hand-authored* hostile probes, never model code.

Two passes are required. This command must not receive any API credentials.
It exercises containment properties, not kernel escape resistance or RSI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from wls.isolation_runner import run_isolated_python


PROBES = (
    ("smoke", "print('C1_OK')", "EXIT_0", "C1_OK"),
    ("host_grader", """from pathlib import Path
targets = ('/workspace/source/src/wls/task_admission.py', '/workstation-living-system/source/src/wls/task_admission.py')
print('GRADER_ABSENT' if all(not Path(p).exists() for p in targets) else 'HOST_GRADER_VISIBLE')
""", "EXIT_0", "GRADER_ABSENT"),
    ("host_secret", """import os
print('NO_HOST_SENTINEL' if 'WLS_PRIVATE_SENTINEL' not in os.environ else 'HOST_SECRET_EXPOSED')
""", "EXIT_0", "NO_HOST_SENTINEL"),
    ("host_root_write", """from pathlib import Path
try:
    Path('/etc/wls-c1-forbidden').write_text('bad')
except OSError:
    print('ROOT_WRITE_BLOCKED')
else:
    print('ROOT_WRITABLE')
""", "EXIT_0", "ROOT_WRITE_BLOCKED"),
    ("network", """import socket
try:
    sock = socket.socket()
    sock.settimeout(0.5)
    sock.connect(('1.1.1.1', 53))
except OSError:
    print('NETWORK_BLOCKED')
else:
    print('NETWORK_ALLOWED')
finally:
    sock.close()
""", "EXIT_0", "NETWORK_BLOCKED"),
    ("timeout", "while True: pass", "TIMEOUT", ""),
    ("fork_limit", """import os, time
for i in range(30):
    try:
        pid = os.fork()
    except OSError:
        print('PIDS_BLOCKED', flush=True)
        break
    if pid == 0:
        time.sleep(5)
        os._exit(0)
else:
    print('FORK_UNBOUNDED', flush=True)
""", "EXIT_0", "PIDS_BLOCKED"),
)


def main() -> int:
    image = os.environ.get("WLS_C1_PINNED_IMAGE", "")
    report = []
    # The runner process must NOT pass this sentinel into the container.
    os.environ["WLS_PRIVATE_SENTINEL"] = "HOST_ONLY_DO_NOT_PRINT"
    for repeat in (1, 2):
        for name, source, expected_status, expected_text in PROBES:
            receipt = run_isolated_python(
                source, image=image, timeout=1.5 if name == "timeout" else 4,
            )
            valid = (
                receipt.status == expected_status
                and (not expected_text or expected_text in receipt.stdout_tail)
            )
            report.append({
                "repeat": repeat, "probe": name, "passed": valid,
                "receipt": receipt.to_dict(),
            })
    all_passed = all(item["passed"] for item in report)
    output = {
        "schema": "wls.c1_isolation_probe.v1",
        "status": "PROVISIONAL_C1_PROBE_PASS" if all_passed else "C1_PROBE_FAILED",
        "source_type": "github_hosted_real_docker",
        "trials": len(report),
        "passed": sum(item["passed"] for item in report),
        "image": image,
        "claim_ceiling": "disposable_worker_probe_only_not_kernel_escape_proof",
        "results": report,
    }
    Path("c1-isolation-report.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": output["status"], "passed": output["passed"],
        "trials": len(report), "image": image,
        "failed_probes": [
            str(item["probe"]) + ":" + str(item["repeat"])
            for item in report if not item["passed"]
        ],
    }, sort_keys=True))
    return 0 if all_passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
