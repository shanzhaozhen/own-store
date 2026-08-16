@echo off
rem 双击这个文件一键打包：检查代码 → 跑测试 → 拷 OCR 模型 → 生成图标 → PyInstaller。
rem 产物在 dist\打印助手\，拷到店铺机用 安装到店铺电脑.bat 装。
rem 换过依赖、改过 add-data 时加参数：打包.bat -Clean
rem 真正的逻辑在同目录的 pack.ps1（这个 bat 内容保持 ASCII 防乱码）。
setlocal
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0pack.ps1" %*
echo.
pause
