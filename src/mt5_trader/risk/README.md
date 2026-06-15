# `risk/`

Position sizing and capital protection, applied between a book's raw signal and
the orders sent live. Parameters come from `config/risk.yaml`.

| File | Description |
|------|-------------|
| `manager.py` | Round-aware risk management: volatility-targeted sizing (scale position by realized vol toward a target) + loss **kill switch** (flatten/halt on a drawdown breach). The portfolio-level safety net — per-trade stops are off by design (see `config/portfolio.yaml`). |
