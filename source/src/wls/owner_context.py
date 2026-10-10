"""Owner-reviewed personal context import into the sole canonical WLS stores.

Input lives only in <WLS_HOME>/secrets/owner_context.json. Never check personal
context into a source repository, put it in a GitHub Action artifact, or treat
imported text as instructions with authority over WLS policy.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .db import Database
from .schemas import Goal, MemoryItem, RiskLevel
from .stores import GoalStore, MemoryStore

SCHEMA = "wls.owner_context.v1"
MAX_BYTES = 512_000
MAX_ENTRIES = 128
KINDS = {"preference", "method", "skill", "goal", "history", "hypothesis"}
ID_RE = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,79}\Z")


class OwnerContextBridge:
    """One-way bounded import. No second memory or goal authority."""

    def __init__(
        self, config: RuntimeConfig, db: Database,
        memories: MemoryStore, goals: GoalStore,
    ) -> None:
        self.config = config
        self.db = db
        self.memories = memories
        self.goals = goals

    @property
    def path(self) -> Path:
        return self.config.secret_path.parent / "owner_context.json"

    def import_pending(self) -> dict[str, Any]:
        path = self.path
        if not path.exists() and not path.is_symlink():
            return {"status": "NO_LOCAL_BUNDLE", "imported": 0}
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise ValueError("owner context must be a regular local non-symlink file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("owner context exceeds import byte limit")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("owner context is not valid UTF-8 JSON") from exc
        return self.import_bundle(payload, source_sha256=hashlib.sha256(raw).hexdigest())

    def import_bundle(
        self, payload: Any, *, source_sha256: str,
    ) -> dict[str, Any]:
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ValueError("unsupported owner context schema")
        if payload.get("consent") != "owner_reviewed_local_import":
            raise PermissionError("owner-reviewed import consent is required")
        if set(payload) != {"schema", "consent", "entries"}:
            raise ValueError("unexpected owner context top-level keys")
        items = payload["entries"]
        if not isinstance(items, list) or not 1 <= len(items) <= MAX_ENTRIES:
            raise ValueError("owner context entries must be a bounded non-empty list")
        if not re.fullmatch(r"[0-9a-f]{64}", source_sha256):
            raise ValueError("source_sha256 must be exact")

        # Preflight *every* record before any DB mutation. In particular,
        # a changed entry ID may not silently overwrite remembered truth.
        prepared: list[tuple[MemoryItem, Goal | None]] = []
        seen: set[str] = set()
        for entry in items:
            if not isinstance(entry, dict) or not set(entry) <= {
                "id", "kind", "text", "source_ref", "origin", "activate_readonly"
            }:
                raise ValueError("invalid owner context entry")
            if not {"id", "kind", "text", "source_ref", "origin"} <= set(entry):
                raise ValueError("owner context entry lacks provenance")
            entry_id, kind, text = entry["id"], entry["kind"], entry["text"]
            source_ref, origin = entry["source_ref"], entry["origin"]
            if (not isinstance(entry_id, str) or not ID_RE.fullmatch(entry_id)
                    or entry_id in seen):
                raise ValueError("duplicate or invalid owner context ID")
            seen.add(entry_id)
            if kind not in KINDS or not isinstance(text, str) or not 1 <= len(text.strip()) <= 1500:
                raise ValueError("invalid kind or unbounded owner context text")
            if (not isinstance(source_ref, str) or not 1 <= len(source_ref) <= 180
                    or origin not in {"owner_direct", "owner_retrospective", "assistant_summary"}):
                raise ValueError("owner context requires explicit source and origin")
            activate = entry.get("activate_readonly", False)
            if not isinstance(activate, bool):
                raise ValueError("activate_readonly must be boolean")
            if activate and (kind != "goal" or origin != "owner_direct"):
                raise PermissionError("only direct owner goals may be activated")
            digest = hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:32]
            content = {
                "text": text.strip(), "kind": kind,
                "source_ref": source_ref,
                "origin": origin,
                "evidence": "USER_REPORTED" if origin != "assistant_summary" else "UNVERIFIED_SUMMARY",
                "authority": "context_only_not_policy_or_executable_skill",
            }
            memory = MemoryItem(
                memory_id="mem_owner_" + digest,
                memory_type="owner_context",
                content=content,
                source_ids=["owner_context:" + entry_id],
                importance=0.85 if kind in {"goal", "method"} else 0.7,
                confidence=0.7 if origin == "owner_direct" else 0.4,
                tags=["owner_context", "candidate_only", kind],
            )
            goal = None
            if activate:
                goal = Goal(
                    goal_id="goal_owner_" + digest,
                    title=text.strip()[:120],
                    description=text.strip(),
                    source="owner_context",
                    origin="owner",
                    autonomous=False,
                    priority=0.8,
                    risk=RiskLevel.READ,
                    success_criteria=[
                        "Verify observable external results before claiming goal progress"
                    ],
                    task_spec={
                        "operation": "INSPECT", "proposal_only": True,
                        "owner_context_id": entry_id,
                        "read_only_scope": True,
                    },
                )
            old = self.db.query_one(
                "SELECT content_json FROM memories WHERE memory_id=?", (memory.memory_id,)
            )
            if old is not None and json.loads(old["content_json"]) != content:
                raise ValueError("entry ID revision requires a new ID")
            if goal is not None:
                prior = self.db.query_one(
                    "SELECT title,description FROM goals WHERE goal_id=?", (goal.goal_id,)
                )
                if prior is not None and (
                    prior["title"] != goal.title or prior["description"] != goal.description
                ):
                    raise ValueError("activated goal revision requires a new ID")
            prepared.append((memory, goal))

        created_memories = 0
        created_goals = 0
        for memory, goal in prepared:
            if self.db.query_one(
                "SELECT memory_id FROM memories WHERE memory_id=?", (memory.memory_id,)
            ) is None:
                self.memories.add(memory)
                created_memories += 1
            if goal is not None and self.goals.get(goal.goal_id) is None:
                self.goals.add(goal)
                created_goals += 1
        receipt = {
            "status": "IMPORTED",
            "source_sha256": source_sha256,
            "records": len(prepared),
            "imported": created_memories,
            "read_only_goals": created_goals,
            "declarative_skills_only": True,
            "model_weights_changed": False,
            "external_sync": False,
        }
        self.db.set_runtime("owner_context_last_import", receipt)
        return receipt
