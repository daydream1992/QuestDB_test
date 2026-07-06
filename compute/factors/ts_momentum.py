"""时序动量因子

脚本路径: K:\QuestDB_test\\compute\\factors\\ts_momentum.py
用途: 5m K 线 N 根累计收益 (动量), 解决"无时序特征"问题
依赖: pandas / compute.factors
数据源: ctx.indicators_df (含 5m K 线 + 时间戳)
说明:
  - 看 N 根 5m K 线的累计涨幅, 越大越看多
  - 用 5m K 线是因为 intraday_loop 60s 块每轮 c4 拉一根 5m, 时间序列连贯
  - warmup_bars=10: 至少 10 根 5m K 线 (50 分钟) 才计算
  - 同时算加速度 (近 3 根 vs 前 3 根), 捕捉趋势加速/减速
"""

from typing import List, Optional

import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import winsorize_zscore


@FactorRegistry.register
class Momentum5M(FactorBase):
    """5m K 线 N 根累计动量"""
    name = 'ts_momentum_5m'
    version = '1.0'
    timeframe = 'minute'
    warmup_bars = 10
    direction = +1                 # 大=看多

    # 阈值
    _LOOKBACK_BARS = 10            # 看近 10 根 5m = 50 分钟
    _MIN_BARS_REQUIRED = 5         # 至少 5 根才计算

    def required_inputs(self) -> List[str]:
        return ['indicators_df']

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        df = ctx.indicators_df
        if df is None or df.empty:
            return None
        # indicators_df 应含 code, snapshot_time (或 kline_time), close
        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in df.columns:
                time_col = c
                break
        if time_col is None or 'code' not in df.columns or 'close' not in df.columns:
            return None

        # 按 code + time 排序, 取每只票最近 N 根
        df = df.sort_values([time_col])
        result = {}
        for code, g in df.groupby('code'):
            if len(g) < self._MIN_BARS_REQUIRED:
                continue
            recent = g.tail(self._LOOKBACK_BARS)
            close_first = float(recent['close'].iloc[0])
            close_last = float(recent['close'].iloc[-1])
            if close_first <= 0:
                continue
            momentum = (close_last - close_first) / close_first
            result[code] = momentum
        if not result:
            return None
        return pd.Series(result, name=self.name)

    def normalize(self, raw: pd.Series) -> pd.Series:
        # 动量分布近似正态, 用 winsorize_zscore
        return winsorize_zscore(raw)


@FactorRegistry.register
class MomentumAcceleration(FactorBase):
    """动量加速度: 近 3 根动量 - 前 3 根动量

    捕捉趋势加速 (启动期) vs 减速 (衰竭期)
    """
    name = 'ts_acceleration'
    version = '1.0'
    timeframe = 'minute'
    warmup_bars = 10
    direction = +1

    _LOOKBACK_BARS = 10

    def required_inputs(self) -> List[str]:
        return ['indicators_df']

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        df = ctx.indicators_df
        if df is None or df.empty:
            return None
        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in df.columns:
                time_col = c
                break
        if time_col is None or 'code' not in df.columns or 'close' not in df.columns:
            return None

        df = df.sort_values([time_col])
        result = {}
        for code, g in df.groupby('code'):
            if len(g) < self._LOOKBACK_BARS:
                continue
            recent = g.tail(self._LOOKBACK_BARS)
            closes = recent['close'].astype(float).values
            # 近 3 根动量
            if len(closes) >= 6 and closes[-6] > 0:
                m_recent = (closes[-1] - closes[-3]) / closes[-3]
                m_prev = (closes[-3] - closes[-6]) / closes[-6]
                result[code] = m_recent - m_prev
        if not result:
            return None
        return pd.Series(result, name=self.name)

    def normalize(self, raw: pd.Series) -> pd.Series:
        return winsorize_zscore(raw)
