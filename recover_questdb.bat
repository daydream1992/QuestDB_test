@echo off
chcp 65001 >nul
cd /d K:\QuestDB\bin

echo ================================
echo   QuestDB 灾难恢复
echo   停库 → 启动 → 重建表注册
echo ================================
echo.

:: 1) 停已有进程
echo [1/4] 停已有 QuestDB 进程...
wmic process where "name='java.exe'" get processid /value 2>nul | findstr "ProcessId" >nul && (
    wmic process where "name='java.exe' and commandline like '%%questdb%%'" delete >nul
    timeout /t 3 /nobreak >nul
)
echo   OK

:: 2) 启动
echo [2/4] 启动 QuestDB...
start "" questdb.exe
echo   等待 10 秒就绪...
timeout /t 10 /nobreak >nul
echo   OK

:: 3) 跑恢复脚本
echo [3/4] 重建表注册...
cd /d K:\QuestDB_test
python scripts/recover_tables.py
echo   OK

:: 4) 确认
echo [4/4] 确认...
python -c "
import sys, os
sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', closefd=False)
sys.path.insert(0, 'K:/QuestDB_test')
from lib.qdb import connect
import pandas as pd
con = connect()
tables = pd.read_sql_query(\"SELECT count(*) as n FROM tables() WHERE table_name LIKE 'qd_%%'\", con)
cnt = int(tables.iloc[0]['n'])
print(f'  qd_* 表数: {cnt}')
con.close()
"
echo.
if %ERRORLEVEL% EQU 0 (
    echo ================================
    echo   恢复完成
    echo   Web Console: http://127.0.0.1:9000
    echo   PG: 127.0.0.1:8812
    echo ================================
) else (
    echo !! 恢复失败，检查日志
)
pause
