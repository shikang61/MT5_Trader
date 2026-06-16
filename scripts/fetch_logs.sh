#!/usr/bin/env bash
# Pull runner logs from the VPS (run on the Mac).
#   bash scripts/fetch_logs.sh         # copy all logs to reports/vps_logs/<timestamp>/
#   bash scripts/fetch_logs.sh tail    # live-follow the newest log over ssh (Ctrl+C to stop)
#   bash scripts/fetch_logs.sh ticks   # mirror captured broker ticks -> data/ticks/
set -euo pipefail

HOST="mt5-vps"
REMOTE="Desktop/MT5_Trader/logs"
REMOTE_TICKS="Desktop/MT5_Trader/data/ticks"

case "${1:-pull}" in
pull)
    DEST="reports/vps_logs/$(date +%Y-%m-%d_%H%M%S)"
    mkdir -p "$DEST"
    scp -q "$HOST:$REMOTE/*.jsonl" "$DEST/"
    echo "pulled to $DEST:"
    ls -lh "$DEST"
    ;;
tail)
    ssh -t "$HOST" "\$f = Get-ChildItem $REMOTE\\*.jsonl | Sort-Object LastWriteTime -Descending | Select-Object -First 1; Write-Output ('following ' + \$f.Name); Get-Content \$f.FullName -Tail 10 -Wait"
    ;;
ticks)
    # Mirror the broker tick parquet (log_ticks.py) for local backtesting.
    # Files are immutable once written, so re-copies are idempotent; back up with
    # load_ticks("data/ticks", "XAUUSD").
    mkdir -p data/ticks
    scp -q -r "$HOST:$REMOTE_TICKS/." data/ticks/
    echo "pulled broker ticks to data/ticks/"
    du -sh data/ticks/*/ 2>/dev/null || true
    ;;
*)
    echo "usage: $0 [pull|tail|ticks]"; exit 1 ;;
esac
