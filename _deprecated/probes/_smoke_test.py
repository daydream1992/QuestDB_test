"""qd_01 烟雾测试: 只取 5 个代码, 验证入库链路"""
import sys, os, time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')

sys.path.insert(0, os.environ.get('TQCENTER_PATH', r'K:\txdlianghua\PYPlugins\sys'))
from tqcenter import tq
import psycopg2
from loguru import logger

sys.path.insert(0, str(Path(__file__).parent))
from qd_01_collect_88fields import (
    connect, stock_code_to_tdx, parse_row, copy_to_qdb, INSERT_COLUMNS
)

logger.add(Path(__file__).resolve().parent.parent / 'logs' / 'smoke_test.log')

# 测 5 个代表性代码
TEST_CODES = ['000001.SZ', '600519.SH', '300750.SZ', '880002.SH', '000688.SZ']

tq.initialize(os.path.abspath(__file__))
con = connect()
try:
    fetch_time = datetime.now()
    rows = []
    for code in TEST_CODES:
        try:
            tdx = stock_code_to_tdx(code)
            data = tq.get_more_info(tdx, field_list=[])
            if data:
                stock_type = 'stock' if not code.startswith('88') else 'sector'
                row = parse_row(code, stock_type, data, fetch_time)
                rows.append(row)
                print(f'  OK {code} ZAF={data.get("ZAF")} ZTPrice={data.get("ZTPrice")}')
            else:
                print(f'  ✗ {code} 返回空')
        except Exception as e:
            print(f'  ✗ {code} 异常: {e}')

    if rows:
        copy_to_qdb(con, rows)
        print(f'\n[OK] 写入 {len(rows)} 行到 qd_snapshots_full')

        # 读回验证
        cur = con.cursor()
        cur.execute("SELECT code, snapshot_time, Now, ZAF FROM qd_snapshots_full WHERE snapshot_time > dateadd('s', -10, now()) ORDER BY snapshot_time DESC LIMIT 10")
        print('\n[读回验证]')
        for r in cur.fetchall():
            print(f'  {r[0]:<12} {r[1]} 现价={r[2]} 涨跌幅={r[3]}')
        cur.execute("SELECT COUNT(*) FROM qd_snapshots_full")
        print(f'\n表总行数: {cur.fetchone()[0]}')
finally:
    tq.close()
    con.close()
