@echo off
rem 双击这个文件把「打印助手」装到店铺电脑上。
rem 真正的逻辑在同目录的 install-to-shop.ps1（中文提示都写在那里；
rem 这个 bat 内容全部保持 ASCII，避免 cmd 换代码页时中文变乱码）。
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-to-shop.ps1" %*
echo.
pause
