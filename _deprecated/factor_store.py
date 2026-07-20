"""因子快照落库

脚本路径: K:\QuestDB_test\\compute\\factor_store.py
用途: 把每轮 alpha_df 落到 qd_factor_snapshot / qd_alpha_score, 供回测和复盘
依赖: pandas / loguru / lib.qdb
说明:
  - 因子快照: 每只票每轮的各因子原始值 (含归一化后), 用于回放
  - alpha 快照: alpha_score + rank + decile + top_factors
  - 写入幂等 (DEDUP UPSERT KEYS), 重算可重写
"""

from datetime import datetime
from typing import Optional

import pandas as pd
from loguru import logger


def store_alpha_snapshot(con, alpha_df: pd.DataFrame,
                          coverage: Optional[dict] = None) -> int:
    """写 qd_alpha_score (alpha + rank + decile + top_factors)

    Returns:
        写入行数
    """
    if alpha_df is None or alpha_df.empty:
        return 0
    try:
        from lib.qdb import executemany_batch

        # 收集所有因子列 (排除计算结果列)
        result_cols = {'alpha_score', 'rank', 'decile', 'top_factors',
                       'sector', 'sector_rank', 'universe_rank'}
        factor_cols = [c for c in alpha_df.columns if c not in result_cols]

        cols = (['calc_time', 'code', 'alpha_score', 'rank', 'decile',
                 'sector_rank', 'top_factors'] + factor_cols)
        now = datetime.now()
        rows = []
        for code, r in alpha_df.iterrows():
            row = [
                now, str(code),
                float(r.get('alpha_score', 0)),
                int(r.get('rank', 0)),
                int(r.get('decile', 0)) if pd.notna(r.get('decile')) else -1,
                int(r.get('sector_rank', 0)) if pd.notna(r.get('sector_rank')) else 0,
                str(r.get('top_factors', '[]')),
            ]
            for fc in factor_cols:
                v = r.get(fc)
                if pd.isna(v):
                    row.append(None)
                else:
                    try:
                        row.append(float(v))
                    except (TypeError, ValueError):
                        row.append(None)
            rows.append(tuple(row))

        n = executemany_batch(con, 'qd_alpha_score', cols, rows)
        logger.debug('alpha 快照写入 {} 行 (覆盖率={})',
                     n, f'{sum(coverage.values())/len(coverage):.2f}'
                     if coverage else 'N/A')
        return n
    except Exception as e:
        logger.warning('alpha 快照写入失败: {}', e)
        return 0


def get_latest_alpha(con, lookback_minutes: int = 60) -> pd.DataFrame:
    """读最近 N 分钟的 alpha 快照 (供策略/回测用)

    Returns:
        DataFrame index=code, columns=[calc_time, alpha_score, rank, ...]
    """
    try:
        from lib.qdb import query_df
        sql = f"""
            SELECT * FROM qd_alpha_score
            WHERE calc_time > dateadd('n', -{int(lookback_minutes)}, now())
            ORDER BY calc_time DESC
        """
        df = query_df(con, sql)
        if df is None or df.empty:
            return pd.DataFrame()
        return df
    except Exception as e:
        logger.warning('读 alpha 快照失败: {}', e)
        return pd.DataFrame()
