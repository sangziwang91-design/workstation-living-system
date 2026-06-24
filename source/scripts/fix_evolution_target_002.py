from pathlib import Path

path = Path(__file__).resolve().parents[2] / "source/tests/test_cognition.py"
text = path.read_text(encoding="utf-8")
old = '''    context = {
        "cycle_id": new_id("cycle"),
        "workspace": runtime.attention.select(
            reserved,
            [],
            retrieved,
            *runtime.drives.load(),
        ),
'''
new = '''    workspace_items = runtime.attention.select(
        reserved,
        [],
        retrieved,
        *runtime.drives.load(),
    )
    context = {
        "cycle_id": new_id("cycle"),
        "workspace": [item.to_dict() for item in workspace_items],
'''
if old not in text:
    raise RuntimeError("typing repair anchor missing")
text = text.replace(old, new, 1)
text = text.replace(
    '    context["workspace"] = [item.to_dict() for item in context["workspace"]]\n',
    "",
    1,
)
path.write_text(text, encoding="utf-8")
