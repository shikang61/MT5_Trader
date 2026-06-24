@echo off
REM Broker tick-stream logger (scripts/log_ticks.py), own process — separate from
REM the trading loop so a logger fault can't touch it. Captures every book's
REM symbols (XAUUSD/XAGUSD) to data/ticks/<SYMBOL>/<date>_<ms>.parquet, the
REM canonical schema load_ticks() reads. Run via the MT5TickLogger scheduled task
REM (schtasks /run /tn MT5TickLogger) or directly. Single-instance locked.
cd /d C:\Users\shikang\Desktop\MT5_Trader
REM --extra-symbols: research-only captures beyond the traded XAUUSD/XAGUSD.
REM GBPUSD added 2026-06-24 to grow data for the parked vwap_trend@GBPUSD diversifier.
.venv\Scripts\python.exe scripts\log_ticks.py --flush-seconds 60 --extra-symbols GBPUSD >> logs\log_ticks_stdout.log 2>&1
