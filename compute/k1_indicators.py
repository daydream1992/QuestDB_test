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
  - 只入库最新一根 K 线的指标 (增量; 历史已在前序轮次入库, DEDUP 幂等)
  - DEDUP UPSERT KEYS(calc_time, code) 自动去重, 幂等

C2 修复: fetch_kline 改用 ROW_NUMBER() OVER 取每个 code 最近 30 根
  (而非旧版 since_minutes=10 时间窗只读 2 根), 保证 rolling(20)/ewm(26)
  有足够样本, 盘初也能读到 daily_init 补的历史 K 线。calc_one_code 只输出
  最新一根 (保留增量意图, 不全量重算历史)。
"""

import os
import sys

import pandas as pd

# 确保项目根在 sys.path (支持 python compute/k1_indicators.py 独立运行)
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df, executemany_batch, cutoff  # noqa: E402

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

# 每个 code 取最近多少根 K 线 (>= max(rolling20, ewm26)=26, 留余量取 30)
KLINE_ROWS_PER_CODE = 30

# k1 增量 watermark: 模块级, 上次 run() 处理过的最大 kline_time (Python datetime)
# 进程重启后为 None → run() 走全量路径 (since_ts=None), 无副作用
_LAST_KLINE_TS = None


def fetch_kline(con, rows_per_code=KLINE_ROWS_PER_CODE, since_ts=None) -> pd.DataFrame:
    """读每个 code 最近 rows_per_code 根 5m K 线 (k1 增量: 可选 since_ts 过滤)

    用 ROW_NUMBER() OVER (PARTITION BY code ORDER BY kline_time DESC) 取每 code
    最近 N 根 (而非时间窗口), 确保 rolling(20)/ewm(26) 有足够样本; 盘初也能读到
    daily_init 补的历史 K 线 (旧版 since_minutes=10 只读 2 根导致指标永远算不出)。

    Args:
        con: psycopg2 连接
        rows_per_code: 每个 code 取最近多少根 (默认 30)
        since_ts: k1 增量优化, 给定时只返回至少有一根 kline_time > since_ts 的 code
                  的最近 rows_per_code 根 (其余 code 跳过, 节省 90%+ 计算量)。
                  None 时返回所有 code。
                  QuestDB 9.4.3 不支持 WHERE IN (SELECT ...) 子查询, 故先拉本轮
                  更新 code 列表, Python 端 .isin() 过滤 outer (比纯 SQL 增量
                  略慢但稳定)。
    """
    if since_ts is not None:
        if isinstance(since_ts, pd.Timestamp):
            since_ts = since_ts.to_pydatetime()
        since_lit = since_ts.strftime('%Y-%m-%dT%H:%M:%S')
        # 先查本轮有 K 线更新的 code 列表
        updated_codes_df = query_df(
            con,
            f"SELECT DISTINCT code FROM {SRC_KLINE} WHERE kline_time > '{since_lit}'"
        )
        if updated_codes_df.empty:
            return updated_codes_df  # 空 DataFrame
        updated_codes = updated_codes_df['code'].tolist()
        # 再对这些 code 拉最近 rows_per_code 根
        codes_lit = "','".join(updated_codes)
        sql = (
            f"SELECT code, kline_time, open, high, low, close FROM ("
            f"  SELECT code, kline_time, open, high, low, close, "
            f"         row_number() OVER (PARTITION BY code ORDER BY kline_time DESC) AS rn "
            f"  FROM {SRC_KLINE} WHERE code IN ('{codes_lit}')"
            f") WHERE rn <= {rows_per_code} ORDER BY code, kline_time"
        )
        return query_df(con, sql)
    sql = (
        f"SELECT code, kline_time, open, high, low, close FROM ("
        f"  SELECT code, kline_time, open, high, low, close, "
        f"         row_number() OVER (PARTITION BY code ORDER BY kline_time DESC) AS rn "
        f"  FROM {SRC_KLINE}"
        f") WHERE rn <= {rows_per_code} ORDER BY code, kline_time"
    )
    return query_df(con, sql)


def _to_dt(v):
    """pandas Timestamp → python datetime (QuestDB TIMESTAMP 接受 datetime)"""
    if hasattr(v, 'to_pydatetime'):
        return v.to_pydatetime()
    return v


def calc_one_code(g: pd.DataFrame) -> list:
    """对单个 code 的 K 线序列计算指标, 返回待入库行列表 (只含最新一根)

    Returns:
        list[tuple]: 每行顺序与 INSERT_COLUMNS 一致; 仅最新一根 (指标就绪时)
    """
    g = g.sort_values('kline_time').reset_index(drop=True)
    if g.empty:
        return []
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

    # 只输出最新一根 (增量入库; 历史已在前序轮次写入, DEDUP 幂等)
    i = len(g) - 1
    if (pd.isna(hist.iloc[i]) or pd.isna(boll_mid.iloc[i])
            or pd.isna(pressure_high.iloc[i]) or pd.isna(ma20.iloc[i])):
        return []  # 最新一根指标未就绪 (样本不足), 本轮跳过
    return [(
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
    )]


def run(con=None, since_ts=None):
    """指标计算主流程 (k1 增量: since_ts 透传到 fetch_kline)

    Args:
        con: psycopg2 连接, None 则自建 (用完关闭)
        since_ts: 增量 watermark, 给定时只算有 K 线更新的 code;
                  None 时用模块级 _LAST_KLINE_TS (首次调用或进程重启后为 None → 全量)
    """
    global _LAST_KLINE_TS
    if since_ts is None:
        since_ts = _LAST_KLINE_TS  # None → 全量
    logger.info('▶ k1 指标计算开始 since_ts={}', since_ts)
    own = con is None
    if own:
        con = connect()
    try:
        df = fetch_kline(con, since_ts=since_ts)
        if df.empty:
            logger.warning('qd_kline_5m 无数据, 跳过')
            return
        logger.info('读到 {} 根 K 线, code 数={}', len(df), df['code'].nunique())

        all_rows = []
        for code, g in df.groupby('code'):
            rs = calc_one_code(g)
            if rs:
                all_rows.extend(rs)

        if all_rows:
            n = executemany_batch(con, DST, INSERT_COLUMNS, all_rows)
            logger.info('✓ k1 入库 {} 条指标 ({} 个 code)', n, len(all_rows))
            # watermark 推进: 本轮 df 最大 kline_time (原始 df 包括可能跳过 calc 的 code)
            max_ts = df['kline_time'].max()
            if hasattr(max_ts, 'to_pydatetime'):
                max_ts = max_ts.to_pydatetime()
            _LAST_KLINE_TS = max_ts
        else:
            logger.info('k1 本轮无新增指标 (可能 watermark 内所有 K 线窗口不足)')
    finally:
        if own:
            con.close()


if __name__ == '__main__':
    run()
