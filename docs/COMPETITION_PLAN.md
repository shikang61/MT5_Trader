# Competition Plan — Model to Market (Jun 15–27, 2026)

## Key times (BST = UTC+1; configs use UTC)

| Event | BST | UTC |
|---|---|---|
| Strategy prep starts | Jun 15 | — |
| Round 1 | Jun 21 23:00 → Jun 22 22:00 | Jun 21 22:00 → Jun 22 21:00 |
| Round 2 | → Jun 23 22:00 | → Jun 23 21:00 |
| Round 3 | → Jun 24 22:00 | → Jun 24 21:00 |
| Finals (London) | Jun 27 | — |

Cutoffs: top 200 → 150 → 100. Ranking = end-of-round account equity.

## Checklist: before Jun 15

- [x] Research + backtest pipeline (this repo)
- [ ] Provision Windows VPS (any MT5-friendly provider; 2 vCPU / 4 GB is plenty).
      Install: MT5 terminal, Python 3.11+, `pip install -e .` of this repo.
- [ ] Test VPS end-to-end with any free MT5 demo account:
      `python scripts/run_live.py --strategy donchian --symbol XAUUSD`
      and confirm orders appear in the terminal + `logs/*.jsonl` written.

## Checklist: Jun 15–21 (prep phase)

- [ ] Register competition account; put credentials in VPS env vars only.
- [ ] Verify exact broker symbol names in Market Watch → update `config/instruments.yaml`.
- [ ] Historical dump drops → fill in real `config/schema_map.yaml` → `scripts/ingest_dump.py`.
- [ ] Run the research checklist in `docs/STRATEGY_PLAYBOOK.md` (costs, OFI
      validation, opening-range stats, gold/silver β, FX triangle).
- [ ] Backtest + walk-forward every candidate; record metrics in `reports/`.
- [ ] Paper trade the round-1 lineup for at least 3 sessions; compare paper
      fills vs backtest assumptions → recalibrate `slippage_bps`.
- [ ] Dry-run the kill switch (set `loss_limit` tiny on paper, confirm flatten).
- [ ] Decide round-1 book and freeze parameters by Jun 20.

## Round execution routine

Per round: confirm runner alive at round start (`logs/` heartbeat), check
position vs leaderboard at London open / NY open / 2h before round end.
Manual overrides (cutoff proximity, lock-the-seat de-risking) per the
tournament-context section of the playbook. After each round: archive logs,
review fills vs expectations, adjust `config/risk.yaml` for the next round.

## Risks / open questions

- Exact dump schema unknown → `schema_map.yaml` is the single point to fix.
- Broker symbol naming may differ (e.g. `XAUUSD.x`) → instruments.yaml.
- Live API "streams 5-level depth in real time" — MT5's Python API exposes
  depth via `market_book_*` only for some brokers; if unavailable, OFI
  strategy must consume the organizer's separate live API instead. Decide
  during prep week.
- Equity snapshots at round end: flatten or de-risk into 21:00 UTC to avoid
  a spread-mark hit at the snapshot.
