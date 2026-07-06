"""横截面排序

脚本路径: K:\QuestDB_test\\compute\\ranking.py
用途: 把 alpha_df 排序为可执行的候选池, 支持行业中性
依赖: pandas / loguru / lib.relation_graph
说明:
  - rank_universe: 简单截断 top-N
  - rank_sector_neutral: 板块内排序 + 每板块上限, 避免单一板块过度集中
  - rank_decay_blend: 与上一轮排名加权混合, 减少排名跳变 (降低换手)
"""

from typing import Optional

import pandas as pd
from loguru import logger


def rank_universe(alpha_df: pd.DataFrame, top_n: int = 50) -> pd.DataFrame:
    """简单截断: 全市场按 alpha_score 排序, 取 top-N

    Returns:
        alpha_df 的子集, 含原始列 + 'universe_rank' 列
    """
    if alpha_df is None or alpha_df.empty:
        return pd.DataFrame()
    result = alpha_df.copy()
    result = result.nsmallest(top_n, 'rank')
    result['universe_rank'] = range(1, len(result) + 1)
    return result


def rank_sector_neutral(alpha_df: pd.DataFrame,
                         sector_map: Optional[dict] = None,
                         top_n: int = 50,
                         per_sector_max: int = 5) -> pd.DataFrame:
    """行业中性排序

    1. 给每只票打 sector 标签 (从 sector_map 或 relation_graph 取)
    2. 板块内按 alpha_score 排序, 每板块最多取 per_sector_max 只
    3. 全市场再按 alpha 取 top-N

    Args:
        alpha_df: AlphaEngine.compute 的输出
        sector_map: {code: sector_name}; None 则从 lib.relation_graph 取
        top_n: 全市场最大候选数
        per_sector_max: 单板块最大候选数

    Returns:
        候选池 DataFrame, 含 'sector' / 'sector_rank' / 'universe_rank' 列
    """
    if alpha_df is None or alpha_df.empty:
        return pd.DataFrame()

    result = alpha_df.copy()

    # 1. 打 sector 标签
    if sector_map is None:
        try:
            from lib.relation_graph import get_stock_sectors
            sector_map = {}
            for code in result.index:
                sectors = get_stock_sectors(code)
                if sectors:
                    sector_map[code] = sectors[0].get('block_name', 'UNKNOWN')
                else:
                    sector_map[code] = 'UNKNOWN'
        except Exception as e:
            logger.warning('取板块映射失败, 全部标 UNKNOWN: {}', e)
            sector_map = {c: 'UNKNOWN' for c in result.index}
    result['sector'] = result.index.map(lambda c: sector_map.get(c, 'UNKNOWN'))

    # 2. 板块内排名
    result['sector_rank'] = result.groupby('sector')['alpha_score']\
        .rank(ascending=False, method='min').astype(int)

    # 3. 板块上限过滤
    candidates = result[result['sector_rank'] <= per_sector_max].copy()

    # 4. 全市场 top-N
    candidates = candidates.nsmallest(top_n, 'rank')
    candidates['universe_rank'] = range(1, len(candidates) + 1)

    logger.debug('行业中性排序: {} 候选 (来自 {} 板块)',
                 len(candidates), candidates['sector'].nunique())
    return candidates


def rank_decay_blend(current_df: pd.DataFrame,
                      prev_df: Optional[pd.DataFrame],
                      decay: float = 0.3) -> pd.DataFrame:
    """排名衰减混合: 当前 alpha 与上一轮 alpha 加权, 减少排名跳变

    Args:
        current_df: 当前轮 alpha_df
        prev_df: 上一轮 alpha_df (None 则不混合)
        decay: 上一轮权重 (0-1), 越大越平滑

    Returns:
        新的 alpha_df, alpha_score 已被混合, rank 已重算
    """
    if current_df is None or current_df.empty:
        return pd.DataFrame()
    if prev_df is None or prev_df.empty or decay <= 0:
        return current_df

    result = current_df.copy()
    # 对齐 index
    common = result.index.intersection(prev_df.index)
    if len(common) < 10:
        return result

    blended = (result.loc[common, 'alpha_score'] * (1 - decay)
               + prev_df.loc[common, 'alpha_score'] * decay)
    result.loc[common, 'alpha_score'] = blended
    result['rank'] = result['alpha_score'].rank(ascending=False, method='min').astype(int)
    return result
