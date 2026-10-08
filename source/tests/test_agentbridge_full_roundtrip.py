"""Real cross-repository subprocess handoff: WLS -> AgentBridge -> WLS.

CI installs the *pinned* public AgentBridge package only for Python 3.13.
The executable is a controlled OpenCode-compatible subprocess, not a model.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from wls.agentic_mailbox import AgenticFileMailbox
from wls.config import default_config
from wls.runtime import LivingSystem
from wls.schemas import TaskNodeStatus


def test_two_actual_repositories_exchange_and_accept_work(tmp_path: Path):
    pytest.importorskip("agentbridge")
    from agentbridge.cli import app
    from typer.testing import CliRunner

    runtime = LivingSystem(default_config(tmp_path / "wls-home"))
    receipt = runtime.agentic.admit_and_compile(
        "Inspect repository docs and record an inspection summary",
        acceptance=["inspection result is recorded"],
        evidence_required=["agentic_task_graph_compiled"],
    )
    graph_id = receipt["graph"]["graph_id"]
    lease = runtime.agentic.acquire_ready_leases(
        graph_id, worker_id="readonly-inspector",
    )[0]
    root = tmp_path / "shared-mailbox"
    export = runtime.agentic.export_node_task_envelope(
        graph_id, lease.node_id, lease_id=lease.lease_id,
        mailbox_root=root, recipient="agentbridge",
    )
    original = AgenticFileMailbox(root).read_task(export["message_id"])

    workspace = tmp_path / "candidate"
    workspace.mkdir()
    # Runs as an actual child process using the *existing* OpenCode executor
    # protocol. This proves transport/execution/verification, not paid AI.
    shim = tmp_path / "opencode-integration-shim"
    shim.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "from pathlib import Path\n"
        "if '--version' in sys.argv:\n"
        "    print('1.18.10')\n"
        "    raise SystemExit(0)\n"
        "if '--help' in sys.argv:\n"
        "    print('--format --dir --agent --title')\n"
        "    raise SystemExit(0)\n"
        "Path('inspection.txt').write_text('inspected', encoding='utf-8')\n"
        "print('inspection complete')\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    argv = [
        "wls-cycle", original.message_id,
        "--mailbox-root", str(root),
        "--workspace", str(workspace),
        "--check-file", "inspection.txt",
        "--verify-command",
        "python -c \"from pathlib import Path; assert Path('inspection.txt').read_text() == 'inspected'\"",
        "--db", str(tmp_path / "bridge.db"),
        "--runs-dir", str(tmp_path / "bridge-runs"),
        "--executor", "opencode",
        "--allow-model-usage",
        "--opencode-executable", str(shim),
    ]
    ran = CliRunner().invoke(app, argv)
    assert ran.exit_code == 0, ran.output
    replies = sorted((root / "results").glob("*.json"))
    assert len(replies) == 1
    reply = json.loads(replies[0].read_text(encoding="utf-8"))
    assert reply["status"] == "SUCCEEDED"
    assert reply["in_reply_to"] == original.message_id
    assert reply["payload"]["agentbridge_attempt_id"]
    assert {item["check_id"] for item in reply["payload"]["verified_checks"]} == {
        "WLS_FILE_1", "WLS_COMMAND_1",
    }

    # Canonical WLS performs a separate local artifact check, rather than
    # treating a worker's SUCCESS text as sufficient to close its graph.
    imported = runtime.agentic.import_node_result_envelope(
        mailbox_root=root,
        message_id=reply["message_id"],
        artifact_root=workspace,
        acceptance_checks=[{
            "check_id": "wls_independent_output",
            "type": "artifact_exists",
            "config": {"path": "inspection.txt"},
        }],
    )
    assert imported["receipt_type"] == "AGENTIC_RESULT_ENVELOPE_IMPORTED"
    assert imported["completion"]["acceptance_report"]["passed"] is True
    assert runtime.agentic.load_graph(graph_id).nodes[lease.node_id].status is (
        TaskNodeStatus.SUCCEEDED
    )
    assert runtime.ledger.verify()[0]
    assert (root / "processed" / replies[0].name).exists()
