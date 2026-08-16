@echo off
rem 双击这个文件采集店铺电脑的环境信息，结果会存到桌面「店铺环境」文件夹。
rem 真正的逻辑在同目录的 collect-shop-env.ps1（这个 bat 内容保持 ASCII 防乱码）。
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0collect-shop-env.ps1" %*
echo.
pause
