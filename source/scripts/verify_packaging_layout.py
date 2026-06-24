from __future__ import annotations

from pathlib import Path
import ast
import json
import re
import tomllib


IGNORED_PARTS = {".git", ".venv", "venv", "build", "dist", "__pycache__"}
CANONICAL_PACKAGE = Path("source/src/wls")
CANONICAL_VERSION_FILE = CANONICAL_PACKAGE / "_version.py"
CORE_CONCURRENT_WORKFLOWS = {
    "ci.yml",
    "packaging-layout.yml",
    "evolution-regressions.yml",
    "evolution-chain.yml",
}
RETIRED_DUPLICATE_WORKFLOWS = {
    "apply-evolution-target-001.yml",
    "build-evolution-target-002.yml",
    "verify-evolution-target-003.yml",
}
FULL_SUITE_MARKER = "python -m pytest source/tests -q"
QUALITY_STACK_MARKERS = (
    FULL_SUITE_MARKER,
    "python -m ruff check source/src source/tests source/scripts",
    "python -m mypy source/src/wls source/tests source/scripts",
    "python -m bandit -q -r source/src/wls source/scripts",
    "python -m build .",
)


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def relative_files(repository: Path, name: str) -> list[str]:
    results: list[str] = []
    for path in repository.rglob(name):
        relative = path.relative_to(repository)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        results.append(relative.as_posix())
    return sorted(results)


def canonical_version(repository: Path) -> str:
    path = repository / CANONICAL_VERSION_FILE
    module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in module.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            continue
        value = ast.literal_eval(node.value)
        if isinstance(value, str) and value:
            return value
    raise ValueError("canonical __version__ assignment was not found")


def yaml_development_version(text: str) -> str | None:
    match = re.search(
        r'^\s*development_version:\s*["\']?([^"\'\s]+)',
        text,
        re.MULTILINE,
    )
    return match.group(1) if match else None


def package_roots(repository: Path) -> list[str]:
    roots: list[str] = []
    for init_file in repository.rglob("wls/__init__.py"):
        relative = init_file.parent.relative_to(repository)
        if any(part in IGNORED_PARTS or part == "legacy" for part in relative.parts):
            continue
        roots.append(relative.as_posix())
    return sorted(roots)


def workflow_map(repository: Path) -> dict[str, str]:
    workflow_root = repository / ".github" / "workflows"
    if not workflow_root.exists():
        return {}
    paths = sorted(workflow_root.glob("*.yml")) + sorted(workflow_root.glob("*.yaml"))
    return {path.name: path.read_text(encoding="utf-8") for path in paths}


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    config = load_toml(repository / "pyproject.toml")
    project = config["project"]
    version = canonical_version(repository)
    readme = (repository / "README.md").read_text(encoding="utf-8")
    current_state = (repository / "CURRENT_STATE.yaml").read_text(encoding="utf-8")
    current_chain = json.loads(
        (repository / ".evolution" / "CURRENT_CHAIN.json").read_text(encoding="utf-8")
    )
    workflow_files = workflow_map(repository)
    workflows = "\n".join(workflow_files.values())

    project_manifests = relative_files(repository, "pyproject.toml")
    setup_py = relative_files(repository, "setup.py")
    setup_cfg = relative_files(repository, "setup.cfg")
    full_suite_owners = sorted(
        name for name, text in workflow_files.items() if FULL_SUITE_MARKER in text
    )
    quality_stack_owners = sorted(
        name
        for name, text in workflow_files.items()
        if all(marker in text for marker in QUALITY_STACK_MARKERS)
    )

    checks: dict[str, bool] = {
        "single_project_manifest": project_manifests == ["pyproject.toml"],
        "no_setup_py": setup_py == [],
        "no_setup_cfg": setup_cfg == [],
        "single_package_root": package_roots(repository) == [CANONICAL_PACKAGE.as_posix()],
        "canonical_cli_exists": (repository / CANONICAL_PACKAGE / "cli.py").is_file(),
        "canonical_main_exists": (repository / CANONICAL_PACKAGE / "__main__.py").is_file(),
        "root_package_dir": config["tool"]["setuptools"]["package-dir"]
        == {"": "source/src"},
        "root_find_dir": config["tool"]["setuptools"]["packages"]["find"]["where"]
        == ["source/src"],
        "root_test_dir": config["tool"]["pytest"]["ini_options"]["testpaths"]
        == ["source/tests"],
        "version_is_dynamic": project.get("dynamic") == ["version"]
        and "version" not in project,
        "version_attr_is_canonical": config["tool"]["setuptools"]["dynamic"]["version"]
        == {"attr": "wls._version.__version__"},
        "readme_version_matches": f"`{version}`" in readme,
        "current_state_version_matches": yaml_development_version(current_state) == version,
        "current_chain_version_matches": current_chain["canonical_baseline"]["development_version"]
        == version,
        "duplicate_source_metadata_absent": all(
            not (repository / path).exists()
            for path in ("source/pyproject.toml", "source/README.md", "source/LICENSE")
        ),
        "legacy_installer_not_at_root": all(
            not (repository / path).exists()
            for path in (
                "INSTALL.ps1",
                "START.ps1",
                "STOP.ps1",
                "VERIFY.ps1",
                "UNINSTALL.ps1",
                "VERSION.txt",
                "WHEEL_SHA256.txt",
                "SHA256SUMS.txt",
                "dist/workstation_living_system-1.0.0-py3-none-any.whl",
            )
        ),
        "retired_bootstrap_workflow_absent": not (
            repository / ".github/workflows/bootstrap-source.yml"
        ).exists(),
        "retired_metadata_mutator_absent": not (
            repository / ".github/workflows/activate-extended-entrypoint.yml"
        ).exists(),
        "retired_duplicate_evolution_workflows_absent": not (
            RETIRED_DUPLICATE_WORKFLOWS & workflow_files.keys()
        ),
        "consolidated_evolution_workflow_present": "evolution-regressions.yml"
        in workflow_files,
        "single_full_regression_owner": full_suite_owners == ["ci.yml"],
        "single_quality_stack_owner": quality_stack_owners == ["ci.yml"],
        "core_workflows_cancel_superseded_runs": all(
            name in workflow_files
            and "concurrency:" in workflow_files[name]
            and "cancel-in-progress: true" in workflow_files[name]
            for name in CORE_CONCURRENT_WORKFLOWS
        ),
        "no_direct_main_push_workflow": "git push origin HEAD:main" not in workflows
        and "git push origin main" not in workflows,
        "no_source_project_install": 'pip install -e "source' not in workflows
        and "pip install -e source" not in workflows,
        "no_source_project_build": "python -m build source" not in workflows,
        "no_source_working_directory": "working-directory: source" not in workflows,
    }

    report = {
        "success": all(checks.values()),
        "canonical_project_root": ".",
        "canonical_package": CANONICAL_PACKAGE.as_posix(),
        "canonical_version_file": CANONICAL_VERSION_FILE.as_posix(),
        "canonical_version": version,
        "project_manifests": project_manifests,
        "package_roots": package_roots(repository),
        "full_suite_owners": full_suite_owners,
        "quality_stack_owners": quality_stack_owners,
        "checks": checks,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
