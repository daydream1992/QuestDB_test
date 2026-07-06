"""p10: 竞价缺口异动

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p10_auction_gap.py
用途: 开盘竞价平开巨量 或 低开异动两种异常形态
依赖: 策略上下文 ctx (auction_df / more_info_df)
入库: qd_decisions (由 runner 写入)
条件 (满足其一即触发 watch):
  - 平开巨量: |gap_pct| < 0.5 且 量比 > 3
  - 低开异动: gap_pct < -3 且 量比 > 2
  量比 = auction_amount / 昨日成交额 (CJJEPre1)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_FLAT_GAP_MAX = 0.5
_FLAT_VOL_MIN = 3.0
_LOW_GAP_MAX = -3.0
_LOW_VOL_MIN = 2.0


def _safe_float(v, default=0.0) -> float:
    return StrategyBase.safe_float(v, default)


@StrategyRegistry.register
class AuctionGapStrategy(StrategyBase):
    name = 'auction_gap'
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

        opens = df[df['auction_type'] == 'open']
        if opens.empty:
            return []
        if 'auction_time' in opens.columns:
            opens = opens.sort_values('auction_time').groupby('code', as_index=False).last()
        else:
            opens = opens.groupby('code', as_index=False).last()

        cjj_map = {}
        mi = ctx.more_info_df
        if mi is not None and not mi.empty and 'CJJEPre1' in mi.columns:
            if 'snapshot_time' in mi.columns:
                mi = mi.sort_values('snapshot_time')
            for c, g in mi.groupby('code'):
                cjj_map[c] = _safe_float(g.iloc[-1]['CJJEPre1'])

        for _, r in opens.iterrows():
            gap = _safe_float(r.get('gap_pct'))
            amt = _safe_float(r.get('auction_amount'))
            cjj = cjj_map.get(r['code'], 0.0)
            vol_ratio = amt / cjj if cjj > 0 else 0.0

            tag = None
            if abs(gap) < _FLAT_GAP_MAX and vol_ratio > _FLAT_VOL_MIN:
                tag = '平开巨量'
            elif gap < _LOW_GAP_MAX and vol_ratio > _LOW_VOL_MIN:
                tag = '低开异动'
            if not tag:
                continue
            decisions.append(Decision(
                action='watch', code=r['code'], strategy=self.name,
                reason=f'竞价缺口异动: {tag} gap={gap:.2f}% 量比{vol_ratio:.2f}',
                price=_safe_float(r.get('auction_price')),
                score=min(100.0, 55.0 + vol_ratio * 8),
            ))
        return decisions
