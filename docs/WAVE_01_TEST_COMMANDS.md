# Wave 01 Reproduction Commands

```bash
python -m compileall -q src
PYTHONPATH=src pytest -q
python -m pip wheel . --no-deps -w dist
```
