"""k1: 技术指标计算

脚本路径: K:\QuestDB_test\\compute\\k1_indicators.py
用途: 读 5m K 线, 计算 MACD/BOLL/压力位/支撑位/MA, 写 qd_indicators
数据源: qd_kline_5m (5 分钟 K 线)
入库表: qd_indicators
频率: 10 秒/轮 (跟主循环)
指标参数:
  MACD:   EMA12, EMA26, DEA9
  BOLL:   20 周期, 2 倍标准差
  压力位: 20 根 max(high)
  支撑位: 20 根 min(low)
  MA:     MA5/MA10/MA20
字段映射:
  code           ← K 线 code
  calc_time      ← K 线 kline_time (与 K 线时间对齐, 便于信号层 join)
  close          ← K 线 close
  macd_dif       ← DIF = EMA12 - EMA26
  macd_dea       ← DEA = DIF 的 EMA9
  macd_hist      ← HIST = (DIF - DEA) * 2
  pressure_high  ← 20 根 high 的 max
  support_low    ← 20 根 low 的 min
  boll_upper     ← mid + 2*std
  boll_mid       ← 20 根 close 的 mean
  boll_lower     ← mid - 2*std
  ma5            ← 5 根 close 的 mean
  ma10           ← 10 根 close 的 mean
  ma20           ← 20 根 close 的 mean

说明:
  - 用 lib.qdb 的 connect / query_df / executemany_batch
  - QuestDB PG 协议占位符用 %s, autocommit=True
  - 按 code 分组, pandas 计算
  - 只入库核心指标都有值的行 (NaN 跳过)
  - DEDUP UPSERT KEYS(calc_time, code) 自动去重, 幂等
"""

import os
import sys

import pandas as pd

# 确保项目根在 sys.path (支持 python compute/k1_indicators.py 独立运行)
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df, executemany_batch  # noqa: E402

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k1_indicators_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# 表名
SRC_KLINE = 'qd_kline_5m'
DST = 'qd_indicators'

# 入库列 (顺序与 rows tuple 一致, 与 ddl/05_indicators.sql 一致)
INSERT_COLUMNS = [
    'code', 'calc_time', 'close',
    'macd_dif', 'macd_dea', 'macd_hist',
    'pressure_high', 'support_low',
    'boll_upper', 'boll_mid', 'boll_lower',
    'ma5', 'ma10', 'ma20',
]

# 指标参数
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BOLL_N, BOLL_K = 20, 2.0
PRESS_N = 20
MA5_N, MA10_N, MA20_N = 5, 10, 20


# 最近一次入库 calc_time (供 run_incremental 使用)
_last_time = None


def fetch_kline(con, since_minutes=10) -> pd.DataFrame:
    """读 5m K 线 (只读最近 since_minutes 分钟, 避免全表扫描)

    Args:
        con: psycopg2 连接
        since_minutes: 只取最近多少分钟内的 K 线 (默认 10 分钟, 覆盖 1 个 5m 周期)
    """
    sql = (
        f"SELECT code, kline_time, open, high, low, close "
        f"FROM {SRC_KLINE} "
        f"WHERE kline_time > dateadd('m', -{since_minutes}, now()) "
        f"ORDER BY code, kline_time"
    )
    return query_df(con, sql)


def _to_dt(v):
    """pandas Timestamp → python datetime (QuestDB TIMESTAMP 接受 datetime)"""
    if hasattr(v, 'to_pydatetime'):
        return v.to_pydatetime()
    return v


def calc_one_code(g: pd.DataFrame) -> list:
    """对单个 code 的 K 线序列计算指标, 返回待入库行列表

    Returns:
        list[tuple]: 每行顺序与 INSERT_COLUMNS 一致
    """
    g = g.sort_values('kline_time').reset_index(drop=True)
    close = g['close'].astype(float)
    high = g['high'].astype(float)
    low = g['low'].astype(float)

    # MACD
    ema_fast = close.ewm(span=MACD_FAST, adjust=False).mean()
    ema_slow = close.ewm(span=MACD_SLOW, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=MACD_SIGNAL, adjust=False).mean()
    hist = (dif - dea) * 2

    # BOLL
    boll_mid = close.rolling(BOLL_N).mean()
    boll_std = close.rolling(BOLL_N).std(ddof=0)
    boll_upper = boll_mid + BOLL_K * boll_std
    boll_lower = boll_mid - BOLL_K * boll_std

    # 压力 / 支撑
    pressure_high = high.rolling(PRESS_N).max()
    support_low = low.rolling(PRESS_N).min()

    # MA
    ma5 = close.rolling(MA5_N).mean()
    ma10 = close.rolling(MA10_N).mean()
    ma20 = close.rolling(MA20_N).mean()

    rows = []
    for i in range(len(g)):
        # 核心指标都有值才入库 (rolling 20 门槛 + macd)
        if (pd.isna(hist.iloc[i]) or pd.isna(boll_mid.iloc[i])
                or pd.isna(pressure_high.iloc[i]) or pd.isna(ma20.iloc[i])):
            continue
        rows.append((
            g['code'].iloc[i],
            _to_dt(g['kline_time'].iloc[i]),
            float(close.iloc[i]),
            float(dif.iloc[i]),
            float(dea.iloc[i]),
            float(hist.iloc[i]),
            float(pressure_high.iloc[i]),
            float(support_low.iloc[i]),
            float(boll_upper.iloc[i]),
            float(boll_mid.iloc[i]),
            float(boll_lower.iloc[i]),
            float(ma5.iloc[i]),
            float(ma10.iloc[i]),
            float(ma20.iloc[i]),
        ))
    return rows


def run(con=None):
    """指标计算主流程

    Args:
        con: psycopg2 连接, None 则自建 (用完关闭)
    """
    logger.info('▶ k1 指标计算开始')
    own = con is None
    if own:
        con = connect()
    try:
        df = fetch_kline(con)
        if df.empty:
            logger.warning('qd_kline_5m 无数据, 跳过')
            return
        logger.info('读到 {} 根 K 线, code 数={}', len(df), df['code'].nunique())

        all_rows = []
        for code, g in df.groupby('code'):
            rs = calc_one_code(g)
            logger.info('  {}: {} 条指标', code, len(rs))
            all_rows.extend(rs)

        n = executemany_batch(con, DST, INSERT_COLUMNS, all_rows)
        logger.info('✓ k1 入库 {} 条指标', n)
    finally:
        if own:
            con.close()


if __name__ == '__main__':
    run()
