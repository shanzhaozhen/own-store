@echo off
rem Launcher for the shop print assistant (dev mode: runs from source).
rem Options: -Packaged (run the built exe) / -SelfCheck / -DebugLog
rem All logic + all Chinese text live in run.ps1.
rem This file stays pure ASCII on purpose: cmd reads a .bat with the console
rem code page, and non-ASCII bytes here (even inside rem) desync the parser.
setlocal
chcp 65001 >nul
where /q pwsh
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
)
if errorlevel 1 (
    echo.
    pause
)
