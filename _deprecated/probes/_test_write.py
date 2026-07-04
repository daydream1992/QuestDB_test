"""测 3 种 QuestDB 写入方式: COPY / executemany / ILP"""
import sys, os, io
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / '.env')
sys.path.insert(0, os.environ.get('TQCENTER_PATH', r'K:\txdlianghua\PYPlugins\sys'))
from tqcenter import tq
import psycopg2
import time

INSERT_COLUMNS = [
    'code', 'snapshot_time', 'HqDate', 'stock_type',
    'ZTPrice', 'DTPrice', 'FzAmo', 'ZAF', 'VOpenZAF',
    'ZAFYesterday', 'ZAFPre2D', 'ZAFPre5', 'ZAFPre10',
    'ZAFPre20', 'ZAFPre30', 'ZAFPre60', 'ZAFYear',
    'ZAFPreMyMonth', 'ZAFPreOneYear',
    'TotalBVol', 'TotalSVol', 'FCAmo', 'BCancel',
    'SCancel', 'L2TicNum', 'L2OrderNum',
    'OpenZAF', 'OpenAmo', 'OpenZTBuy', 'OpenAmoPre1', 'OpenVolPre1',
    'CJJEPre1', 'CJJEPre3', 'FDEPre1', 'FDEPre2',
    'fLianB', 'LastStartZT', 'LastZTHzNum', 'ZTGPNum',
    'EverZTCount', 'YearZTDay', 'ConZAFDateNum',
    'HisHigh', 'HisLow', 'IPO_Price', 'MainBusiness',
    'DynaPE', 'MorePE', 'StaticPE_TTM', 'DYRatio',
    'PB_MRQ', 'BetaValue', 'TPFlag', 'IsT0Fund',
    'IsZCZGP', 'IsKzz', 'Kzz_HSCode', 'QHMainYYMM',
    'Yield', 'FreeLtgb', 'vzangsu', 'Wtb',
    'fetch_time',
]
N = len(INSERT_COLUMNS)

conn = psycopg2.connect(host='127.0.0.1', port=8812, user='admin', password='quest', dbname='qdb')
conn.autocommit = True
cur = conn.cursor()

# 准备 1 行测试数据
tq.initialize(os.path.abspath(__file__))
data = tq.get_more_info('000001.SZ', field_list=[])
tq.close()

ft = datetime.now()
hq = datetime(2026, 7, 2).date()

def get_val(col):
    if col == 'code': return '000001.SZ'
    if col == 'snapshot_time': return ft
    if col == 'HqDate': return hq
    if col == 'stock_type': return 'stock'
    if col == 'fetch_time': return ft
    v = data.get(col)
    if v is None or v == '' or v == '--': return None
    return v

row = tuple(get_val(c) for c in INSERT_COLUMNS)
print(f'准备 1 行: 列数={N}')

# 方式 1: 单行 INSERT
print('\n--- 方式 1: 单行 INSERT ---')
placeholders = ','.join(['%s'] * N)
sql = f"INSERT INTO qd_snapshots_full ({','.join(INSERT_COLUMNS)}) VALUES ({placeholders})"
t0 = time.time()
try:
    cur.execute(sql, row)
    print(f'  OK ({time.time()-t0:.3f}s)')
except Exception as e:
    print(f'  ERR: {str(e)[:200]}')

# 验证
cur.execute("SELECT COUNT(*) FROM qd_snapshots_full")
print(f'  表行数: {cur.fetchone()[0]}')

# 方式 2: executemany
print('\n--- 方式 2: executemany (3行) ---')
rows2 = [row, row, row]
t0 = time.time()
try:
    cur.executemany(sql, rows2)
    print(f'  OK ({time.time()-t0:.3f}s)')
except Exception as e:
    print(f'  ERR: {str(e)[:200]}')

cur.execute("SELECT COUNT(*) FROM qd_snapshots_full")
print(f'  表行数: {cur.fetchone()[0]}')

# 方式 3: COPY FROM STDIN (纯 csv 数据, 不带 header)
print('\n--- 方式 3: COPY FROM STDIN ---')
buf = io.StringIO()
for r in [row, row]:
    cells = []
    for v in r:
        if v is None:
            cells.append('')
        elif isinstance(v, datetime):
            cells.append(v.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3])
        else:
            cells.append(str(v))
    buf.write(','.join(cells) + '\n')
buf.seek(0)
t0 = time.time()
try:
    cur.copy_expert(f"COPY qd_snapshots_full FROM STDIN", buf)
    print(f'  OK ({time.time()-t0:.3f}s)')
except Exception as e:
    print(f'  ERR: {str(e)[:200]}')

cur.execute("SELECT COUNT(*) FROM qd_snapshots_full")
print(f'  表行数: {cur.fetchone()[0]}')

# 读出
cur.execute("SELECT code, snapshot_time, ZAF FROM qd_snapshots_full LIMIT 5")
print('\n--- 读出 ---')
for r in cur.fetchall():
    print(' ', r)

conn.close()
