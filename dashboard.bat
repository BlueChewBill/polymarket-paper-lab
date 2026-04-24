@echo off
REM dashboard.bat
REM Opens a live-updating dashboard window showing the paper
REM account's wallet + bridge activity (opportunities, fills,
REM slippage, fees). Refreshes every 3 seconds. Safe to run
REM alongside start-paper-lab.bat -- SQLite is opened read-only.

start "Paper Lab - DASHBOARD" powershell.exe -NoExit -Command "Set-Location 'C:\Users\dylan\polymarket\files\polymarket-paper-lab'; python scripts\dashboard.py"

exit /b
