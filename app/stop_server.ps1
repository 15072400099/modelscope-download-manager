# 关闭「模搭 · 模型下载管理器」
# 1) 先请求服务自己优雅退出（会保存任务记录）
# 2) 再兜底清掉本目录残留的 python 进程
# 3) 最后关掉之前留下的启动黑窗口
# 只处理本目录里的进程，不会碰别的 Python 程序。

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$pythonDir = (Join-Path $root 'python').ToLower()

$port = 8765
$settingsPath = Join-Path $root 'data\settings.json'
if (Test-Path $settingsPath) {
    try { $port = [int]((Get-Content $settingsPath -Raw | ConvertFrom-Json).port) } catch { }
}
$ports = @($port) + (8766..8776) | Select-Object -Unique

Write-Host ''
Write-Host ' 正在关闭「模型下载管理器」...' -ForegroundColor Cyan

# ---- 1) 通知服务退出 -------------------------------------------------
$notified = $false
foreach ($p in $ports) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$p/api/shutdown" -Method Post -TimeoutSec 2 -UseBasicParsing | Out-Null
        Write-Host "   已通知正在运行的服务（端口 $p）保存并退出" -ForegroundColor Gray
        $notified = $true
        Start-Sleep -Milliseconds 1300
        break
    } catch { }
}
if (-not $notified) {
    Write-Host '   没有发现正在运行的服务（可能已经关了）' -ForegroundColor Gray
}

# ---- 2) 兜底：只杀本目录的 server.py ---------------------------------
$killed = 0
foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue)) {
    $exe = "$($proc.ExecutablePath)"
    $cmd = "$($proc.CommandLine)"
    if ($exe -and $exe.ToLower().StartsWith($pythonDir) -and $cmd -match 'server\.py') {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
            $killed++
        } catch { }
    }
}
if ($killed -gt 0) {
    Write-Host "   已强制结束 $killed 个残留进程" -ForegroundColor Gray
    Start-Sleep -Milliseconds 600
}

# ---- 3) 关掉之前留下的启动窗口 ---------------------------------------
$closedWindows = 0
foreach ($proc in @(Get-CimInstance Win32_Process -Filter "Name='cmd.exe'" -ErrorAction SilentlyContinue)) {
    $cmd = "$($proc.CommandLine)"
    if ($cmd -like '*启动下载管理器*') {
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
            $closedWindows++
        } catch { }
    }
}
if ($closedWindows -gt 0) {
    Write-Host "   已关掉 $closedWindows 个启动窗口" -ForegroundColor Gray
}

# ---- 4) 复核 ---------------------------------------------------------
Start-Sleep -Milliseconds 700
$alive = $null
foreach ($p in $ports) {
    try {
        Invoke-WebRequest -Uri "http://127.0.0.1:$p/api/ping" -TimeoutSec 1 -UseBasicParsing | Out-Null
        $alive = $p
        break
    } catch { }
}

Write-Host ''
if ($alive) {
    Write-Host " 还有服务在端口 $alive 上运行，可能不是这个目录启动的。" -ForegroundColor Yellow
    Write-Host ' 可以在任务管理器里手动结束它。' -ForegroundColor Yellow
} else {
    Write-Host ' 已关闭，可以放心关掉这个窗口了。' -ForegroundColor Green
    Write-Host ' 下载记录都已经保存，下次启动能看到原来的任务。' -ForegroundColor Gray
}
Write-Host ''
