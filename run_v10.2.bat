@echo off
REM ============================================================
REM  v10.2 统一生产入口 — 盘前检查 + 启动雷达
REM  用法: run_v10.2.bat [--push] [--force] [--rounds N]
REM  --push 真推飞书; 无参数 = dry-run (守红线禁推)
REM ============================================================
setlocal
cd /d %~dp0

echo [%date% %time%] === v10.2 启动 ===

REM 1. 盘前检查 (可跳过: run_v10.2.bat --skip-prepare)
if "%1"=="--skip-prepare" (
    shift
    echo [SKIP] 跳过盘前检查
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "prepare_v10.2.ps1"
    if errorlevel 1 (
        echo [ERR] 盘前检查失败, 请修复后再启动
        exit /b 1
    )
)

REM 2. 启动雷达 (参数透传: --push/--force/--rounds)
echo [%date% %time%] 启动 radar_main.py %*
python testv10.2\radar_main.py %*

echo [%date% %time%] radar 退出 (exitcode=%errorlevel%)
exit /b %errorlevel%
