from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "source/src/wls/cognition.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one replacement, found {count}: {old[:80]!r}")
    return text.replace(old, new)


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '''        return sorted(unique.values(), key=lambda item: (item.score, item.key), reverse=True)[: self.maximum_hypotheses]
''',
        '''        return sorted(
            unique.values(),
            key=lambda item: (
                item.score,
                item.key.startswith("causal_memory_"),
                item.key,
            ),
            reverse=True,
        )[: self.maximum_hypotheses]
''',
    )
    text = replace_once(
        text,
        '''        if include_memories:
            for memory in context.get("memories", []):
                overlap = self._overlap(query, tokens(json.dumps(memory.get("content", {}), ensure_ascii=False, sort_keys=True)))
''',
        '''        if include_memories:
            for memory in context.get("memories", []):
                content = memory.get("content", {})
                if candidate.key.startswith("causal_memory_"):
                    continue
                if (
                    isinstance(content, dict)
                    and isinstance(content.get("decision_guidance"), dict)
                ):
                    continue
                overlap = self._overlap(
                    query,
                    tokens(
                        json.dumps(
                            content,
                            ensure_ascii=False,
                            sort_keys=True,
                        )
                    ),
                )
''',
    )
    count = text.count("0.97, original.base_score + 0.04")
    if count != 2:
        raise RuntimeError(f"expected two causal score replacements, found {count}")
    text = text.replace(
        "0.97, original.base_score + 0.04",
        "1.0, original.base_score + 0.04",
    )
    PATH.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
