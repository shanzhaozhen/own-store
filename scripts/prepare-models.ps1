# 把 RapidOCR 的模型拷到 assets\models\，好让打包产物自带模型、运行时不联网下载。
#
# rapidocr 第一次识别时会把模型下到自己的包目录里（site-packages\rapidocr\models\）。
# 店铺网络不一定稳，长辈不能卡在"正在下载模型"上，所以打包前必须先拷过来。
#
#   .\scripts\prepare-models.ps1



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
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw "找不到虚拟环境：$python`n先跑 .\scripts\setup-dev.ps1" }

$target = Join-Path $root 'src\shop_print\assets\models'
New-Item -ItemType Directory -Force -Path $target | Out-Null

$source = & $python -c "import pathlib, rapidocr; print(pathlib.Path(rapidocr.__file__).parent / 'models')"
if (-not (Test-Path $source)) {
    Write-Host "rapidocr 还没下载过模型（$source 不存在）。" -ForegroundColor Yellow
    Write-Host '先跑一次识别让它下载：' -ForegroundColor Yellow
    Write-Host "  & '$python' -m pytest tests\test_ocr.py -m needs_samples -q"
    exit 1
}

$copied = 0
foreach ($file in Get-ChildItem $source -Filter *.onnx -File) {
    Copy-Item $file.FullName -Destination $target -Force
    Write-Host ("  拷贝 {0}　（{1:N1} MB）" -f $file.Name, ($file.Length / 1MB))
    $copied++
}
if (-not $copied) { throw "$source 里没有 .onnx 模型文件" }

$size = (Get-ChildItem $target -Filter *.onnx -File | Measure-Object -Property Length -Sum).Sum / 1MB
Write-Host ''
Write-Host ("好了：$copied 个模型，共 {0:N1} MB → $target" -f $size) -ForegroundColor Green
Write-Host '模型不进 git（.gitignore 忽略），换机器开发要重新跑一次这个脚本。'
