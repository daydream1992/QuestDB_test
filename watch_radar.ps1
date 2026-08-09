# v10.2 进程守护 — 盘中雷达崩溃自动重启 + 心跳新鲜度监控
# 用法: powershell -ExecutionPolicy Bypass -File watch_radar.ps1 [--push]
# PS5.1 兼容 (UTF-8 with BOM 保存)
# 逻辑: 30s 轮询, ①radar_main 进程不在 → 重启 (生产 --push) ②心跳文件 mtime >90s → 假死告警
# 可选: 用 Windows 任务计划 9:05 启动, 盘中无人值守

param([switch]$Push, [switch]$Restart)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$heartbeat = Join-Path $root "logs\heartbeats\radar_main.ts"
$log = Join-Path $root "logs\watch_radar.log"
$lastRestart = Get-Date

function Log($msg) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] $msg"
    Write-Host $line -ForegroundColor Cyan
    Add-Content -Path $log -Value $line -Encoding UTF8
}

Log "watch_radar 启动 (push=$Push restart=$Restart)"

while ($true) {
    $proc = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'radar_main\.py' }

    if ($null -eq $proc) {
        # 进程不在
        $inTrading = $true
        $now = Get-Date
        $t = $now.ToString('HH:mm')
        if (($t -lt '09:15') -or ($t -ge '15:10')) { $inTrading = $false }
        if ($inTrading -and $Restart -and (($now - $lastRestart).TotalMinutes -gt 1)) {
            Log "⚠️ 盘中 radar 进程消失, 自动重启..."
            Start-Process -FilePath "python" -ArgumentList "testv10.2\radar_main.py","--push" -WorkingDirectory $root
            $lastRestart = Get-Date
            Start-Sleep -Seconds 10
        } else {
            Log "radar 进程不在 (非重启模式/非盘中/1min 内刚重启)"
        }
    } else {
        # 进程在: 查心跳新鲜度 (假死检测)
        if (Test-Path $heartbeat) {
            $hbAge = (Get-Date) - (Get-Item $heartbeat).LastWriteTime
            if ($hbAge.TotalSeconds -gt 90) {
                Log "⚠️ 心跳过期 ${hbAge} (雷达可能假死, 检查 logs/testv10.2_radar_*.log)"
            }
        } else {
            Log "⚠️ 心跳文件不存在 (雷达可能没写, 检查 logs/heartbeats/)"
        }
    }
    Start-Sleep -Seconds 30
}
