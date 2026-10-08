"""Model-independent proposal port for owner-started WLS RSI experiments.

A model generates untrusted candidate source. It never grades, executes, or
promotes that source. HTTP transport is opt-in and credentials stay outside
candidate artifacts and the WLS evidence ledger.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol
from urllib import error, parse, request

from .experiment_decision import ExperimentPolicy, MetricResult
from .rsi_artifact_gate import RsiArtifactGate
from .rsi_evolution import RsiEvolutionPilot


@dataclass(frozen=True, slots=True)
class RsiModelCapabilities:
    text_generation: bool = True
    structured_output: bool = False
    tool_calls: bool = False
    context_tokens: int | None = None

    def __post_init__(self) -> None:
        if not self.text_generation:
            raise ValueError("RSI requires text generation")
        if self.context_tokens is not None and self.context_tokens < 256:
            raise ValueError("model context budget is too small")


@dataclass(frozen=True, slots=True)
class RsiProposalRequest:
    model_id: str
    parent_id: str
    generation: int
    branch: int
    objective: str
    allowed_files: tuple[str, ...]
    max_output_bytes: int = 32_768
    experiment_id: str = ""

    def __post_init__(self) -> None:
        if not self.model_id.strip() or len(self.model_id) > 160:
            raise ValueError("model_id required")
        if self.experiment_id and not re.fullmatch(r"[a-z0-9-]{1,32}", self.experiment_id):
            raise ValueError("invalid experiment_id")
        if self.generation < 1 or self.branch < 0:
            raise ValueError("invalid generation or branch")
        if not self.objective.strip() or len(self.objective) > 8_192:
            raise ValueError("objective must be bounded")
        if not self.allowed_files or len(set(self.allowed_files)) != len(self.allowed_files):
            raise ValueError("allowed files required and unique")
        if not 1 <= self.max_output_bytes <= 1_000_000:
            raise ValueError("invalid output size cap")


@dataclass(frozen=True, slots=True)
class RsiProposalResponse:
    model_id: str
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.input_tokens is not None and self.input_tokens < 0:
            raise ValueError("invalid input token count")
        if self.output_tokens is not None and self.output_tokens < 0:
            raise ValueError("invalid output token count")


class RsiModelPort(Protocol):
    """Provider-neutral adapter. Plugins implement this single boundary."""

    @property
    def model_id(self) -> str: ...

    @property
    def capabilities(self) -> RsiModelCapabilities: ...

    def propose(self, instruction: str, *, max_output_bytes: int) -> RsiProposalResponse: ...


class ModelProtocolError(ValueError):
    """Adapter returned a response that cannot be trusted as a proposal."""


class RsiModelCandidateBuilder:
    """Connect a model port to the existing WLS signed candidate archive."""

    def __init__(
        self,
        port: RsiModelPort,
        artifact_gate: RsiArtifactGate,
        *,
        policy_digest: str,
        evaluator_digest: str,
    ) -> None:
        self.port = port
        self.artifact_gate = artifact_gate
        self.policy_digest = policy_digest
        self.evaluator_digest = evaluator_digest

    def propose(self, spec: RsiProposalRequest) -> str:
        if spec.model_id != self.port.model_id:
            raise ModelProtocolError("model identity changed during experiment")
        if not self.port.capabilities.text_generation:
            raise ModelProtocolError("provider lacks text generation")
        if not set(spec.allowed_files).issubset(self.artifact_gate.allowed_files):
            raise ModelProtocolError("proposal requests files outside frozen scope")
        parent = self.artifact_gate.verify(spec.parent_id)
        if parent["policy_digest"] != self.policy_digest:
            raise ModelProtocolError("candidate policy does not match parent")
        if parent["evaluator_digest"] != self.evaluator_digest:
            raise ModelProtocolError("candidate evaluator does not match parent")

        instruction = (
            "You are only proposing a candidate for a controlled experiment. "
            "Return only one JSON object containing a 'files' mapping from "
            "allowed relative filenames to complete UTF-8 file contents. "
            "Do not add a score, verdict, shell commands, or extra keys. "
            "Your output is stored, never executed here.\n"
            f"Goal: {spec.objective}\n"
            f"Parent: {spec.parent_id}\n"
            f"Generation: {spec.generation} branch: {spec.branch}\n"
            f"Allowed files: {json.dumps(sorted(spec.allowed_files))}\n"
        )
        response = self.port.propose(instruction, max_output_bytes=spec.max_output_bytes)
        # The provider is outside the trust boundary: reject parent mutations
        # before accepting the model's response as a child of that parent.
        if self.artifact_gate.verify(spec.parent_id)["manifest_digest"] != parent["manifest_digest"]:
            raise ModelProtocolError("parent candidate changed during proposal")
        if response.model_id != spec.model_id:
            raise ModelProtocolError("response model does not match request")
        if len(response.text.encode("utf-8")) > spec.max_output_bytes:
            raise ModelProtocolError("model output exceeds size cap")
        try:
            payload = json.loads(
                response.text,
                parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")),
            )
        except (ValueError, TypeError) as exc:
            raise ModelProtocolError("response must be strict JSON") from exc
        if not isinstance(payload, dict) or set(payload) != {"files"}:
            raise ModelProtocolError("response schema must contain only files")
        files = payload["files"]
        if not isinstance(files, dict) or not files:
            raise ModelProtocolError("candidate files must be a nonempty mapping")
        if not set(files).issubset(spec.allowed_files):
            raise ModelProtocolError("candidate contains unauthorized files")
        if any(not isinstance(value, str) for value in files.values()):
            raise ModelProtocolError("candidate file contents must be UTF-8 text")
        namespace = f"{spec.experiment_id}-" if spec.experiment_id else ""
        candidate_id = f"rsi-{namespace}g{spec.generation:06d}-b{spec.branch:03d}"
        self.artifact_gate.register(
            artifact_id=candidate_id,
            parent_id=spec.parent_id,
            generation=spec.generation,
            branch=spec.branch,
            files={name: value.encode("utf-8") for name, value in files.items()},
            policy_digest=self.policy_digest,
            evaluator_digest=self.evaluator_digest,
        )
        return candidate_id


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenAICompatibleProposalPort:
    """Explicit opt-in HTTPS/loopback adapter; no model calls in default CI.

    Compatible *chat/completions* endpoints only. Other provider protocols
    must implement RsiModelPort; 'any model' does not mean any wire format.
    """

    def __init__(
        self,
        *,
        model_id: str,
        base_url: str,
        api_key_env: str = "WLS_RSI_API_KEY",
        timeout_seconds: float = 45,
        max_output_tokens: int = 1024,
    ) -> None:
        uri = parse.urlsplit(base_url)
        if (
            uri.username or uri.password or uri.query or uri.fragment
            or not uri.hostname or uri.path not in {"", "/"}
            or uri.scheme not in {"https", "http"}
            or (uri.scheme == "http" and uri.hostname not in {
                "127.0.0.1", "localhost", "::1",
            })
        ):
            raise ValueError("base_url must be HTTPS or loopback HTTP without credentials")
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,80}", api_key_env):
            raise ValueError("invalid credential environment variable")
        if not model_id.strip() or not math.isfinite(timeout_seconds) or timeout_seconds <= 0 or timeout_seconds > 180:
            raise ValueError("invalid model or timeout")
        if not 1 <= max_output_tokens <= 8192:
            raise ValueError("invalid per-call token limit")
        self._base_url = base_url.rstrip("/")
        self._model_id = model_id
        self._api_key_env = api_key_env
        self._timeout = timeout_seconds
        self._max_output_tokens = max_output_tokens
        self._opener = request.build_opener(_NoRedirect())

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def capabilities(self) -> RsiModelCapabilities:
        return RsiModelCapabilities()

    def propose(self, instruction: str, *, max_output_bytes: int) -> RsiProposalResponse:
        if not 1 <= max_output_bytes <= 1_000_000:
            raise ValueError("invalid output size cap")
        if not instruction or len(instruction) > 12_000:
            raise ValueError("prompt too long or empty")
        key = os.environ.get(self._api_key_env, "")
        if not key and not self._base_url.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]")):
            raise RuntimeError("model credential not configured")
        body = json.dumps({
            "model": self._model_id,
            "messages": [{"role": "user", "content": instruction}],
            "max_tokens": self._max_output_tokens,
        }).encode("utf-8")
        req = request.Request(
            self._base_url + "/v1/chat/completions",
            data=body,
            headers={
                "Content-Type": "application/json",
                **({"Authorization": "Bearer " + key} if key else {}),
            },
            method="POST",
        )
        try:
            with self._opener.open(req, timeout=self._timeout) as response:
                # Bound server-provided bytes; never embed raw service responses
                # in logs or evidence (could contain credentials).
                raw = response.read(max_output_bytes * 4 + 1)
        except (error.HTTPError, error.URLError, TimeoutError):
            raise RuntimeError("model endpoint request failed") from None
        if len(raw) > max_output_bytes * 4:
            raise ModelProtocolError("model endpoint response exceeds byte cap")
        try:
            payload = json.loads(raw)
            text = payload["choices"][0]["message"]["content"]
            if not isinstance(text, str):
                raise TypeError("expected text")
            usage = payload.get("usage") or {}
            return RsiProposalResponse(
                model_id=self._model_id,
                text=text,
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
            )
        except (KeyError, IndexError, ValueError, TypeError):
            raise ModelProtocolError("model endpoint response is invalid") from None


class RsiModelExperiment:
    """End-to-end owner-started model -> candidate archive -> *trusted* evaluator.

    Candidate bytes are never executed here. The externally provided evaluator
    must use its own isolated and pinned scoring process for executable code.
    """

    def __init__(
        self,
        pilot: RsiEvolutionPilot,
        builder: RsiModelCandidateBuilder,
        policy: ExperimentPolicy,
        *,
        objective: str,
        allowed_files: tuple[str, ...],
        independent_evaluator: Callable[[str], MetricResult],
    ) -> None:
        if builder.policy_digest != policy.digest():
            raise ValueError("pilot policy does not match artifact builder")
        if builder.evaluator_digest != policy.evaluator_digest:
            raise ValueError("pilot evaluator does not match artifact builder")
        self.pilot = pilot
        self.builder = builder
        self.policy = policy
        self.objective = objective
        self.allowed_files = allowed_files
        self.independent_evaluator = independent_evaluator

    def start(self, run_id: str, baseline_id: str, *, branches: int = 2) -> dict:
        manifest = self.builder.artifact_gate.verify(baseline_id)
        if manifest["evaluator_digest"] != self.policy.evaluator_digest:
            raise ModelProtocolError("baseline evaluator does not match policy")
        if manifest["policy_digest"] != self.policy.digest():
            raise ModelProtocolError("baseline policy does not match run")
        baseline = self._measure_verified(baseline_id)
        return self.pilot.start(
            run_id=run_id, policy=self.policy, baseline_id=baseline_id,
            baseline=baseline, branches=branches,
            max_no_gain_rounds=self.policy.max_rounds,
        )

    def _measure_verified(self, artifact_id: str) -> MetricResult:
        # A score applies only to immutable bytes admitted to the HMAC ledger.
        # Neither model output nor a trusted scoring callback can bypass this
        # check by changing the candidate after reading its source.
        before = self.builder.artifact_gate.verify(artifact_id)
        measured = self.independent_evaluator(artifact_id)
        after = self.builder.artifact_gate.verify(artifact_id)
        if after["manifest_digest"] != before["manifest_digest"]:
            raise ModelProtocolError("candidate identity changed during evaluation")
        if not isinstance(measured, MetricResult):
            raise TypeError("independent evaluator must return MetricResult")
        return measured

    def advance(self, run_id: str) -> dict:
        # The run ID is hashed rather than embedded verbatim in filesystem paths.
        namespace = hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:12]

        def propose(parent_id: str, generation: int, branch: int) -> str:
            spec = RsiProposalRequest(
                model_id=self.builder.port.model_id,
                parent_id=parent_id,
                generation=generation,
                branch=branch,
                experiment_id=namespace,
                objective=self.objective,
                allowed_files=self.allowed_files,
            )
            return self.builder.propose(spec)

        return self.pilot.advance(
            run_id, policy=self.policy, propose=propose,
            evaluate=self._measure_verified,
        )

    def run_bounded(self, run_id: str) -> dict:
        state = self.pilot.read(run_id)
        if state is None:
            raise ValueError("unknown run")
        while state["status"] == "READY":
            state = self.advance(run_id)
        return state
