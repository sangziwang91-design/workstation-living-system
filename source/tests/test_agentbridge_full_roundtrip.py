"""GitHub-hosted cross-repository WLS -> AgentBridge -> WLS coding checks.

An actual OS child process modifies a Python source file in a disposable
workspace. Independent tests are outside that workspace and can reject
incorrect code even if the worker claims success. No real LLM is called.
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


@pytest.mark.parametrize("correct_patch", [True, False], ids=["valid-patch", "broken-patch"])
def test_two_repos_execute_coding_patch_and_independently_grade(
    tmp_path: Path, correct_patch: bool
):
    pytest.importorskip("agentbridge")
    from agentbridge.cli import app
    from typer.testing import CliRunner

    runtime = LivingSystem(default_config(tmp_path / "wls-home"))
    receipt = runtime.agentic.admit_and_compile(
        "Fix clamp(x, minimum, maximum) for out-of-range inputs",
        acceptance=["source passes hidden regression tests"],
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

    # A genuine source-code task. Initial source is wrong, not merely absent.
    workspace = tmp_path / "candidate-worktree"
    workspace.mkdir()
    source = workspace / "candidate.py"
    source.write_text(
        "def clamp(value, lower, upper):\n    return value\n",
        encoding="utf-8",
    )

    # The evaluator and its source are not in the agent's worktree. A marker
    # file cannot satisfy these assertions.
    hidden = tmp_path / "frozen-owner-tests"
    hidden.mkdir()
    oracle = hidden / "test_clamp_contract.py"
    oracle.write_text(
        "import sys\n"
        "sys.path.insert(0, " + repr(str(workspace)) + ")\n"
        "from candidate import clamp\n"
        "\n"
        "def test_inside():\n"
        "    assert clamp(3, 0, 5) == 3\n"
        "\n"
        "def test_over_limit():\n"
        "    assert clamp(12, 0, 5) == 5\n"
        "\n"
        "def test_under_limit():\n"
        "    assert clamp(-7, 0, 5) == 0\n",
        encoding="utf-8",
    )

    patch = (
        "def clamp(value, lower, upper):\n"
        "    return min(max(value, lower), upper)\n"
        if correct_patch else
        "def clamp(value, lower, upper):\n"
        "    return max(value, lower)\n"
    )
    # Tests the *real* OpenCode executable/subprocess adapter with a
    # deterministic compatibility worker. This is not evidence that an
    # LLM can synthesize the patch unaided.
    shim = tmp_path / "opencode-coding-shim"
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
        f"Path('candidate.py').write_text({patch!r}, encoding='utf-8')\n"
        "Path('result.txt').write_text('worker executed', encoding='utf-8')\n"
        "print('worker done')\n",
        encoding="utf-8",
    )
    shim.chmod(0o755)
    verify_command = f'"{sys.executable}" -m pytest -q "{oracle}"'
    argv = [
        "wls-cycle", original.message_id,
        "--mailbox-root", str(root),
        "--workspace", str(workspace),
        "--check-file", "result.txt",
        "--verify-command", verify_command,
        "--db", str(tmp_path / "bridge.db"),
        "--runs-dir", str(tmp_path / "bridge-runs"),
        "--executor", "opencode",
        "--allow-model-usage",
        "--opencode-executable", str(shim),
    ]
    ran = CliRunner().invoke(app, argv)
    # The failing candidate must fail *despite* a zero-exit worker and file.
    assert (ran.exit_code == 0) is correct_patch, ran.output
    assert source.read_text(encoding="utf-8") == patch
    assert (workspace / "result.txt").read_text(encoding="utf-8") == "worker executed"

    replies = sorted((root / "results").glob("*.json"))
    assert len(replies) == 1
    reply = json.loads(replies[0].read_text(encoding="utf-8"))
    assert reply["in_reply_to"] == original.message_id
    assert reply["payload"]["agentbridge_attempt_id"]
    checks = {c["check_id"]: c["status"] for c in reply["payload"]["verified_checks"]}
    assert checks["WLS_FILE_1"] == "PASS"
    assert checks["WLS_COMMAND_1"] == ("PASS" if correct_patch else "FAIL")
    assert reply["status"] == ("SUCCEEDED" if correct_patch else "FAILED")

    # Import is not a fake mailbox exchange: WLS must independently decide
    # whether the graph node may complete and retain signed evidence.
    imported = runtime.agentic.import_node_result_envelope(
        mailbox_root=root,
        message_id=reply["message_id"],
        artifact_root=workspace,
        acceptance_checks=[{
            "check_id": "wls_source_present",
            "type": "artifact_exists",
            "config": {"path": "candidate.py"},
        }],
    )
    assert imported["receipt_type"] == "AGENTIC_RESULT_ENVELOPE_IMPORTED"
    status = runtime.agentic.load_graph(graph_id).nodes[lease.node_id].status
    if correct_patch:
        assert imported["completion"]["acceptance_report"]["passed"] is True
        assert status is TaskNodeStatus.SUCCEEDED
    else:
        assert status is TaskNodeStatus.FAILED
    assert runtime.ledger.verify()[0]
    assert (root / "processed" / replies[0].name).exists()
