@echo off
REM Launch the full live stack on the VPS, each detached to its own stdout log:
REM   - portfolio bot         (magic 1001)  run_portfolio.py
REM   - consolidation passive (magic 2003)  run_passive_paper.py
REM   - tick logger                         log_ticks.py
REM
REM pdhl passive (magic 2002) was RETIRED and is no longer a runnable book (the
REM runner is consolidation-only now; see start_passive.bat).
REM Every bot takes a single-instance lock (single_instance.acquire_or_exit), so
REM re-running this is SAFE: a bot already up is refused, not double-launched.
cd /d C:\Users\shikang\Desktop\MT5_Trader
start "portfolio" /b cmd /c ".venv\Scripts\python.exe scripts\run_portfolio.py >> logs\portfolio_stdout.log 2>&1"
start "passive_consol" /b cmd /c ".venv\Scripts\python.exe scripts\run_passive_paper.py --magic 2003 --range-n 96 --regime-coarsen 6 >> logs\passive_consolidation_stdout.log 2>&1"
start "ticks" /b cmd /c ".venv\Scripts\python.exe scripts\log_ticks.py --flush-seconds 60 >> logs\log_ticks_stdout.log 2>&1"
echo Launched portfolio + consolidation passive + tick logger. Verify logs\*.jsonl
