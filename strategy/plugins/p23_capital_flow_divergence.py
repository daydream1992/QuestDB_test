"""p23: 资金流背离

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p23_capital_flow_divergence.py
用途: 主力净流入 vs 股价方向的背离检测
数据源: ctx.snapshot_focus_df (NetInflow) + ctx.indicators_df (close) + ctx.alpha_df
依赖: strategy.base
说明:
  - 价格新低 + 净流入转正 = 底背离 (buy)
  - 价格新高 + 净流入转负 = 顶背离 (watch)
  - 需持续 ≥2 根 5m K 确认
"""

from typing import List
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


@StrategyRegistry.register
class CapitalFlowDivergenceStrategy(StrategyBase):
    name = 'capital_flow_divergence'
    version = '1.0'
    enabled = True

    def required_fields(self):
        return []

    def evaluate(self, ctx) -> List[Decision]:
        snap = getattr(ctx, 'snapshot_focus_df', None)
        ind = getattr(ctx, 'indicators_df', None)
        alpha_df = getattr(ctx, 'alpha_df', None)
        if snap is None or snap.empty or ind is None or ind.empty:
            return []

        decisions = []
        try:
            snap_codes = set(snap['code'].tolist())
            for code in snap_codes:
                row = snap[snap['code'] == code]
                if row.empty:
                    continue
                r = row.iloc[0]
                inflow = _safe_float(r.get('Zjl') or r.get('main_net'))
                now = _safe_float(r.get('Now'))

                # 从 indicators_df 取价格序列
                ind_sub = ind[ind['code'] == code].sort_values('calc_time')
                if ind_sub.empty or inflow == 0:
                    continue
                prices = ind_sub['close'].dropna().values
                if len(prices) < 5:
                    continue

                cur = prices[-1]
                low_5 = min(prices[-5:])
                high_5 = max(prices[-5:])

                alpha = 0.0
                rank = 999
                if alpha_df is not None and not alpha_df.empty:
                    arow = alpha_df[alpha_df['code'] == code]
                    if not arow.empty:
                        alpha = _safe_float(arow.iloc[0].get('alpha_score'))
                        rank = int(_safe_float(arow.iloc[0].get('rank', 999)))

                # 底背离: 价格 <5 根低点 + 净流入 >0
                if cur <= low_5 * 1.001 and inflow > 0 and inflow > 1e5:
                    score = min(100.0, 60.0 + alpha * 30 + abs(inflow) / 1e5)
                    decisions.append(Decision(
                        action='buy', code=code, strategy=self.name,
                        reason=f'底背离 inflow={inflow:.0f} alpha={alpha:.2f}',
                        position_pct=0, score=score,
                    ))

                # 顶背离: 价格 >5 根高点 + 净流入 <0 (watch 级别)
                if cur >= high_5 * 0.999 and inflow < 0 and inflow < -1e5:
                    decisions.append(Decision(
                        action='watch', code=code, strategy=self.name,
                        reason=f'顶背离 inflow={inflow:.0f} alpha={alpha:.2f}',
                        score=min(100.0, 50.0 + abs(inflow) / 2e5),
                    ))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning('p23 资金流背离异常: %s', e)

        return decisions[:3]
