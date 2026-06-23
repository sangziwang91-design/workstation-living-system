from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
import json
import os

from .config import RuntimeConfig
from .schemas import ActionSpec, Plan, RiskLevel


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, "redirect blocked", headers, fp)


class PlanningProvider(ABC):
    @abstractmethod
    def create_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class DeterministicProvider(PlanningProvider):
    """Grounded fallback planner that does not require a model."""

    TEXT_SUFFIXES = {
        ".txt",
        ".md",
        ".json",
        ".yaml",
        ".yml",
        ".toml",
        ".py",
        ".js",
        ".ts",
        ".ps1",
        ".bat",
        ".sh",
        ".ini",
        ".cfg",
        ".csv",
        ".log",
        ".tex",
    }

    def create_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        actions: list[dict[str, Any]] = []
        max_actions = int(context["budget"].get("max_actions", 0))
        outbox = Path(context["paths"]["outbox"])
        for item in context.get("workspace", []):
            if len(actions) >= max_actions:
                break
            if item.get("item_type") != "event":
                continue
            payload = item.get("payload", {})
            observation = payload.get("payload", {}).get("observation", {})
            kind = observation.get("kind")
            subject = str(observation.get("subject", ""))
            value = observation.get("value")
            if kind == "filesystem_change" and value in {"created", "modified"}:
                path = Path(subject)
                if path.suffix.lower() in self.TEXT_SUFFIXES:
                    actions.append(
                        {
                            "tool": "read_file",
                            "arguments": {"path": subject, "max_bytes": 524288},
                            "purpose": f"Inspect changed text file observed in {item['reference_id']}",
                            "expected_result": "File content or bounded binary preview",
                            "risk": "READ",
                            "goal_id": None,
                            "skill_id": None,
                            "acceptance": [
                                "output contains path",
                                "output contains text or binary marker",
                            ],
                        }
                    )
            elif kind in {"sensor_error", "service_health", "process_change"}:
                note_path = outbox / f"attention-{item['reference_id']}.json"
                actions.append(
                    {
                        "tool": "emit_note",
                        "arguments": {
                            "path": str(note_path),
                            "title": f"WLS attention: {kind}",
                            "body": item.get("summary", "")[:4000],
                        },
                        "purpose": "Persist a bounded internal attention note for later human or connector review",
                        "expected_result": "Internal note written to the WLS outbox",
                        "risk": "READ",
                        "goal_id": None,
                        "skill_id": None,
                        "acceptance": ["output contains path"],
                    }
                )
            elif (
                kind == "external_event"
                and isinstance(value, dict)
                and value.get("action") == "inspect_path"
            ):
                target_path = str(value.get("path", ""))
                if target_path:
                    actions.append(
                        {
                            "tool": "list_directory",
                            "arguments": {"path": target_path, "limit": 200},
                            "purpose": "Inspect a user-requested path without modifying it",
                            "expected_result": "Bounded directory listing",
                            "risk": "READ",
                            "goal_id": None,
                            "skill_id": None,
                            "acceptance": ["output contains items"],
                        }
                    )
        if not actions and max_actions > 0 and context.get("matching_skills"):
            skill = context["matching_skills"][0]
            if skill.get("status") in {"APPROVED", "PROMOTED"}:
                for step in skill.get("steps", [])[:max_actions]:
                    actions.append(
                        {
                            "tool": step["tool"],
                            "arguments": dict(step.get("arguments", {})),
                            "purpose": f"[skill:{skill['skill_id']}] {step.get('purpose', skill.get('description', 'Run learned skill'))}",
                            "expected_result": "Step completes under its tool contract",
                            "risk": skill.get("risk", "READ"),
                            "goal_id": None,
                            "skill_id": skill["skill_id"],
                            "acceptance": list(step.get("acceptance", [])),
                        }
                    )
        meaningful_workspace = any(
            item.get("item_type") in {"event", "goal", "memory"}
            for item in context.get("workspace", [])
        )
        if not actions and max_actions > 0 and meaningful_workspace:
            actions.append(
                {
                    "tool": "noop",
                    "arguments": {
                        "reason": "No safe grounded external action was justified"
                    },
                    "purpose": "Record a deliberate no-op instead of inventing activity",
                    "expected_result": "No external change",
                    "risk": "READ",
                    "goal_id": None,
                    "skill_id": None,
                    "acceptance": ["output ok is true"],
                }
            )
        return {
            "rationale": "Deterministic grounded plan derived only from current workspace items and configured budget.",
            "actions": actions,
            "memory_ids": [
                item["reference_id"]
                for item in context.get("workspace", [])
                if item.get("item_type") == "memory"
            ],
            "world_fact_ids": [
                fact["fact_id"] for fact in context.get("world_facts", [])[:10]
            ],
            "unknowns": context.get("unknowns", []),
        }


class OpenAICompatibleProvider(PlanningProvider):
    """Optional JSON planner using an OpenAI-compatible chat-completions endpoint.

    The provider is not required for installation. Outputs are parsed against a
    strict local plan schema before they can reach policy or tools.
    """

    def __init__(self, settings: dict[str, Any]):
        self.base_url = str(
            settings.get("base_url", "https://api.openai.com/v1")
        ).rstrip("/")
        parsed = urlparse(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("invalid provider base_url")
        if parsed.scheme != "https" and parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("non-local provider endpoints require HTTPS")
        self.model = str(settings.get("model", ""))
        self.api_key_env = str(settings.get("api_key_env", "OPENAI_API_KEY"))
        self.timeout = float(settings.get("timeout_seconds", 60.0))
        if not self.model:
            raise ValueError("provider model is required")

    def create_plan(self, context: dict[str, Any]) -> dict[str, Any]:
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            raise RuntimeError(
                f"missing API key environment variable: {self.api_key_env}"
            )
        system = (
            "You are the bounded planner of a persistent local software-life runtime. "
            "Use only facts, memories, skills, and tools in the supplied context. "
            "Do not invent observations. Prefer no action over unsupported action. "
            "Return one JSON object with keys rationale, actions, memory_ids, world_fact_ids, unknowns. "
            "Every action must have tool, arguments, purpose, expected_result, risk, goal_id, acceptance."
        )
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(context, ensure_ascii=False, sort_keys=True),
                },
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        opener = build_opener(NoRedirect)
        with opener.open(request, timeout=self.timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"]
        return json.loads(content)


class Planner:
    def __init__(self, config: RuntimeConfig):
        provider_type = str(config.provider.get("type", "deterministic"))
        if provider_type == "deterministic":
            self.provider: PlanningProvider = DeterministicProvider()
        elif provider_type == "openai_compatible":
            self.provider = OpenAICompatibleProvider(config.provider)
        else:
            raise ValueError(f"unknown provider type: {provider_type}")

    def plan(self, context: dict[str, Any]) -> Plan:
        raw = self.provider.create_plan(context)
        if not isinstance(raw, dict):
            raise ValueError("planner output must be an object")
        required = {"rationale", "actions", "memory_ids", "world_fact_ids", "unknowns"}
        if set(raw) - required:
            raise ValueError(f"unexpected plan keys: {sorted(set(raw) - required)}")
        if not isinstance(raw.get("rationale"), str):
            raise ValueError("rationale must be a string")
        if not isinstance(raw.get("actions"), list):
            raise ValueError("actions must be a list")
        actions: list[ActionSpec] = []
        for index, item in enumerate(raw["actions"]):
            if not isinstance(item, dict):
                raise ValueError(f"action {index} must be an object")
            expected_keys = {
                "tool",
                "arguments",
                "purpose",
                "expected_result",
                "risk",
                "goal_id",
                "skill_id",
                "acceptance",
            }
            extra = set(item) - expected_keys
            if extra:
                raise ValueError(f"unexpected action keys at {index}: {sorted(extra)}")
            actions.append(
                ActionSpec(
                    tool=str(item["tool"]),
                    arguments=dict(item.get("arguments", {})),
                    purpose=str(item["purpose"]),
                    expected_result=str(item["expected_result"]),
                    risk=RiskLevel(str(item.get("risk", "READ"))),
                    goal_id=item.get("goal_id"),
                    skill_id=item.get("skill_id"),
                    acceptance=[str(value) for value in item.get("acceptance", [])],
                )
            )
        return Plan(
            rationale=raw["rationale"],
            actions=actions,
            memory_ids=[str(value) for value in raw.get("memory_ids", [])],
            world_fact_ids=[str(value) for value in raw.get("world_fact_ids", [])],
            unknowns=[str(value) for value in raw.get("unknowns", [])],
        )
