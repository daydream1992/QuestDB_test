@echo off
REM ============================================================
REM  v10.2 统一生产入口 — 盘前检查 + 启动面板 + 雷达
REM  用法: run_v10.2.bat [--push] [--skip-prepare] [--skip-panel]
REM  --push        真推飞书 (默认 dry-run 禁推)
REM  --skip-prepare 跳过盘前检查 (prepare_v10.2.ps1)
REM  --skip-panel   跳过启动面板 (默认带面板: 倒计时到9:15 + 依赖检查)
REM  默认: 带面板启动, 提前手工启动时倒计时, 9:15 自动进竞价
REM ============================================================
setlocal enabledelayedexpansion
cd /d %~dp0

echo [%date% %time%] === v10.2 启动 ===

REM 1. 解析参数, 过滤 skip 项, 只透传有效参数给 radar_main
set SKIP_PREPARE=0
set SKIP_PANEL=0
set ARGS=
for %%a in (%*) do (
    if "%%a"=="--skip-prepare" ( set SKIP_PREPARE=1
    ) else if "%%a"=="--skip-panel" ( set SKIP_PANEL=1
    ) else ( set "ARGS=!ARGS! %%a" )
)

REM 2. 盘前检查
if "%SKIP_PREPARE%"=="1" (
    echo [SKIP] 跳过盘前检查
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "prepare_v10.2.ps1"
    if errorlevel 1 (
        echo [ERR] 盘前检查失败, 请修复后再启动
        exit /b 1
    )
)

REM 3. 启动面板 + 雷达 (默认带 --panel; --skip-panel 则不带)
set PANEL_FLAG=
if not "%SKIP_PANEL%"=="1" set PANEL_FLAG=--panel
echo [%date% %time%] 启动 radar_main.py %ARGS% %PANEL_FLAG%
python testv10.2\radar_main.py %ARGS% %PANEL_FLAG%

echo [%date% %time%] radar 退出 (exitcode=%errorlevel%)
exit /b %errorlevel%
