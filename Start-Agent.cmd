@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0scripts\Start-Agent.ps1"
if errorlevel 1 (
  echo Agent launcher could not start. See workspace\launcher or docs\ONE-CLICK-START-zh.md.
  pause
)
endlocal
