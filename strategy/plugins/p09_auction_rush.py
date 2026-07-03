"""p09: 竞价抢筹

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p09_auction_rush.py
用途: 开盘竞价不可撤单阶段 (9:20-9:25) 高开抢筹信号
依赖: 策略上下文 ctx (auction_df / more_info_df)
入库: qd_decisions (由 runner 写入)
条件:
  - 不可撤单阶段: auction_type == 'open' (开盘竞价)
  - 竞价缺口 > 3% (gap_pct > 3)
  - 量比 > 2 (auction_amount / 昨日成交额 CJJEPre1)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_GAP_MIN = 3.0
_VOL_RATIO_MIN = 2.0


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class AuctionRushStrategy(StrategyBase):
    name = 'auction_rush'
    version = '1.0'

    def required_fields(self):
        return ['gap_pct', 'auction_type', 'auction_amount', 'CJJEPre1']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        df = ctx.auction_df
        if df is None or df.empty:
            return []
        need = ['code', 'gap_pct', 'auction_type', 'auction_amount']
        if not all(c in df.columns for c in need):
            return []

        # 仅开盘竞价 (不可撤单阶段)
        opens = df[df['auction_type'] == 'open']
        if opens.empty:
            return []
        if 'auction_time' in opens.columns:
            opens = opens.sort_values('auction_time').groupby('code', as_index=False).last()
        else:
            opens = opens.groupby('code', as_index=False).last()

        # 昨成交额
        cjj_map = {}
        mi = ctx.more_info_df
        if mi is not None and not mi.empty and 'CJJEPre1' in mi.columns:
            for c, g in mi.groupby('code'):
                cjj_map[c] = _safe_float(g.iloc[-1]['CJJEPre1'])

        for _, r in opens.iterrows():
            gap = _safe_float(r.get('gap_pct'))
            if gap <= _GAP_MIN:
                continue
            amt = _safe_float(r.get('auction_amount'))
            cjj = cjj_map.get(r['code'], 0.0)
            vol_ratio = amt / cjj if cjj > 0 else 0.0
            if vol_ratio < _VOL_RATIO_MIN:
                continue
            decisions.append(Decision(
                action='buy', code=r['code'], strategy=self.name,
                reason=f'竞价抢筹: 缺口{gap:.2f}% 量比{vol_ratio:.2f}',
                position_pct=8, stop_loss=4, stop_profit=10,
                price=_safe_float(r.get('auction_price')),
                score=min(100.0, 60.0 + gap * 5),
            ))
        return decisions
