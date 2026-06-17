@echo off
REM Launch the consolidation passive-limit paper book, detached, with its own log.
REM --magic keeps it from netting against the portfolio bot (magic 1001). Single-
REM instance locked per level source (acquire_or_exit("passive_consolidation")), so
REM a second run is refused, not duplicated.
REM
REM pdhl (magic 2002) was RETIRED: over 24h live paper it never filled once -- its
REM prior-day high/low levels sit too far OTM, and most arms failed order_send with
REM 10015 "Invalid price". The runner is consolidation-only now; the pdhl tick
REM screen (eval_passive_limits.py) is kept for research.
cd /d C:\Users\shikang\Desktop\MT5_Trader
start "passive_consol" /b cmd /c ".venv\Scripts\python.exe scripts\run_passive_paper.py --magic 2003 >> logs\passive_consolidation_stdout.log 2>&1"
echo Launched passive book: consolidation (magic 2003).
