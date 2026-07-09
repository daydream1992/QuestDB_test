"""c5: 个股交易数据 (GP系列) 采集

脚本路径: K:\QuestDB_test\\collect\\c5_gpjy.py
用途: 调 tqcenter get_gpjy_value 拉 GP 字段 (涨跌停/连板/次日红盘率/机构等),
      取每 code 最新日期值, 写 qd_stock_gpjy
依赖: tqcenter (需客户端下载股票数据包)
数据源: tqcenter get_gpjy_value
入库表: qd_stock_gpjy
频率: 日级 (daily_init 或 daily_close 跑一次; 盘中不变)
说明:
  - GP 是日级历史时序 (每天一条), 今天收盘后才有; 盘后/盘前用
  - 返回 {code: {field: [{Date, Value}]}}; 每 code 每 field 取最新 Date 的 Value
  - Value 是 list, 各 GP 子字段语义见 ddl/17_stock_gpjy.sql
  - 全场分批拉 (tqcenter 单次有限制), start_time 取近 7 天减返回量
"""

import os
import sys

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402
from lib.qdb import connect, executemany_batch  # noqa: E402
from lib.tq_client import safe_call  # noqa: E402
from tqcenter import tq  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c5_gpjy_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

DST = 'qd_stock_gpjy'

# 入库列 (顺序与 rows tuple 一致, 与 ddl/17_stock_gpjy.sql 一致)
INSERT_COLUMNS = [
    'code', 'date',
    'gp15_status', 'gp15_seal',
    'gp14_zt_amo', 'gp14_break_cnt',
    'gp38_zt_cnt', 'gp38_premium_cnt',
    'gp39_first_seal_rate', 'gp39_next_red_rate',
    'gp40_lb_rate', 'gp40_last_zt_time',
    'gp09_inst_cnt', 'gp09_inst_amo',
]

# 要拉的 GP 字段
GP_FIELDS = ['GP15', 'GP14', 'GP38', 'GP39', 'GP40', 'GP09']

# 每批拉多少只 (tqcenter 单次返回量控制)
_BATCH_SIZE = 200


def _to_float(v, default=None):
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return default


def _latest_value(entries):
    """从 [{Date, Value}, ...] 取最新 Date 的 Value (list); 无则 None"""
    if not entries:
        return None, None
    # 按 Date 降序取第一个
    latest = max(entries, key=lambda e: e.get('Date', ''))
    return latest.get('Date'), latest.get('Value')


def _parse_code(code, data):
    """解析单 code 的 GP 数据 → (date, 各子字段) 或 None"""
    if not data:
        return None
    date_final = None
    vals = {}
    for field in GP_FIELDS:
        entries = data.get(field)
        d, value = _latest_value(entries)
        if d and (date_final is None or d > date_final):
            date_final = d
        vals[field] = (d, value)
    if date_final is None:
        return None
    # 各 GP 的 Value 子字段拆解 (无值填 None)
    def _idx(field, i):
        _, v = vals.get(field, (None, None))
        if isinstance(v, list) and len(v) > i:
            return _to_float(v[i]) if field != 'GP40' or i == 0 else v[i]
        return None
    # GP40_last_zt_time 是字符串 (Value[1])
    _, v40 = vals.get('GP40', (None, None))
    last_zt_time = v40[1] if isinstance(v40, list) and len(v40) > 1 else None
    return {
        'date': date_final,
        'gp15_status': _idx('GP15', 0),
        'gp15_seal': _idx('GP15', 1),
        'gp14_zt_amo': _idx('GP14', 0),
        'gp14_break_cnt': _idx('GP14', 1),
        'gp38_zt_cnt': _idx('GP38', 0),
        'gp38_premium_cnt': _idx('GP38', 1),
        'gp39_first_seal_rate': _idx('GP39', 0),
        'gp39_next_red_rate': _idx('GP39', 1),
        'gp40_lb_rate': _idx('GP40', 0),
        'gp40_last_zt_time': last_zt_time,
        'gp09_inst_cnt': _idx('GP09', 0),
        'gp09_inst_amo': _idx('GP09', 1),
    }


def _date_to_ts(yyyymmdd):
    """'20240221' → '2024-02-21T00:00:00' (QuestDB TIMESTAMP 字面量)"""
    if not yyyymmdd or len(yyyymmdd) < 8:
        return None
    try:
        return f'{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}T00:00:00'
    except Exception:
        return None


def run(codes=None, start_time='', end_time='', con=None):
    """拉全场 GP 数据, 取最新 Date 入库

    Args:
        codes: 股票代码列表, None 则从 qd_code_registry 取
        start_time: 起始日期 YYYYMMDD (默认近7天, 减返回量)
        end_time: 结束日期 YYYYMMDD (默认空=至今)
        con: psycopg2 连接, None 自建
    """
    logger.info('▶ c5 GP 采集开始')
    own = con is None
    if own:
        con = connect()
    try:
        if codes is None:
            from lib.qdb import query_df
            codes = query_df(con, "SELECT code FROM qd_code_registry WHERE code_type='stock'")['code'].tolist()
        if not start_time:
            # 近 7 天, 减少历史返回 (只取最新)
            from datetime import datetime, timedelta
            start_time = (datetime.now() - timedelta(days=7)).strftime('%Y%m%d')
        logger.info('共 {} 只, start={} end={}', len(codes), start_time, end_time)

        rows = []
        err = 0
        for i in range(0, len(codes), _BATCH_SIZE):
            batch = codes[i:i + _BATCH_SIZE]
            try:
                result = safe_call(tq.get_gpjy_value, stock_list=batch,
                                   field_list=GP_FIELDS, start_time=start_time, end_time=end_time)
            except Exception as e:
                logger.warning('get_gpjy_value 批次 {} 失败: {}', i, e)
                err += len(batch)
                continue
            if not isinstance(result, dict):
                continue
            for code, data in result.items():
                parsed = _parse_code(code, data)
                if not parsed:
                    continue
                ts = _date_to_ts(parsed['date'])
                if not ts:
                    continue
                rows.append((
                    code, ts,
                    parsed['gp15_status'], parsed['gp15_seal'],
                    parsed['gp14_zt_amo'], parsed['gp14_break_cnt'],
                    parsed['gp38_zt_cnt'], parsed['gp38_premium_cnt'],
                    parsed['gp39_first_seal_rate'], parsed['gp39_next_red_rate'],
                    parsed['gp40_lb_rate'], parsed['gp40_last_zt_time'],
                    parsed['gp09_inst_cnt'], parsed['gp09_inst_amo'],
                ))
        n = executemany_batch(con, DST, INSERT_COLUMNS, rows) if rows else 0
        logger.info('✓ c5 入库 {} 行 GP 数据 ({} 个 code, 错误 {} 只)', n, len(rows), err)
        return n
    finally:
        if own:
            con.close()


if __name__ == '__main__':
    from lib.tq_client import init, close
    init()
    try:
        run()
    finally:
        close()