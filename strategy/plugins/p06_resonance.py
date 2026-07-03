"""p06: 多层共振

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p06_resonance.py
用途: 大盘 + 行业 + 个股三层同向涨且共振分数高的买点
依赖: 策略上下文 ctx (resonance_df)
入库: qd_decisions (由 runner 写入)
条件:
  - 大盘 + 行业 + 个股同向涨 (market_change>0, sector_change>0, stock_change>0)
  - resonance_score >= 80
  - 个股涨幅 > 3%
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_SCORE_MIN = 80.0
_STOCK_CHANGE_MIN = 3.0


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class ResonanceTripleStrategy(StrategyBase):
    name = 'resonance_triple'
    version = '1.0'

    def required_fields(self):
        return ['resonance_score', 'market_change', 'sector_change', 'stock_change']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        df = ctx.resonance_df
        if df is None or df.empty:
            return []
        need = ['code', 'resonance_score', 'market_change',
                'sector_change', 'stock_change']
        if not all(c in df.columns for c in need):
            return []

        for _, r in df.iterrows():
            score = _safe_float(r.get('resonance_score'))
            mkt = _safe_float(r.get('market_change'))
            sec = _safe_float(r.get('sector_change'))
            stk = _safe_float(r.get('stock_change'))
            if score < _SCORE_MIN:
                continue
            if not (mkt > 0 and sec > 0 and stk > 0):   # 三层同向涨
                continue
            if stk <= _STOCK_CHANGE_MIN:
                continue
            decisions.append(Decision(
                action='buy', code=r['code'], strategy=self.name,
                reason=f'三层共振: 大盘{mkt:.2f}% 板块{sec:.2f}% '
                       f'个股{stk:.2f}% 分数{score:.0f}',
                position_pct=10, stop_loss=5, stop_profit=10,
                price=0, score=score,
            ))
        return decisions
