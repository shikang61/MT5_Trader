@echo off
REM Launch the live stack on the VPS: the portfolio bot (magic 1001), detached
REM to its own stdout log.
REM
REM The passive limit-order books are PARKED — they lost money in live paper
REM trading (adverse selection on fills). Their code is kept (start_passive.bat
REM / run_passive_paper.py) for a possible future revisit but is NOT launched
REM here. To run them manually, call scripts\start_passive.bat.
REM
REM Each bot takes a single-instance lock (single_instance.acquire_or_exit), so
REM a duplicate launch is refused rather than double-positioning.
cd /d C:\Users\shikang\Desktop\MT5_Trader
start "portfolio" /b cmd /c ".venv\Scripts\python.exe scripts\run_portfolio.py >> logs\portfolio_stdout.log 2>&1"
echo Launched portfolio bot. Verify logs\portfolio.jsonl
