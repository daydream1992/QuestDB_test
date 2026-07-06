"""因子归一化工具

脚本路径: K:\QuestDB_test\\compute\\factors\\_normalize.py
用途: 提供 winsorize / zscore / rank_normalize 等归一化函数
依赖: pandas / numpy
说明:
  - 因子原始值量级千差万别 (动量 0.05 vs 成交额 1e8), 必须归一化才能加权组合
  - winsorize_zscore: 默认, 适合近似正态分布 (动量/微结构失衡)
  - rank_normalize: 厚尾分布更稳健 (成交额/资金流, 极值多)
  - robust_zscore: 用中位数+MAD, 抗异常点
"""

import numpy as np
import pandas as pd


def winsorize_zscore(s: pd.Series, lower: float = 0.01,
                     upper: float = 0.99) -> pd.Series:
    """1%/99% 缩尾 + z-score 标准化

    Args:
        s: 原始因子值 (index=code)
        lower, upper: 缩尾分位数

    Returns:
        归一化后的 Series (均值 0, 标准差 1)
    """
    if s is None or s.empty:
        return pd.Series(dtype='float64')
    s = s.astype('float64').replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) < 3:
        return pd.Series(dtype='float64')
    q_lo = s.quantile(lower)
    q_hi = s.quantile(upper)
    s = s.clip(q_lo, q_hi)
    mu = s.mean()
    sigma = s.std()
    if sigma == 0 or np.isnan(sigma):
        return pd.Series(0.0, index=s.index)
    return (s - mu) / sigma


def rank_normalize(s: pd.Series) -> pd.Series:
    """百分位归一化到 [0, 1]

    对厚尾分布 (成交额/资金流) 更稳健, 极值不会被压扁
    """
    if s is None or s.empty:
        return pd.Series(dtype='float64')
    s = s.astype('float64').replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) < 3:
        return pd.Series(dtype='float64')
    return s.rank(pct=True) - 0.5  # 居中到 [-0.5, 0.5] 便于与 zscore 混用


def robust_zscore(s: pd.Series, k: float = 1.4826) -> pd.Series:
    """基于中位数 + MAD 的稳健 z-score

    抗异常点, 适合有黑天鹅的因子 (如撤单率)
    """
    if s is None or s.empty:
        return pd.Series(dtype='float64')
    s = s.astype('float64').replace([np.inf, -np.inf], np.nan).dropna()
    if len(s) < 3:
        return pd.Series(dtype='float64')
    med = s.median()
    mad = (s - med).abs().median()
    if mad == 0:
        return pd.Series(0.0, index=s.index)
    return (s - med) / (k * mad)


def cross_section_zscore_by_group(s: pd.Series,
                                   groups: pd.Series) -> pd.Series:
    """按组 (e.g. 板块) 做横截面 z-score

    用于行业中性化: 同板块内排名, 而非全市场排名
    Args:
        s: 因子值 (index=code)
        groups: 分组标签 (index=code, value=板块名)
    """
    if s is None or s.empty:
        return pd.Series(dtype='float64')
    df = pd.DataFrame({'val': s, 'group': groups})
    result = df.groupby('group')['val'].transform(
        lambda x: winsorize_zscore(x) if len(x) >= 3 else x * 0
    )
    return result
