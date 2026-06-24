from __future__ import annotations

from pathlib import Path


path = Path(__file__).resolve().parents[1] / "source/src/wls/memory_ablation.py"
text = path.read_text(encoding="utf-8")
replacements = [
    (
        "from typing import Any\n",
        "from typing import Any, TypedDict\n",
    ),
    (
        '''ADVANTAGE_MEMORY_ID = "mem_et003_advantage"
REFUTED_MEMORY_ID = "mem_et003_refutation"


''',
        '''ADVANTAGE_MEMORY_ID = "mem_et003_advantage"
REFUTED_MEMORY_ID = "mem_et003_refutation"


class _AblationTask(TypedDict):
    task_id: str
    project_id: str
    entity_id: str
    failure_signature: str
    task_kind: str
    expected_behavior: str
    path_enabled: Path
    path_disabled: Path


class _RefutationTask(TypedDict):
    path: Path
    project_id: str
    entity_id: str
    failure_signature: str
    task_kind: str
    expected_behavior: str


''',
    ),
    ("    tasks = [\n", "    tasks: list[_AblationTask] = [\n"),
    ("    common = {\n", "    common: _RefutationTask = {\n"),
]
for old, new in replacements:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one replacement, found {count}: {old[:60]!r}")
    text = text.replace(old, new)
path.write_text(text, encoding="utf-8")
