## What & why

<!-- one or two lines -->

## Checklist

- [ ] `uv run pytest -q` passes
- [ ] New strategy cleared the evidence gate (weekly splits + realistic-cost stress + parameter plateau) — see docs/WORKFLOW.md
- [ ] No change to `config/portfolio.yaml` / `config/risk.yaml` without live-deploy owner sign-off
- [ ] Public API changes exported in the package `__init__.py` (+ a `tests/test_public_api.py` entry)
- [ ] Docs updated if behaviour/usage changed
