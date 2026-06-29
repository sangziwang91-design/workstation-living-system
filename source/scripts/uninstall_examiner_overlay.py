from __future__ import annotations

from pathlib import Path
import argparse
import hashlib
import json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Rollback an unmodified WLS examiner overlay")
    parser.add_argument("target", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    target = args.target.resolve()
    receipt_path = target / ".evolution/examiner/INSTALL_RECEIPT.json"
    if not receipt_path.exists():
        raise SystemExit("install receipt is missing")
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    plan = []
    for item in reversed(receipt["files"]):
        path = target / item["path"]
        created = bool(item.get("created_by_installer", True))
        status = "missing" if created else "preexisting_keep"
        if path.exists() and created:
            status = (
                "safe_delete"
                if sha256(path) == item["installed_sha256"]
                else "modified_keep"
            )
        elif path.exists() and not created:
            status = "preexisting_keep"
        plan.append({"path": item["path"], "status": status})
    print(json.dumps({"apply": args.apply, "files": plan}, indent=2))
    if not args.apply:
        return 0
    for item in plan:
        path = target / item["path"]
        if item["status"] == "safe_delete":
            path.unlink()
    # Keep the receipt if any file was modified; otherwise remove it last.
    if not any(item["status"] == "modified_keep" for item in plan):
        receipt_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
