"""xs_lhb_broker_rank: 龙虎榜营业部横截面因子

脚本路径: K:\QuestDB_test\\compute\\factors\\xs_lhb_broker_rank.py
用途: 统计近 5 日知名游资营业部上榜的票的质量评分
数据源: QuestDB qd_lhb_detail 表 (T+1 数据)
依赖: compute.factors.base, config.broker_list
说明:
  - 知名游资/机构席位上榜越多 = 营业部质量分越高
  - T+1 数据, 开盘即可用
"""

import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import rank_normalize
from config.broker_list import FAMOUS_BROKERS
from compute.factors._normalize import rank_normalize

# 营业部质量分 (按身份标识)
_BROKER_QUALITY = {
    'hot_money_xz': 1, 'hot_money_hz': 2, 'hot_money_zg': 3, 'hot_money_zx': 3,
    'hot_money_ht': 2, 'hot_money_gs': 2, 'hot_money_zs': 2, 'hot_money_pa': 1,
    'hot_money_sw': 2, 'hot_money_ht2': 2, 'hot_money_gd': 3, 'hot_money_fz': 2,
    'hot_money_zs2': 2, 'institution': 5, 'north_sh': 4, 'north_sz': 4,
}


@FactorRegistry.register
class LhbBrokerRank(FactorBase):
    name = 'xs_lhb_broker_rank'
    version = '1.0'
    timeframe = 'daily'
    warmup_bars = 1
    direction = +1

    def required_inputs(self) -> list:
        return []

    def compute_raw(self, ctx) -> pd.Series:
        """计算营业部质量分

        Returns:
            pd.Series: index=code, value=营业部质量分 (越大越好)
        """
        from lib.qdb import connect, query_df, cutoff

        con = connect()
        try:
            df = query_df(con,
                "SELECT code, buy_amount, sell_amount, broker_name "
                "FROM qd_lhb_detail "
                f"WHERE lhb_date > '{cutoff(days=5)}'")
        finally:
            con.close()

        if df is None or df.empty:
            return pd.Series(dtype=float)

        # 聚合: 每只票的营业部质量分
        score_map = {}
        for _, r in df.iterrows():
            code = r.get('code')
            if not code:
                continue
            broker_name = str(r.get('broker_name', ''))
            buy_amt = _safe_float(r.get('buy_amount'))
            sell_amt = _safe_float(r.get('sell_amount'))
            net = buy_amt - sell_amt

            # 查 _BROKER_QUALITY 或通过 FAMOUS_BROKERS 映射
            identity = FAMOUS_BROKERS.get(broker_name)
            if identity:
                quality = _BROKER_QUALITY.get(identity, 0)
            else:
                quality = 0
                for pattern, iden in FAMOUS_BROKERS.items():
                    if pattern in broker_name:
                        quality = _BROKER_QUALITY.get(iden, 0)
                        break

            if quality > 0 and net > 0:
                # 好营业部净买入 = 加分
                score_map[code] = score_map.get(code, 0) + quality * (net / 1e6)

        if not score_map:
            return pd.Series(dtype=float)

        return pd.Series(score_map, dtype=float)

    def normalize(self, raw: pd.Series) -> pd.Series:
        return rank_normalize(raw)
