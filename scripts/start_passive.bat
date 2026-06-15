@echo off
REM Launch both passive-limit paper books, detached, each with its own log.
REM Distinct --magic keeps them from netting against each other or the
REM portfolio bot (magic 1001). Safe to run only if no passive book is already
REM running -- there is no singleton guard yet (a second run = duplicate books).
cd /d C:\Users\shikang\Desktop\MT5_Trader
start "passive_pdhl" /b cmd /c ".venv\Scripts\python.exe scripts\run_passive_paper.py --level-source pdhl --magic 2002 >> logs\passive_pdhl_stdout.log 2>&1"
start "passive_consol" /b cmd /c ".venv\Scripts\python.exe scripts\run_passive_paper.py --level-source consolidation --magic 2003 >> logs\passive_consolidation_stdout.log 2>&1"
echo Launched passive books: pdhl (magic 2002), consolidation (magic 2003).
