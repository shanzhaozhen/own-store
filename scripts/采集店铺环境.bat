@echo off
rem Collect shop-PC environment info; report lands on the desktop.
rem Read-only, no admin needed.
rem All logic + all Chinese text live in collect-shop-env.ps1.
rem Keep this file pure ASCII (see the note in the launcher bat).
setlocal
chcp 65001 >nul
where /q pwsh
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect-shop-env.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect-shop-env.ps1" %*
)
echo.
pause
