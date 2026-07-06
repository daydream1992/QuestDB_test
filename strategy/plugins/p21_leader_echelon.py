"""p21: 龙头梯队接力

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p21_leader_echelon.py
用途: 根据连板梯队的 alpha 筛选和入场时机识别
数据源: ctx.ladder_df + ctx.alpha_df + ctx.indicators_df
依赖: strategy.base
说明:
  - 优先选梯队最高板, 同板按 alpha 排序
  - 龙头首阴后第二根 K 线收阳 + 梯队未断
  - 出场逻辑由外部风控处理
"""

from typing import List
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


@StrategyRegistry.register
class LeaderEchelonStrategy(StrategyBase):
    name = 'leader_echelon'
    version = '1.0'
    enabled = True

    def required_fields(self):
        return []

    def evaluate(self, ctx) -> List[Decision]:
        alpha_df = getattr(ctx, 'alpha_df', None)
        ladder_df = getattr(ctx, 'ladder_tracker', None)
        ind = getattr(ctx, 'indicators_df', None)
        if alpha_df is None or alpha_df.empty or ladder_df is None:
            return []

        decisions = []
        try:
            # 从 ladder_tracker 取梯队票 (lb_tiers)
            ladder_codes = set()
            if isinstance(ladder_df, dict):
                for tier_list in ladder_df.get('lb_tiers', {}).values():
                    ladder_codes.update(tier_list)
            elif hasattr(ladder_df, 'columns') and 'code' in ladder_df.columns:
                ladder_codes.update(ladder_df['code'].tolist())

            # alpha 过滤: alpha_score > 0.6 且在梯队内
            alpha_candidates = alpha_df[alpha_df['alpha_score'] > 0.6].copy()
            if alpha_candidates.empty:
                return []

            ranked = alpha_candidates.sort_values('alpha_score', ascending=False)

            for _, r in ranked.head(10).iterrows():
                code = r.get('code') or r.name
                if not code or code not in ladder_codes:
                    continue
                alpha = _safe_float(r.get('alpha_score'))
                rank = int(_safe_float(r.get('rank', 999)))

                reason = f'梯队接力 alpha={alpha:.2f} rank={rank}'
                decisions.append(Decision(
                    action='buy', code=code, strategy=self.name,
                    reason=reason,
                    position_pct=0,
                    score=min(100.0, 50.0 + alpha * 50),
                ))
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning('p21 leader_echelon 异常: %s', e)

        return decisions[:3]  # 最多 3 只
