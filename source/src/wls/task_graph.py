from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .schemas import (
    RiskLevel,
    TaskDomain,
    TaskNodeStatus,
    TaskOperation,
    digest_json,
    new_id,
    utc_now,
)
from .task_admission import TaskIntent


_TERMINAL = {
    TaskNodeStatus.SUCCEEDED,
    TaskNodeStatus.FAILED,
    TaskNodeStatus.BLOCKED,
    TaskNodeStatus.CANCELLED,
}

_ALLOWED: dict[TaskNodeStatus, set[TaskNodeStatus]] = {
    TaskNodeStatus.PENDING: {
        TaskNodeStatus.READY,
        TaskNodeStatus.BLOCKED,
        TaskNodeStatus.CANCELLED,
    },
    TaskNodeStatus.READY: {
        TaskNodeStatus.LEASED,
        TaskNodeStatus.WAITING_APPROVAL,
        TaskNodeStatus.CANCELLED,
    },
    TaskNodeStatus.LEASED: {
        TaskNodeStatus.SUCCEEDED,
        TaskNodeStatus.FAILED,
        TaskNodeStatus.READY,
    },
    TaskNodeStatus.WAITING_APPROVAL: {
        TaskNodeStatus.READY,
        TaskNodeStatus.CANCELLED,
    },
    TaskNodeStatus.FAILED: {
        TaskNodeStatus.READY,
        TaskNodeStatus.BLOCKED,
        TaskNodeStatus.CANCELLED,
    },
    TaskNodeStatus.SUCCEEDED: set(),
    TaskNodeStatus.BLOCKED: set(),
    TaskNodeStatus.CANCELLED: set(),
}


@dataclass(slots=True)
class TaskNode:
    node_id: str
    title: str
    role: str
    acceptance: list[str]
    dependencies: set[str] = field(default_factory=set)
    conflict_domain: str = "readonly"
    risk: RiskLevel = RiskLevel.READ
    status: TaskNodeStatus = TaskNodeStatus.PENDING
    max_attempts: int = 2
    attempts: int = 0
    lease_id: str | None = None
    worker_id: str | None = None
    result_digest: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.node_id.strip() or not self.title.strip() or not self.role.strip():
            raise ValueError("node_id, title, and role are required")
        if not self.acceptance:
            raise ValueError("task node requires acceptance criteria")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.node_id in self.dependencies:
            raise ValueError("a task node cannot depend on itself")

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["dependencies"] = sorted(self.dependencies)
        data["risk"] = self.risk.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskNode:
        data = dict(payload)
        data["dependencies"] = set(data.get("dependencies", []))
        data["risk"] = RiskLevel(str(data.get("risk", RiskLevel.READ.value)))
        data["status"] = TaskNodeStatus(
            str(data.get("status", TaskNodeStatus.PENDING.value))
        )
        return cls(**data)


class TaskGraph:
    """Bounded DAG snapshot owned by the canonical WLS runtime."""

    def __init__(
        self,
        *,
        graph_id: str | None = None,
        intent_id: str,
        max_revisions: int = 3,
        created_at: str | None = None,
    ) -> None:
        if not intent_id.strip():
            raise ValueError("intent_id is required")
        if max_revisions < 0:
            raise ValueError("max_revisions must be >= 0")
        self.graph_id = graph_id or new_id("graph")
        self.intent_id = intent_id
        self.max_revisions = max_revisions
        self.revision_count = 0
        self.created_at = created_at or utc_now()
        self.updated_at = self.created_at
        self.nodes: dict[str, TaskNode] = {}

    def add_nodes(self, nodes: Iterable[TaskNode]) -> None:
        additions = list(nodes)
        previous = deepcopy(self.nodes)
        try:
            for node in additions:
                if node.node_id in self.nodes:
                    raise ValueError(f"duplicate node_id: {node.node_id}")
                self.nodes[node.node_id] = node
            self.validate()
        except Exception:
            self.nodes = previous
            raise
        self._refresh_ready()

    def validate(self) -> None:
        missing = {
            dependency
            for node in self.nodes.values()
            for dependency in node.dependencies
            if dependency not in self.nodes
        }
        if missing:
            raise ValueError(f"missing dependencies: {sorted(missing)}")
        indegree = {node_id: 0 for node_id in self.nodes}
        children: dict[str, set[str]] = {node_id: set() for node_id in self.nodes}
        for node in self.nodes.values():
            for dependency in node.dependencies:
                indegree[node.node_id] += 1
                children[dependency].add(node.node_id)
        queue = deque(sorted(key for key, degree in indegree.items() if degree == 0))
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for child in sorted(children[current]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if visited != len(self.nodes):
            raise ValueError("task graph contains a cycle")

    def ready_frontier(self) -> list[TaskNode]:
        self._refresh_ready()
        return [
            self.nodes[node_id]
            for node_id in sorted(self.nodes)
            if self.nodes[node_id].status is TaskNodeStatus.READY
        ]

    def transition(self, node_id: str, target: TaskNodeStatus) -> TaskNode:
        node = self._node(node_id)
        if target not in _ALLOWED[node.status]:
            raise ValueError(
                f"illegal transition {node.status.value} -> {target.value}"
            )
        node.status = target
        if target is TaskNodeStatus.LEASED:
            node.attempts += 1
            if node.attempts > node.max_attempts:
                node.status = TaskNodeStatus.BLOCKED
                node.error = "attempt budget exhausted"
        self.updated_at = utc_now()
        self._refresh_ready()
        return node

    def complete(self, node_id: str, result: dict[str, Any]) -> TaskNode:
        node = self._node(node_id)
        if node.status is not TaskNodeStatus.LEASED:
            raise ValueError("only LEASED nodes can complete")
        node.status = TaskNodeStatus.SUCCEEDED
        node.result_digest = digest_json(result)
        node.error = None
        node.lease_id = None
        self.updated_at = utc_now()
        self._refresh_ready()
        return node

    def fail(self, node_id: str, error: str) -> TaskNode:
        node = self._node(node_id)
        if node.status is not TaskNodeStatus.LEASED:
            raise ValueError("only LEASED nodes can fail")
        node.status = TaskNodeStatus.FAILED
        node.error = error
        node.lease_id = None
        self._block_dependents(node.node_id, "dependency did not succeed")
        if node.attempts >= node.max_attempts:
            node.status = TaskNodeStatus.BLOCKED
        self.updated_at = utc_now()
        self._refresh_ready()
        return node

    def snapshot(self) -> dict[str, Any]:
        return {
            "graph_id": self.graph_id,
            "intent_id": self.intent_id,
            "max_revisions": self.max_revisions,
            "revision_count": self.revision_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "nodes": {
                node_id: node.to_dict()
                for node_id, node in sorted(self.nodes.items())
            },
            "graph_digest": digest_json(
                {
                    "intent_id": self.intent_id,
                    "nodes": {
                        node_id: node.to_dict()
                        for node_id, node in sorted(self.nodes.items())
                    },
                }
            ),
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict[str, Any]) -> TaskGraph:
        graph = cls(
            graph_id=str(snapshot["graph_id"]),
            intent_id=str(snapshot["intent_id"]),
            max_revisions=int(snapshot.get("max_revisions", 3)),
            created_at=str(snapshot.get("created_at", utc_now())),
        )
        graph.revision_count = int(snapshot.get("revision_count", 0))
        graph.updated_at = str(snapshot.get("updated_at", graph.created_at))
        graph.nodes = {
            node_id: TaskNode.from_dict(payload)
            for node_id, payload in dict(snapshot.get("nodes", {})).items()
        }
        graph.validate()
        graph._refresh_ready()
        return graph

    def terminal(self) -> bool:
        return bool(self.nodes) and all(node.terminal for node in self.nodes.values())

    def _node(self, node_id: str) -> TaskNode:
        try:
            return self.nodes[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown task node: {node_id}") from exc

    def _refresh_ready(self) -> None:
        for node in self.nodes.values():
            if node.status not in {TaskNodeStatus.PENDING, TaskNodeStatus.READY}:
                continue
            dependencies = [self.nodes[dep].status for dep in node.dependencies]
            if any(status in _TERMINAL - {TaskNodeStatus.SUCCEEDED} for status in dependencies):
                node.status = TaskNodeStatus.BLOCKED
                node.error = "dependency did not succeed"
            elif all(status is TaskNodeStatus.SUCCEEDED for status in dependencies):
                node.status = TaskNodeStatus.READY
            else:
                node.status = TaskNodeStatus.PENDING

    def _block_dependents(self, node_id: str, reason: str) -> None:
        queue = deque([node_id])
        seen = {node_id}
        while queue:
            current = queue.popleft()
            for node in self.nodes.values():
                if current not in node.dependencies or node.node_id in seen:
                    continue
                if node.status not in {
                    TaskNodeStatus.SUCCEEDED,
                    TaskNodeStatus.CANCELLED,
                }:
                    node.status = TaskNodeStatus.BLOCKED
                    node.error = reason
                seen.add(node.node_id)
                queue.append(node.node_id)


class TaskGraphCompiler:
    """Compiles an admitted task into a small deterministic graph template."""

    def compile(self, intent: TaskIntent) -> TaskGraph:
        graph = TaskGraph(intent_id=intent.intent_id)
        graph.add_nodes(self._nodes_for(intent))
        return graph

    def _nodes_for(self, intent: TaskIntent) -> list[TaskNode]:
        acceptance = intent.acceptance
        if intent.operation is TaskOperation.ANSWER and intent.risk_floor is RiskLevel.READ:
            return [
                TaskNode(
                    "answer",
                    "Prepare bounded answer",
                    "executor",
                    acceptance,
                    conflict_domain="readonly",
                    risk=RiskLevel.READ,
                )
            ]
        if intent.domain is TaskDomain.RESEARCH:
            return [
                TaskNode(
                    "scope",
                    "Scope research question",
                    "planner",
                    ["research scope is explicit"],
                    conflict_domain="readonly",
                    risk=RiskLevel.READ,
                ),
                TaskNode(
                    "gather",
                    "Gather source evidence",
                    "researcher",
                    ["source evidence is recorded"],
                    dependencies={"scope"},
                    conflict_domain="readonly",
                    risk=RiskLevel.READ,
                ),
                TaskNode(
                    "synthesize",
                    "Synthesize sourced result",
                    "synthesizer",
                    acceptance,
                    dependencies={"gather"},
                    conflict_domain="readonly",
                    risk=RiskLevel.READ,
                ),
            ]
        return [
            TaskNode(
                "plan",
                "Plan bounded work",
                "planner",
                ["execution plan has acceptance and rollback"],
                conflict_domain="readonly",
                risk=RiskLevel.READ,
            ),
            TaskNode(
                "execute",
                "Execute bounded work through canonical policy",
                "executor",
                acceptance,
                dependencies={"plan"},
                conflict_domain=f"{intent.domain.value.lower()}:{intent.operation.value.lower()}",
                risk=intent.risk_floor,
            ),
            TaskNode(
                "verify",
                "Verify result and evidence",
                "reviewer",
                intent.evidence_required,
                dependencies={"execute"},
                conflict_domain="readonly",
                risk=RiskLevel.READ,
            ),
        ]
