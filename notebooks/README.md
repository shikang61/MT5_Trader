# `notebooks/`

Exploration front-end for the research stages — **thin wrappers over
`mt5_trader.*`**, never reimplementing signal/engine logic (research–production
parity). The same `signal()` runs here, in the backtester, and live.

- `signal_research.ipynb` — WORKFLOW Stage 1–2: IC / rank-IC, decay + half-life,
  turnover, by-regime for a chosen signal.
- `backtesting.ipynb` — WORKFLOW Stage 3: equity curve, cost stress, parameter
  sweeps, and the structured gate verdict.

Both run on real ticks (`data/processed`) if present, else deterministic
synthetic bars, so they work before the dump lands. The **reproducible gate**
stays in the CLI (`uv run scripts/strategy.py validate`); notebooks are for
interactive/visual work only.

**Outputs are stripped on commit** by nbstripout (keeps `.ipynb` diffs clean).
After a fresh clone, enable the filter once:

    uv run nbstripout --install --attributes .gitattributes

Discipline: **Kernel → Restart & Run All** before trusting a result; graduate a
validated idea to a registered strategy + the pipeline (`scripts/strategy.py`),
don't leave it living in a cell.
