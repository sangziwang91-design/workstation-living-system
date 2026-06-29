from __future__ import annotations

from pathlib import Path
import argparse
import json
import tempfile

from wls.examiner import AnchorBank, CandidateChange, Constitution, ExaminerStore, ExaminerSystem, ExaminerVersion, StandaloneDatabase, current_implementation_digest
from wls.examiner.fixtures import RULE_SEQUENCE


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate one WLS change candidate in shadow mode")
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--constitution", type=Path)
    parser.add_argument("--anchors", type=Path)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[2]
    constitution_path = args.constitution or root / ".evolution/examiner/constitution.json"
    anchors_path = args.anchors or root / ".evolution/examiner/anchors/public_anchors.jsonl"
    constitution = Constitution.load(str(constitution_path))
    anchors = AnchorBank.load_jsonl(anchors_path, "WLS-EXAMINER-PUBLIC-ANCHORS-V1", "public")
    candidate = CandidateChange.from_dict(json.loads(args.candidate.read_text(encoding="utf-8")))

    with tempfile.TemporaryDirectory(prefix="wls-examiner-cli-") as directory:
        store = ExaminerStore(StandaloneDatabase(Path(directory) / "examiner.db"))
        version = ExaminerVersion(
            version_id="wls-examiner-v1-shadow",
            epoch_id="EXAM-EPOCH-0001",
            constitution_digest=constitution.digest,
            anchor_manifest_digest=anchors.digest,
            implementation_digest=current_implementation_digest(),
            enabled_rules=RULE_SEQUENCE,
            created_at="2026-06-28T15:32:06+08:00",
        )
        system = ExaminerSystem(constitution, store, version)
        result = system.evaluate(candidate)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False, sort_keys=True))
        return 0 if result.verdict.value == "ALLOW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
