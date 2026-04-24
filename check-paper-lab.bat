@echo off
REM check-paper-lab.bat
REM One-click progress report. Opens a PowerShell window that:
REM   1. Syncs the sniper's JSONL into SQLite
REM   2. Runs the paper-lab fill-rate + slippage verdict
REM   3. Runs the side-by-side strategy comparison
REM Stays open so you can read the output. Safe to run while the
REM main start-paper-lab terminals are still running.

start "Paper Lab - CHECK" powershell.exe -NoExit -Command "Set-Location 'C:\Users\dylan\polymarket\files\polymarket-paper-lab'; Write-Host '=== sync sniper JSONL -> SQLite ===' -ForegroundColor Yellow; python scripts\sync_sniper_to_sqlite.py; Write-Host ''; Write-Host '=== paper-lab fill-rate + slippage verdict ===' -ForegroundColor Yellow; python scripts\analyze_trades.py; Write-Host ''; Write-Host '=== side-by-side comparison ===' -ForegroundColor Yellow; python scripts\compare_strategies.py; Write-Host ''; Write-Host 'Close this window when done.' -ForegroundColor Gray"

exit /b
