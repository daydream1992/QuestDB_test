"""ts_reversal_open: 开盘反转因子

脚本路径: K:\QuestDB_test\\compute\\factors\\ts_reversal_open.py
用途: 利用开盘缺口 + 回补方向判断反转信号
数据源: ctx.snapshot_focus_df (Open/PreClose/Close) + ctx.indicators_df (5m 前 6 根 K 线)
依赖: compute.factors.base, numpy, pandas
说明:
  - 缺口大+不回补=继续看空; 缺口大+快速回补=看多
  - direction 动态根据 sign 决定（非固定）
  - warmup_bars=6 对应 30 分钟开盘数据
"""

import numpy as np
import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import rank_normalize


@FactorRegistry.register
class ReversalOpen(FactorBase):
    name = 'ts_reversal_open'
    version = '1.0'
    timeframe = 'minute'
    warmup_bars = 6
    direction = 0  # 动态决定, compute_raw 返回带符号值

    def required_inputs(self) -> list:
        return ['snapshot_focus_df', 'indicators_df']

    def compute_raw(self, ctx) -> pd.Series:
        """计算开盘反转信号

        Returns:
            pd.Series: index=code, value=带符号反转强度 (-1~1 归一化)
                      正=看多(回补), 负=看空(不回补)
        """
        snap = ctx.snapshot_focus_df
        if snap is None or snap.empty:
            return pd.Series(dtype=float)

        codes = snap['code'].tolist()
        values = []
        for code in codes:
            try:
                row = snap[snap['code'] == code]
                if row.empty:
                    values.append(0.0)
                    continue
                r = row.iloc[0]
                lc = _safe_float(r.get('LastClose') or r.get('PreClose'))
                op = _safe_float(r.get('Open'))
                now = _safe_float(r.get('Now'))
                if lc <= 0:
                    values.append(0.0)
                    continue

                # 缺口幅度
                gap_pct = (op - lc) / lc * 100
                # 当前涨幅
                chg_pct = (now - lc) / lc * 100

                # 缺口 > 1% 才算有效
                if abs(gap_pct) < 1.0:
                    values.append(0.0)
                    continue

                # 缺口回补比例 = (now - op) / (lc * gap_pct%) → 看 op→now 方向
                if gap_pct > 0:
                    # 高开: 回补=now低于op, 不回补=now在op之上
                    fill_ratio = (op - now) / abs(gap_pct * lc / 100) if gap_pct != 0 else 0
                    # 高开+回补>50%=看多, 高开+不回补=看空
                    raw = fill_ratio * (1 + abs(gap_pct) * 0.5)
                else:
                    # 低开: 回补=now高于op, 不回补=now在op之下
                    fill_ratio = (now - op) / abs(gap_pct * lc / 100) if gap_pct != 0 else 0
                    raw = fill_ratio * (1 + abs(gap_pct) * 0.5)

                values.append(max(-1.0, min(1.0, raw)))
            except Exception:
                values.append(0.0)

        idx = pd.Index(codes, name='code')
        return pd.Series(values, index=idx, dtype=float)

    def normalize(self, raw: pd.Series) -> pd.Series:
        return rank_normalize(raw)
