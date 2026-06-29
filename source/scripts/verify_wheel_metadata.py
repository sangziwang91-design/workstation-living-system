from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json
import re
import sys
import zipfile


_VERSION_RE = re.compile(r'^__version__\s*=\s*["\']([^"\']+)["\']\s*$', re.MULTILINE)
FORBIDDEN_SUFFIXES = (".key", ".db", ".db-wal", ".db-shm")
FORBIDDEN_PARTS = {
    "soak_home",
    "artifacts",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
REQUIRED_ENTRYPOINTS = {
    "wls = wls.cli:main",
    "wls-provider = wls.provider_cli:main",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_version(repository: Path) -> str:
    text = (repository / "source" / "src" / "wls" / "_version.py").read_text(
        encoding="utf-8"
    )
    match = _VERSION_RE.search(text)
    if match is None:
        raise ValueError("canonical version could not be parsed")
    return match.group(1)


def _metadata_fields(text: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if ": " not in line:
            continue
        key, value = line.split(": ", 1)
        fields.setdefault(key, value)
    return fields


def verify(wheel: Path) -> dict:
    repository = Path(__file__).resolve().parents[2]
    wheel = wheel.resolve()
    if not wheel.is_file():
        raise FileNotFoundError(wheel)
    canonical_version = _canonical_version(repository)
    with zipfile.ZipFile(wheel) as archive:
        names = sorted(archive.namelist())
        metadata_names = [
            name for name in names if name.endswith(".dist-info/METADATA")
        ]
        entrypoint_names = [
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        ]
        if len(metadata_names) != 1:
            raise ValueError(
                f"expected exactly one METADATA file, found {len(metadata_names)}"
            )
        metadata = _metadata_fields(
            archive.read(metadata_names[0]).decode("utf-8")
        )
        entrypoints = (
            archive.read(entrypoint_names[0]).decode("utf-8")
            if len(entrypoint_names) == 1
            else ""
        )
        forbidden = [
            name
            for name in names
            if any(part in FORBIDDEN_PARTS for part in Path(name).parts)
            or name.endswith(FORBIDDEN_SUFFIXES)
            or ".egg-info/" in name
        ]
    filename_version = wheel.name.removeprefix(
        "workstation_living_system-"
    ).split("-", 1)[0]
    missing_entrypoints = sorted(
        item for item in REQUIRED_ENTRYPOINTS if item not in entrypoints
    )
    checks = {
        "distribution_name": metadata.get("Name") == "workstation-living-system",
        "metadata_version": metadata.get("Version") == canonical_version,
        "filename_version": filename_version == canonical_version,
        "single_entrypoint_file": len(entrypoint_names) == 1,
        "required_entrypoints": not missing_entrypoints,
        "forbidden_files_absent": not forbidden,
    }
    return {
        "passed": all(checks.values()),
        "wheel": str(wheel),
        "wheel_sha256": _sha256(wheel),
        "wheel_size_bytes": wheel.stat().st_size,
        "canonical_version": canonical_version,
        "metadata": {
            "name": metadata.get("Name"),
            "version": metadata.get("Version"),
            "requires_python": metadata.get("Requires-Python"),
            "path": metadata_names[0],
        },
        "filename_version": filename_version,
        "entrypoints": entrypoints.splitlines(),
        "missing_entrypoints": missing_entrypoints,
        "forbidden_files": forbidden,
        "archive_file_count": len(names),
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = verify(args.wheel)
    except Exception as exc:
        report = {
            "passed": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
