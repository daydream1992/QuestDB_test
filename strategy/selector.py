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
    """动态选重点池 (连板梯队)

    连板判定 (2026-07-14 修正): LastZTHzNum(几板)>=1 或 EverZTCount(连板天)>0,
    字段来自 qd_stock_daily。旧版用 fLianB 是双重 BUG: (1) fLianB=量比非连板
    (docs/通达信量化平台说明书/.../获取股票更多信息.md:39); (2) 旧 _get_focus_codes
    的 SQL 只 SELECT code, 连板字段根本没进来 → 池恒空 (与有无连板无关)。
    正确连板字段 = LastZTHzNum(主)/EverZTCount(兜底), 已在 qd_stock_daily。

    Args:
        pricevol_df: 全场价量 DataFrame (code/...)
        more_info_df: qd_stock_daily 行 (含 LastZTHzNum/EverZTCount)

    Returns:
        list[str]: 连板股票代码 (按连板高度降序, 截断 ≤800 防 c3 COM 过载)
    """
    if pricevol_df is None or pricevol_df.empty:
        logger.warning('选股器: pricevol_df 为空')
        return [], {}

    df = _latest_per_code(pricevol_df)
    codes_in = set(df['code'].tolist())
    pool_codes: list = []

    mi = _latest_per_code(more_info_df) if more_info_df is not None else pd.DataFrame()
    if not mi.empty:
        lb_cols = [c for c in ('LastZTHzNum', 'EverZTCount') if c in mi.columns]
        if lb_cols:
            mi_sub = mi[['code'] + lb_cols].drop_duplicates('code')
            df = df.merge(mi_sub, on='code', how='left')
            for c in lb_cols:
                df[c] = df[c].apply(_safe_float)
            if 'LastZTHzNum' in df.columns and 'EverZTCount' in df.columns:
                mask = (df['LastZTHzNum'].fillna(0) >= 1) | (df['EverZTCount'].fillna(0) > 0)
                hit = df[mask].sort_values('LastZTHzNum', ascending=False)
                pool_codes = hit['code'].head(800).tolist()
            elif 'EverZTCount' in df.columns:
                pool_codes = df[df['EverZTCount'].fillna(0) > 0]['code'].head(800).tolist()

    pool = set(pool_codes) & codes_in
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
