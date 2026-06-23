# Cash deployment: paper → real money

What it takes to run the live portfolio on real capital and have the backtested
edge actually translate. Derived 2026-06-23 from the live 100k demo (config
`risk.yaml` + `portfolio.yaml`, broker specs, and the live trade-size
distribution).

## The binding constraint is lot granularity, not margin

Position size is **vol-targeted**, not margin-driven:

```
lots = leverage_frac × equity / (contract_size × price)
leverage_frac = min(target_vol / realized_vol, max_leverage)   # ~0.75× on gold
```

- `target_vol = 0.30`, `max_leverage = 2`, `exposure_cap = 0.5`/symbol → the book
  runs **~0.75× equity exposure**. It sizes by volatility and will not lever up
  past that no matter what the broker allows.
- Broker `lot_min = lot_step = 0.01` (standard XAUUSD account).
- On the 100k demo the **median target position is 0.0163 lots** — barely above
  the 0.01 floor — and the smallest signals (target ~0.0047 lots) **already round
  to zero at 100k**.

Because positions scale linearly with equity, the floor is purely about how many
0.01-lot quanta the median target spans. Margin is never binding (at 0.75×
exposure you use a sliver of even 1:10 leverage).

## Minimum capital

"Execute properly" = median target ≥ ~5 lot-quanta, so rounding error (per trade)
is small relative to the edge.

| Tier | Standard account (lot 0.01) | Cent account (lot 0.0001 effective) |
|------|------------------------------|--------------------------------------|
| Hard floor (median hits the quantum; below = most trades round to 0) | ~$60k | ~$600 |
| Functional (works, coarse, smallest books drop) | ~$100–150k | ~$1,000–1,500 |
| **Execute properly (faithful exposure, edge translates)** | **~$300k** | **~$3,000** |

Returns are %-based (scale-invariant) **above** the floor — the backtested % edge
holds at $300k the same as $100k. Below the floor, rounding destroys the
smaller-weight books and you run a degraded subset.

## Leverage does NOT lower the capital floor

500× (or any) broker leverage changes nothing about sizing — the bot is
vol-targeted and self-limits to ~0.75× exposure. Leverage only buys margin
headroom. The levers that *actually* reduce the capital needed:

1. **Finer lot granularity (the real answer).** A **cent / micro account
   (`lot_min 0.001`–`0.0001`)** drops the floor ~100×: ~$3k runs the full book at
   $300k-standard fidelity. Same risk, finer resolution.
2. **Run hotter** (raise `target_vol`/`max_leverage`) — lowers the floor
   proportionally but scales drawdown **1:1**. A modest 2× (target_vol 0.30→0.60)
   halves the floor; do **not** crank to use 500× — the edge is ~0.5%/week, and
   high leverage trips the 4% daily kill constantly and risks ruin.
3. **Fewer books** — Donchian + passive clear the floor at lower capital; you lose
   diversification.

## Cash-transition checklist

The capital number is the easy part. These decide whether the edge survives:

1. **Spread parity — make or break.** The books are **cost-marginal** (die above
   ~1bp gold spread; backtests assume ~0.21bp). Cent/micro accounts are usually
   **B-book** (broker is counterparty) with **wider spreads + worse fills**.
   **Measure the real account's gold spread before funding** — if it's 1–2bp the
   edge is gone regardless of capital.
2. **Verify `symbol_spec`.** The bot auto-adapts to a cent account *only if* the
   gateway reads the broker's actual `contract_size`/`lot_min`/`lot_step`. Confirm
   `MT5Gateway.symbol_spec()` returns the cent-scaled values — **no code change**
   expected, but verify.
3. **Fill quality.** Taker books (Donchian, breakouts) eat real slippage; the
   passive consolidation book depends on **limit fills** — check real fill rates
   vs the demo.
4. **Balance caps.** Many brokers cap cent-account balances (~$1–5k) and won't let
   them scale — fine as a validation bridge, not for growth.
5. **Risk controls (already deployed, real-money-critical):** 4% daily kill
   switch, exposure cap 0.5, chandelier disaster stop (sl_atr_mult 8), passive
   `daily_loss_cap` 150, news blackout. Consider adding an account-level max-DD
   halt for real money.
6. **Passive book sizing.** Fixed `lots = 0.05` (does NOT scale with equity). At
   low capital it dominates; either fund enough that 0.05 lots is proportionate or
   make its sizing equity-scaled before going live.

## Recommended path

1. **Cent account, ~$3k** — prove the edge survives real spread + fills with small
   capital at risk. This is the real paper→cash validation step.
2. Run ≥2–4 weeks; compare realized per-book P&L and fill quality to the demo.
3. If the edge holds (esp. spread parity), **graduate to a standard account at
   $100k+** for proper size.

Infra (VPS, MT5, scheduled tasks, restart-on-failure, kill switch, blackout) is
already real-money-ready — going live is funding + creds swap + the checks above.
