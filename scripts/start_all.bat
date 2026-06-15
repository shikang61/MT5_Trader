@echo off
REM One command to launch the whole live stack on the VPS: the portfolio bot
REM first (it must own magic 1001 before the passive books post), then both
REM passive-limit books. All detached, each to its own stdout log.
REM
REM WARNING: no singleton guard yet. Running this while anything is already
REM running spawns DUPLICATE bots = double positions. Stop all python first:
REM   Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Stop-Process -Force
cd /d C:\Users\shikang\Desktop\MT5_Trader
start "portfolio" /b cmd /c ".venv\Scripts\python.exe scripts\run_portfolio.py >> logs\portfolio_stdout.log 2>&1"
call scripts\start_passive.bat
echo Launched portfolio + passive books. Verify logs\portfolio.jsonl, logs\passive_*.jsonl
