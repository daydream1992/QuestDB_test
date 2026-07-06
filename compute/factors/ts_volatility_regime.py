"""ts_volatility_regime: 波动率状态因子

脚本路径: K:\QuestDB_test\\compute\\factors\\ts_volatility_regime.py
用途: 近 20 根 5m K 线的已实现波动率分档
数据源: ctx.indicators_df (close 列)
依赖: compute.factors.base, numpy
说明:
  - 低波动+价格突破=高胜率信号(direction=+1 时加权)
  - 高波动=减仓信号(direction=-1)
  - 输出 [-1, +1] 归一化
"""

import numpy as np
import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import winsorize_zscore


@FactorRegistry.register
class VolatilityRegime(FactorBase):
    name = 'ts_volatility_regime'
    version = '1.0'
    timeframe = 'minute'
    warmup_bars = 20
    direction = 0  # 动态决定

    def required_inputs(self) -> list:
        return ['indicators_df']

    def compute_raw(self, ctx) -> pd.Series:
        """计算波动率状态 [-1, +1]

        Returns:
            pd.Series: index=code, value=(-1 ~ +1)
               正=低波动且有趋势(看多), 负=高波动(看空)
        """
        ind = ctx.indicators_df
        if ind is None or ind.empty or 'close' not in ind.columns:
            return pd.Series(dtype=float)

        # 按 code 分组算 realized vol
        result = {}
        for code, g in ind.groupby('code'):
            try:
                g = g.sort_values('calc_time')
                prices = g['close'].dropna().values
                if len(prices) < 10:
                    result[code] = 0.0
                    continue

                # 对数收益率
                returns = np.diff(np.log(prices))
                rv = np.std(returns) * np.sqrt(48)  # 年化(5min→日: sqrt(48))

                # 当前价格 vs 5 根前 (趋势判断)
                cur = prices[-1]
                prev = prices[min(-6, -len(prices))]
                trend = (cur - prev) / prev * 100

                # 分档
                if rv < 0.15:
                    # 低波动
                    regime = 0.5 if trend > 1.0 else (-0.3 if trend < -1.0 else 0.0)
                elif rv < 0.3:
                    # 中波动
                    regime = 0.2 if trend > 1.5 else (-0.1 if trend < -1.5 else 0.0)
                else:
                    # 高波动
                    regime = -0.5

                result[code] = max(-1.0, min(1.0, regime))
            except Exception:
                result[code] = 0.0

        if not result:
            return pd.Series(dtype=float)
        return pd.Series(result, dtype=float)

    def normalize(self, raw: pd.Series) -> pd.Series:
        # 已自定义 [-1, +1], 不做额外归一化
        return raw
