@echo off
REM Launch the consolidation passive-limit paper book, detached, with its own log.
REM --magic keeps it from netting against the portfolio bot (magic 1001). Safe to
REM run only if no passive book is already running -- there is no singleton guard
REM yet (a second run = duplicate books).
REM
REM pdhl (magic 2002) was RETIRED: over 24h live paper it never filled once -- its
REM prior-day high/low levels sit too far OTM, and most arms failed order_send with
REM 10015 "Invalid price". Code is kept (run_passive_paper.py --level-source pdhl,
REM eval_passive_limits.py) for research but is no longer launched.
cd /d C:\Users\shikang\Desktop\MT5_Trader
start "passive_consol" /b cmd /c ".venv\Scripts\python.exe scripts\run_passive_paper.py --level-source consolidation --magic 2003 >> logs\passive_consolidation_stdout.log 2>&1"
echo Launched passive book: consolidation (magic 2003).
