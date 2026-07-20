"""p13: 机构龙虎榜

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p13_lhb_inst.py
用途: 龙虎榜机构席位净买占多 (>=2 席位) 且净额为正的买点
依赖: 策略上下文 ctx (lhb_data)  lhb_data 由 strategy.lhb_analyzer.analyze 产出
入库: qd_decisions (由 runner 写入)
条件:
  - 龙虎榜机构净买席位 >= 2 (institution_count >= 2)
  - 净额 > 0 (net_buy > 0)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_INST_SEATS_MIN = 2


def _safe_float(v, default=0.0) -> float:
    return StrategyBase.safe_float(v, default)


@StrategyRegistry.register
class LhbInstitutionStrategy(StrategyBase):
    name = 'lhb_institution'
    version = '1.0'

    def required_fields(self):
        return ['institution_count', 'net_buy']

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
            inst_cnt = int(item.get('institution_count', 0) or 0)
            net_buy = _safe_float(item.get('net_buy'))
            if inst_cnt < _INST_SEATS_MIN or net_buy <= 0:
                continue
            decisions.append(Decision(
                action='buy', code=code, strategy=self.name,
                reason=f'机构龙虎榜: 机构席位{inst_cnt}个 净买{net_buy / 1e8:.2f}亿',
                position_pct=10, stop_loss=5, stop_profit=15,
                score=min(100.0, 65.0 + inst_cnt * 8),
            ))
        return decisions
