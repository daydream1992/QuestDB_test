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
    """动态选重点池

    Args:
        pricevol_df: 全场价量 DataFrame (code/LastClose/Now/Volume)
        more_info_df: 88 字段 DataFrame (code/fHSL/fLianB/ZTPrice 等)

    Returns:
        list[str]: 重点池股票代码列表 (去重)
    """
    if pricevol_df is None or pricevol_df.empty:
        logger.warning('选股器: pricevol_df 为空')
        return []

    df = _latest_per_code(pricevol_df)
    codes_in = set(df['code'].tolist())

    # 涨幅
    df['change_pct'] = df.apply(
        lambda r: _safe_change(r.get('Now'), r.get('LastClose')), axis=1)

    pool = set()

    # 1. 涨幅前 100
    top_change = df.nlargest(TOP_N, 'change_pct')['code'].tolist()
    pool.update(top_change)

    # 2. 量比前 100 (用 Volume 代理, pricevol 无均量字段)
    if 'Volume' in df.columns:
        top_volume = df.nlargest(TOP_N, 'Volume')['code'].tolist()
        pool.update(top_volume)

    # 合并 more_info
    mi = _latest_per_code(more_info_df) if more_info_df is not None else pd.DataFrame()
    if not mi.empty:
        merge_cols = [c for c in ('fHSL', 'fLianB', 'ZTPrice') if c in mi.columns]
        if merge_cols:
            mi_sub = mi[['code'] + merge_cols].drop_duplicates('code')
            df = df.merge(mi_sub, on='code', how='left')

        # 3. 换手前 100
        if 'fHSL' in df.columns:
            df['fHSL'] = df['fHSL'].apply(lambda v: _safe_float(v))
            top_hsl = df.nlargest(TOP_N, 'fHSL')['code'].tolist()
            pool.update(top_hsl)

        # 4. 连板梯队 (fLianB > 0)
        if 'fLianB' in df.columns:
            lianb = df[df['fLianB'].apply(lambda v: _safe_float(v) > 0)]['code'].tolist()
            pool.update(lianb)

        # 5. 接近涨停 (距 ZTPrice 1% 以内)
        if 'ZTPrice' in df.columns:
            near_zt = df[df.apply(_near_zt, axis=1)]['code'].tolist()
            pool.update(near_zt)

    # 仅保留 pricevol 中存在的 code (合并可能引入空值)
    pool &= codes_in
    result = sorted(pool)
    detail = {
        'top_change': len(top_change),
        'top_volume': len(top_volume) if 'Volume' in df.columns else 0,
        'high_hsl': len(top_hsl) if 'fHSL' in df.columns else 0,
        'lianban': len(lianb) if 'fLianB' in df.columns else 0,
        'near_zt': len(near_zt) if 'ZTPrice' in df.columns else 0,
    }
    logger.info('选股器: 重点池 {} 只 (涨幅{} 量比{} 换手{} 连板{} 近涨停{})',
                len(result), detail['top_change'], detail['top_volume'],
                detail['high_hsl'], detail['lianban'], detail['near_zt'])
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
