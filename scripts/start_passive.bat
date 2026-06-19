@echo off
REM Launch the consolidation passive-limit paper book (magic 2003) for the
REM MT5Passive scheduled task. Python runs DIRECTLY (no start /b) so the task
REM owns the process tree -> schtasks /end stops it cleanly, like MT5Portfolio.
REM Single-instance locked (acquire_or_exit("passive_consolidation")): a second
REM run is refused, not duplicated. daily_loss_cap defaults to 150 (PassiveCfg).
cd /d C:\Users\shikang\Desktop\MT5_Trader
.venv\Scripts\python.exe scripts\run_passive_paper.py --magic 2003 --range-n 96 --regime-coarsen 6 >> logs\passive_consolidation_stdout.log 2>&1
