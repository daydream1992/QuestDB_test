"""k5: 当天 K 线本地合成 (1m / 5m)

脚本路径: K:\QuestDB_test\\compute\\k5_kline_synth.py
用途: tqcenter get_market_data 只返回历史 K (昨天及之前), 当天 K 拉不到;
      本模块用 qd_stock_snapshot 的高频 Now/Volume/Amount 按分钟桶聚合成 1m/5m K,
      写入 qd_kline_1m / qd_kline_5m (与 c4 历史K同表 DEDUP, 今天合成覆盖/补充)。
数据源: qd_stock_snapshot (focus 池, c2 高频快照, 含 Now/Volume/Amount/snapshot_time)
入库表: qd_kline_1m / qd_kline_5m (DEDUP UPSERT KEYS(kline_time, code) 幂等)
频率: 60s/轮 (跟 60s 块, 在 c4 之后 k1 之前, 让 k1 读到今天的 5m K)

合成逻辑 (按 code × 周期桶):
  open   = 桶内第一笔 Now
  high   = max(桶内 Now)   ← 注意: 用 Now 极值, 不用 snapshot.Max (那是当日累计)
  low    = min(桶内 Now)
  close  = 桶内最后一笔 Now
  volume = 桶内最后 Volume - 桶内第一 Volume (累计成交量差分; 负值置 0)
  amount = 桶内最后 Amount - 桶内第一 Amount (累计成交额差分)

说明:
  - 只合成 focus 池 (qd_stock_snapshot 是 focus 池高频); 全场扩展可用 qd_pricevol (无 Amount) TODO
  - 双形态行: c2@T 有 Now/Volume/Amount, c3@T+1s intraday 无; dropna(Now) 过滤掉 c3 行
  - DEDUP 同 kline_time+code: 当前分钟桶每轮覆盖更新 (进行中 K), 已收盘分钟定型
"""

import os
import sys

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402
from lib.qdb import connect, query_df, executemany_batch  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'k5_kline_synth_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

KLINE_COLS = ['code', 'kline_time', 'Open', 'High', 'Low', 'Close', 'Volume', 'Amount']

# 读近 N 分钟 snapshot (够覆盖当前 + 上一个已收盘周期, 多笔/分钟保证桶内聚合)
# 6min: 1 个当前 5m 桶 + 1 个上一 5m 桶, 历史由 c4(get_market_data) 提供; 太大会拖慢合成
_LOOKBACK_MIN = 6


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _synth_period(df, freq):
    """按 code × freq 桶聚合 → K 线 DataFrame

    Args:
        df: snapshot DataFrame, 含 code/snapshot_time/Now/Volume/Amount (Now 已 dropna)
        freq: '1min' / '5min'
    """
    if df is None or df.empty:
        return pd.DataFrame()
    d = df.copy()
    d['snapshot_time'] = pd.to_datetime(d['snapshot_time'])
    d['bucket'] = d['snapshot_time'].dt.floor(freq)
    # 只保留有 Now 的行 (过滤 c3 intraday 双形态行)
    d = d.dropna(subset=['Now'])
    if d.empty:
        return pd.DataFrame()
    g = d.groupby(['code', 'bucket'])
    out = g.agg(
        open=('Now', 'first'),
        high=('Now', 'max'),
        low=('Now', 'min'),
        close=('Now', 'last'),
        v_first=('Volume', 'first'),
        v_last=('Volume', 'last'),
        a_first=('Amount', 'first'),
        a_last=('Amount', 'last'),
    ).reset_index()
    # 累计量差分 (Volume/Amount 是当日累计, 桶内末-首 = 该周期增量)
    out['volume'] = (out['v_last'] - out['v_first']).clip(lower=0)
    out['amount'] = (out['a_last'] - out['a_first']).clip(lower=0)
    out['kline_time'] = out['bucket']
    return out[['code', 'kline_time', 'open', 'high', 'low', 'close', 'volume', 'amount']]


def _to_rows(kdf):
    """K线 DataFrame → rows tuple (Volume/Amount 转 float/int, NaT/NaN 处理)"""
    rows = []
    for _, r in kdf.iterrows():
        kt = r['kline_time']
        if hasattr(kt, 'to_pydatetime'):
            kt = kt.to_pydatetime()
        vol = r['volume']
        vol = int(vol) if pd.notna(vol) else None
        amt = _to_float(r['amount'])
        rows.append((r['code'], kt, _to_float(r['open']), _to_float(r['high']),
                     _to_float(r['low']), _to_float(r['close']), vol, amt))
    return rows


def run(con=None):
    """合成当天 1m + 5m K 线 → qd_kline_1m / qd_kline_5m"""
    logger.info('▶ k5 K线合成开始')
    own = con is None
    if own:
        con = connect()
    try:
        # 用 Python 本地 now 算 cutoff (QuestDB now() 是 UTC, 与 snapshot_time 本地写入错位,
        # 用 SQL now() 会命中全部今天数据导致 lookback 失效 + 合成慢)
        from datetime import datetime as _dt, timedelta
        cutoff = _dt.now() - timedelta(minutes=_LOOKBACK_MIN)
        df = query_df(
            con, "SELECT code, snapshot_time, Now, Volume, Amount "
                 "FROM qd_stock_snapshot "
                 "WHERE snapshot_time > '" + cutoff.strftime('%Y-%m-%dT%H:%M:%S') + "'")
        if df is None or df.empty:
            logger.warning('qd_stock_snapshot 近 {} 分钟无数据, 跳过合成', _LOOKBACK_MIN)
            return
        logger.info('读到 {} 行 snapshot (合成源)', len(df))

        n_total = 0
        for freq, table in (('1min', 'qd_kline_1m'), ('5min', 'qd_kline_5m')):
            kdf = _synth_period(df, freq)
            if kdf is None or kdf.empty:
                logger.info('  {}: 合成 0 行', table)
                continue
            rows = _to_rows(kdf)
            n = executemany_batch(con, table, KLINE_COLS, rows)
            logger.info('  {} 合成入库 {} 行 ({} 个 code)', table, n, kdf['code'].nunique())
            n_total += n
        logger.info('✓ k5 合成完成, 共 {} 行', n_total)
    finally:
        if own:
            con.close()


if __name__ == '__main__':
    run()
