@echo off
rem Install the packaged app onto the shop PC (needs admin; will self-elevate).
rem All logic + all Chinese text live in install-to-shop.ps1.
rem Keep this file pure ASCII (see the note in the launcher bat).
setlocal
chcp 65001 >nul
where /q pwsh
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-to-shop.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-to-shop.ps1" %*
)
echo.
pause
