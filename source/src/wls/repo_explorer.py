from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import digest_json, new_id, utc_now


@dataclass(slots=True)
class RepoFile:
    path: str
    kind: str
    symbols: list[str]
    dependencies: list[str]
    size_bytes: int
    digest: str = ""


@dataclass(slots=True)
class RepoMap:
    map_id: str
    root: str
    files: list[RepoFile] = field(default_factory=list)
    instruction_files: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)

    def find_file(self, name: str) -> RepoFile | None:
        for f in self.files:
            if f.path.endswith(name):
                return f
        return None

    def find_symbol(self, name: str) -> list[RepoFile]:
        return [f for f in self.files if any(name in s for s in f.symbols)]

    def upstream_of(self, path: str) -> list[str]:
        result: list[str] = []
        target = self.find_file(path)
        if target:
            for f in self.files:
                if f.path in target.dependencies:
                    result.append(f.path)
        return result

    def downstream_of(self, path: str) -> list[str]:
        result: list[str] = []
        for f in self.files:
            if path in f.dependencies:
                result.append(f.path)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "root": self.root,
            "file_count": len(self.files),
            "instruction_files": self.instruction_files,
            "files": [
                {
                    "path": f.path,
                    "kind": f.kind,
                    "symbols": f.symbols,
                    "dependencies": f.dependencies,
                    "size_bytes": f.size_bytes,
                    "digest": f.digest,
                }
                for f in self.files
            ],
            "created_at": self.created_at,
        }


class RepoExplorer:
    """Repository exploration organ. Builds a file/symbol/dependency map
    from a local source tree without requiring AST/LSP tooling. Supports
    instruction discovery and on-demand nested context.
    """

    def explore(self, root: str | Path, *, extensions: list[str] | None = None) -> RepoMap:
        root_path = Path(root).resolve()
        ext_set = set(extensions) if extensions else {".py", ".js", ".ts", ".json", ".yaml", ".yml", ".md", ".txt", ".ps1", ".toml", ".cfg"}
        instruction_names = {"AGENTS.md", "CLAUDE.md", "CODEX.md", "README.md", "CONTRIBUTING.md"}
        repo_map = RepoMap(map_id=new_id("repomap"), root=str(root_path))

        for file_path in root_path.rglob("*"):
            if file_path.is_file() and file_path.suffix in ext_set:
                if self._should_skip(file_path):
                    continue
                rel = str(file_path.relative_to(root_path))
                symbols, deps = self._extract_symbols_and_deps(file_path)
                try:
                    size = file_path.stat().st_size
                except OSError:
                    size = 0
                rf = RepoFile(
                    path=rel,
                    kind=file_path.suffix.lstrip("."),
                    symbols=symbols,
                    dependencies=deps,
                    size_bytes=size,
                    digest=digest_json({"path": rel, "size": size}),
                )
                repo_map.files.append(rf)
                if file_path.name in instruction_names:
                    repo_map.instruction_files.append(rel)

        return repo_map

    def nested_context(
        self,
        repo_map: RepoMap,
        target_path: str,
        *,
        depth: int = 2,
        max_files: int = 10,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = {target_path}
        queue: list[str] = [target_path]
        while queue and len(result) < max_files and depth > 0:
            next_queue: list[str] = []
            for path in queue:
                upstream = repo_map.upstream_of(path)
                downstream = repo_map.downstream_of(path)
                for p in upstream + downstream:
                    if p not in seen:
                        seen.add(p)
                        f = repo_map.find_file(p)
                        if f:
                            result.append({"path": p, "kind": f.kind, "symbols": f.symbols})
                            if len(result) >= max_files:
                                break
                        next_queue.append(p)
            queue = next_queue
            depth -= 1
        return result

    def _should_skip(self, path: Path) -> bool:
        skip_dirs = {"__pycache__", ".git", ".venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist", "build"}
        for part in path.parts:
            if part in skip_dirs:
                return True
        return False

    def _extract_symbols_and_deps(self, path: Path) -> tuple[list[str], list[str]]:
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return [], []
        symbols: list[str] = []
        deps: list[str] = []
        import re

        if path.suffix == ".py":
            for match in re.finditer(r"^(?:def|class|async def)\s+(\w+)", content, re.MULTILINE):
                symbols.append(match.group(1))
            for match in re.finditer(r"^(?:from|import)\s+([\w.]+)", content, re.MULTILINE):
                mod = match.group(1)
                if "." in mod and not mod.startswith("."):
                    pkg = mod.split(".")[0]
                    deps.append(pkg)
        elif path.suffix in (".js", ".ts"):
            for match in re.finditer(r"(?:function|class|const)\s+(\w+)", content):
                symbols.append(match.group(1))
            for match in re.finditer(r"""from\s+['"]([^'"]+)['"]""", content):
                deps.append(match.group(1))
            for match in re.finditer(r"""require\s*\(\s*['"]([^'"]+)['"]""", content):
                deps.append(match.group(1))
        elif path.suffix in (".json",):
            deps = []

        return list(set(symbols)), list(set(deps))
