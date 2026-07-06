"""微结构失衡因子

脚本路径: K:\QuestDB_test\\compute\\factors\\micro_imbalance.py
用途: 5 档买卖盘压力失衡, 捕捉短线供需倾斜
依赖: pandas / compute.factors
数据源: ctx.snapshot_focus_df (含 Buyp1..5/Buyv1..5/Sellp1..5/Sellv1..5)
说明:
  - 经典 OBV 思路: 买盘挂单多 = 短线看多
  - 公式: (ΣBuyv - ΣSellv) / (ΣBuyv + ΣSellv), 范围 [-1, +1]
  - 加权版: 用价格档位倒数加权 (近档权重大), 更敏感
  - direction=+1: 买盘大=看多
  - 用 winsorize_zscore (近似正态)
"""

from typing import List, Optional

import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import winsorize_zscore


def _safe_float(v, default=0.0) -> float:
    try:
        r = float(v)
        return default if r != r else r  # NaN check
    except (TypeError, ValueError):
        return default


@FactorRegistry.register
class OrderBookImbalance(FactorBase):
    """5 档买卖盘失衡 (简单等权)"""
    name = 'micro_imbalance'
    version = '1.0'
    timeframe = 'tick'
    warmup_bars = 1
    direction = +1

    def required_inputs(self) -> List[str]:
        return ['snapshot_focus_df']

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        df = ctx.snapshot_focus_df
        if df is None or df.empty:
            return None
        if 'code' not in df.columns:
            return None
        # 检查 5 档字段
        buyv_cols = [f'Buyv{i}' for i in range(1, 6)]
        sellv_cols = [f'Sellv{i}' for i in range(1, 6)]
        if not all(c in df.columns for c in buyv_cols + sellv_cols):
            return None

        # 每只票最新一行
        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in df.columns:
                time_col = c
                break
        if time_col:
            df = df.sort_values(time_col).groupby('code', as_index=False).last()
        else:
            df = df.groupby('code', as_index=False).last()

        result = {}
        for _, r in df.iterrows():
            buyv = sum(_safe_float(r.get(c)) for c in buyv_cols)
            sellv = sum(_safe_float(r.get(c)) for c in sellv_cols)
            total = buyv + sellv
            if total > 0:
                result[r['code']] = (buyv - sellv) / total
        if len(result) < 10:
            return None
        return pd.Series(result, name=self.name)

    def normalize(self, raw: pd.Series) -> pd.Series:
        return winsorize_zscore(raw)


@FactorRegistry.register
class OrderBookWeightedImbalance(FactorBase):
    """5 档加权失衡 (近档权重大)

    用 1/档位 作为权重: Buyv1 权重 1.0, Buyv5 权重 0.2
    比等权更敏感, 捕捉盘口即时变化
    """
    name = 'micro_imbalance_weighted'
    version = '1.0'
    timeframe = 'tick'
    warmup_bars = 1
    direction = +1

    _WEIGHTS = [1.0, 0.8, 0.6, 0.4, 0.2]  # 第 1 档权重最大

    def required_inputs(self) -> List[str]:
        return ['snapshot_focus_df']

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        df = ctx.snapshot_focus_df
        if df is None or df.empty:
            return None
        if 'code' not in df.columns:
            return None
        buyv_cols = [f'Buyv{i}' for i in range(1, 6)]
        sellv_cols = [f'Sellv{i}' for i in range(1, 6)]
        if not all(c in df.columns for c in buyv_cols + sellv_cols):
            return None

        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in df.columns:
                time_col = c
                break
        if time_col:
            df = df.sort_values(time_col).groupby('code', as_index=False).last()
        else:
            df = df.groupby('code', as_index=False).last()

        result = {}
        for _, r in df.iterrows():
            buyv_w = sum(_safe_float(r.get(c)) * w
                         for c, w in zip(buyv_cols, self._WEIGHTS))
            sellv_w = sum(_safe_float(r.get(c)) * w
                          for c, w in zip(sellv_cols, self._WEIGHTS))
            total = buyv_w + sellv_w
            if total > 0:
                result[r['code']] = (buyv_w - sellv_w) / total
        if len(result) < 10:
            return None
        return pd.Series(result, name=self.name)

    def normalize(self, raw: pd.Series) -> pd.Series:
        return winsorize_zscore(raw)
