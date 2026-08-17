@echo off
rem One-click build: ruff -> pytest -> copy OCR models -> icon -> PyInstaller.
rem Options: -Clean (full rebuild) / -SkipTests
rem All logic + all Chinese text live in pack.ps1.
rem Keep this file pure ASCII (see the note in the launcher bat).
setlocal
chcp 65001 >nul
where /q pwsh
if %errorlevel%==0 (
    pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1" %*
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1" %*
)
echo.
pause
