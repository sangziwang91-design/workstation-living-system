from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .schemas import new_id


class CodingWorktree:
    """Isolated Git worktree for candidate implementation and testing."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.worktrees_dir = self.root / ".evolution" / "worktrees"
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)

    def create(self, base_ref: str = "HEAD") -> dict[str, Any]:
        worktree_id = new_id("wt")
        path = self.worktrees_dir / worktree_id
        
        # Use git worktree add to create an isolated directory
        # We use --detach to avoid creating a branch for every worktree
        subprocess.run(
            ["git", "-C", str(self.root), "worktree", "add", "--detach", str(path), base_ref],
            check=True,
            capture_output=True,
            text=True
        )
        
        return {
            "worktree_id": worktree_id,
            "path": str(path),
            "base_ref": base_ref
        }

    def remove(self, worktree_id: str) -> None:
        path = self.worktrees_dir / worktree_id
        if not path.exists():
            return

        subprocess.run(
            ["git", "-C", str(self.root), "worktree", "remove", "--force", str(path)],
            check=True,
            capture_output=True,
            text=True
        )

    def list_worktrees(self) -> list[dict[str, Any]]:
        result = subprocess.run(
            ["git", "-C", str(self.root), "worktree", "list", "--porcelain"],
            check=True,
            capture_output=True,
            text=True
        )
        
        worktrees = []
        current: dict[str, Any] = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("HEAD "):
                current["head"] = line[5:]
            elif line.startswith("branch "):
                current["branch"] = line[7:]
        if current:
            worktrees.append(current)
            
        return [w for w in worktrees if str(self.worktrees_dir) in w["path"]]

