from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
import hmac
import hashlib
import json

from .models import AnchorCase, canonical_json, digest_json


@dataclass(frozen=True, slots=True)
class AnchorBank:
    cases: tuple[AnchorCase, ...]
    bank_id: str
    split: str

    @classmethod
    def load_jsonl(cls, path: str | Path, bank_id: str, split: str) -> "AnchorBank":
        cases: list[AnchorCase] = []
        with Path(path).open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    cases.append(AnchorCase.from_dict(json.loads(stripped)))
                except Exception as exc:  # pragma: no cover - detailed failure path
                    raise ValueError(f"invalid anchor at {path}:{line_number}: {exc}") from exc
        return cls(cases=tuple(cases), bank_id=bank_id, split=split)

    @property
    def digest(self) -> str:
        return digest_json(
            {
                "bank_id": self.bank_id,
                "split": self.split,
                "cases": [case.to_dict() for case in self.cases],
            }
        )

    def ids(self) -> set[str]:
        return {case.anchor_id for case in self.cases}

    def assert_unique(self) -> None:
        ids = [case.anchor_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("anchor IDs must be unique")

    def assert_disjoint(self, *others: "AnchorBank") -> None:
        mine = self.ids()
        for other in others:
            overlap = mine & other.ids()
            if overlap:
                raise ValueError(f"anchor leakage across splits: {sorted(overlap)}")


@dataclass(frozen=True, slots=True)
class SealedAnchorManifest:
    bank_id: str
    split: str
    case_count: int
    bank_digest: str
    seal: str

    @classmethod
    def create(cls, bank: AnchorBank, secret: bytes) -> "SealedAnchorManifest":
        payload = {
            "bank_id": bank.bank_id,
            "split": bank.split,
            "case_count": len(bank.cases),
            "bank_digest": bank.digest,
        }
        seal = hmac.new(secret, canonical_json(payload).encode("utf-8"), hashlib.sha256).hexdigest()
        return cls(
            bank_id=bank.bank_id,
            split=bank.split,
            case_count=len(bank.cases),
            bank_digest=bank.digest,
            seal=seal,
        )

    def verify(self, secret: bytes) -> bool:
        payload = {
            "bank_id": self.bank_id,
            "split": self.split,
            "case_count": self.case_count,
            "bank_digest": self.bank_digest,
        }
        expected = hmac.new(
            secret, canonical_json(payload).encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(self.seal, expected)

    def to_dict(self) -> dict[str, object]:
        return {
            "bank_id": self.bank_id,
            "split": self.split,
            "case_count": self.case_count,
            "bank_digest": self.bank_digest,
            "seal": self.seal,
        }


def write_jsonl(path: str | Path, cases: Iterable[AnchorCase]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            handle.write(json.dumps(case.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
