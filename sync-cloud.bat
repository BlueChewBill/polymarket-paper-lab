@echo off
REM sync-cloud.bat
REM One-click pull of the cloud sniper's JSONL + state.json down to
REM ~/.ols-sniper-cloud/var/logs/ so the local dashboard can read it.
REM Also rebuilds ~/.ols-sniper-cloud/sniper.db.
REM
REM Requires: SSH key at C:\Users\dylan\.ssh\LightsailDefaultKey-us-east-1.pem
REM Pass --help on the command line for overrides.

start "Paper Lab - SYNC" powershell.exe -NoExit -Command "Set-Location 'C:\Users\dylan\polymarket\files\polymarket-paper-lab'; python scripts\sync_remote_sniper.py; Write-Host ''; Write-Host 'Done. Close this window when ready.' -ForegroundColor Gray"

exit /b
