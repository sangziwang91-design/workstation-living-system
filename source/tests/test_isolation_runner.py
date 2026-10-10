"""C1 contract checks; Docker execution evidence comes from hosted probe workflow."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from wls.isolation_runner import (
    IsolationReceipt,
    MAX_SOURCE_BYTES,
    build_command,
    run_isolated_python,
)


IMAGE = "python@sha256:" + "a" * 64
NAME = "wls-rsi-" + "b" * 24


def test_exact_digests_and_source_budgets_fail_closed():
    for invalid in ("python:3.13-slim", "python@sha256:bad", "", "alpine@sha256:" + "a" * 64):
        with pytest.raises(ValueError, match="pinned"):
            build_command(invalid, NAME, "print(1)")
    with pytest.raises(ValueError, match="byte budget"):
        build_command(IMAGE, NAME, "x" * (MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="container name"):
        build_command(IMAGE, "bad", "print(1)")


def test_command_has_no_host_mounts_network_secrets_or_privileged_switches():
    command = build_command(IMAGE, NAME, "print('clean')")
    assert command[:4] == ["docker", "--host", "unix:///var/run/docker.sock", "run"]
    assert "--network=none" in command
    assert "--read-only" in command
    assert "--cap-drop=ALL" in command
    assert "--security-opt=no-new-privileges" in command
    assert "--pids-limit=12" in command
    assert "--memory=128m" in command
    assert "--memory-swap=128m" in command
    assert "--pull=never" in command
    assert "--rm" in command
    assert "--user=65534:65534" in command
    assert "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m" in command
    assert "-v" not in command
    assert "--mount" not in command
    assert "--privileged" not in command
    assert "--env" not in command
    assert "-e" not in command
    assert command[-5:] == [IMAGE, "python", "-I", "-B", "-c", "print('clean')"][-5:]


def test_receipt_is_candidate_only_and_serializable():
    receipt = IsolationReceipt(
        status="TIMEOUT", exit_code=None,
        source_sha256="f" * 64, pinned_image=IMAGE,
        elapsed_seconds=1.0, stdout_tail="", stderr_tail="",
    )
    assert receipt.to_dict() == dataclasses.asdict(receipt)


def test_runner_requires_real_linux_docker_socket(monkeypatch):
    monkeypatch.setattr(Path, "is_socket", lambda self: False)
    with pytest.raises(RuntimeError, match="trusted local Linux Docker"):
        run_isolated_python("print(1)", image=IMAGE, timeout=1)


def test_probe_definitions_contain_five_hostile_boundary_checks():
    import runpy

    probes = runpy.run_path("source/scripts/verify_rsi_isolation.py")["PROBES"]
    names = {item[0] for item in probes}
    assert {
        "smoke", "host_grader", "host_secret", "host_root_write",
        "network", "timeout", "fork_limit",
    } <= names
    assert len(names) == len(probes)
