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
  Buyp[5]  -> Buyp1..Buyp5
  Buyv[5]  -> Buyv1..Buyv5
  Sellp[5] -> Sellp1..Sellp5
  Sellv[5] -> Sellv1..Sellv5

说明:
  - 入库 code 用标准代码 ('000001.SZ'), 与 qd_code_registry / route_type 一致
  - get_market_snapshot 直接传标准代码 (实测 tqcenter 不接受 tdx 格式 '0#000001')
  - 返回值为字符串, 解析时不强制转换 (psycopg2 写入 QuestDB 会自动转 DOUBLE/BIGINT)
  - 板块/指数不存 5 档 (无意义), 指数额外去掉内外盘/内外标志等字段
  - tqcenter COM 单进程串行, 用 safe_call 包装
  - C2 熔断机制: 连续 N 只失败则跳过剩余标的，防止停牌股卡死整轮
"""

import os
import sys
import threading
from datetime import datetime

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call, init  # noqa: E402
from lib.tq_utils import to_tdx, route_type, fetch_all_codes, classify_code, route_type_to_table  # noqa: E402
from lib.qdb import connect, executemany_batch  # noqa: E402

from tqcenter import tq  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c2_snapshot_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

# === 熔断配置 (C2) ===
# 单只连续失败阈值：超过后跳过剩余标的（防止停牌股/网络问题卡死整轮）
_CIRCUIT_BREAKER_THRESHOLD = 5

# === 分批写入配置 ===
# 每采集 _BATCH_WRITE_SIZE 只就写入一次，避免内存累积
_BATCH_WRITE_SIZE = 500

# === qd_stock_snapshot 列顺序 (与 DDL 02_snapshot.sql 严格一致) ===
STOCK_TABLE_COLS = [
    'code', 'code_type', 'snapshot_time',
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
    'code', 'code_type', 'snapshot_time',
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'NowVol', 'Amount', 'Inside', 'Outside',
    'TickDiff', 'InOutFlag', 'Average', 'XsFlag',
    'UpHome', 'DownHome', 'Before5MinNow', 'Zangsu', 'ZAFPre3', 'Jjjz',
]
_SECTOR_PLAIN = SECTOR_TABLE_COLS[3:]  # 21 字段 (跳过 code/code_type/snapshot_time)

# === qd_index_snapshot 列顺序 (13 列) ===
INDEX_TABLE_COLS = [
    'code', 'code_type', 'snapshot_time',
    'ItemNum', 'LastClose', 'Open', 'Max', 'Min', 'Now',
    'Volume', 'Amount', 'Average', 'TickDiff', 'ZAFPre3',
]
_INDEX_PLAIN = INDEX_TABLE_COLS[3:]  # 11 字段 (跳过 code/code_type/snapshot_time)

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

def parse_snapshot(code, data, snapshot_time, code_type, route_to):
    """解析 get_market_snapshot 返回 -> row (按 route_to 选择列集)

    Args:
        code: 标准代码
        data: get_market_snapshot 返回 dict
        snapshot_time: 采集时刻 datetime
        code_type: 标的类型 (classify_code 结果)
        route_to: 表路由 'stock' / 'sector' / 'index'

    Returns:
        tuple: 与对应表列顺序一致 (含 code_type)
    """
    if not data:
        return None

    if route_to == 'stock':
        plain = [data.get(f) for f in _STOCK_PLAIN]
        buyp = _expand_5levels(data, 'Buyp')
        buyv = _expand_5levels(data, 'Buyv')
        sellp = _expand_5levels(data, 'Sellp')
        sellv = _expand_5levels(data, 'Sellv')
        return (code, code_type, snapshot_time, *plain,
                *buyp, *buyv, *sellp, *sellv)
    elif route_to == 'sector':
        plain = [data.get(f) for f in _SECTOR_PLAIN]
        return (code, code_type, snapshot_time, *plain)
    else:  # index
        plain = [data.get(f) for f in _INDEX_PLAIN]
        return (code, code_type, snapshot_time, *plain)

def _fetch_one(code):
    """调用 get_market_snapshot 单只采集 (带 10s 超时, 防卡死整轮)"""
    import signal as _sig
    # Windows 不支持 signal.alarm, 用 threading.Timer 兜底
    _result, _exception = [], []

    def _do():
        try:
            _data = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[])
            _result.append(_data)
        except Exception as e:
            _exception.append(e)

    _t = threading.Thread(target=_do, daemon=True)
    _t.start()
    _t.join(timeout=10)
    if _t.is_alive():
        return None  # 超时 → 跳过 (不会卡死整轮)
    if _exception:
        raise _exception[0]
    return _result[0] if _result else None

def run(focus_codes=None, all_codes=None, con=None, limit=None, code_type_map=None):
    """快照采集主入口

    Args:
        focus_codes: 重点标的列表 (标准代码), 全部采集
        all_codes:   全场标的列表 (标准代码), 用于轮换; None 时自动 fetch_all_codes
        con: psycopg2 连接, None 则自建
        limit: 限制数量 (测试用)
        code_type_map: dict[str, str] 代码→类型映射

    Returns:
        dict: {'stock': n, 'sector': n, 'index': n, 'error': n}
    """
    own_con = con is None
    if own_con:
        con = connect()

    try:
        # 【约束】c2 必须全场采集 (~43s/轮, 实测稳定)，不得仅采集 focus 池
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
        total_stock = total_sector = total_index = 0

        # C2 熔断器: 连续失败计数
        consecutive_fail = 0

        for idx, code in enumerate(todo):
            ctype = classify_code(code, code_type_map)
            route_to = route_type_to_table(ctype)
            try:
                data = _fetch_one(code)
                row = parse_snapshot(code, data, snapshot_time, ctype, route_to)
                if row is None:
                    err += 1
                    consecutive_fail += 1
                    if consecutive_fail >= _CIRCUIT_BREAKER_THRESHOLD:
                        remaining = len(todo) - idx - 1
                        logger.error('熔断触发: 连续 {} 只失败, 跳过剩余 {} 只',
                                     consecutive_fail, remaining)
                        break
                    continue
                consecutive_fail = 0  # 成功则重置计数
                if route_to == 'stock':
                    stock_rows.append(row)
                elif route_to == 'sector':
                    sector_rows.append(row)
                else:
                    index_rows.append(row)

                # 每 _BATCH_WRITE_SIZE 只分批写入，释放内存
                if len(stock_rows) + len(sector_rows) + len(index_rows) >= _BATCH_WRITE_SIZE:
                    n_s = executemany_batch(con, 'qd_stock_snapshot', STOCK_TABLE_COLS, stock_rows)
                    n_sec = executemany_batch(con, 'qd_sector_snapshot', SECTOR_TABLE_COLS, sector_rows)
                    n_i = executemany_batch(con, 'qd_index_snapshot', INDEX_TABLE_COLS, index_rows)
                    total_stock += n_s
                    total_sector += n_sec
                    total_index += n_i
                    logger.debug('分批写入: stock={}, sector={}, index={} (进度 {}/{})',
                                 n_s, n_sec, n_i, idx + 1, len(todo))
                    stock_rows, sector_rows, index_rows = [], [], []

            except Exception as e:
                err += 1
                consecutive_fail += 1
                logger.warning('快照采集失败 code={} type={}: {}', code, ctype, e)
                if consecutive_fail >= _CIRCUIT_BREAKER_THRESHOLD:
                    remaining = len(todo) - idx - 1
                    logger.error('熔断触发: 连续 {} 只失败, 跳过剩余 {} 只',
                                 consecutive_fail, remaining)
                    break
                continue

        # 写入剩余数据 (即使写失败也要清空内存防止积压)
        try:
            n_stock = executemany_batch(con, 'qd_stock_snapshot', STOCK_TABLE_COLS, stock_rows) if stock_rows else 0
        except Exception as e:
            logger.warning('剩余 stock 写入失败: {}', e)
            n_stock = 0
        try:
            n_sector = executemany_batch(con, 'qd_sector_snapshot', SECTOR_TABLE_COLS, sector_rows) if sector_rows else 0
        except Exception as e:
            logger.warning('剩余 sector 写入失败: {}', e)
            n_sector = 0
        try:
            n_index = executemany_batch(con, 'qd_index_snapshot', INDEX_TABLE_COLS, index_rows) if index_rows else 0
        except Exception as e:
            logger.warning('剩余 index 写入失败: {}', e)
            n_index = 0

        logger.info('快照写入完成: stock={}, sector={}, index={}, error={} (ts={})',
                    n_stock + total_stock, n_sector + total_sector, n_index + total_index, err, snapshot_time)
        return {'stock': n_stock + total_stock, 'sector': n_sector + total_sector, 'index': n_index + total_index, 'error': err}
    finally:
        if own_con:
            con.close()

def main():
    import argparse
    parser = argparse.ArgumentParser(description='c2 快照采集')
    parser.add_argument('--limit', type=int, default=None, help='限制采集数量 (测试用)')
    args = parser.parse_args()

    run(limit=args.limit)

if __name__ == '__main__':
    main()
