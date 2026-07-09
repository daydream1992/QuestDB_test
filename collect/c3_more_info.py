"""c3: 88 字段 more_info 采集 (日级 + 盘中高频)

脚本路径: K:\QuestDB_test\\collect\\c3_more_info.py
用途: 逐个 get_more_info 采集 88 字段, 按 route_type 分流到 daily / intraday 表
数据源: tqcenter get_more_info(stock_code, field_list=[])
入库表:
  daily 模式:
    - qd_stock_daily  (个股日级, 50 字段)
    - qd_sector_daily (板块日级, 15 字段)
    - qd_index_daily  (指数日级, 10 字段)
  intraday 模式:
    - qd_stock_intraday  (个股盘中高频, 15 字段)
    - qd_sector_snapshot (板块盘中高频, merge)
    - qd_index_snapshot  (指数盘中高频, merge)
  (C8 拆表后 stock intraday 写 qd_stock_intraday, 非 qd_stock_snapshot)
频率: 全场 60s/轮, 重点 10s/轮
字段映射来源: config/fields.py 的
  STOCK_DAILY_FIELDS / SECTOR_DAILY_FIELDS / INDEX_DAILY_FIELDS / STOCK_INTRADAY_FIELDS

字段映射 (daily):
  code  ← 标准代码
  date  ← HqDate (解析为 TIMESTAMP, 失败回退 datetime.now())
  其余  ← STOCK_DAILY_FIELDS / SECTOR_DAILY_FIELDS / INDEX_DAILY_FIELDS 对应字段
字段映射 (intraday):
  code           ← 标准代码
  snapshot_time  ← datetime.now()
  其余           ← STOCK_INTRADAY_FIELDS 对应字段

说明:
  - 入库 code 用标准代码, 与 qd_code_registry / route_type 一致
  - get_more_info 直接传标准代码 (实测 tqcenter 不接受 tdx 格式 '0#000001')
  - 注: STOCK_INTRADAY_FIELDS 字段 (ZAF/fHSL/Fzhsl 等) 与 qd_*_snapshot 表列名无交集,
    当前 DDL 缺这些列。intraday 模式写入若失败会用 try-except 兜底并记录日志,
    待 DDL 扩展后可正常写入。
  - tqcenter COM 单进程串行, 用 safe_call 包装
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
from lib.tq_utils import to_tdx, route_type, classify_code, route_type_to_table  # noqa: E402
from lib.qdb import connect, executemany_batch  # noqa: E402

from config.fields import (  # noqa: E402
    STOCK_DAILY_FIELDS, SECTOR_DAILY_FIELDS, INDEX_DAILY_FIELDS,
    STOCK_INTRADAY_FIELDS,
)
from tqcenter import tq  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c3_more_info_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

# === daily 表列顺序 (与 DDL 01_daily.sql 严格一致, 含 code_type) ===
STOCK_DAILY_COLS = ['code', 'code_type', 'date'] + STOCK_DAILY_FIELDS
SECTOR_DAILY_COLS = ['code', 'code_type', 'date'] + SECTOR_DAILY_FIELDS
INDEX_DAILY_COLS = ['code', 'code_type', 'date'] + INDEX_DAILY_FIELDS

# === intraday 写入 (C8 拆表: stock 独立成 qd_stock_intraday, sector/index 写 daily 表) ===
# 原 c3 写 qd_stock_snapshot 与 c2@T 双形态行冲突, 现拆独立表, 不再 +1s 错开
_INTRADAY_TABLE = {
    'stock': 'qd_stock_intraday',
    'sector': 'qd_sector_daily',
    'index': 'qd_index_daily',
}
_INTRADAY_COLS = {
    # stock: code_type + snapshot_time + intraday 高频字段
    'stock': ['code', 'code_type', 'snapshot_time'] + STOCK_INTRADAY_FIELDS,
    # sector/index: 写 daily 表, date 用 HqDate
    'sector': SECTOR_DAILY_COLS,
    'index': INDEX_DAILY_COLS,
}

_HQDATE_FORMATS = ('%Y%m%d', '%Y-%m-%d', '%Y/%m/%d', '%Y%m%d %H:%M:%S',
                   '%Y-%m-%d %H:%M:%S')


def _parse_hqdate(hqdate):
    """解析 HqDate → datetime

    支持多种格式, 失败回退 datetime.now()
    """
    if not hqdate:
        return datetime.now()
    if isinstance(hqdate, datetime):
        return hqdate
    s = str(hqdate).strip()
    for fmt in _HQDATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    # 最后尝试 fromisoformat
    try:
        return datetime.fromisoformat(s)
    except Exception:
        logger.warning('HqDate 解析失败: {}, 用 now()', hqdate)
        return datetime.now()


def _build_daily_row(code, data, fields, code_type):
    """构建 daily 表一行: (code, code_type, date, *fields)"""
    hqdate = data.get('HqDate')
    date = _parse_hqdate(hqdate)
    values = [data.get(f) for f in fields]
    return (code, code_type, date, *values)


def _build_intraday_row(code, data, snapshot_time, code_type):
    """构建 stock intraday 行: (code, code_type, snapshot_time, *STOCK_INTRADAY_FIELDS)
    """
    values = [data.get(f) for f in STOCK_INTRADAY_FIELDS]
    return (code, code_type, snapshot_time, *values)


def _build_sector_intraday_row(code, data, snapshot_time, code_type):
    """构建 sector intraday 行"""
    hqdate = data.get('HqDate')
    date = _parse_hqdate(hqdate) if hqdate else snapshot_time
    values = [data.get(f) for f in SECTOR_DAILY_FIELDS]
    return (code, code_type, date, *values)


def _build_index_intraday_row(code, data, snapshot_time, code_type):
    """构建 index intraday 行"""
    hqdate = data.get('HqDate')
    date = _parse_hqdate(hqdate) if hqdate else snapshot_time
    values = [data.get(f) for f in INDEX_DAILY_FIELDS]
    return (code, code_type, date, *values)


def _fetch_one(code):
    """调用 get_more_info 单只采集 (带 10s 超时, 防卡死整轮)"""
    _result, _exception = [], []

    def _do():
        try:
            _data = safe_call(tq.get_more_info, stock_code=code, field_list=[])
            _result.append(_data)
        except Exception as e:
            _exception.append(e)

    _t = threading.Thread(target=_do, daemon=True)
    _t.start()
    _t.join(timeout=10)
    if _t.is_alive():
        return None  # 超时 → 跳过
    if _exception:
        raise _exception[0]
    return _result[0] if _result else None


def _write_daily(con, buckets, code_type_map=None):
    """daily 模式写入 3 张 daily 表 (逐表 try/except, 单表失败不影响其他)"""
    if code_type_map is None:
        code_type_map = {}
    counts = {}
    # stock
    rows = []
    for code, data in buckets.get('stock', []):
        try:
            ct = code_type_map.get(code, 'stock')
            rows.append(_build_daily_row(code, data, STOCK_DAILY_FIELDS, ct))
        except Exception as e:
            logger.warning('构建 stock daily 行失败 code={}: {}', code, e)
    if rows:
        try:
            counts['qd_stock_daily'] = executemany_batch(con, 'qd_stock_daily', STOCK_DAILY_COLS, rows)
        except Exception as e:
            logger.error('写入 qd_stock_daily 失败: {}', e)
            counts['qd_stock_daily'] = 0
    else:
        counts['qd_stock_daily'] = 0

    # sector
    rows = []
    for code, data in buckets.get('sector', []):
        try:
            ct = code_type_map.get(code, 'sector')
            rows.append(_build_daily_row(code, data, SECTOR_DAILY_FIELDS, ct))
        except Exception as e:
            logger.warning('构建 sector daily 行失败 code={}: {}', code, e)
    if rows:
        try:
            counts['qd_sector_daily'] = executemany_batch(con, 'qd_sector_daily', SECTOR_DAILY_COLS, rows)
        except Exception as e:
            logger.error('写入 qd_sector_daily 失败: {}', e)
            counts['qd_sector_daily'] = 0
    else:
        counts['qd_sector_daily'] = 0

    # index
    rows = []
    for code, data in buckets.get('index', []):
        try:
            ct = code_type_map.get(code, 'index')
            rows.append(_build_daily_row(code, data, INDEX_DAILY_FIELDS, ct))
        except Exception as e:
            logger.warning('构建 index daily 行失败 code={}: {}', code, e)
    if rows:
        try:
            counts['qd_index_daily'] = executemany_batch(con, 'qd_index_daily', INDEX_DAILY_COLS, rows)
        except Exception as e:
            logger.error('写入 qd_index_daily 失败: {}', e)
            counts['qd_index_daily'] = 0
    else:
        counts['qd_index_daily'] = 0
    return counts


_INTRADAY_BUILDERS = {
    'stock': _build_intraday_row,
    'sector': _build_sector_intraday_row,
    'index': _build_index_intraday_row,
}


def _write_intraday(con, buckets, snapshot_time, code_type_map=None):
    """intraday 模式: stock 写 intraday 表, sector/index 写 daily 表 (HqDate 日期)

    stock   → qd_stock_intraday  (code_type + snapshot_time + STOCK_INTRADAY_FIELDS)
    sector  → qd_sector_daily  (HqDate → date + SECTOR_DAILY_FIELDS)
    index   → qd_index_daily   (HqDate → date + INDEX_DAILY_FIELDS)
    """
    if code_type_map is None:
        code_type_map = {}
    counts = {}
    for ctype, table in _INTRADAY_TABLE.items():
        cols = _INTRADAY_COLS[ctype]
        builder = _INTRADAY_BUILDERS[ctype]
        rows = []
        for code, data in buckets.get(ctype, []):
            try:
                ct = code_type_map.get(code, ctype)
                rows.append(builder(code, data, snapshot_time, ct))
            except Exception as e:
                logger.warning('构建 {} intraday 行失败 code={}: {}', ctype, code, e)
        if not rows:
            counts[table] = 0
            continue
        try:
            counts[table] = executemany_batch(con, table, cols, rows)
            logger.info('写入 {} {} 行', table, counts[table])
        except Exception as e:
            counts[table] = 0
            logger.error('写入 {} 失败: {}', table, e)
    return counts


def run(codes, mode='daily', con=None, code_type_map=None):
    """more_info 采集主入口

    Args:
        codes: 待采集代码列表 (标准代码)
        mode:  'daily' 写 daily 表; 'intraday' 写 snapshot 表
        con:   psycopg2 连接, None 则自建
        code_type_map: dict[str, str] 代码→类型映射

    Returns:
        dict: 各表写入行数
    """
    if mode not in ('daily', 'intraday'):
        raise ValueError("mode 必须是 'daily' 或 'intraday', 实际: {}".format(mode))

    own_con = con is None
    if own_con:
        con = connect()

    try:
        if not codes:
            logger.warning('codes 为空, 退出')
            return {}

        logger.info('more_info 采集开始 mode={} 共 {} 只', mode, len(codes))

        # 按 classify_code + route_type_to_table 分桶 (etf/kzz/reits → stock 桶)
        buckets = {'stock': [], 'sector': [], 'index': []}
        err = 0
        snapshot_time = datetime.now()
        # C8 拆表后: stock intraday 写独立表 qd_stock_intraday, 不再与 c2 冲突, 无需 +1s 错开

        for code in codes:
            ctype = classify_code(code, code_type_map)
            route_to = route_type_to_table(ctype)
            try:
                data = _fetch_one(code)
                if not data:
                    err += 1
                    continue
                buckets[route_to].append((code, data))
            except Exception as e:
                err += 1
                logger.warning('more_info 采集失败 code={} type={}: {}', code, ctype, e)
                continue

        logger.info('采集成功: stock={}, sector={}, index={}, error={}',
                    len(buckets['stock']), len(buckets['sector']),
                    len(buckets['index']), err)

        if mode == 'daily':
            counts = _write_daily(con, buckets, code_type_map)
        else:
            counts = _write_intraday(con, buckets, snapshot_time, code_type_map)

        logger.info('more_info 写入完成 mode={}: {}', mode, counts)
        return counts
    finally:
        if own_con:
            con.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='c3 more_info 88 字段采集')
    parser.add_argument('--mode', choices=['daily', 'intraday'], default='daily',
                        help='采集模式')
    parser.add_argument('--limit', type=int, default=None, help='限制采集数量 (测试用)')
    args = parser.parse_args()

    from lib.tq_utils import fetch_all_codes
    meta = fetch_all_codes()
    codes = [c['code'] for c in meta]
    if args.limit:
        codes = codes[:args.limit]
    run(codes, mode=args.mode)


if __name__ == '__main__':
    main()
