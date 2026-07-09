"""选股器

脚本路径: K:\QuestDB_test\\strategy\\selector.py
用途: 动态筛选重点监控池, 聚合涨幅/量比/换手/连板/接近涨停多维度, 去重后 300-500 只
依赖: pandas, loguru
数据源:
  - pricevol_df  全场价量 (code/LastClose/Now/Volume)
  - more_info_df 88 字段 (fHSL 换手 / fLianB 连板 / ZTPrice 涨停价)
筛选维度:
  - 涨幅前 100   (Now/LastClose - 1)
  - 量比前 100   (用 Volume 代理, pricevol 无均量字段)
  - 换手前 100   (more_info.fHSL)
  - 连板梯队     (more_info.fLianB > 0)
  - 接近涨停     (距 ZTPrice 1% 以内)
说明:
  - 合并 pricevol + more_info (按 code 取最新一行)
  - 多维度并集去重, 通常 300-500 只
"""

import pandas as pd
from loguru import logger
from typing import Tuple

TOP_N = 100
NEAR_ZT_THRESHOLD = 0.01  # 接近涨停: 距涨停价 1% 以内


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _latest_per_code(df) -> pd.DataFrame:
    """按 code 取最新一行 (按 snapshot_time/date 倒序)"""
    if df is None or df.empty:
        return pd.DataFrame()
    df = df.copy()
    if 'snapshot_time' in df.columns:
        df = df.sort_values('snapshot_time').groupby('code', as_index=False).last()
    elif 'date' in df.columns:
        df = df.sort_values('date').groupby('code', as_index=False).last()
    else:
        df = df.groupby('code', as_index=False).last()
    return df


def select_focus_pool(pricevol_df, more_info_df) -> Tuple[list, dict]:
    """动态选重点池 (仅连板梯队)

    Args:
        pricevol_df: 全场价量 DataFrame (code/LastClose/Now/Volume)
        more_info_df: 88 字段 DataFrame (code/fHSL/fLianB/ZTPrice 等)

    Returns:
        list[str]: 连板股票代码列表 (去重)
    """
    if pricevol_df is None or pricevol_df.empty:
        logger.warning('选股器: pricevol_df 为空')
        return [], {}

    df = _latest_per_code(pricevol_df)

    # 仅保留有板块映射的 code
    codes_in = set(df['code'].tolist())

    pool = set()

    # 合并 more_info
    mi = _latest_per_code(more_info_df) if more_info_df is not None else pd.DataFrame()
    if not mi.empty:
        merge_cols = [c for c in ('fLianB',) if c in mi.columns]
        if merge_cols:
            mi_sub = mi[['code'] + merge_cols].drop_duplicates('code')
            df = df.merge(mi_sub, on='code', how='left')

        # 仅保留连板梯队 (fLianB > 0)
        if 'fLianB' in df.columns:
            df['fLianB'] = df['fLianB'].apply(lambda v: _safe_float(v))
            lianb = df[df['fLianB'] > 0]['code'].tolist()
            pool.update(lianb)

    # 仅保留 pricevol 中存在的 code
    pool &= codes_in
    result = sorted(pool)
    detail = {'lianban': len(result)}
    logger.info('选股器: 连板池 {} 只', len(result))
    return result, detail


def _safe_change(now, lastclose) -> float:
    try:
        now = float(now)
        lastclose = float(lastclose)
        if lastclose <= 0:
            return 0.0
        return (now - lastclose) / lastclose * 100
    except (TypeError, ValueError):
        return 0.0


def _near_zt(row) -> bool:
    """判断是否接近涨停 (距涨停价 1% 以内)"""
    zt = _safe_float(row.get('ZTPrice'))
    now = _safe_float(row.get('Now'))
    if zt <= 0 or now <= 0:
        return False
    return (zt - now) / zt < NEAR_ZT_THRESHOLD and now <= zt
