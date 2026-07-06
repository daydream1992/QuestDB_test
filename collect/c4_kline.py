"""c4: K 线直拉采集 (1m / 5m)

脚本路径: K:\QuestDB_test\\collect\\c4_kline.py
用途: 调 get_market_data 拉 K 线, 转长格式后写 qd_kline_1m / qd_kline_5m
数据源: tqcenter get_market_data(stock_list, period, count)
入库表:
  - qd_kline_1m (period='1m')
  - qd_kline_5m (period='5m')
频率: 60s/轮
字段映射:
  code       ← 标准代码 (DataFrame columns 即标准代码, 直接用)
  kline_time ← K 线周期时间戳 (DataFrame index)
  open       ← open
  high       ← high
  low        ← low
  close      ← close
  volume     ← volume
  amount     ← amount

说明:
  - get_market_data 返回 dict{字段名: DataFrame(index=time, columns=code)}
  - 传入标准代码 stock_list (实测 tqcenter 全系 API 只接受标准代码, 不接受 tdx 格式)
  - DataFrame columns 即标准代码, 入库 code 直接用, 无需反向映射
  - 字段名兼容大小写 (open/Open)
  - tqcenter COM 单进程串行, 用 safe_call 包装
"""

import os
import sys
from datetime import datetime

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call, init, close  # noqa: E402
from lib.tq_utils import fetch_all_codes  # noqa: E402
from lib.qdb import connect, executemany_batch  # noqa: E402

from tqcenter import tq  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c4_kline_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# period → 表名
PERIOD_TABLE = {
    '1m': 'qd_kline_1m',
    '5m': 'qd_kline_5m',
}

KLINE_COLS = ['code', 'kline_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Amount']

# 字段名候选 (兼容大小写)
_FIELD_CANDIDATES = {
    'open':   ['open', 'Open', 'OPEN'],
    'high':   ['high', 'High', 'HIGH'],
    'low':    ['low', 'Low', 'LOW'],
    'close':  ['close', 'Close', 'CLOSE'],
    'volume': ['volume', 'Volume', 'VOLUME'],
    'amount': ['amount', 'Amount', 'AMOUNT'],
    'Open':   ['Open', 'open', 'OPEN'],
    'High':   ['High', 'high', 'HIGH'],
    'Low':    ['Low', 'low', 'LOW'],
    'Close':  ['Close', 'close', 'CLOSE'],
    'Volume': ['Volume', 'volume', 'VOLUME'],
    'Amount': ['Amount', 'amount', 'AMOUNT'],
}


def _pick_field(data, key):
    """从 dict 中按候选名取 DataFrame"""
    for name in _FIELD_CANDIDATES[key]:
        df = data.get(name)
        if df is not None:
            return df
    return None


def parse_kline(data):
    """解析 get_market_data 返回 → rows

    Args:
        data: dict{字段名: DataFrame(index=time, columns=标准code)}

    Returns:
        list[tuple]: (code, kline_time, open, high, low, close, volume, amount)
    """
    if not data:
        return []

    # 收集各字段的 DataFrame, 转 long format, 按 (time, code) 合并
    long_frames = []
    for key in ('open', 'high', 'low', 'close', 'volume', 'amount'):
        df = _pick_field(data, key)
        if df is None or df.empty:
            continue
        # df: index=time, columns=标准code
        stacked = df.stack().reset_index()
        # 列名兜底: 通达信可能返回 'time'/'code' 或 'index'/'level_1'
        stacked.columns = ['kline_time', 'code', key]
        long_frames.append((key, stacked))

    if not long_frames:
        return []

    # 以第一个为基准, 逐个 left merge
    _, merged = long_frames[0]
    for key, frame in long_frames[1:]:
        merged = merged.merge(frame, on=['kline_time', 'code'], how='outer')

    rows = []
    for _, r in merged.iterrows():
        rows.append((
            r['code'],
            r['kline_time'],
            r.get('open'),
            r.get('high'),
            r.get('low'),
            r.get('close'),
            r.get('volume'),
            r.get('amount'),
        ))
    return rows


def run(codes, period='1m', count=1, con=None):
    """K 线采集主入口

    Args:
        codes:  待采集代码列表 (标准代码)
        period: '1m' / '5m'
        count:  K 线根数
        con:    psycopg2 连接, None 则自建

    Returns:
        int: 写入行数
    """
    if period not in PERIOD_TABLE:
        raise ValueError("period 必须是 {} 之一, 实际: {}".format(list(PERIOD_TABLE), period))

    own_con = con is None
    if own_con:
        con = connect()

    try:
        if not codes:
            logger.warning('codes 为空, 退出')
            return 0

        logger.info('K 线采集 period={} count={} 共 {} 只', period, count, len(codes))

        # 调用 get_market_data (直接传标准代码, tqcenter 不接受 tdx 格式)
        data = safe_call(tq.get_market_data, stock_list=codes, period=period, count=count)
        if not data:
            logger.warning('get_market_data 返回空')
            return 0

        # 解析 → rows
        rows = parse_kline(data)
        logger.info('解析得到 {} 行 K 线', len(rows))
        if not rows:
            return 0

        # 写入对应表
        table = PERIOD_TABLE[period]
        n = executemany_batch(con, table, KLINE_COLS, rows)
        logger.info('写入 {}: {} 行', table, n)
        return n
    finally:
        if own_con:
            con.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='c4 K 线采集')
    parser.add_argument('--period', choices=['1m', '5m'], default='1m', help='K 线周期')
    parser.add_argument('--count', type=int, default=1, help='K 线根数')
    parser.add_argument('--limit', type=int, default=None, help='限制采集数量 (测试用)')
    args = parser.parse_args()

    init()
    try:
        meta = fetch_all_codes()
        codes = [c['code'] for c in meta if c.get('tdx_code')]
        if args.limit:
            codes = codes[:args.limit]
        run(codes, period=args.period, count=args.count)
    finally:
        close()


if __name__ == '__main__':
    main()
