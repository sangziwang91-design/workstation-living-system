"""C1 disposable Docker execution boundary for *candidate-only* Python probes.

This is a worker, not a second WLS runtime, independent judge, or deployment
authority. A trusted caller must pull a pinned image before invocation. Never
pass owner credentials, GitHub tokens, repository mounts, or grader files.
Docker is defense in depth; this is not proof of resistance to kernel escape.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import os
from pathlib import Path
import re
import subprocess  # nosec B404 -- static Docker argv, no shell
import tempfile
import time
from uuid import uuid4


_PINNED_IMAGE = re.compile(r"python@sha256:[0-9a-f]{64}\Z")
MAX_SOURCE_BYTES = 8192
MAX_CAPTURE_BYTES = 65536
MAX_REPORT_BYTES = 4096


@dataclass(frozen=True)
class IsolationReceipt:
    status: str
    exit_code: int | None
    source_sha256: str
    pinned_image: str
    elapsed_seconds: float
    stdout_tail: str
    stderr_tail: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_command(image: str, name: str, source: str) -> list[str]:
    """Return a bounded Docker command; never a host shell command."""
    if not _PINNED_IMAGE.fullmatch(image):
        raise ValueError("image must be an exact pinned python@sha256 digest")
    if not re.fullmatch(r"wls-rsi-[a-f0-9]{24}", name):
        raise ValueError("invalid disposable container name")
    if not isinstance(source, str):
        raise ValueError("source must be a string")
    if not 1 <= len(source.encode("utf-8")) <= MAX_SOURCE_BYTES:
        raise ValueError("candidate source outside byte budget")
    return [
        "docker", "--host", "unix:///var/run/docker.sock", "run",
        "--rm", "--pull=never", "--name", name,
        "--network=none", "--read-only", "--ipc=none",
        "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--pids-limit=12", "--memory=128m", "--memory-swap=128m",
        "--cpus=1.0", "--ulimit=nofile=64:64", "--ulimit=fsize=1048576:1048576",
        "--user=65534:65534", "--workdir=/tmp",
        "--tmpfs=/tmp:rw,noexec,nosuid,nodev,size=8m",
        image, "python", "-I", "-B", "-c", source,
    ]


def run_isolated_python(source: str, *, image: str, timeout: float = 5) -> IsolationReceipt:
    """Run a bounded probe; time/output exhaustion is failure, not success.

    The Docker CLI's host environment is restricted too. No host paths,
    persistent writable volumes, credential envs, or network are provided
    to the candidate. Linux Docker service must be an explicitly trusted,
    ephemeral GitHub-hosted worker. No model-written code is used by CI.
    """
    if os.name != "posix" or not Path("/var/run/docker.sock").is_socket():
        raise RuntimeError("C1 requires a trusted local Linux Docker socket")
    if not 0.2 <= timeout <= 30:
        raise ValueError("timeout outside fixed C1 bounds")
    name = "wls-rsi-" + uuid4().hex[:24]
    command = build_command(image, name, source)
    start = time.monotonic()
    status = "RUNTIME_FAILURE"
    code: int | None = None
    # docker run logs flow into files, not an unbounded in-memory pipe.
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(  # nosec B603 -- fully scoped Docker argv
            command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
            env={"PATH": os.defpath, "HOME": "/nonexistent"},
        )
        try:
            while True:
                code = process.poll()
                if code is not None:
                    status = "EXIT_0" if code == 0 else "EXIT_NONZERO"
                    break
                if (os.fstat(stdout.fileno()).st_size > MAX_CAPTURE_BYTES
                        or os.fstat(stderr.fileno()).st_size > MAX_CAPTURE_BYTES):
                    status = "OUTPUT_LIMIT"
                    break
                if time.monotonic() - start >= timeout:
                    status = "TIMEOUT"
                    break
                time.sleep(0.05)
        finally:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=3)
            finally:
                # Docker CLI death need not stop its container; force cleanup.
                subprocess.run(  # nosec B603 -- static container ID and argv
                    ["docker", "--host", "unix:///var/run/docker.sock",
                     "rm", "-f", name],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env={"PATH": os.defpath, "HOME": "/nonexistent"},
                    timeout=8, check=False,
                )
        stdout.seek(0)
        stderr.seek(0)
        out = stdout.read(MAX_REPORT_BYTES).decode("utf-8", errors="replace")
        err = stderr.read(MAX_REPORT_BYTES).decode("utf-8", errors="replace")
    return IsolationReceipt(
        status=status, exit_code=code,
        source_sha256=sha256(source.encode("utf-8")).hexdigest(),
        pinned_image=image,
        elapsed_seconds=round(time.monotonic() - start, 3),
        stdout_tail=out, stderr_tail=err,
    )
