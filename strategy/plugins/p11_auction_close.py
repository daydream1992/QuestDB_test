"""p11: 尾盘竞价异动

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p11_auction_close.py
用途: 14:57-15:00 尾盘竞价急涨(拉升)或急跌(砸盘)异动
依赖: 策略上下文 ctx (auction_df)
入库: qd_decisions (由 runner 写入)
条件 (auction_type == 'close' 尾盘竞价):
  - 急涨: gap_pct > 3 → watch (资金尾盘抢筹)
  - 急跌: gap_pct < -3 → warn (尾盘砸盘风险)
  gap_pct 为尾盘竞价相对前收盘的涨跌幅
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_PUMP_MIN = 3.0
_DUMP_MIN = -3.0


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class AuctionCloseStrategy(StrategyBase):
    name = 'auction_close'
    version = '1.0'

    def required_fields(self):
        return ['gap_pct', 'auction_type', 'auction_price']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        df = ctx.auction_df
        if df is None or df.empty:
            return []
        need = ['code', 'gap_pct', 'auction_type']
        if not all(c in df.columns for c in need):
            return []

        closes = df[df['auction_type'] == 'close']
        if closes.empty:
            return []
        if 'auction_time' in closes.columns:
            closes = closes.sort_values('auction_time').groupby('code', as_index=False).last()
        else:
            closes = closes.groupby('code', as_index=False).last()

        for _, r in closes.iterrows():
            gap = _safe_float(r.get('gap_pct'))
            if gap > _PUMP_MIN:
                decisions.append(Decision(
                    action='watch', code=r['code'], strategy=self.name,
                    reason=f'尾盘竞价急涨: gap={gap:.2f}% (尾盘抢筹)',
                    price=_safe_float(r.get('auction_price')),
                    score=min(100.0, 55.0 + gap * 5),
                ))
            elif gap < _DUMP_MIN:
                decisions.append(Decision(
                    action='warn', code=r['code'], strategy=self.name,
                    reason=f'尾盘竞价急跌: gap={gap:.2f}% (尾盘砸盘)',
                    price=_safe_float(r.get('auction_price')),
                    score=min(100.0, 55.0 + abs(gap) * 5),
                ))
        return decisions
