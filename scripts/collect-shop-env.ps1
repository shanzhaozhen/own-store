# 采集店铺电脑的环境信息。**去店铺之前先让父母或自己在店铺机上跑一次**，
# 把结果发回来，据此定默认配置（尤其是柯美 225i 的准确名称和微信目录）。
# 一般由 采集店铺环境.bat 双击调起。不需要管理员，不改任何东西，只读。
#
# 产出（桌面「店铺环境」文件夹）：
#   店铺环境.txt   —— 人看的
#   店铺环境.json  —— 直接喂给代码用的
#
# 为什么要这些：见 docs/01-环境与设备.md 里"待现场确认"的那几项。

[CmdletBinding()]
param([string]$OutDir = '')

$ErrorActionPreference = 'Continue'
if (-not $OutDir) { $OutDir = Join-Path ([Environment]::GetFolderPath('Desktop')) '店铺环境' }
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

$report = [ordered]@{}
$lines = [System.Collections.Generic.List[string]]::new()
function 记录($text) { $lines.Add($text); Write-Host $text }
function 小节($title) { 记录 ''; 记录 "── $title ──────────────────────────" }

function 安全取($name, [scriptblock]$block) {
    try { & $block } catch { 记录 "  （$name 读取失败：$($_.Exception.Message)）"; $null }
}

function 注册表值($key, $name) {
    # 注册表项不存在是常态（没装 WPS、微信版本不同），不该刷一行英文报错出来
    if (-not (Test-Path $key)) { return $null }
    $item = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
    if (-not $item) { return $null }
    $value = $item.PSObject.Properties[$name]
    if (-not $value) { return $null }
    return $value.Value
}

记录 "店铺电脑环境采集　$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
记录 "计算机名：$env:COMPUTERNAME　当前用户：$env:USERNAME"

# ── 系统 ───────────────────────────────────────────────────────
小节 '系统'
$os = 安全取 '系统信息' { Get-CimInstance Win32_OperatingSystem }
if ($os) {
    记录 "  $($os.Caption)　版本 $($os.Version)　构建 $($os.BuildNumber)　$($os.OSArchitecture)"
    记录 ("  内存：{0:N1} GB" -f ($os.TotalVisibleMemorySize / 1MB))
    $report.os = @{
        caption = $os.Caption; version = $os.Version
        build = $os.BuildNumber; arch = $os.OSArchitecture
        memory_gb = [math]::Round($os.TotalVisibleMemorySize / 1MB, 1)
    }
    # PySide6 6.11 要 Win10 1809（build 17763）以上
    if ([int]$os.BuildNumber -lt 17763) {
        记录 '  ⚠ 这个 Windows 版本太老（低于 Win10 1809），界面库可能起不来 —— 一定要告诉开发者'
    }
}
$cpu = 安全取 'CPU' { Get-CimInstance Win32_Processor | Select-Object -First 1 }
if ($cpu) {
    记录 "  CPU：$($cpu.Name.Trim())　$($cpu.NumberOfCores) 核 $($cpu.NumberOfLogicalProcessors) 线程"
    $report.cpu = @{ name = $cpu.Name.Trim(); cores = $cpu.NumberOfCores; threads = $cpu.NumberOfLogicalProcessors }
}
$disk = 安全取 'C 盘' { Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='C:'" }
if ($disk) {
    记录 ("  C 盘剩余：{0:N1} GB / 共 {1:N1} GB（程序约要 500 MB）" -f ($disk.FreeSpace / 1GB), ($disk.Size / 1GB))
    $report.disk_free_gb = [math]::Round($disk.FreeSpace / 1GB, 1)
}

# ── 屏幕 ───────────────────────────────────────────────────────
小节 '屏幕（界面布局要按这个来）'
$screens = 安全取 '分辨率' {
    Get-CimInstance Win32_VideoController |
        Where-Object { $_.CurrentHorizontalResolution } |
        Select-Object Name, CurrentHorizontalResolution, CurrentVerticalResolution
}
$report.screens = @()
foreach ($s in @($screens)) {
    记录 "  $($s.CurrentHorizontalResolution) × $($s.CurrentVerticalResolution)　（$($s.Name)）"
    $report.screens += @{ width = $s.CurrentHorizontalResolution; height = $s.CurrentVerticalResolution; adapter = $s.Name }
    if ([int]$s.CurrentVerticalResolution -lt 720) {
        记录 '  ⚠ 竖向像素偏少，界面要专门压一版 —— 告诉开发者'
    }
}
$dpi = 安全取 '缩放比例' {
    (Get-ItemProperty 'HKCU:\Control Panel\Desktop\WindowMetrics' -Name AppliedDPI -ErrorAction Stop).AppliedDPI
}
if ($dpi) {
    记录 "  显示缩放：$([math]::Round($dpi / 96 * 100))%　（AppliedDPI=$dpi）"
    $report.dpi = $dpi
}

# ── 打印机（最关键）────────────────────────────────────────────
小节 '打印机（把柯美 225i 那一行的名字原样抄给开发者）'
$printers = 安全取 '打印机列表' { Get-Printer | Select-Object Name, DriverName, PortName, PrinterStatus, Shared }
if (-not $printers) {
    $printers = 安全取 '打印机列表(WMI)' {
        Get-CimInstance Win32_Printer | Select-Object Name, DriverName, PortName, PrinterStatus, Shared
    }
}
$defaultPrinter = 安全取 '默认打印机' {
    (Get-CimInstance Win32_Printer -Filter 'Default = True' | Select-Object -First 1).Name
}
记录 "  默认打印机：$defaultPrinter"
$report.printers = @()
$report.default_printer = $defaultPrinter
foreach ($p in @($printers)) {
    $mark = if ($p.Name -eq $defaultPrinter) { '★' } else { ' ' }
    记录 "  $mark $($p.Name)"
    记录 "      驱动：$($p.DriverName)　端口：$($p.PortName)　状态：$($p.PrinterStatus)"
    $duplex = 安全取 '双面能力' {
        (Get-PrintConfiguration -PrinterName $p.Name -ErrorAction Stop).DuplexingMode
    }
    $color = 安全取 '彩色能力' {
        (Get-PrintConfiguration -PrinterName $p.Name -ErrorAction Stop).Color
    }
    if ($duplex -or $null -ne $color) { 记录 "      双面：$duplex　彩色：$color" }
    $report.printers += @{
        name = $p.Name; driver = $p.DriverName; port = $p.PortName
        status = "$($p.PrinterStatus)"; duplex = "$duplex"; color = "$color"
        is_default = ($p.Name -eq $defaultPrinter)
    }
}
if (-not @($printers | Where-Object { $_.Name -match '225|KONICA|柯美|bizhub' })) {
    记录 '  ⚠ 没看到名字里带 225 / KONICA / bizhub 的打印机 —— 确认打印机开着并且驱动装好了'
}

# ── 微信 ───────────────────────────────────────────────────────
小节 '微信（决定监控哪个目录）'
$documents = [Environment]::GetFolderPath('MyDocuments')
记录 "  文档目录：$documents"
$report.wechat = @{ documents = $documents; dirs = @(); version = $null; kind = $null; save_path = $null }

# 微信可以把接收目录改到别的盘。注册表里的 FileSavePath 就是用户设的那个，
# 值为 "MyDocument:" 表示还是默认的文档目录。
$saveRoots = [System.Collections.Generic.List[string]]::new()
$saveRoots.Add($documents)
foreach ($key in @('HKCU:\Software\Tencent\WeChat', 'HKCU:\Software\Tencent\Weixin')) {
    foreach ($name in @('FileSavePath', 'OldFileSavePath')) {
        $value = 注册表值 $key $name
        if (-not $value) { continue }
        记录 "  微信里设的保存位置（$name）：$value"
        if ($value -ne 'MyDocument:' -and (Test-Path $value)) {
            $saveRoots.Add($value)
            $report.wechat.save_path = "$value"
        }
    }
}

foreach ($root in ($saveRoots | Select-Object -Unique)) {
    foreach ($item in @(
            @{ kind = '3.x'; sub = 'WeChat Files'; leaf = 'FileStorage\File' },
            @{ kind = '4.x'; sub = 'xwechat_files'; leaf = 'msg\file' })) {
        $base = Join-Path $root $item.sub
        if (-not (Test-Path $base)) { continue }
        记录 "  找到 $($item.kind) 布局：$base"
        $report.wechat.kind = $item.kind
        foreach ($account in Get-ChildItem $base -Directory -ErrorAction SilentlyContinue) {
            if ($account.Name -in @('All Users', 'Applet', 'WMPF')) { continue }
            $dir = Join-Path $account.FullName $item.leaf
            if (-not (Test-Path $dir)) { continue }
            $recent = @(Get-ChildItem $dir -Recurse -File -ErrorAction SilentlyContinue |
                Where-Object { $_.LastWriteTime -gt (Get-Date).AddDays(-7) }).Count
            记录 "    $dir　（最近 7 天有 $recent 个文件）"
            $report.wechat.dirs += @{ path = "$dir"; recent_files = $recent; kind = $item.kind }
        }
    }
}
if (-not $report.wechat.dirs.Count) {
    记录 '  ⚠ 没找到微信接收文件的目录。请在微信里点「设置 → 文件管理」，把里面显示的路径抄下来'
}
foreach ($key in @('HKCU:\Software\Tencent\WeChat', 'HKCU:\Software\Tencent\Weixin')) {
    $install = 注册表值 $key 'InstallPath'
    if (-not $install) { continue }
    记录 "  安装位置：$install"
    foreach ($exe in @('WeChat.exe', 'Weixin.exe')) {
        $path = Join-Path $install $exe
        if (Test-Path $path) {
            $version = (Get-Item $path).VersionInfo.FileVersion
            记录 "  版本：$exe $version"
            $report.wechat.version = "$exe $version"
        }
    }
}

# ── Office ─────────────────────────────────────────────────────
小节 'Office（doc/xls 转 PDF 要用它）'
$report.office = @{ word = $null; excel = $null; wps = $null; click_to_run = $null }
foreach ($pair in @(@{ n = 'word'; k = 'winword.exe' }, @{ n = 'excel'; k = 'excel.exe' })) {
    $path = 注册表值 "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\$($pair.k)" '(default)'
    if ($path -and (Test-Path $path)) {
        $version = (Get-Item $path).VersionInfo.ProductVersion
        记录 "  $($pair.n)：$path　（$version）"
        $report.office[$pair.n] = @{ path = "$path"; version = "$version" }
    } else {
        记录 "  $($pair.n)：没找到"
    }
}
$ctr = 注册表值 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' 'VersionToReport'
if ($ctr) { 记录 "  Office 版本号：$ctr"; $report.office.click_to_run = "$ctr" }
$wps = 注册表值 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\wps.exe' '(default)'
if ($wps) {
    记录 "  另外装了 WPS：$wps"
    记录 '  ⚠ 如果这台机器只有 WPS 没有 Microsoft Office，转换要走另一套接口 —— 一定要告诉开发者'
    $report.office.wps = "$wps"
}

# ── 待打印文件夹 ───────────────────────────────────────────────
小节 '待打印文件夹'
$inbox = 'C:\打印\待打印'
if (Test-Path $inbox) {
    记录 "  已存在：$inbox"
} else {
    记录 "  还没有（装的时候会自动建）：$inbox"
}
$report.inbox_exists = (Test-Path $inbox)

# ── 落盘 ───────────────────────────────────────────────────────
$txtPath = Join-Path $OutDir '店铺环境.txt'
$jsonPath = Join-Path $OutDir '店铺环境.json'
Set-Content -Path $txtPath -Value ($lines -join "`r`n") -Encoding UTF8
Set-Content -Path $jsonPath -Value ($report | ConvertTo-Json -Depth 6) -Encoding UTF8

Write-Host ''
Write-Host '采集完了。把桌面「店铺环境」这个文件夹整个发给开发者就行：' -ForegroundColor Green
Write-Host "  $txtPath"
Write-Host "  $jsonPath"
