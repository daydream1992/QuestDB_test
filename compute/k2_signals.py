"""k2: 原子信号检测

脚本路径: K:\QuestDB_test\\compute\\k2_signals.py
用途: 读 qd_indicators, 检测 MACD 金叉/死叉 + 突破压力位/跌破支撑位, 写 qd_signals
数据源: qd_indicators (技术指标表, 由 k1_indicators 产出)
入库表: qd_signals
频率: 10 秒/轮 (跟主循环)
信号类型:
  golden_cross    MACD 金叉, DIF 上穿 DEA
  death_cross     MACD 死叉, DIF 下穿 DEA
  break_pressure  突破压力位, close 上穿 pressure_high
  break_support   跌破支撑位, close 下穿 support_low
检测逻辑 (相邻两根, prev → cur):
  golden_cross:   prev.dif <= prev.dea AND cur.dif >  cur.dea
  death_cross:    prev.dif >= prev.dea AND cur.dif <  cur.dea
  break_pressure: prev.close <  prev.pressure_high AND cur.close >= cur.pressure_high
  break_support:  prev.close >  prev.support_low  AND cur.close <= cur.support_low
字段映射 (方案 A):
  code           ← indicator 的 code
  signal_time    ← indicator 的 calc_time (取 cur 行, 信号触发时刻)
  strategy_name  ← 'k2_atom' (原子信号标识)
  signal_type    ← golden_cross / death_cross / break_pressure / break_support
  signal_score   ← 1.0
  price          ← close (cur 行)
  volume         ← NULL (不传)
  reason         ← 人类可读描述 (如 "MACD 金叉 DIF 上穿 DEA")
  metadata       ← JSON(close/dif/dea/hist/pressure_high/support_low)
说明:
  - 用 lib.qdb 的 connect / query_df / executemany_batch
  - QuestDB PG 协议占位符用 %s, autocommit=True
  - 按 code 分组, 相邻两根比较
  - DEDUP UPSERT KEYS(signal_time, code) 自动去重, 幂等
"""

import os
import sys
import json
from datetime import datetime

import pandas as pd

# 确保项目根在 sys.path (支持 python compute/k2_signals.py 独立运行)
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df, executemany_batch, cutoff  # noqa: E402

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k2_signals_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

# 表名
SRC = 'qd_indicators'
DST = 'qd_signals'

# 原子信号标识
STRATEGY_NAME = 'k2_atom'

# 入库列 (顺序与 rows tuple 一致, 与 ddl/06_signals.sql 一致)
INSERT_COLUMNS = [
    'code', 'signal_time', 'strategy_name', 'signal_type',
    'signal_score', 'price', 'volume', 'reason', 'metadata',
]

# 信号类型常量
GOLDEN_CROSS = 'golden_cross'
DEATH_CROSS = 'death_cross'
BREAK_PRESSURE = 'break_pressure'
BREAK_SUPPORT = 'break_support'


# 模块级: 上次已处理的指标时间戳 (watermark)
_LAST_WATERMARK = None


def fetch_indicators(con) -> pd.DataFrame:
    """读指标数据 (增量模式: 首次 3 天, 后续仅新增)"""
    global _LAST_WATERMARK
    if _LAST_WATERMARK is None:
        since = cutoff(days=3)
    else:
        since = _LAST_WATERMARK.strftime('%Y-%m-%dT%H:%M:%S.%f')
    sql = (
        f"SELECT code, calc_time, close, "
        f"macd_dif, macd_dea, macd_hist, pressure_high, support_low "
        f"FROM {SRC} "
        f"WHERE calc_time > '{since}' "
        f"ORDER BY code, calc_time"
    )
    return query_df(con, sql)


def _to_dt(v):
    """pandas Timestamp → python datetime (QuestDB TIMESTAMP 接受 datetime)"""
    if hasattr(v, 'to_pydatetime'):
        return v.to_pydatetime()
    return v


def _meta_row(close, dif, dea, hist, pressure_high, support_low) -> str:
    """构造 metadata JSON 字符串"""
    return json.dumps({
        'close': close,
        'dif': dif,
        'dea': dea,
        'hist': hist,
        'pressure_high': pressure_high,
        'support_low': support_low,
    }, ensure_ascii=False)


def detect_one_code(g: pd.DataFrame) -> list:
    """对单个 code 的指标序列检测原子信号, 返回待入库行列表

    相邻两根 (prev, cur) 比较, 命中则产生一条信号 (signal_time 取 cur 行 calc_time)。
    同一根 cur 可同时命中多类信号 (各自独立判断)。

    Returns:
        list[tuple]: 每行顺序与 INSERT_COLUMNS 一致
    """
    g = g.sort_values('calc_time').reset_index(drop=True)
    rows = []
    for i in range(1, len(g)):
        prev = g.iloc[i - 1]
        cur = g.iloc[i]

        prev_dif = prev['macd_dif']
        prev_dea = prev['macd_dea']
        cur_dif = cur['macd_dif']
        cur_dea = cur['macd_dea']
        prev_close = prev['close']
        cur_close = cur['close']
        prev_press = prev['pressure_high']
        cur_press = cur['pressure_high']
        prev_sup = prev['support_low']
        cur_sup = cur['support_low']

        # k1 已保证非 NaN, 这里防御性跳过
        vals = [prev_dif, prev_dea, cur_dif, cur_dea,
                prev_close, cur_close, prev_press, cur_press, prev_sup, cur_sup]
        if any(pd.isna(v) for v in vals):
            continue

        code = cur['code']
        signal_time = _to_dt(cur['calc_time'])
        price = float(cur_close)
        meta = _meta_row(
            price, float(cur_dif), float(cur_dea),
            float(cur['macd_hist']), float(cur_press), float(cur_sup),
        )

        # MACD 金叉
        if prev_dif <= prev_dea and cur_dif > cur_dea:
            rows.append((
                code, signal_time, STRATEGY_NAME, GOLDEN_CROSS,
                1.0, price, None,
                'MACD 金叉 DIF 上穿 DEA', meta,
            ))
        # MACD 死叉
        if prev_dif >= prev_dea and cur_dif < cur_dea:
            rows.append((
                code, signal_time, STRATEGY_NAME, DEATH_CROSS,
                1.0, price, None,
                'MACD 死叉 DIF 下穿 DEA', meta,
            ))
        # 突破压力位
        if prev_close < prev_press and cur_close >= cur_press:
            rows.append((
                code, signal_time, STRATEGY_NAME, BREAK_PRESSURE,
                1.0, price, None,
                '突破压力位 close 上穿 pressure_high', meta,
            ))
        # 跌破支撑位
        if prev_close > prev_sup and cur_close <= cur_sup:
            rows.append((
                code, signal_time, STRATEGY_NAME, BREAK_SUPPORT,
                1.0, price, None,
                '跌破支撑位 close 下穿 support_low', meta,
            ))
    return rows


def run(con=None):
    """原子信号检测主流程

    Args:
        con: psycopg2 连接, None 则自建 (用完关闭)
    """
    global _LAST_WATERMARK
    logger.info('▶ k2 原子信号检测开始')
    own = con is None
    if own:
        con = connect()
    try:
        df = fetch_indicators(con)
        if df.empty:
            logger.debug('k2 无新指标 (watermark={})', _LAST_WATERMARK)
            return
        logger.info('读到 {} 行指标, code 数={}', len(df), df['code'].nunique())

        all_rows = []
        for code, g in df.groupby('code'):
            rs = detect_one_code(g)
            if rs:
                logger.info('  {}: {} 条信号', code, len(rs))
            all_rows.extend(rs)

        n = executemany_batch(con, DST, INSERT_COLUMNS, all_rows)
        logger.info('✓ k2 入库 {} 条信号', n)
        _LAST_WATERMARK = datetime.now()
    finally:
        if own:
            con.close()


if __name__ == '__main__':
    run()
