"""qd_03: 读 K 线 → MACD + 压力位 + 布林带 → 入 qd_indicators

输入: qd_kline_5m (用 5m K 线计算, 噪声小)
输出: qd_indicators

指标默认参数:
  - MACD:  EMA12, EMA26, DEA9
  - BOLL:  20 周期, 2 倍标准差
  - 压力位: 20 根 max(high)
  - 支撑位: 20 根 min(low)

按 code 分组, pandas 计算, executemany 入库 (DEDUP UPSERT 幂等)
"""
import os
from pathlib import Path
import pandas as pd
import psycopg2
from dotenv import load_dotenv
from loguru import logger

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

QDB = dict(
    host=os.environ['QDB_HOST'],
    port=int(os.environ['QDB_PORT']),
    user=os.environ['QDB_USER'],
    password=os.environ['QDB_PASSWORD'],
    dbname=os.environ['QDB_DBNAME'],
)

LOG_DIR = Path(__file__).resolve().parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logger.add(LOG_DIR / 'qd_03_{time:YYYYMMDD}.log', rotation='1 day', retention='7 days')

SRC_KLINE = 'qd_kline_5m'
DST = 'qd_indicators'

INSERT_COLUMNS = [
    'code', 'indicator_time', 'close',
    'macd_dif', 'macd_dea', 'macd_hist',
    'pressure_high', 'support_low',
    'boll_upper', 'boll_mid', 'boll_lower',
]

# 默认参数(测试项目暂用固定值)
MACD_FAST, MACD_SLOW, MACD_SIGNAL = 12, 26, 9
BOLL_N, BOLL_K = 20, 2.0
PRESS_N = 20


def connect():
    """QuestDB 9.4.3 PG 协议存在事务快照延迟, 用 autocommit=True 避免"""
    con = psycopg2.connect(**QDB)
    con.autocommit = True
    return con


def fetch_kline(con):
    """读全表 K 线 (测试场景数据量小, 直接全表)"""
    sql = f"""
    SELECT code, kline_time, open, high, low, close
    FROM {SRC_KLINE}
    ORDER BY code, kline_time
    """
    return pd.read_sql(sql, con)


def calc_one_code(g: pd.DataFrame) -> list:
    """对单个 code 的 K 线序列计算指标, 返回待入库行"""
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
    mid = close.rolling(BOLL_N).mean()
    std = close.rolling(BOLL_N).std(ddof=0)
    upper = mid + BOLL_K * std
    lower = mid - BOLL_K * std

    # 压力/支撑
    pres = high.rolling(PRESS_N).max()
    supp = low.rolling(PRESS_N).min()

    rows = []
    for i in range(len(g)):
        # 必须三项核心都有值才入库
        if pd.isna(hist.iloc[i]) or pd.isna(mid.iloc[i]) or pd.isna(pres.iloc[i]):
            continue
        rows.append((
            g['code'].iloc[i],
            g['kline_time'].iloc[i].to_pydatetime() if hasattr(g['kline_time'].iloc[i], 'to_pydatetime') else g['kline_time'].iloc[i],
            float(close.iloc[i]),
            float(dif.iloc[i]),
            float(dea.iloc[i]),
            float(hist.iloc[i]),
            float(pres.iloc[i]),
            float(supp.iloc[i]),
            float(upper.iloc[i]) if not pd.isna(upper.iloc[i]) else None,
            float(mid.iloc[i]),
            float(lower.iloc[i]) if not pd.isna(lower.iloc[i]) else None,
        ))
    return rows


def save(con, rows):
    if not rows:
        return 0
    placeholders = ','.join(['%s'] * len(INSERT_COLUMNS))
    sql = f"INSERT INTO {DST} ({','.join(INSERT_COLUMNS)}) VALUES ({placeholders})"
    cur = con.cursor()
    # QuestDB 9.4.3 不支持 DELETE FROM, 表已用 DEDUP UPSERT KEYS(indicator_time, code) 自动去重
    try:
        cur.executemany(sql, rows)
    except Exception as e:
        logger.error(f'INSERT 失败: {e}')
        raise
    return len(rows)


def run(con=None):
    logger.info('▶ qd_03 指标计算开始')
    own = con is None
    if own:
        con = connect()
    try:
        df = fetch_kline(con)
        logger.info(f'读到 {len(df)} 根 K 线, code 数={df["code"].nunique() if not df.empty else 0}')
        if df.empty:
            logger.warning('K 线条数为 0, 跳过')
            return
        all_rows = []
        for code, g in df.groupby('code'):
            rs = calc_one_code(g)
            logger.info(f'  {code}: {len(rs)} 条指标')
            all_rows.extend(rs)
        n = save(con, all_rows)
        logger.info(f'✓ qd_03 入库 {n} 条指标')
    finally:
        if own:
            con.close()


if __name__ == '__main__':
    run()
