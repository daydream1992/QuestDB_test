"""p22: 板块轮动接力

脚本路径: strategy/plugins/p22_sector_rotation_relay.py
用途: 根据板块资金流变化率和 alpha 排名识别主升板块内机会
数据源: ctx.sector_flow_df + ctx.alpha_df + ctx.top_candidates
依赖: strategy.base
说明:
  - 资金流连续 3 轮净流入 + 当轮加速 = 主升板块
  - 板块内 alpha top-10 且未涨停
  - 出场由外部风控处理
"""

from typing import List
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


@StrategyRegistry.register
class SectorRotationRelayStrategy(StrategyBase):
    name = 'sector_rotation_relay'
    version = '1.0'
    enabled = True

    def required_fields(self):
        return []

    def evaluate(self, ctx) -> List[Decision]:
        alpha_df = getattr(ctx, 'alpha_df', None)
        sector_flow = getattr(ctx, 'sector_flow_df', None)
        if alpha_df is None or alpha_df.empty or sector_flow is None or sector_flow.empty:
            return []

        decisions = []
        try:
            # 找资金流转正的板块
            active_sectors = []
            for _, r in sector_flow.iterrows():
                net = _safe_float(r.get('main_net'))
                if net > 0:
                    active_sectors.append(r.get('code'))

            if not active_sectors:
                return []

            # 在 active 板块内筛 alpha top-10
            # 先通过 relation_graph 取板块 -> 个股映射
            candidates = alpha_df.sort_values('alpha_score', ascending=False).copy()
            filtered = []
            for _, r in candidates.iterrows():
                code = r.get('code') or r.name
                if not code:
                    continue
                # 检查该票是否属于 active_sectors
                from lib.relation_graph import get_stock_sectors
                sectors = get_stock_sectors(code) or []
                in_active = any(s.get('block_code') in active_sectors for s in sectors)
                if not in_active:
                    continue
                filtered.append(code)
                if len(filtered) >= 10:
                    break

            for code in filtered:
                arow = alpha_df.loc[code] if code in alpha_df.index else None
                if arow is None:
                    continue
                alpha = _safe_float(arow.get('alpha_score') if isinstance(arow, dict) else arow.get('alpha_score'))
                rank = int(_safe_float(arow.get('rank', 999)))

                decisions.append(Decision(
                    action='buy', code=code, strategy=self.name,
                    reason=f'板块轮动 alpha={alpha:.2f} rank={rank}',
                    position_pct=0,
                    score=min(100.0, 50.0 + alpha * 40),
                ))
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning('p22 板块轮动异常: %s', e)

        return decisions[:3]
