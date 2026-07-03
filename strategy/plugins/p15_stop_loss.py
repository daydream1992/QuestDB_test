"""p15: 止损退出

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p15_stop_loss.py
用途: 持仓亏损触及止损线时输出卖出决策
依赖: 策略上下文 ctx (positions / pricevol_df)
入库: qd_decisions (由 runner 写入)
条件:
  - 持仓亏损 <= 止损线: (Now - cost_price) / cost_price * 100 <= -stop_loss
  - position.stop_loss 为正数百分比 (如 5 表示 -5%)
  - 输出 action='sell'
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class StopLossStrategy(StrategyBase):
    name = 'stop_loss'
    version = '1.0'

    def required_fields(self):
        return ['code', 'entry_price', 'stop_loss', 'Now']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        positions = ctx.positions
        pv = ctx.pricevol_df
        if not positions or pv is None or pv.empty:
            return []

        # 个股最新价 (按 code 取最新一行 Now)
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
            sl = _safe_float(p.get('stop_loss'))
            if not code or cost <= 0 or sl <= 0:
                continue
            now = price_map.get(code, 0.0)
            if now <= 0:
                continue
            change_pct = (now - cost) / cost * 100
            if change_pct <= -sl:
                decisions.append(Decision(
                    action='sell', code=code, strategy=self.name,
                    reason=f'止损退出: 亏损{change_pct:.2f}% 达 -{sl:.0f}%'
                           f' (成本{cost:.2f} 现价{now:.2f})',
                    price=now, score=80.0,
                ))
        return decisions
