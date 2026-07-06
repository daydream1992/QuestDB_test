"""c2: 3 类标的盘中高频快照采集

脚本路径: K:\QuestDB_test\\collect\\c2_snapshot.py
用途: 逐个 get_market_snapshot 采集快照, 按 route_type 分流到 3 张表
数据源: tqcenter get_market_snapshot(stock_code, field_list=[])
入库表:
  - qd_stock_snapshot  (个股, 43 列含 5 档买卖盘)
  - qd_sector_snapshot (板块, 23 列无 5 档)
  - qd_index_snapshot  (指数, 13 列无 5 档/内外盘)
频率: focus 全采 + all_codes 全量 (非轮换, runner 层面控频)
字段映射来源: config/fields.py 的 STOCK_SNAPSHOT_FIELDS / SECTOR_SNAPSHOT_FIELDS
5 档映射:
  Buyp[5]  → Buyp1..Buyp5
  Buyv[5]  → Buyv1..Buyv5
  Sellp[5] → Sellp1..Sellp5
  Sellv[5] → Sellv1..Sellv5

说明:
  - 入库 code 用标准代码 ('000001.SZ'), 与 qd_code_registry / route_type 一致
  - get_market_snapshot 直接传标准代码 (实测 tqcenter 不接受 tdx 格式 '0#000001')
  - 返回值为字符串, 解析时不强制转换 (psycopg2 写入 QuestDB 会自动转 DOUBLE/BIGINT)
  - 板块/指数不存 5 档 (无意义), 指数额外去掉内外盘/内外标志等字段
  - tqcenter COM 单进程串行, 用 safe_call 包装
"""

import os
import sys
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call, init, close  # noqa: E402
from lib.tq_utils import to_tdx, route_type, fetch_all_codes  # noqa: E402
from lib.qdb import connect, executemany_batch  # noqa: E402

from tqcenter import tq  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c2_snapshot_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# === qd_stock_snapshot 列顺序 (与 DDL 02_snapshot.sql 严格一致) ===
STOCK_TABLE_COLS = [
    'code', 'snapshot_time',
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'NowVol', 'Amount', 'Inside', 'Outside',
    'TickDiff', 'InOutFlag', 'Jjjz', 'Average', 'XsFlag',
    'UpHome', 'DownHome', 'Before5MinNow', 'Zangsu', 'ZAFPre3',
    'Buyp1', 'Buyp2', 'Buyp3', 'Buyp4', 'Buyp5',
    'Buyv1', 'Buyv2', 'Buyv3', 'Buyv4', 'Buyv5',
    'Sellp1', 'Sellp2', 'Sellp3', 'Sellp4', 'Sellp5',
    'Sellv1', 'Sellv2', 'Sellv3', 'Sellv4', 'Sellv5',
]
# 个股普通字段 (21 个, 无 5 档)
_STOCK_PLAIN = [
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'NowVol', 'Amount', 'Inside', 'Outside',
    'TickDiff', 'InOutFlag', 'Jjjz', 'Average', 'XsFlag',
    'UpHome', 'DownHome', 'Before5MinNow', 'Zangsu', 'ZAFPre3',
]

# === qd_sector_snapshot 列顺序 (23 列, 无 5 档, Jjjz 在末尾) ===
SECTOR_TABLE_COLS = [
    'code', 'snapshot_time',
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'NowVol', 'Amount', 'Inside', 'Outside',
    'TickDiff', 'InOutFlag', 'Average', 'XsFlag',
    'UpHome', 'DownHome', 'Before5MinNow', 'Zangsu', 'ZAFPre3', 'Jjjz',
]
_SECTOR_PLAIN = SECTOR_TABLE_COLS[2:]  # 21 字段

# === qd_index_snapshot 列顺序 (13 列) ===
INDEX_TABLE_COLS = [
    'code', 'snapshot_time',
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'Amount', 'Average', 'TickDiff', 'ZAFPre3',
]
_INDEX_PLAIN = INDEX_TABLE_COLS[2:]  # 11 字段


def _expand_5levels(data, prefix):
    """把 data[prefix] (list[5]) 展开为 5 个值

    Args:
        data: get_market_snapshot 返回的 dict
        prefix: 'Buyp' / 'Buyv' / 'Sellp' / 'Sellv'

    Returns:
        list: 5 个值, 不足补 None
    """
    arr = data.get(prefix) or []
    out = []
    for i in range(5):
        out.append(arr[i] if i < len(arr) else None)
    return out


def parse_snapshot(code, data, snapshot_time, code_type):
    """解析 get_market_snapshot 返回 → row (按 code_type 选择列集)

    Args:
        code: 标准代码
        data: get_market_snapshot 返回 dict
        snapshot_time: 采集时刻 datetime
        code_type: 'stock' / 'sector' / 'index'

    Returns:
        tuple: 与对应表列顺序一致
    """
    if not data:
        return None

    if code_type == 'stock':
        plain = [data.get(f) for f in _STOCK_PLAIN]
        buyp = _expand_5levels(data, 'Buyp')
        buyv = _expand_5levels(data, 'Buyv')
        sellp = _expand_5levels(data, 'Sellp')
        sellv = _expand_5levels(data, 'Sellv')
        return (code, snapshot_time, *plain,
                *buyp, *buyv, *sellp, *sellv)
    elif code_type == 'sector':
        plain = [data.get(f) for f in _SECTOR_PLAIN]
        return (code, snapshot_time, *plain)
    else:  # index
        plain = [data.get(f) for f in _INDEX_PLAIN]
        return (code, snapshot_time, *plain)


def _fetch_one(code):
    """调用 get_market_snapshot 单只采集 (直接用标准代码, tqcenter 不接受 tdx 格式)"""
    return safe_call(tq.get_market_snapshot, stock_code=code, field_list=[])


def run(focus_codes=None, all_codes=None, con=None, limit=None):
    """快照采集主入口

    Args:
        focus_codes: 重点标的列表 (标准代码), 全部采集
        all_codes:   全场标的列表 (标准代码), 用于轮换; None 时自动 fetch_all_codes
        con: psycopg2 连接, None 则自建
        limit: 限制数量 (测试用)

    Returns:
        dict: {'stock': n, 'sector': n, 'index': n, 'error': n}
    """
    own_con = con is None
    if own_con:
        con = connect()

    try:
        # 合并待采集列表: focus 全采 + all_codes 轮换
        if all_codes is None:
            meta = fetch_all_codes()
            all_codes = [c['code'] for c in meta]

        todo = list(focus_codes or [])
        for c in all_codes:
            if c not in todo:
                todo.append(c)
        if limit:
            todo = todo[:limit]
            logger.info('测试模式: 仅取前 {} 只', limit)

        if not todo:
            logger.warning('无待采集代码, 退出')
            return {'stock': 0, 'sector': 0, 'index': 0, 'error': 0}

        logger.info('快照采集开始, 共 {} 只', len(todo))

        # 分桶
        stock_rows, sector_rows, index_rows = [], [], []
        err = 0
        snapshot_time = datetime.now()

        for code in todo:
            ctype = route_type(code)
            try:
                data = _fetch_one(code)
                row = parse_snapshot(code, data, snapshot_time, ctype)
                if row is None:
                    err += 1
                    continue
                if ctype == 'stock':
                    stock_rows.append(row)
                elif ctype == 'sector':
                    sector_rows.append(row)
                else:
                    index_rows.append(row)
            except Exception as e:
                err += 1
                logger.warning('快照采集失败 code={} type={}: {}', code, ctype, e)
                continue

        # 批量写入
        n_stock = executemany_batch(con, 'qd_stock_snapshot', STOCK_TABLE_COLS, stock_rows)
        n_sector = executemany_batch(con, 'qd_sector_snapshot', SECTOR_TABLE_COLS, sector_rows)
        n_index = executemany_batch(con, 'qd_index_snapshot', INDEX_TABLE_COLS, index_rows)

        logger.info('快照写入完成: stock={}, sector={}, index={}, error={} (ts={})',
                    n_stock, n_sector, n_index, err, snapshot_time)
        return {'stock': n_stock, 'sector': n_sector, 'index': n_index, 'error': err}
    finally:
        if own_con:
            con.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='c2 快照采集')
    parser.add_argument('--limit', type=int, default=None, help='限制采集数量 (测试用)')
    args = parser.parse_args()

    init()
    try:
        run(limit=args.limit)
    finally:
        close()


if __name__ == '__main__':
    main()
