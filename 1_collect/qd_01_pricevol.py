"""qd_01: 全市场批量价量 (get_pricevol 1 次拉全市场, 3 字段)

- 输入: stock_list (含股票 + 板块 88xxx)
- 输出: dict{code: {LastClose, Now, Volume}}
- 1 次调用拿到全场数据
- 写 qd_pricevol (新表)
- 飞书汇报
"""
import os, sys, time
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

QDB_HOST = 'localhost'
QDB_PORT = 8812
QDB_USER = 'admin'
QDB_PASS = 'quest'


def to_tdx(code: str) -> str:
    if code.endswith('.SH') or code.endswith('.SZ'): return code
    if code.startswith(('88', '9')): return code
    return f'{code}.SH' if code.startswith('6') else f'{code}.SZ'


def fetch_all_codes() -> list:
    """拿全市场股票+板块 (tdx 格式)"""
    sectors = tq.get_sector_list() or []
    all_set = set()
    for s in sectors:
        cs = tq.get_stock_list_in_sector(s) or []
        all_set.update(cs)
    all_set.update(sectors)
    return [to_tdx(c) for c in sorted(all_set)]


def run(limit: int = None):
    logger.info('▶ qd_01 全市场批量价量 (get_pricevol)')
    con = psycopg2.connect(host=QDB_HOST, port=QDB_PORT, user=QDB_USER, password=QDB_PASS, dbname='qdb')
    con.autocommit = True

    codes = fetch_all_codes()
    if limit:
        codes = codes[:limit]
    logger.info(f'目标 {len(codes)} 只 (1 次 API 调用)')
    lark_push(f'[qd_01] 开始批量价量 {len(codes)} 只')

    # 1 次 API 调用拿全部
    t0 = time.time()
    try:
        d = tq.get_pricevol(stock_list=codes)
    except Exception as e:
        logger.error(f'get_pricevol 失败: {e}')
        lark_push(f'[qd_01] get_pricevol 失败: {e}')
        con.close()
        tq.close()
        return
    fetch_t = time.time() - t0
    logger.info(f'API 耗时 {fetch_t:.2f}s, 返回 {len(d)} 只')
    if not d:
        lark_push(f'[qd_01] 返回空, 跳过')
        con.close()
        tq.close()
        return

    # 解析 → rows
    ts = datetime.now()
    rows = []
    for code, v in d.items():
        try:
            lc = float(v.get('LastClose', 0) or 0)
            nw = float(v.get('Now', 0) or 0)
            vol = int(float(v.get('Volume', 0) or 0))
            rows.append((code, ts, lc, nw, vol))
        except Exception as e:
            logger.debug(f'{code} 解析失败: {e}')

    logger.info(f'解析后 {len(rows)} 行')

    # executemany 写表
    sql = 'INSERT INTO qd_pricevol (code, snapshot_time, last_close, now, volume) VALUES (%s, %s, %s, %s, %s)'
    cur = con.cursor()
    BATCH = 500
    t1 = time.time()
    for i in range(0, len(rows), BATCH):
        cur.executemany(sql, rows[i:i+BATCH])
    write_t = time.time() - t1
    logger.info(f'写入完成: {len(rows)} 行, 耗时 {write_t:.2f}s')

    # 验证
    cur.execute("SELECT COUNT(*), COUNT(DISTINCT code) FROM qd_pricevol WHERE snapshot_time = %s", (ts,))
    total, uniq = cur.fetchone()
    logger.info(f'表内 {ts}  行数: {total}  唯一 code: {uniq}')

    con.close()
    tq.close()
    lark_push(f'[qd_01] 完成 {uniq}/{len(codes)} 只, API {fetch_t:.1f}s + 写 {write_t:.1f}s, 共 {total} 行')


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--limit', type=int, default=None)
    args = ap.parse_args()
    run(limit=args.limit)
