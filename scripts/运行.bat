@echo off
rem 双击这个文件启动打印助手（源码模式，开发机用）。
rem 想跑打包好的 exe：运行.bat -Packaged        只出自检报告：运行.bat -SelfCheck
rem 真正的逻辑在同目录的 run.ps1（这个 bat 内容保持 ASCII 防乱码）。
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run.ps1" %*
rem 只有出错才停下来让人看错误信息；正常关窗口就直接结束
if errorlevel 1 (
    echo.
    pause
)
