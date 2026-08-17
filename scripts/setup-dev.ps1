# 开发环境安装。装完直接能跑 python -m shop_print。
#
# 为什么不能只 pip install -e ".[dev]"：
#   rapidocr 的依赖里写的是 opencv-python（完整版），会**覆盖**我们要的
#   opencv-python-headless。完整版自带一套 Qt，和 PySide6 的 Qt 冲突，
#   会导致启动时崩溃或 Qt plugin 加载失败。
#   所以装完必须把完整版卸掉、把 headless 装回来。
#
# 用法：  .\scripts\setup-dev.ps1



# 控制台输出统一成 UTF-8。
# Windows PowerShell 5.1（店铺机上大概只有这个）自身的输出按系统代码页编码，
# 和 bat 里的 chcp 65001 不一致时中文全是乱码 —— 实测踩过。
# 这里把代码页和 .NET 的输出编码一起对齐，怎么启动都不乱。
if ([Console]::OutputEncoding.CodePage -ne 65001) {
    try {
        chcp 65001 > $null
        [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    } catch {
        # 非交互环境没有控制台，设不了就算了，不能因此不干活
    }
}
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
$headless = "opencv-python-headless==4.14.0.94"

if (-not (Test-Path $py)) {
    Write-Host "==> 创建虚拟环境 .venv (Python 3.14)" -ForegroundColor Cyan
    py -3.14 -m venv .venv
}

Write-Host "==> 升级 pip" -ForegroundColor Cyan
& $py -m pip install --upgrade pip --quiet

Write-Host "==> 安装依赖" -ForegroundColor Cyan
& $py -m pip install -e ".[dev]"

Write-Host "==> 清掉被 rapidocr 拖进来的完整版 OpenCV" -ForegroundColor Cyan
& $py -m pip uninstall -y opencv-python opencv-contrib-python 2>$null | Out-Null
& $py -m pip install --force-reinstall --no-deps $headless --quiet

Write-Host "==> 检查" -ForegroundColor Cyan
& $py -c @"
import cv2, os, sys
d = os.path.dirname(cv2.__file__)
assert not os.path.isdir(os.path.join(d, 'qt')), 'cv2 里带了 Qt —— 装成完整版了，会和 PySide6 冲突'
import PySide6, pymupdf, rapidocr, numpy, PIL, docx, win32print, win32ui, watchdog
print(f'OK  python={sys.version.split()[0]}  cv2={cv2.__version__}  PySide6={PySide6.__version__}')
"@

Write-Host ""
Write-Host "装好了。常用命令：" -ForegroundColor Green
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python -m shop_print                          # 启动界面"
Write-Host "  python -m shop_print.core.enhance <图片>       # 增强算法调试"
Write-Host "  ruff check . ; pytest -q"
