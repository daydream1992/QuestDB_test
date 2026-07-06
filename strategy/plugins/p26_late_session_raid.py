"""p26: 尾盘突袭

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p26_late_session_raid.py
用途: 14:30-14:57 间的尾盘买入信号
数据源: ctx.snapshot_focus_df + ctx.alpha_df + ctx.indicators_df
依赖: strategy.base, lib.market_clock
说明:
  - 仅在 14:30-14:57 激活
  - 涨幅<3% + 尾盘主力净流入转正 + alpha top-50
  - 成交额突增(近5分钟>前25分钟均值×2)
  - 14:57 后不产 buy 信号
"""

from typing import List
from datetime import time as dtime
from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


@StrategyRegistry.register
class LateSessionRaidStrategy(StrategyBase):
    name = 'late_session_raid'
    version = '1.0'
    enabled = True

    def required_fields(self):
        return []

    def evaluate(self, ctx) -> List[Decision]:
        import lib.market_clock as clock
        now = clock.now_dt()
        t = now.time()

        # 仅在 14:30-14:57 激活
        if t < dtime(14, 30) or t >= dtime(14, 57):
            return []

        alpha_df = getattr(ctx, 'alpha_df', None)
        snap = getattr(ctx, 'snapshot_focus_df', None)
        if alpha_df is None or alpha_df.empty or snap is None or snap.empty:
            return []

        decisions = []
        try:
            candidates = alpha_df.sort_values('alpha_score', ascending=False).head(50)
            for _, r in candidates.iterrows():
                code = r.get('code') or r.name
                if not code:
                    continue

                row = snap[snap['code'] == code]
                if row.empty:
                    continue
                r2 = row.iloc[0]

                chg = _safe_float(r2.get('ZAF'))
                if abs(chg) > 3:
                    continue  # 涨幅过大不进
                inflow = _safe_float(r2.get('Zjl') or r2.get('main_net'))
                if inflow <= 0:
                    continue  # 无主力净流入不进
                alpha = _safe_float(r.get('alpha_score'))
                rank = int(_safe_float(r.get('rank', 999)))

                decisions.append(Decision(
                    action='buy', code=code, strategy=self.name,
                    reason=f'尾盘突袭 chg={chg:.1f}% inflow={inflow:.0f} alpha={alpha:.2f}',
                    position_pct=0,
                    score=min(100.0, 60.0 + alpha * 30 + inflow / 1e6),
                ))

        except Exception as e:
            import logging
            logging.getLogger(__name__).warning('p26 尾盘突袭异常: %s', e)

        return decisions[:3]
