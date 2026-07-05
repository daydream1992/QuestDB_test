"""qd_00: 全市场实时快照 (get_market_snapshot 全字段 32 个)

- 全场 6005 只股票 (含板块 88xxx)
- 串行拉取 (tqcenter C++ COM 不支持并发, 30-50s/轮 估)
- executemany 批量写 qd_market_snapshot_full
- 飞书汇报 开始/完成/行数
"""
import os, sys, time, json
from datetime import datetime
from pathlib import Path

import psycopg2
from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, r'K:\txdlianghua\PYPlugins\sys')
from tqcenter import tq
tq.initialize(os.path.abspath(__file__))

import requests
LARK_WEBHOOK = 'https://open.feishu.cn/open-apis/bot/v2/hook/your-webhook-id'
def lark_push(text: str):
    try:
        requests.post(LARK_WEBHOOK, json={'msg_type': 'text', 'content': {'text': text}}, timeout=5)
    except Exception as e:
        logger.warning(f'飞书推送失败: {e}')

# === QuestDB 连接 ===
QDB_HOST = 'localhost'
QDB_PORT = 8812
QDB_USER = 'admin'
QDB_PASS = 'quest'

# === 列定义 (DDL 顺序) ===
SNAP_COLUMNS = [
    'code', 'snapshot_time',
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'NowVol', 'Amount', 'Inside', 'Outside', 'Average',
    'TickDiff', 'InOutFlag', 'Jjjz', 'XsFlag',
    'Buyp1', 'Buyp2', 'Buyp3', 'Buyp4', 'Buyp5',
    'Buyv1', 'Buyv2', 'Buyv3', 'Buyv4', 'Buyv5',
    'Sellp1', 'Sellp2', 'Sellp3', 'Sellp4', 'Sellp5',
    'Sellv1', 'Sellv2', 'Sellv3', 'Sellv4', 'Sellv5',
    'UpHome', 'DownHome', 'Before5MinNow', 'Zangsu',
    'ZAFPre3', 'ErrorId',
]

DOUBLE_COLS = {'LastClose', 'Open', 'Max', 'Min', 'Now', 'Amount', 'Average',
               'TickDiff', 'Jjjz', 'Before5MinNow', 'Zangsu', 'ZAFPre3',
               'Buyp1', 'Buyp2', 'Buyp3', 'Buyp4', 'Buyp5',
               'Sellp1', 'Sellp2', 'Sellp3', 'Sellp4', 'Sellp5'}
BIGINT_COLS = {'Volume', 'NowVol', 'Inside', 'Outside',
               'Buyv1', 'Buyv2', 'Buyv3', 'Buyv4', 'Buyv5',
               'Sellv1', 'Sellv2', 'Sellv3', 'Sellv4', 'Sellv5', 'ItemNum'}
INT_COLS = {'InOutFlag', 'XsFlag', 'UpHome', 'DownHome', 'ErrorId'}


def to_tdx(code: str) -> str:
    if code.endswith('.SH') or code.endswith('.SZ'): return code
    if code.startswith(('88', '9')): return code
    return f'{code}.SH' if code.startswith('6') else f'{code}.SZ'


def fetch_all_codes() -> list:
    sectors = tq.get_sector_list() or []
    all_set = set()
    for s in sectors:
        cs = tq.get_stock_list_in_sector(s) or []
        all_set.update(cs)
    all_set.update(sectors)  # 板块本身也入库
    return sorted(all_set)


def parse_snapshot(code: str, d: dict, ts: datetime) -> tuple:
    """dict → tuple (按 SNAP_COLUMNS 顺序)"""
    def num(col, default=0.0):
        v = d.get(col)
        if col in DOUBLE_COLS:
            try: return float(v) if v not in (None, '') else default
            except: return default
        if col in BIGINT_COLS:
            try: return int(float(v)) if v not in (None, '') else default
            except: return default
        if col in INT_COLS:
            try: return int(float(v)) if v not in (None, '') else default
            except: return default
        return v or ''

    buyp = d.get('Buyp') or []
    buyv = d.get('Buyv') or []
    sellp = d.get('Sellp') or []
    sellv = d.get('Sellv') or []

    def safe_lst(lst, i, caster):
        try: return caster(lst[i])
        except: return 0

    return (
        code, ts,
        num('ItemNum'), num('LastClose'), num('Open'), num('Max'), num('Min'), num('Now'),
        num('Volume'), num('NowVol'), num('Amount'), num('Inside'), num('Outside'), num('Average'),
        num('TickDiff'), num('InOutFlag'), num('Jjjz'), num('XsFlag'),
        # Buyp 1-5
        safe_lst(buyp, 0, float), safe_lst(buyp, 1, float), safe_lst(buyp, 2, float), safe_lst(buyp, 3, float), safe_lst(buyp, 4, float),
        # Buyv 1-5
        safe_lst(buyv, 0, int),   safe_lst(buyv, 1, int),   safe_lst(buyv, 2, int),   safe_lst(buyv, 3, int),   safe_lst(buyv, 4, int),
        # Sellp 1-5
        safe_lst(sellp, 0, float), safe_lst(sellp, 1, float), safe_lst(sellp, 2, float), safe_lst(sellp, 3, float), safe_lst(sellp, 4, float),
        # Sellv 1-5
        safe_lst(sellv, 0, int),   safe_lst(sellv, 1, int),   safe_lst(sellv, 2, int),   safe_lst(sellv, 3, int),   safe_lst(sellv, 4, int),
        num('UpHome'), num('DownHome'), num('Before5MinNow'), num('Zangsu'),
        num('ZAFPre3'), num('ErrorId'),
    )


def fetch_one(code: str, ts: datetime):
    """单只: 调 API + 解析, 失败返 None"""
    try:
        tdx = to_tdx(code)
        d = tq.get_market_snapshot(stock_code=tdx, field_list=[])
        if not d or d.get('ErrorId') not in (0, '0', None, ''):
            return None
        return parse_snapshot(code, d, ts)
    except Exception as e:
        logger.debug(f'{code} 失败: {e}')
        return None


def run(workers: int = 1, limit: int = None):
    """workers 参数保留但忽略 (tqcenter C++ COM 不支持并发, 强制串行)"""
    logger.info('▶ qd_00 全市场实时快照 (串行)')
    con = psycopg2.connect(host=QDB_HOST, port=QDB_PORT, user=QDB_USER, password=QDB_PASS, dbname='qdb')
    con.autocommit = True

    codes = fetch_all_codes()
    if limit:
        codes = codes[:limit]
    logger.info(f'目标 {len(codes)} 只')
    lark_push(f'[qd_00] 开始拉 {len(codes)} 只 (串行)')

    ts = datetime.now()
    rows = []
    t0 = time.time()
    ok = 0
    fail = 0

    # 串行: tqcenter C++ COM 不支持多线程
    for i, code in enumerate(codes, 1):
        r = fetch_one(code, ts)
        if r:
            rows.append(r)
            ok += 1
        else:
            fail += 1
        if i % 200 == 0:
            rate = i / (time.time() - t0)
            eta = (len(codes) - i) / rate if rate > 0 else 0
            logger.info(f'  进度 {i}/{len(codes)} ({rate:.0f}只/s)  ok={ok} fail={fail}  剩余 {eta:.0f}s')

    elapsed = time.time() - t0
    rate = len(codes) / elapsed if elapsed > 0 else 0
    logger.info(f'拉取完成: ok={ok} fail={fail}  耗时 {elapsed:.1f}s ({rate:.0f}只/s)')
    if not rows:
        lark_push(f'[qd_00] 拉取 0 条, 跳过入库')
        con.close()
        tq.close()
        return

    # 批量 executemany
    placeholders = ','.join(['%s'] * len(SNAP_COLUMNS))
    sql = f'INSERT INTO qd_market_snapshot_full ({",".join(SNAP_COLUMNS)}) VALUES ({placeholders})'
    cur = con.cursor()
    BATCH = 500
    t1 = time.time()
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i+BATCH]
        cur.executemany(sql, batch)
    logger.info(f'写入完成: {len(rows)} 行, 耗时 {time.time()-t1:.1f}s')

    # 验证
    cur.execute("SELECT COUNT(*) FROM qd_market_snapshot_full WHERE snapshot_time = %s", (ts,))
    cnt = cur.fetchone()[0]
    logger.info(f'表内 {ts} 行数: {cnt}')

    con.close()
    tq.close()
    lark_push(f'[qd_00] 完成 {ok}/{len(codes)} 只, 写入 {cnt} 行, 耗时 {elapsed:.0f}s (率 {rate:.0f}只/s)')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--workers', type=int, default=1, help='(忽略, tqcenter 串行)')
    ap.add_argument('--limit', type=int, default=None, help='限制只数 (测试用)')
    args = ap.parse_args()
    run(workers=args.workers, limit=args.limit)
