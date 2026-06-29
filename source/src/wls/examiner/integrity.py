from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import hashlib

from .models import digest_json


# The evaluator version must bind executable judgment semantics, not only rule IDs.
# Defaults/anchors/constitution are bound separately by their own digests.
_IMPLEMENTATION_FILES = (
    "examiner/__init__.py",
    "examiner/models.py",
    "examiner/constitution.py",
    "examiner/anchors.py",
    "examiner/metrics.py",
    "examiner/integrity.py",
    "examiner/rules.py",
    "examiner/store.py",
    "examiner/system.py",
    "examiner_plugin.py",
)


@lru_cache(maxsize=1)
def current_implementation_digest() -> str:
    wls_root = Path(__file__).resolve().parent.parent
    records: list[dict[str, str]] = []
    for relative in _IMPLEMENTATION_FILES:
        path = wls_root / relative
        if not path.is_file():
            raise RuntimeError(f"examiner implementation file is missing: {relative}")
        records.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
        )
    return digest_json({"files": records})
