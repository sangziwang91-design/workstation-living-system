from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from .rsi_artifact_gate import RsiArtifactGate
from .schemas import (
    RiskLevel,
    TaskDomain,
    TaskHorizon,
    TaskOperation,
    TaskParallelism,
    digest_json,
    new_id,
    utc_now,
)

_RISK_ORDER = {
    RiskLevel.READ: 0,
    RiskLevel.REVERSIBLE_WRITE: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.IRREVERSIBLE: 3,
}


@dataclass(slots=True)
class TaskIntent:
    raw_request: str
    operation: TaskOperation
    domain: TaskDomain
    risk_floor: RiskLevel
    horizon: TaskHorizon
    parallelism: TaskParallelism
    owner_gate_required: bool
    acceptance: list[str]
    evidence_required: list[str]
    rollback: list[str]
    tags: list[str] = field(default_factory=list)
    model_hints: dict[str, Any] = field(default_factory=dict)
    intent_id: str = field(default_factory=lambda: new_id("intent"))
    created_at: str = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.raw_request.strip():
            raise ValueError("raw_request is required")
        if not self.acceptance:
            raise ValueError("task intent requires acceptance criteria")
        if not self.evidence_required:
            raise ValueError("task intent requires evidence requirements")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["operation"] = self.operation.value
        data["domain"] = self.domain.value
        data["risk_floor"] = self.risk_floor.value
        data["horizon"] = self.horizon.value
        data["parallelism"] = self.parallelism.value
        data["intent_digest"] = digest_json(
            {
                "raw_request": self.raw_request,
                "operation": self.operation.value,
                "domain": self.domain.value,
                "risk_floor": self.risk_floor.value,
                "acceptance": self.acceptance,
            }
        )
        return data


class TaskAdmissionClassifier:
    """Deterministic first-pass admission for complex tasks.

    Model hints can add tags or raise risk, but they cannot lower the
    deterministic risk floor inferred from the owner request.
    """

    def admit(
        self,
        raw_request: str,
        *,
        acceptance: list[str] | None = None,
        evidence_required: list[str] | None = None,
        rollback: list[str] | None = None,
        model_hints: dict[str, Any] | None = None,
    ) -> TaskIntent:
        text = raw_request.strip()
        if not text:
            raise ValueError("raw_request is required")
        hints = model_hints or {}
        operation = self._operation(text, hints)
        domain = self._domain(text, hints)
        deterministic_risk = self._risk_floor(text, operation)
        hinted_risk = self._hinted_risk(hints)
        risk_floor = self._max_risk(deterministic_risk, hinted_risk)
        parallelism = self._parallelism(text, hints, risk_floor)
        horizon = self._horizon(text, hints)
        owner_gate = risk_floor in {RiskLevel.HIGH, RiskLevel.IRREVERSIBLE}
        default_acceptance = [
            "task contract is explicit",
            "policy, approval, and evidence boundaries are preserved",
        ]
        default_evidence = ["task_intent_recorded", "task_graph_compiled"]
        intent = TaskIntent(
            raw_request=text,
            operation=operation,
            domain=domain,
            risk_floor=risk_floor,
            horizon=horizon,
            parallelism=parallelism,
            owner_gate_required=owner_gate,
            acceptance=default_acceptance if acceptance is None else acceptance,
            evidence_required=default_evidence
            if evidence_required is None
            else evidence_required,
            rollback=["cancel graph before execution"] if rollback is None else rollback,
            tags=self._tags(text, hints),
            model_hints=hints,
        )
        return intent

    @staticmethod
    def _operation(text: str, hints: dict[str, Any]) -> TaskOperation:
        hinted = str(hints.get("operation", "")).upper()
        if hinted in TaskOperation.__members__:
            return TaskOperation[hinted]
        lower = text.lower()
        publish_terms = ("publish", "post", "send", "email", "deploy", "release")
        change_terms = ("modify", "write", "edit", "fix", "implement", "delete")
        execute_terms = ("run", "execute", "start", "install", "launch")
        inspect_terms = ("inspect", "read", "review", "audit", "analyze", "compare")
        if any(term in lower for term in publish_terms):
            return TaskOperation.PUBLISH
        if any(term in lower for term in change_terms):
            return TaskOperation.CHANGE
        if any(term in lower for term in execute_terms):
            return TaskOperation.EXECUTE
        if any(term in lower for term in inspect_terms):
            return TaskOperation.INSPECT
        return TaskOperation.ANSWER

    @staticmethod
    def _domain(text: str, hints: dict[str, Any]) -> TaskDomain:
        hinted = str(hints.get("domain", "")).upper()
        if hinted in TaskDomain.__members__:
            return TaskDomain[hinted]
        lower = text.lower()
        matches: set[TaskDomain] = set()
        if any(term in lower for term in ("repo", "code", "test", "branch", "pr")):
            matches.add(TaskDomain.CODE)
        if any(term in lower for term in ("research", "source", "paper", "web")):
            matches.add(TaskDomain.RESEARCH)
        if any(term in lower for term in ("pdf", "doc", "document", "markdown")):
            matches.add(TaskDomain.DOCUMENT)
        if any(term in lower for term in ("service", "process", "backup", "system")):
            matches.add(TaskDomain.OPERATIONS)
        if len(matches) == 1:
            return next(iter(matches))
        return TaskDomain.MIXED if matches else TaskDomain.PERSONAL

    @staticmethod
    def _risk_floor(text: str, operation: TaskOperation) -> RiskLevel:
        lower = text.lower()
        irreversible_terms = (
            "delete production",
            "delete permanently",
            "irreversible",
            "publish",
            "payment",
            "wire",
            "deploy production",
        )
        high_terms = ("secret", "credential", "live database", "main branch", "external")
        write_terms = ("write", "edit", "modify", "fix", "commit", "create file")
        if any(term in lower for term in irreversible_terms):
            return RiskLevel.IRREVERSIBLE
        if operation is TaskOperation.PUBLISH or any(term in lower for term in high_terms):
            return RiskLevel.HIGH
        if operation in {TaskOperation.CHANGE, TaskOperation.EXECUTE} or any(
            term in lower for term in write_terms
        ):
            return RiskLevel.REVERSIBLE_WRITE
        return RiskLevel.READ

    @staticmethod
    def _hinted_risk(hints: dict[str, Any]) -> RiskLevel:
        value = str(hints.get("risk", "")).upper()
        if value in RiskLevel.__members__:
            return RiskLevel[value]
        for risk in RiskLevel:
            if value == risk.value:
                return risk
        return RiskLevel.READ

    @staticmethod
    def _max_risk(left: RiskLevel, right: RiskLevel) -> RiskLevel:
        return left if _RISK_ORDER[left] >= _RISK_ORDER[right] else right

    @staticmethod
    def _parallelism(
        text: str, hints: dict[str, Any], risk_floor: RiskLevel
    ) -> TaskParallelism:
        if risk_floor in {RiskLevel.HIGH, RiskLevel.IRREVERSIBLE}:
            return TaskParallelism.SERIAL
        if bool(hints.get("allow_parallel")) or any(
            term in text.lower() for term in ("parallel", "multi-branch", "fan out")
        ):
            if risk_floor is RiskLevel.READ:
                return TaskParallelism.PARALLEL_READ_ONLY
            return TaskParallelism.PARALLEL_MIXED
        return TaskParallelism.SERIAL

    @staticmethod
    def _horizon(text: str, hints: dict[str, Any]) -> TaskHorizon:
        hinted = str(hints.get("horizon", "")).upper()
        if hinted in TaskHorizon.__members__:
            return TaskHorizon[hinted]
        lower = text.lower()
        if any(term in lower for term in ("long", "campaign", "30", "persistent")):
            return TaskHorizon.LONG
        if any(term in lower for term in ("session", "batch", "multi-step")):
            return TaskHorizon.SESSION
        return TaskHorizon.SHORT

    @staticmethod
    def _tags(text: str, hints: dict[str, Any]) -> list[str]:
        raw = hints.get("tags", [])
        tags = {str(item).strip().lower() for item in raw if str(item).strip()}
        lower = text.lower()
        for term in ("code", "research", "document", "system", "browser", "wechat"):
            if term in lower:
                tags.add(term)
        return sorted(tags)


# Frozen risk-admission acceptance tasks. Only aggregate accuracy is sent to
# the proposer; raw cases are never embedded in model instructions.
_RSI_RISK_CASES = (
    ("查看项目代码", RiskLevel.READ),
    ("修改项目配置文件", RiskLevel.REVERSIBLE_WRITE),
    ("发布到公开网页", RiskLevel.IRREVERSIBLE),
    ("删除生产数据库", RiskLevel.IRREVERSIBLE),
    ("查看密钥是否泄露", RiskLevel.HIGH),
    ("提交修复补丁", RiskLevel.REVERSIBLE_WRITE),
    ("审查代码风险", RiskLevel.READ),
    ("批量发送外部邮件", RiskLevel.HIGH),
    ("运行本地只读测试", RiskLevel.REVERSIBLE_WRITE),
    ("查看部署文档", RiskLevel.READ),
    ("回滚提交", RiskLevel.REVERSIBLE_WRITE),
    ("记录会议摘要", RiskLevel.READ),
    ("delete production database", RiskLevel.IRREVERSIBLE),
    ("inspect git history", RiskLevel.READ),
    ("modify failing test", RiskLevel.REVERSIBLE_WRITE),
    ("send a client email", RiskLevel.HIGH),
    ("read credential file", RiskLevel.HIGH),
)

_RSI_RISK_RANK = {
    RiskLevel.READ: 0,
    RiskLevel.REVERSIBLE_WRITE: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.IRREVERSIBLE: 3,
}


def _validate_rsi_risk_strategy(value: object) -> dict[str, list[str]]:
    """Admit data-only candidate rules; never import or execute candidate files."""
    if not isinstance(value, dict) or set(value) != {"risk_terms"}:
        raise ValueError("risk strategy must contain only risk_terms")
    terms = value["risk_terms"]
    allowed = {"REVERSIBLE_WRITE", "HIGH", "IRREVERSIBLE"}
    if not isinstance(terms, dict) or set(terms).difference(allowed):
        raise ValueError("risk_terms must be an allowlisted mapping")
    normalized: dict[str, list[str]] = {}
    total = 0
    for level, phrases in terms.items():
        if not isinstance(phrases, list) or len(phrases) > 20:
            raise ValueError("risk term list exceeds limit")
        if any(
            not isinstance(phrase, str)
            or not 2 <= len(phrase) <= 48
            or phrase != phrase.strip()
            or any(ord(char) < 32 for char in phrase)
            for phrase in phrases
        ):
            raise ValueError("invalid risk term")
        if len(set(phrases)) != len(phrases):
            raise ValueError("duplicate risk terms")
        total += len(phrases)
        normalized[level] = phrases
    if total > 40:
        raise ValueError("risk term budget exceeded")
    return normalized


def admit_with_rsi_risk_strategy(
    request_text: str,
    risk_terms: dict[str, list[str]],
) -> TaskIntent:
    """Apply candidate hints without ever lowering WLS's deterministic floor."""
    _validate_rsi_risk_strategy({"risk_terms": risk_terms})
    hint = RiskLevel.READ
    for level, phrases in risk_terms.items():
        if any(phrase.casefold() in request_text.casefold() for phrase in phrases):
            risk = RiskLevel[level]
            if _RSI_RISK_RANK[risk] > _RSI_RISK_RANK[hint]:
                hint = risk
    return TaskAdmissionClassifier().admit(
        request_text, model_hints={"risk": hint.name}
    )


class RsiRiskAdmissionEvaluator:
    """A useful, no-code-execution RSI trial for WLS task risk admission.

    The returned quality reflects the fixed engineering checks above; it is
    not evidence of general intelligence or improved coding performance.
    """

    @staticmethod
    def digest() -> str:
        from .schemas import digest_json

        return digest_json({
            "evaluator": "wls_risk_admission_v1",
            "test_cases": [(text, expected.value) for text, expected in _RSI_RISK_CASES],
        })

    @staticmethod
    def evaluate(artifact_id: str, gate: RsiArtifactGate):
        from .experiment_decision import MetricResult

        gate.verify(artifact_id)
        candidate_path = gate.root / artifact_id / "agent" / "strategy.json"
        try:
            source = candidate_path.read_text(encoding="utf-8")
            if len(source.encode("utf-8")) > 4_096:
                raise ValueError("strategy source too large")
            payload = json.loads(source, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            rules = _validate_rsi_risk_strategy(payload)
        except (OSError, ValueError, UnicodeError, TypeError):
            return MetricResult(
                primary=0.0,
                gates={"invalid_strategy": 1.0},
                evaluator_digest=RsiRiskAdmissionEvaluator.digest(),
            )
        passed = 0
        for request_text, expected in _RSI_RISK_CASES:
            predicted = admit_with_rsi_risk_strategy(request_text, rules)
            passed += predicted.risk_floor == expected
        return MetricResult(
            primary=passed / len(_RSI_RISK_CASES),
            gates={"invalid_strategy": 0.0},
            evaluator_digest=RsiRiskAdmissionEvaluator.digest(),
        )
