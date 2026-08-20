# 把打包好的「打印助手」装到店铺电脑上。一般由 安装到店铺电脑.bat 双击调起。
#
# 做四件事（docs/07-打包与部署.md）：
#   1. 拷到 C:\ShopPrint\（旧版本先改名留着，方便回滚）
#   2. 公共桌面放「打印助手」快捷方式
#   3. 建默认工作区 C:\打印\待打印\，公共桌面放它的快捷方式（工作区路径在设置里可改）
#   4. -AutoStart 时写开机自启
#
# 桌面上**只留这两个图标**。多一个都会让长辈犹豫该点哪个。


[CmdletBinding()]
param(
    [string]$Source = '',
    [string]$Target = 'C:\ShopPrint',
    [switch]$AutoStart
)

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
$AppName = '打印助手'
# 默认工作区（= paths.WORKSPACE_DIR）。装好之后店主可以在设置里改到别的盘
$WorkspaceDir = 'C:\打印\待打印'

function 提示($text) { Write-Host $text -ForegroundColor Cyan }
function 完成($text) { Write-Host "  √ $text" -ForegroundColor Green }

# ── 需要管理员：要往 C:\ 根目录写，还要写公共桌面 ──────────────
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    提示 '需要管理员权限，正在重新以管理员身份启动…'
    $argumentList = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
    if ($Source) { $argumentList += @('-Source', "`"$Source`"") }
    if ($AutoStart) { $argumentList += '-AutoStart' }
    Start-Process powershell -Verb RunAs -ArgumentList $argumentList
    return
}

# ── 找程序文件夹：优先同目录（U 盘拷过来），其次仓库里的 dist ──
if (-not $Source) {
    $candidates = @(
        (Join-Path $PSScriptRoot $AppName),
        (Join-Path (Split-Path -Parent $PSScriptRoot) "dist\$AppName")
    )
    $Source = $candidates | Where-Object { Test-Path (Join-Path $_ "$AppName.exe") } | Select-Object -First 1
}
if (-not $Source -or -not (Test-Path (Join-Path $Source "$AppName.exe"))) {
    Write-Host ''
    Write-Host "没找到程序文件夹（里面应该有 $AppName.exe）。" -ForegroundColor Red
    Write-Host "把打包出来的「$AppName」文件夹放到这个脚本旁边再双击一次。" -ForegroundColor Red
    exit 1
}
$Source = (Resolve-Path $Source).Path
提示 "程序来源：$Source"

# ── 1. 拷文件（旧版本改名留着，回滚就是换回来）─────────────────
if (Test-Path $Target) {
    $backup = "$Target.bak"
    if (Test-Path $backup) { Remove-Item -Recurse -Force $backup }
    Move-Item $Target $backup
    完成 "旧版本已改名留着：$backup（确认新版没问题后可以删）"
}
New-Item -ItemType Directory -Force -Path $Target | Out-Null
Copy-Item -Path (Join-Path $Source '*') -Destination $Target -Recurse -Force
完成 "已装到 $Target"

$exe = Join-Path $Target "$AppName.exe"

# ── 2 & 3. 公共桌面快捷方式 ────────────────────────────────────
# 用公共桌面而不是当前用户桌面：装的时候可能是管理员账号，
# 平时用的是另一个账号 —— 图标必须出现在长辈真正用的那个桌面上。
$desktop = Join-Path $env:PUBLIC 'Desktop'
$shell = New-Object -ComObject WScript.Shell

$appLink = $shell.CreateShortcut((Join-Path $desktop "$AppName.lnk"))
$appLink.TargetPath = $exe
$appLink.WorkingDirectory = $Target
$appLink.Description = '打印顾客发来的文件'
$icon = Join-Path $Target '_internal\assets\icons\app.ico'
if (Test-Path $icon) { $appLink.IconLocation = $icon }
$appLink.Save()
完成 "桌面图标：$AppName"

New-Item -ItemType Directory -Force -Path $WorkspaceDir | Out-Null
$workspaceLink = $shell.CreateShortcut((Join-Path $desktop '待打印.lnk'))
$workspaceLink.TargetPath = $WorkspaceDir
$workspaceLink.Description = '微信里的文件另存到这里，打印助手会自动看到'
$workspaceLink.Save()
完成 "工作区文件夹：$WorkspaceDir（桌面也有图标）"

# ── 4. 开机自启（可选）────────────────────────────────────────
$startup = Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu\Programs\StartUp'
$startupLink = Join-Path $startup "$AppName.lnk"
if ($AutoStart) {
    New-Item -ItemType Directory -Force -Path $startup | Out-Null
    $link = $shell.CreateShortcut($startupLink)
    $link.TargetPath = $exe
    $link.WorkingDirectory = $Target
    $link.Save()
    完成 '已设置开机自动启动'
} elseif (Test-Path $startupLink) {
    Remove-Item $startupLink -Force
    完成 '已取消开机自动启动（要开机自启就加 -AutoStart 再跑一次）'
}

Write-Host ''
Write-Host '装好了。现在在这台机器上逐项试一遍：' -ForegroundColor Green
Write-Host '  1. 双击桌面「打印助手」，看能不能起来（第一次可能要等十几秒）'
Write-Host '  2. 首页「打印文档」选一个文件，确认打印机列表里有柯美 225i'
Write-Host '  3. 拍一张纸质文件，走「照片变清楚再打印」实打一张，看背景干不干净'
Write-Host '  4. Excel 宽表打一次，确认没被切成几十页'
Write-Host '  5. 微信发个文件过来，看首页「微信收到的文件」几秒内出不出现'
Write-Host ''
Write-Host '完整清单见 docs/07-打包与部署.md 的「交付前的检查清单」。'
