# 启动打印助手。一般由 运行.bat 双击调起，也可以直接在 PowerShell 里跑。
#
#   .\scripts\run.ps1              # 跑源码（开发用，改完代码立刻能看到效果）
#   .\scripts\run.ps1 -Packaged    # 跑打包产物 dist\打印助手\打印助手.exe
#   .\scripts\run.ps1 -SelfCheck   # 不开界面，只出自检报告（店铺机排障用这个）
#   .\scripts\run.ps1 -DebugLog    # 日志级别调到 DEBUG（-Debug 是 PowerShell 占用的名字，不能用）

[CmdletBinding()]
param(
    [switch]$Packaged,
    [switch]$SelfCheck,
    [switch]$DebugLog
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$appArgs = @()
if ($SelfCheck) { $appArgs += '--self-check' }
if ($DebugLog) { $appArgs += '--debug' }

# 有人（或某个脚本）把这个环境变量设成 offscreen 的话，程序会正常启动但**没有窗口**，
# 看起来像卡死。这里给子进程清掉，省得排查半天。
if ($env:QT_QPA_PLATFORM -and $env:QT_QPA_PLATFORM -ne 'windows') {
    Write-Warning "QT_QPA_PLATFORM 现在是「$env:QT_QPA_PLATFORM」，那样不会显示窗口，已临时改成 windows"
}
$env:QT_QPA_PLATFORM = 'windows'

if ($Packaged) {
    $exe = Join-Path $root 'dist\打印助手\打印助手.exe'
    if (-not (Test-Path $exe)) {
        Write-Host ''
        Write-Host "还没有打包产物：$exe" -ForegroundColor Red
        Write-Host '先双击 scripts\打包.bat（或者跑 .\scripts\build.ps1）。' -ForegroundColor Red
        exit 1
    }
    if ($SelfCheck) {
        # 打包产物是 --windowed，控制台拿不到它的 stdout；报告文件是 UTF-8 的，
        # 直接等它跑完再念文件，比折腾编码靠谱。
        Write-Host "自检打包版（要跑一次 OCR，十几秒）：$exe" -ForegroundColor Cyan
        $proc = Start-Process -FilePath $exe -ArgumentList $appArgs -PassThru -Wait
        $报告 = Join-Path $env:LOCALAPPDATA 'ShopPrint\logs\自检报告.txt'
        if (Test-Path $报告) {
            Write-Host ''
            Get-Content $报告 -Encoding UTF8
            Write-Host ''
            Write-Host "报告文件：$报告（把它发给开发者就行）" -ForegroundColor Green
        } else {
            Write-Warning "没找到自检报告：$报告"
        }
        exit $proc.ExitCode
    }
    Write-Host "启动打包版：$exe" -ForegroundColor Cyan
    & $exe @appArgs
    exit $LASTEXITCODE
}

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host ''
    Write-Host "找不到虚拟环境：$python" -ForegroundColor Red
    Write-Host '先跑一次 .\scripts\setup-dev.ps1（建 venv + 装依赖）。' -ForegroundColor Red
    exit 1
}

# 完整版 opencv 自带一套 Qt，和 PySide6 冲突会在启动时崩，报的错还看不懂。
# 这里提前查一下，给一句能照着做的提示。
$带Qt = & $python -c "import cv2, os; print(os.path.isdir(os.path.join(os.path.dirname(cv2.__file__), 'qt')))" 2>$null
if ($带Qt -eq 'True') {
    Write-Host ''
    Write-Host '装的是完整版 opencv（cv2 里带着 Qt），会和界面库冲突。' -ForegroundColor Red
    Write-Host '跑一次 .\scripts\setup-dev.ps1 修好再启动。' -ForegroundColor Red
    exit 1
}

if ($SelfCheck) {
    Write-Host '开始自检（不开界面，要跑一次 OCR，十几秒）…' -ForegroundColor Cyan
} else {
    Write-Host '启动打印助手（源码模式）…　关掉窗口就退出' -ForegroundColor Cyan
}
& $python -m shop_print @appArgs
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Host ''
    Write-Host "程序退出码 $code。技术细节在这里：" -ForegroundColor Yellow
    Write-Host "  $env:LOCALAPPDATA\ShopPrint\logs\app.log"
}
exit $code
