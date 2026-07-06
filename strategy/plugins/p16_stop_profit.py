"""p16: 止盈退出

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p16_stop_profit.py
用途: 持仓盈利触及止盈线时输出卖出决策
依赖: 策略上下文 ctx (positions / pricevol_df)
入库: qd_decisions (由 runner 写入)
条件:
  - 持仓盈利 >= 止盈线: (Now - cost_price) / cost_price * 100 >= stop_profit
  - position.stop_profit 为正数百分比 (如 10 表示 +10%)
  - 输出 action='sell'
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


def _safe_float(v, default=0.0) -> float:
    return StrategyBase.safe_float(v, default)


@StrategyRegistry.register
class StopProfitStrategy(StrategyBase):
    name = 'stop_profit'
    version = '1.0'

    def required_fields(self):
        return ['code', 'entry_price', 'stop_profit', 'Now']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        positions = ctx.positions
        pv = ctx.pricevol_df
        if not positions or pv is None or pv.empty:
            return []

        if 'snapshot_time' in pv.columns:
            pv_l = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
        else:
            pv_l = pv.groupby('code', as_index=False).last()
        price_map = {}
        if 'Now' in pv_l.columns:
            for _, r in pv_l.iterrows():
                price_map[r['code']] = _safe_float(r['Now'])

        for p in positions:
            code = p.get('code')
            cost = _safe_float(p.get('entry_price'))
            sp = _safe_float(p.get('stop_profit'))
            if not code or cost <= 0 or sp <= 0:
                continue
            now = price_map.get(code, 0.0)
            if now <= 0:
                continue
            change_pct = (now - cost) / cost * 100
            if change_pct >= sp:
                decisions.append(Decision(
                    action='sell', code=code, strategy=self.name,
                    reason=f'止盈退出: 盈利{change_pct:.2f}% 达 +{sp:.0f}%'
                           f' (成本{cost:.2f} 现价{now:.2f})',
                    price=now, score=85.0,
                ))
        return decisions
