"""Real offline model inference for public WLS task readiness cases.

The pinned local GGUF model sees a task description and available capabilities,
NEVER the expected labels. The grader is separate and never imports/executes
generated code. No tokens from paid APIs, no source promotion, no RSI claim.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.request import urlretrieve

from github_rsi_local_model import MODEL_ID, MODEL_URL, EXPECTED_MODEL_SHA256
from score_task_judgment import read_taskpack, to_input
from wls.task_judgment import JudgmentStatus, TaskJudgmentEngine

MODEL_SAMPLE_IDS = (
    "JT001", "JT006", "JT011", "JT018", "JT021", "JT027",
    "JT031", "JT036", "JT040", "JT042", "JT048", "JT050",
)
MAX_MODEL_BYTES = 500_000_000


def parse_reply(reply: str) -> str | None:
    """Allow only a known classification label, never model-stated grades."""
    raw = reply.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) >= 3 and lines[-1].strip() == "```":
            raw = "\n".join(lines[1:-1])
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or set(data) != {"status"}:
        return None
    status = data["status"]
    if not isinstance(status, str) or status not in {v.value for v in JudgmentStatus}:
        return None
    return status


def model_prompt(case: dict[str, Any]) -> str:
    inp = case["input"]
    # Expected labels, grader descriptions and oracle answers are omitted.
    view = {name: inp.get(name) for name in (
        "request", "tags", "verification_oracles", "required_capabilities",
        "available_capabilities", "required_evidence", "available_evidence",
        "owner_authorization_required", "side_effect_class", "acceptance_defined",
        "estimated_cost_usd", "max_cost_usd", "estimated_wall_seconds",
        "max_wall_seconds",
    ) if name in inp}
    choices = ", ".join(item.value for item in JudgmentStatus)
    return (
        "You classify whether an assistant can start planning a task, not run it. "
        "Use ONLY the supplied task evidence. Read-only may be planned; "
        "external/irreversible operations need owner approval; missing tools "
        "mean capability missing; missing evidence means evidence missing; "
        "no test/verification oracle or ambiguous task means underspecified; "
        "over cost/time budget means budget exceeded. "
        "Return ONLY JSON with one key status and one of: " + choices
        + ".\nTask: " + json.dumps(view, ensure_ascii=False, sort_keys=True)
    )


def verify_model(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise ValueError("model not downloaded or file is a link")
    size = path.stat().st_size
    if not 300_000_000 <= size <= MAX_MODEL_BYTES:
        raise ValueError("unexpected model size")
    digest = sha256()
    with path.open("rb") as fp:
        for part in iter(lambda: fp.read(1_048_576), b""):
            digest.update(part)
    actual = digest.hexdigest()
    if actual != EXPECTED_MODEL_SHA256:
        raise ValueError("downloaded model sha256 differs from frozen identity")
    return actual


def run(dataset: Path, model_file: Path) -> dict[str, Any]:
    contents = read_taskpack(dataset)
    cases = {x["input"]["task_id"]: x for x in contents["cases"]}
    if not set(MODEL_SAMPLE_IDS) <= set(cases):
        raise ValueError("public sample missing a pinned case")
    model_sha = verify_model(model_file)
    from llama_cpp import Llama
    model = Llama(model_path=str(model_file), n_ctx=2048, n_threads=2, verbose=False)
    rows = []
    for task_id in MODEL_SAMPLE_IDS:
        case = cases[task_id]
        prompt = model_prompt(case)
        response = model.create_chat_completion(
            messages=[
                {"role": "system", "content": "Return only a compact JSON object. No tools."},
                {"role": "user", "content": prompt},
            ],
            temperature=0, max_tokens=90,
        )
        content = str(response["choices"][0]["message"]["content"] or "")
        model_status = parse_reply(content)
        expected = case["expected"]["status"]
        deterministic = TaskJudgmentEngine().judge(to_input(case["input"]))
        rows.append({
            "id": task_id, "expected_status": expected,
            "model_status": model_status or "INVALID_RESPONSE",
            "model_correct": model_status == expected,
            "rule_status": deterministic.status.value,
            "rule_correct": deterministic.status.value == expected,
            "response_digest": sha256(content.encode("utf-8")).hexdigest(),
            "prompt_digest": sha256(prompt.encode("utf-8")).hexdigest(),
        })
    return {
        "schema": "wls.task_judgment.local_gguf_public.v1",
        "status": "MODEL_OBSERVED",
        "model_id": MODEL_ID, "model_sha256": model_sha,
        "model_calls_observed": len(rows),
        "model_successes": sum(int(r["model_correct"]) for r in rows),
        "rule_successes": sum(int(r["rule_correct"]) for r in rows),
        "invalid_model_outputs": sum(int(r["model_status"] == "INVALID_RESPONSE") for r in rows),
        "per_case": rows,
        "model_gain": "NOT_ESTABLISHED_BY_PUBLIC_TASKS",
        "heldout_gain": "UNMEASURED", "recursive_improvement": "UNPROVEN",
        "claim_ceiling": "REAL_LOCAL_MODEL_PUBLIC_TASK_JUDGMENT_NOT_SEALED_TRANSFER",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset", type=Path, required=True)
    p.add_argument("--model-file", type=Path, required=True)
    p.add_argument("--report", type=Path, required=True)
    args = p.parse_args()
    try:
        if not args.model_file.is_file():
            if not os.environ.get("GITHUB_ACTIONS"):
                raise ValueError("model auto-download only permitted in GitHub Actions")
            urlretrieve(MODEL_URL, args.model_file)
        output = run(args.dataset, args.model_file)
    except (OSError, ValueError, KeyError, RuntimeError, TypeError) as exc:
        output = {
            "schema": "wls.task_judgment.local_gguf_public.v1",
            "status": "MODEL_UNAVAILABLE", "error_type": type(exc).__name__,
            "heldout_gain": "UNMEASURED", "recursive_improvement": "UNPROVEN",
        }
    args.report.write_text(json.dumps(output, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({k: v for k, v in output.items() if k != "per_case"}, sort_keys=True))
    return 0 if output["status"] == "MODEL_OBSERVED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
