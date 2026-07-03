from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json
import shutil
import subprocess  # nosec B404

BASE_COMMIT = "4c77401deb159e8ee2a2daf3c6e2702ce48876e3"

INCLUDE_PATTERNS = (
    "source/src/wls/examiner/**",
    "source/src/wls/examiner_plugin.py",
    "source/scripts/evaluate_examiner_candidate.py",
    "source/scripts/run_examiner_sandbox.py",
    "source/scripts/run_adversarial_trials.py",
    "source/scripts/run_final_verification.py",
    "source/scripts/install_examiner_overlay.py",
    "source/scripts/uninstall_examiner_overlay.py",
    "source/tests/test_examiner_*.py",
    ".evolution/examiner/**",
    "docs/*.md",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git(target: Path, *args: str) -> str:
    result = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(target), *args],
        check=True,
        text=True,
        capture_output=True,
    )
    return result.stdout.strip()


def collect(package_root: Path) -> list[Path]:
    files: set[Path] = set()
    for pattern in INCLUDE_PATTERNS:
        if pattern.endswith("/**"):
            # Path.glob("**/") matches directories, we need to match files recursively
            recursive_pattern = pattern.replace("/**", "/**/*")
            files.update(path for path in package_root.glob(recursive_pattern) if path.is_file())
        else:
            files.update(path for path in package_root.glob(pattern) if path.is_file())
    return sorted(files)


def main() -> int:
    parser = argparse.ArgumentParser(description="Install WLS examiner overlay onto an isolated branch")
    parser.add_argument("target", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--allow-head-mismatch", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    args = parser.parse_args()

    package_root = Path(__file__).resolve().parents[2]
    target = args.target.resolve()
    if not (target / ".git").exists():
        raise SystemExit("target is not a Git checkout")
    branch = git(target, "branch", "--show-current")
    if branch in {"main", "master", ""}:
        raise SystemExit("refusing to install on main/master or detached HEAD; create an isolated branch")
    head = git(target, "rev-parse", "HEAD")
    if head != BASE_COMMIT and not args.allow_head_mismatch:
        raise SystemExit(
            f"HEAD mismatch: expected {BASE_COMMIT}, got {head}; inspect drift or pass --allow-head-mismatch"
        )
    dirty = git(target, "status", "--porcelain")
    if dirty and not args.allow_dirty:
        raise SystemExit("target checkout is dirty; commit/stash first or pass --allow-dirty")

    receipt_path = target / ".evolution/examiner/INSTALL_RECEIPT.json"
    if receipt_path.exists():
        raise SystemExit("an examiner install receipt already exists; inspect or uninstall first")

    plan = []
    for source in collect(package_root):
        relative = source.relative_to(package_root)
        destination = target / relative
        plan.append(
            {
                "source": str(relative),
                "destination": str(relative),
                "sha256": sha256(source),
                "exists": destination.exists(),
            }
        )
    print(json.dumps({"branch": branch, "head": head, "apply": args.apply, "files": plan}, indent=2))
    if not args.apply:
        return 0

    # Complete conflict preflight before writing any file. This prevents a late
    # collision from leaving a partially installed overlay.
    conflicts = [
        item["destination"]
        for item in plan
        if (target / item["destination"]).exists()
        and sha256(target / item["destination"]) != item["sha256"]
    ]
    if conflicts:
        raise SystemExit(f"refusing to overwrite non-identical files: {conflicts}")

    receipt_files: list[dict[str, object]] = []
    receipt = {
        "branch": branch,
        "base_head": head,
        "package_base_commit": BASE_COMMIT,
        "files": receipt_files,
    }
    created_paths: list[Path] = []
    try:
        for item in plan:
            source = package_root / item["source"]
            destination = target / item["destination"]
            existed = destination.exists()
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not existed:
                shutil.copy2(source, destination)
                created_paths.append(destination)
            receipt_files.append(
                {
                    "path": item["destination"],
                    "installed_sha256": item["sha256"],
                    "created_by_installer": not existed,
                }
            )
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(
            json.dumps(receipt, indent=2, sort_keys=True), encoding="utf-8"
        )
    except Exception:
        for path in reversed(created_paths):
            if path.exists() and sha256(path) == next(
                item["sha256"] for item in plan if target / item["destination"] == path
            ):
                path.unlink()
        receipt_path.unlink(missing_ok=True)
        raise
    print(f"installed {len(receipt_files)} files; plugin remains disabled by default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
