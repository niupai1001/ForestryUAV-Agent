@echo off
setlocal
cd /d "%~dp0"
title Forestry Agent Startup

echo Starting Forestry Agent environment...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" -OpenBrowser %*
if errorlevel 1 (
    echo.
    echo Startup failed. Review the error above, then press any key to close.
    pause >nul
    exit /b 1
)

echo.
echo Forestry Agent is ready. The workbench has been opened in your browser.
timeout /t 3 /nobreak >nul
exit /b 0
