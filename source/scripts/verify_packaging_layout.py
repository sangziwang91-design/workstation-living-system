from __future__ import annotations

from pathlib import Path
import json
import tomllib


def load_toml(path: Path) -> dict:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def main() -> int:
    repository = Path(__file__).resolve().parents[2]
    root_config = load_toml(repository / "pyproject.toml")
    source_config = load_toml(repository / "source" / "pyproject.toml")

    root_project = root_config["project"]
    source_project = source_config["project"]
    shared_fields = (
        "name",
        "version",
        "description",
        "requires-python",
        "dependencies",
        "keywords",
        "classifiers",
    )
    checks: dict[str, bool] = {
        f"metadata_{field}": root_project.get(field) == source_project.get(field)
        for field in shared_fields
    }
    checks.update(
        {
            "scripts_match": root_project.get("scripts") == source_project.get("scripts"),
            "dev_dependencies_match": root_project.get("optional-dependencies", {}).get("dev")
            == source_project.get("optional-dependencies", {}).get("dev"),
            "root_package_dir": root_config["tool"]["setuptools"]["package-dir"]
            == {"": "source/src"},
            "root_find_dir": root_config["tool"]["setuptools"]["packages"]["find"]["where"]
            == ["source/src"],
            "root_test_dir": root_config["tool"]["pytest"]["ini_options"]["testpaths"]
            == ["source/tests"],
            "source_package_dir": source_config["tool"]["setuptools"]["package-dir"]
            == {"": "src"},
            "source_find_dir": source_config["tool"]["setuptools"]["packages"]["find"]["where"]
            == ["src"],
            "canonical_cli_exists": (repository / "source" / "src" / "wls" / "cli.py").is_file(),
            "canonical_main_exists": (repository / "source" / "src" / "wls" / "__main__.py").is_file(),
            "duplicate_root_package_absent": not (repository / "src" / "wls").exists(),
        }
    )
    report = {
        "success": all(checks.values()),
        "canonical_package": "source/src/wls",
        "root_install_command": "python -m pip install -e .",
        "checks": checks,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
