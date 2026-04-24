@echo off
REM start-paper-lab.bat
REM One-click launcher: opens two titled PowerShell windows, one
REM running oracle-lag-sniper and one running the paper-lab bridge.
REM Close a window (or Ctrl-C) to stop that half; the other keeps
REM running.

start "Paper Lab - SNIPER" powershell.exe -NoExit -Command "Set-Location 'C:\Users\dylan\polymarket\files\oracle-lag-sniper'; Write-Host 'SNIPER (demo mode)' -ForegroundColor Cyan; oracle-lag-sniper run"

start "Paper Lab - BRIDGE" powershell.exe -NoExit -Command "Set-Location 'C:\Users\dylan\polymarket\files\polymarket-paper-lab'; Write-Host 'BRIDGE (sum-arb paper)' -ForegroundColor Green; python src\paper_bridge.py"

exit /b
