# v10.2 盘前检查 (一键盘前准备) — 检查通达信/parquet/飞书/依赖
# 用法: powershell -ExecutionPolicy Bypass -File prepare_v10.2.ps1
# PS5.1 兼容 (UTF-8 with BOM 保存)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$pass = 0; $warn = 0; $fail = 0

function Check($name, $ok, $hint) {
    if ($ok) { Write-Host "  [OK] $name" -ForegroundColor Green; $script:pass++ }
    else { Write-Host "  [MISS] $name — $hint" -ForegroundColor Yellow; $script:warn++ }
}

Write-Host "=== v10.2 盘前检查 ===" -ForegroundColor Cyan

# 1. 通达信客户端进程
$tdx = Get-Process tdxw -ErrorAction SilentlyContinue
Check "通达信客户端 (tdxw.exe)" ($null -ne $tdx) "请打开通达信客户端 (COM 必须)"

# 2. sector_mapping.parquet 存在 + 新鲜度
$pq = Join-Path $root "testv10.2\sector_mapping.parquet"
if (Test-Path $pq) {
    $age = (Get-Date) - (Get-Item $pq).LastWriteTime
    if ($age.Days -gt 3) { Check "parquet 映射 (${age}天前, 建议刷新)" $false "跑 refresh_mapping.py 更新映射" }
    else { Check "parquet 映射 (${age}小时前)" $true "" }
} else {
    Check "parquet 映射" $false "跑 testv10.2/refresh_mapping.py 生成"
}

# 3. 飞书凭据
$envFile = Join-Path $root "config\.env"
$envMap = @{}
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)=(.*)$') { $envMap[$matches[1]] = $matches[2] }
    }
}
Check "LARK_WEBHOOK_URL" ($envMap.ContainsKey('LARK_WEBHOOK_URL') -and $envMap['LARK_WEBHOOK_URL']) "config/.env 配 webhook"
Check "LARK_APP_ID/SECRET" ($envMap.ContainsKey('LARK_APP_ID') -and $envMap.ContainsKey('LARK_APP_SECRET')) "config/.env 配飞书 app"
Check "TQCENTER_PATH" ($envMap.ContainsKey('TQCENTER_PATH') -and (Test-Path $envMap['TQCENTER_PATH'])) "config/.env 配 tqcenter 路径"

# 4. 依赖
python -c "import duckdb, pyarrow, openpyxl, pandas, loguru, requests" 2>$null
Check "依赖 (duckdb/pyarrow/openpyxl等)" ($LASTEXITCODE -eq 0) "pip install -r requirements.txt"

Write-Host ""
Write-Host "=== 检查完成: OK=$pass WARN=$warn ===" -ForegroundColor Cyan
if ($warn -eq 0) { Write-Host "✅ 可以启动: python testv10.2\radar_main.py --push" -ForegroundColor Green }
else { Write-Host "⚠️ 有 $warn 项未就绪, 修复后启动" -ForegroundColor Yellow }
