"""横截面成交额异动因子

脚本路径: K:\QuestDB_test\\compute\\factors\\xs_amount_surge.py
用途: 当日成交额横截面 z-score, 解决"无横截面排序"问题
依赖: pandas / compute.factors
数据源: ctx.snapshot_focus_df (含 Amount 字段)
说明:
  - 同一时点, 全市场所有股票的 Amount 横截面比较
  - 成交额排名前 5% 的票 (z-score > 1.65) = 资金聚焦
  - 用 rank_normalize 而非 zscore, 因成交额分布厚尾 (少数票成交额是其他票的 100 倍)
  - direction=+1: 成交额大=资金关注=看多
"""

from typing import List, Optional

import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import rank_normalize


@FactorRegistry.register
class AmountSurge(FactorBase):
    """横截面成交额异动"""
    name = 'xs_amount_surge'
    version = '1.0'
    timeframe = 'tick'
    warmup_bars = 1
    direction = +1

    def required_inputs(self) -> List[str]:
        return ['snapshot_focus_df']

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        df = ctx.snapshot_focus_df
        if df is None or df.empty:
            return None
        if 'code' not in df.columns or 'Amount' not in df.columns:
            return None
        # 每只票最新一行 (focus_df 可能含历史)
        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in df.columns:
                time_col = c
                break
        if time_col:
            df = df.sort_values(time_col).groupby('code', as_index=False).last()
        else:
            df = df.groupby('code', as_index=False).last()
        # Amount 可能为 None / 字符串, 强转 float
        amounts = pd.to_numeric(df['Amount'], errors='coerce').dropna()
        amounts = amounts[amounts > 0]
        if len(amounts) < 10:
            return None
        return amounts

    def normalize(self, raw: pd.Series) -> pd.Series:
        # 成交额分布厚尾, 用 rank 而非 zscore
        return rank_normalize(raw) * 2  # 放大到 [-1, 1] 与 zscore 量级一致


@FactorRegistry.register
class SectorStrength(FactorBase):
    """板块相对强度: 个股所属板块的涨幅 - 全市场平均涨幅

    个股的 alpha 一部分来自板块 (Beta), 一部分来自自身 (Alpha)
    本因子提取板块 Beta 部分, 用于行业中性化
    """
    name = 'xs_sector_strength'
    version = '1.0'
    timeframe = 'tick'
    warmup_bars = 1
    direction = +1

    def required_inputs(self) -> List[str]:
        return ['pricevol_df']  # 全场价量, 含所有股票

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        df = ctx.pricevol_df
        if df is None or df.empty:
            return None
        if 'code' not in df.columns:
            return None
        # 涨幅: 需要 Now 和 LastClose
        if 'Now' not in df.columns or 'LastClose' not in df.columns:
            return None
        # 每只票最新一行
        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in df.columns:
                time_col = c
                break
        if time_col:
            df = df.sort_values(time_col).groupby('code', as_index=False).last()
        else:
            df = df.groupby('code', as_index=False).last()
        df = df.copy()
        df['Now'] = pd.to_numeric(df['Now'], errors='coerce')
        df['LastClose'] = pd.to_numeric(df['LastClose'], errors='coerce')
        df = df.dropna(subset=['Now', 'LastClose'])
        df = df[df['LastClose'] > 0]
        df['pct'] = (df['Now'] - df['LastClose']) / df['LastClose']
        if len(df) < 30:
            return None

        # 全市场平均
        market_avg = df['pct'].mean()

        # 取板块映射
        try:
            from lib.relation_graph import get_stock_sectors
            sector_pct = {}  # sector -> [pcts]
            code_sector = {}
            for _, r in df.iterrows():
                code = r['code']
                sectors = get_stock_sectors(code)
                if not sectors:
                    continue
                sn = sectors[0].get('block_name', 'UNKNOWN')
                code_sector[code] = sn
                sector_pct.setdefault(sn, []).append(r['pct'])
            # 板块均值
            sector_avg = {sn: sum(lst) / len(lst)
                          for sn, lst in sector_pct.items() if lst}
            # 个股的板块强度 = 板块均值 - 全市场均值
            result = {}
            for code, sn in code_sector.items():
                if sn in sector_avg:
                    result[code] = sector_avg[sn] - market_avg
            if len(result) < 10:
                return None
            return pd.Series(result, name=self.name)
        except Exception:
            return None

    def normalize(self, raw: pd.Series) -> pd.Series:
        return rank_normalize(raw) * 2
