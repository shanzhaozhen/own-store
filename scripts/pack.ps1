# 一键打包：备好随包资源 → 跑 PyInstaller → 说清下一步。
# 一般由 打包.bat 双击调起。
#
#   .\scripts\pack.ps1            # 正常打包
#   .\scripts\pack.ps1 -Clean     # 先清掉 build/dist 再打（换过依赖、改过 add-data 时用）
#   .\scripts\pack.ps1 -SkipTests # 跳过打包前的测试（不建议）
#
# 为什么要串成一个脚本：模型和图标都不进 git（体积 + 授权），换台机器 clone
# 下来直接 build 会打出一个「没有 OCR 模型」的产物，而这种产物在开发机上
# 一眼看不出问题 —— 到店铺机上点「照片转文字」才炸。

[CmdletBinding()]
param(
    [switch]$Clean,
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    Write-Host ''
    Write-Host "找不到虚拟环境：$python" -ForegroundColor Red
    Write-Host '先跑一次 .\scripts\setup-dev.ps1。' -ForegroundColor Red
    exit 1
}

function 步骤($n, $text) {
    Write-Host ''
    Write-Host "[$n] $text" -ForegroundColor Cyan
}

$开始 = Get-Date

步骤 '1/5' '检查代码（ruff）'
& $python -m ruff check .
if ($LASTEXITCODE -ne 0) { throw 'ruff 有问题，先修掉再打包' }

if ($SkipTests) {
    步骤 '2/5' '跳过测试（-SkipTests）'
    Write-Warning '打包前不跑测试，风险自己担'
} else {
    步骤 '2/5' '跑测试（pytest，约一两分钟）'
    & $python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw '测试没过，先修掉再打包' }
}

步骤 '3/5' '准备 OCR 模型（不进 git，每台机器都要拷一次）'
& (Join-Path $PSScriptRoot 'prepare-models.ps1')
if ($LASTEXITCODE -ne 0) { throw '模型没准备好，打出来的产物会用不了「照片转文字」' }

步骤 '4/5' '生成图标'
& $python (Join-Path $PSScriptRoot 'make_icon.py')
if ($LASTEXITCODE -ne 0) { throw '图标生成失败' }

步骤 '5/5' '打包（PyInstaller，第一次要几分钟）'
if ($Clean) {
    & (Join-Path $PSScriptRoot 'build.ps1') -Clean
} else {
    & (Join-Path $PSScriptRoot 'build.ps1')
}
if ($LASTEXITCODE -ne 0) { throw '打包失败' }

$用时 = [math]::Round(((Get-Date) - $开始).TotalMinutes, 1)
Write-Host ''
Write-Host "全部完成，用时 $用时 分钟。" -ForegroundColor Green
Write-Host '建议紧接着做两件事：'
Write-Host '  .\scripts\运行.bat -Packaged     # 跑一次打包产物，确认不是靠 .venv 才能起来'
Write-Host '  .\scripts\运行.bat -SelfCheck    # 出自检报告，确认模型/字体/打印机都在'
