from __future__ import annotations

from pathlib import Path


path = Path(__file__).resolve().parents[1] / "source/src/wls/cognition.py"
text = path.read_text(encoding="utf-8")
old = '''        memory_free_score = self._score(selected, context, include_memories=False)
        memory_delta = selected.score - memory_free_score
'''
new = '''        memory_free_score = counter_selected.score
        memory_delta = selected.score - memory_free_score
'''
if text.count(old) != 1:
    raise RuntimeError("expected exactly one memory delta formula")
path.write_text(text.replace(old, new), encoding="utf-8")
