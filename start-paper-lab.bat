@echo off
REM start-paper-lab.bat
REM One-click launcher: opens two PowerShell windows, one for the
REM sniper and one for the paper-lab bridge. Each window title is
REM labeled so you can tell them apart. Close a window (or Ctrl-C)
REM to stop that half; the other keeps running.

start "Paper Lab - SNIPER" powershell.exe -NoExit -Command ^
  "$host.UI.RawUI.WindowTitle = 'Paper Lab - SNIPER'; ^
   Set-Location 'C:\Users\dylan\polymarket\files\oracle-lag-sniper'; ^
   Write-Host 'Launching oracle-lag-sniper (demo mode)' -ForegroundColor Cyan; ^
   oracle-lag-sniper run"

start "Paper Lab - BRIDGE" powershell.exe -NoExit -Command ^
  "$host.UI.RawUI.WindowTitle = 'Paper Lab - BRIDGE'; ^
   Set-Location 'C:\Users\dylan\polymarket\files\polymarket-paper-lab'; ^
   Write-Host 'Launching sum-arb paper bridge' -ForegroundColor Green; ^
   python src\paper_bridge.py"

echo.
echo Both terminals launched. You can close this window.
echo.
timeout /t 5 >nul
