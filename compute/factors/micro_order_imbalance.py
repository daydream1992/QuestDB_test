"""micro_order_imbalance: 委托单失衡 EMA 平滑因子

脚本路径: K:\QuestDB_test\\compute\\factors\\micro_order_imbalance.py
用途: 5 档买卖委托量比值 + EMA 平滑后的趋势失衡信号
数据源: ctx.snapshot_focus_df (Buyp1-5/Buyv1-5/Sellp1-5/Sellv1-5)
依赖: compute.factors.base, numpy
说明:
  - 与已有 micro_imbalance 区别：这个是 EMA 平滑后的趋势失衡
  - 买盘持续 > 卖盘 = 看多 (direction=+1)
  - warmup_bars=12 用于 EMA 预热
"""

import numpy as np
import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import winsorize_zscore

# EMA 缓存 (进程内)
_PREV_EMA = None


@FactorRegistry.register
class OrderImbalance(FactorBase):
    name = 'micro_order_imbalance'
    version = '1.0'
    timeframe = 'tick'
    warmup_bars = 12
    direction = +1

    def required_inputs(self) -> list:
        return ['snapshot_focus_df']

    def compute_raw(self, ctx) -> pd.Series:
        """计算 EMA 平滑后的委托单失衡

        Returns:
            pd.Series: index=code, value=EMA 失衡值 (正=买盘强)
        """
        snap = ctx.snapshot_focus_df
        if snap is None or snap.empty:
            return pd.Series(dtype=float)

        # 累加 5 档买卖量
        codes = snap['code'].tolist()
        imbalance = {}
        for code in codes:
            row = snap[snap['code'] == code]
            if row.empty:
                continue
            r = row.iloc[0]
            bid_vol = sum(_safe_float(r.get(f'Buyv{i}')) for i in range(1, 6))
            ask_vol = sum(_safe_float(r.get(f'Sellv{i}')) for i in range(1, 6))
            total = bid_vol + ask_vol
            if total > 0:
                imbalance[code] = (bid_vol - ask_vol) / total
            else:
                imbalance[code] = 0.0

        if not imbalance:
            return pd.Series(dtype=float)

        s = pd.Series(imbalance, dtype=float)

        # EMA 平滑 (模块级缓存)
        global _PREV_EMA
        alpha = 2 / (self.warmup_bars + 1)
        if _PREV_EMA is not None:
            common = s.index.intersection(_PREV_EMA.index)
            ema = pd.Series(0.0, index=s.index)
            ema[common] = alpha * s[common] + (1 - alpha) * _PREV_EMA[common]
            new_idx = s.index.difference(_PREV_EMA.index)
            ema[new_idx] = s[new_idx]
        else:
            ema = s.copy()
        _PREV_EMA = ema
        return ema

    def normalize(self, raw: pd.Series) -> pd.Series:
        return winsorize_zscore(raw)
