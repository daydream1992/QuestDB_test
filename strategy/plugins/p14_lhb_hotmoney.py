"""p14: 游资龙虎榜

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p14_lhb_hotmoney.py
用途: 龙虎榜知名游资席位聚集 (>=3) 的买点, 机构同时买入则加分
依赖: 策略上下文 ctx (lhb_data)  lhb_data 由 strategy.lhb_analyzer.analyze 产出
入库: qd_decisions (由 runner 写入)
条件:
  - 知名游资 >= 3 (hotmoney_count >= 3)
  - 机构同时买入 (institution_count > 0) 可选加分
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_HOTMONEY_MIN = 3


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class LhbHotmoneyStrategy(StrategyBase):
    name = 'lhb_hotmoney'
    version = '1.0'

    def required_fields(self):
        return ['hotmoney_count', 'institution_count', 'net_buy']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        lhb = ctx.lhb_data
        if not lhb:
            return []

        for item in lhb:
            if not isinstance(item, dict):
                continue
            code = item.get('code')
            if not code:
                continue
            hm_cnt = int(item.get('hotmoney_count', 0) or 0)
            if hm_cnt < _HOTMONEY_MIN:
                continue
            inst_cnt = int(item.get('institution_count', 0) or 0)
            net_buy = _safe_float(item.get('net_buy'))
            bonus = ' 机构共振' if inst_cnt > 0 else ''
            action = 'buy' if inst_cnt > 0 else 'watch'
            decisions.append(Decision(
                action=action, code=code, strategy=self.name,
                reason=f'游资龙虎榜: 知名游资{hm_cnt}个 净买'
                       f'{net_buy / 1e8:.2f}亿{bonus}',
                position_pct=8 if action == 'buy' else 0,
                stop_loss=5, stop_profit=10,
                score=min(100.0, 60.0 + hm_cnt * 6 + inst_cnt * 5),
            ))
        return decisions
