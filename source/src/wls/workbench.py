from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import utc_now, new_id


@dataclass(slots=True)
class WorkbenchTemplate:
    template_id: str
    name: str
    description: str
    allowed_tools: list[str]
    required_files: list[str]
    success_criteria: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)


class Workbench:
    """Task-specific environment configuration and validation."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.templates: dict[str, WorkbenchTemplate] = {}
        self._register_defaults()

    def _register_defaults(self) -> None:
        self.register(WorkbenchTemplate(
            template_id="coding_v1",
            name="General Coding",
            description="Standard coding task with tests and linting",
            allowed_tools=["read_file", "write_file", "list_directory", "run_command"],
            required_files=["pyproject.toml"],
            success_criteria=["tests_pass", "lint_pass"]
        ))
        self.register(WorkbenchTemplate(
            template_id="research_v1",
            name="Information Research",
            description="Web search and document analysis",
            allowed_tools=["http_get", "read_file", "emit_note"],
            required_files=[],
            success_criteria=["summary_produced", "sources_cited"]
        ))

    def register(self, template: WorkbenchTemplate) -> None:
        self.templates[template.template_id] = template

    def get_template(self, template_id: str) -> WorkbenchTemplate:
        if template_id not in self.templates:
            raise KeyError(f"unknown workbench template: {template_id}")
        return self.templates[template_id]

    def validate_setup(self, template_id: str, worktree_path: str | Path) -> dict[str, Any]:
        template = self.get_template(template_id)
        path = Path(worktree_path).resolve()
        
        missing_files = []
        for rel_path in template.required_files:
            if not (path / rel_path).exists():
                missing_files.append(rel_path)
                
        return {
            "template_id": template_id,
            "worktree_path": str(path),
            "valid": len(missing_files) == 0,
            "missing_files": missing_files,
            "timestamp": utc_now()
        }

