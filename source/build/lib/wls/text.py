from __future__ import annotations

import re

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+")
_WORD_RE = re.compile(r"[a-zA-Z0-9_]+")


def tokens(text: str) -> set[str]:
    """Tokenize Latin words and CJK character unigrams/bigrams.

    This keeps retrieval useful for both Chinese and English without adding a
    heavyweight tokenizer dependency.
    """
    lowered = text.lower()
    result = {
        match.group(0)
        for match in _WORD_RE.finditer(lowered)
        if len(match.group(0)) > 1
    }
    for match in _CJK_RE.finditer(lowered):
        segment = match.group(0)
        result.update(segment)
        result.update(segment[index : index + 2] for index in range(len(segment) - 1))
    return result
