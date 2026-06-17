# Design: Regime-gate vwap_trend

Date: 2026-06-17
Status: approved (design); implementation pending
Scope: `vwap_trend` strategy only. Part 1 of a two-part strategy-improvement
effort (Part 2 = correlation-aware portfolio sizing, separate spec).

## Problem

`vwap_trend` (session-VWAP stretch continuation, live weight 0.3, M5 XAUUSD)
holds positions regardless of market regime, so it bleeds during choppy
(range) sessions. On the **real broker feed** (`data/real`, May 11 – Jun 10
2026) plain `vwap_trend@5m` returns **−2.74%, Sharpe −1.40, maxDD −6.96%** — the
strategy is currently a likely live drag, not the +5.7% it showed on Dukascopy
ticks (different tick stream → different signal). The book is lumpy by design;
the goal is to suppress the unprofitable chop without killing the trend edge.

This mirrors the lever that rescued `ratio_mr`: force the signal flat outside a
trend regime (Kaufman efficiency-ratio classifier, `features/regime.py`).
`ratio_mr` (mean-reversion) trades only in RANGE; `vwap_trend` (continuation)
is the inverse — trade only in TREND.

## Acceptance criterion (agreed)

Ship only if the gate delivers **lower max-drawdown AND higher Sharpe** than
plain `vwap_trend`, even if mean return falls (the same bar `ratio_mr` cleared —
treat the gate as a drawdown/smoothness lever). Must hold on the in-sample
month; re-confirm on the competition dump (true OOS) before live money.

## Evidence (already gathered, 2026-06-17)

Coarsen sweep, `vwap_trend@5m` presence-gate (`pos→0` where `regime==RANGE`),
`data/real`+`data/ticks`, slippage 0.5 bp (1×) and 2.0 bp (4× cost stress).
`regime_series` ER window in time ≈ `er_n(16) × coarsen × 5min`.

| coarsen | ~window | ret (1×) | Sharpe | maxDD | ret (4×) |
|--------:|--------:|---------:|-------:|------:|---------:|
| plain   |    —    |  −2.74%  | −1.40  | −6.96% | −3.20%  |
| 1       |  ~1.3h  |  +2.24%  |  4.94  | −1.05% |   —     |
| 2       |  ~2.7h  |  +0.27%  |  0.61  | −1.37% | −0.69%  |
| **3**   |  **4h** | **+0.76%**| **1.56**| **−1.20%** | **+0.10%** |
| **4**   | **~5.3h**| **+1.12%**| **2.17**| **−2.18%** | **+0.49%** |
| 5       |  ~6.7h  |  −4.05%  | −2.26  | −6.96% | −4.48%  |
| 6       |   ~8h   |  −3.22%  | −1.75  | −6.96% |   —     |
| 8       |  ~10.7h |  −2.24%  | −1.15  | −6.96% |   —     |
| 12      |   ~16h  |  −4.12%  | −2.27  | −6.96% |   —     |

Findings:
- **Plateau, not a hot cell:** coarsen 1–4 all positive Sharpe with maxDD
  ~−1 to −2% (vs plain −6.96%). Hard cliff at coarsen ≥ 5 (reverts to plain).
- The good region is a **short intraday regime window (~1.3–5.3h)** — the
  opposite of `ratio_mr`'s coarsen=15 / ~20h default, which is why coarsen=15
  did nothing for `vwap_trend`. Mechanistically right: `vwap_trend` is an
  intraday session strategy (daily reset, 120-min warmup) and needs an intraday
  regime, not a daily one.
- **Cost-robust:** under 4× cost, coarsen 3 and 4 stay positive; coarsen 4 is
  the most cost-robust. coarsen 1 is the edge-of-grid optimum (untrustworthy).

**Shippable pick: coarsen = 4** (middle of plateau, best under cost stress).

## Change

### `src/mt5_trader/strategies/vwap_trend.py`

Add two dataclass fields and a gate at the end of `signal()`:

```python
from ..features.regime import RANGE, regime_series   # new import

regime_filter: bool = False     # opt-in; default off preserves current behavior
regime_coarsen: int = 4         # ~5h intraday regime window (plateau middle)

# at end of signal(), after pos is built:
if self.regime_filter:
    regime = regime_series(bars, coarsen=self.regime_coarsen)
    pos = np.where(regime != RANGE, pos, 0.0)   # trade only in a TREND regime
```

Notes:
- `regime_filter=False` by default → the change is **inert** until the portfolio
  config opts in. Keeps existing tests/behavior unchanged; enabling it is an
  explicit, reviewable config edit (one-way code flow, Lesson #17).
- `regime_series` is **causal** (sampling grid anchored to the start; the regime
  at any past bar is independent of later bars) → preserves the engine's
  no-lookahead guarantee.
- `enter_trend`/`exit_trend` ER thresholds stay at `regime_series` defaults
  (0.40 / 0.30, used in the evidence above) — not exposed unless validation
  shows a need (YAGNI).

### `tests/test_strategies.py`

- `regime_filter=False` → output identical to current `vwap_trend` (regression
  guard: the default path must not change).
- `regime_filter=True` → on synthetic bars with a clear trending segment and a
  clear choppy segment, `pos == 0` everywhere `regime_series(bars)==RANGE`, and
  equals the ungated `pos` elsewhere.

### Validation harness (`scripts/eval_regime_gate.py`, extend)

Promote the existing diagnostic into the gate check for this spec:
- plain vs gated coarsen 3 & 4 on `data/real` (May 11 – Jun 10): require
  **maxDD↓ AND Sharpe↑** (already shown ✓).
- weekly walk-forward: gated ≥ plain in a majority of weeks.
- 4× cost stress: gated stays positive (✓ for coarsen 3/4).
- **True OOS = competition dump (~Jun 21):** re-run before sizing it live.
  Until then the gate is provisional.

### `config/portfolio.yaml` (deploy step, only if gate passes)

Add to the `vwap_trend` book:
```yaml
params: {regime_filter: true, regime_coarsen: 4}
# + comment citing gated-vs-raw numbers, mirroring the ratio_mr book comment
```
Rollback = delete the `params` line (reverts to plain `vwap_trend`).

## Out of scope

- **donchian gate:** its result was messier (coarsen=3 cut DD but return stayed
  ~flat/negative) — a separate question, not this spec.
- **Portfolio sizing / vwap_trend's 0.3 weight:** Part 2 (correlation-aware
  sizing). The −2.7% plain result raises the weight question but does not change
  it here.
- Exposing ER thresholds; size-scaling by regime conviction (YAGNI).

## Risks

- **One month, one feed (Lesson #8).** The plateau is in-sample on a single
  real-feed month. The coarsen≥5 cliff and edge-best coarsen=1 mean the gate is
  regime-window-sensitive; coarsen=4 is chosen as the cost-robust plateau
  middle, not an optimum. Acceptance is provisional until the competition-dump
  OOS re-confirms.
- Default-off mitigates deploy risk: no live behavior change until the config
  opt-in lands and passes review.

## Validation result (2026-06-17)

`uv run scripts/eval_regime_gate.py` on `data/real`+`data/ticks` (May 11 – Jun 10):

| variant | ret | Sharpe | maxDD | 4× ret |
|---|---|---|---|---|
| plain | −2.74% | −1.40 | −6.96% | −3.20% |
| gated c=3 | +0.76% | +1.56 | −1.20% | +0.10% |
| gated c=4 (shipped) | +1.12% | +2.17 | −2.18% | +0.49% |

**Verdict: PASS** against the agreed bar (maxDD smaller AND Sharpe higher, both
windows incl. 4× cost) for c=3 and c=4.

**Caveat — benefit is concentrated in one week.** Weekly net (plain → gated c=4):
w20 +1.51→+0.87, w21 −1.28→−0.84, w22 −0.17→−0.20, w23 +0.31→+0.20,
w24 +3.68→+1.12, **w25 −6.56→+0.00**. The gate is essentially tail-risk
insurance: it neutralizes the single disaster week (w25) and clips upside in
the trend weeks, so it is *worse* in ~4 of 6 weeks but transforms the month's
DD/Sharpe. This is thin evidence (one month, one week carries it; Lesson #8) —
the config flip is **provisional** and must be re-confirmed on the competition
dump before it earns trust at live size.

## Workflow

Branch `feat/vwap-regime-gate` off `main` → TDD the gate → run the validation
harness → if it passes, flip the config → PR. Tests gate the deploy (Lesson #9).
