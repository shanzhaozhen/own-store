# 打包成免安装文件夹。产物：dist\打印助手\打印助手.exe
#
# 为什么是 --onedir 不是 --onefile：onefile 每次启动都要把 onnx 模型和 Qt
# 解压到临时目录，弱机器上要等好几秒 —— 长辈会以为卡死了然后连点好几下。
# 详见 docs/07-打包与部署.md。
#
#   .\scripts\build.ps1            # 正常打包
#   .\scripts\build.ps1 -Clean     # 先清掉 build/dist 再打

[CmdletBinding()]
param(
    [switch]$Clean
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "找不到虚拟环境：$python`n先跑 .\scripts\setup-dev.ps1"
}

# opencv 完整版会带进来一套 Qt，和 PySide6 的 Qt 冲突，打完包才发现就太晚了
$opencvCheck = & $python -c "import cv2, os; print('bad' if os.path.isdir(os.path.join(os.path.dirname(cv2.__file__), 'qt')) else 'ok')"
if ($opencvCheck -ne 'ok') {
    throw 'cv2 里带着 Qt（装的是完整版 opencv-python），会和 PySide6 冲突。先跑 .\scripts\setup-dev.ps1 修好再打包。'
}

if ($Clean) {
    foreach ($dir in @('build', 'dist')) {
        if (Test-Path $dir) {
            Write-Host "清理 $dir"
            Remove-Item -Recurse -Force $dir
        }
    }
}

# 产物还在运行的话，PyInstaller 会因为文件被占用失败，报的是看不懂的
# 「PermissionError: [WinError 5] 拒绝访问 ... _internal\cv2\cv2.pyd」。
# 先说清楚，省得对着这行错误发愁。
$在跑 = Get-Process -Name '打印助手' -ErrorAction SilentlyContinue
if ($在跑) {
    throw "「打印助手」正在运行（PID $($在跑.Id -join ', ')），先关掉它再打包 —— 不然产物文件被占用，PyInstaller 会报「拒绝访问」"
}

$assets = Join-Path $root 'src\shop_print\assets'
$qss = Join-Path $root 'src\shop_print\ui\style.qss'
$icon = Join-Path $assets 'icons\app.ico'

$pyArgs = @(
    '-m', 'PyInstaller',
    '--noconfirm',
    '--clean',
    '--onedir',
    '--windowed',
    '--name', '打印助手',
    '--paths', 'src',
    # 这两个路径要和 paths.bundle_root() 对上：打包后是解包目录，开发时是包目录
    '--add-data', "$assets;assets",
    '--add-data', "$qss;ui",
    # RapidOCR 的模型配置是 yaml 数据文件，PyInstaller 静态分析找不到
    '--collect-all', 'rapidocr',
    '--hidden-import', 'onnxruntime',
    # 别把别的 GUI 框架打进来白占体积
    '--exclude-module', 'PyQt5',
    '--exclude-module', 'PyQt6',
    '--exclude-module', 'PySide2',
    '--exclude-module', 'tkinter',
    '--exclude-module', 'matplotlib',
    '--exclude-module', 'pytest'
)
if (Test-Path $icon) {
    $pyArgs += @('--icon', $icon)
} else {
    Write-Warning "没有图标文件（$icon），先用默认图标打包。交付前补上，长辈靠图标认软件。"
}
$pyArgs += 'scripts\launcher.py'

Write-Host '开始打包（第一次要几分钟）…'
& $python @pyArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败，退出码 $LASTEXITCODE" }

$exe = Join-Path $root 'dist\打印助手\打印助手.exe'
if (-not (Test-Path $exe)) { throw "打包跑完了但没找到 $exe" }

$size = (Get-ChildItem (Join-Path $root 'dist\打印助手') -Recurse -File |
    Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ''
Write-Host ("完成：{0}  （{1:N0} MB）" -f $exe, $size)
Write-Host '下一步（必做，别跳）：'
Write-Host '  1. 关掉虚拟环境、在别的窗口直接双击这个 exe，确认它不是靠开发机的 .venv 才能起来'
Write-Host '  2. 跑一次「照片变清楚」和「照片转文字」，确认 OCR 模型确实被打进去了'
Write-Host '  3. 拷到店铺机，按 docs/07-打包与部署.md 的清单逐项验'
