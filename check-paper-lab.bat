@echo off
REM check-paper-lab.bat
REM One-click progress report. Opens a PowerShell window that:
REM   1. Syncs the sniper's JSONL into SQLite
REM   2. Runs the side-by-side comparison
REM   3. Stays open so you can read the verdict
REM
REM Safe to run any time while the two main loops are running.

start "Paper Lab - CHECK" powershell.exe -NoExit -Command ^
  "$host.UI.RawUI.WindowTitle = 'Paper Lab - CHECK'; ^
   Set-Location 'C:\Users\dylan\polymarket\files\polymarket-paper-lab'; ^
   Write-Host '=== sync sniper JSONL -> SQLite ===' -ForegroundColor Yellow; ^
   python scripts\sync_sniper_to_sqlite.py; ^
   Write-Host ''; ^
   Write-Host '=== paper-lab fill-rate + slippage verdict ===' -ForegroundColor Yellow; ^
   python scripts\analyze_trades.py; ^
   Write-Host ''; ^
   Write-Host '=== side-by-side comparison ===' -ForegroundColor Yellow; ^
   python scripts\compare_strategies.py; ^
   Write-Host ''; ^
   Write-Host 'Close this window when done.' -ForegroundColor Gray"
