"""p08: 暗资金异动

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p08_dark_money.py
用途: 暗资金撤单差分异常 + 委托买卖比偏高的关注信号
依赖: 策略上下文 ctx (money_flow_df)
入库: qd_decisions (由 runner 写入)
条件:
  - 暗资金 cancel_diff 异常 (> 50)
  - 委托买卖比 wtb > 20
  - 输出 action='watch' (关注, 不直接买)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_CANCEL_DIFF_MIN = 10.0   # 暗资金异动最小幅度 (撤单差分绝对值) -- 从 50 下调
_WTB_MIN = 10.0            # 委比最小阈值 -- 从 20 下调


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class DarkMoneyAnomalyStrategy(StrategyBase):
    name = 'dark_money_anomaly'
    version = '1.0'

    def required_fields(self):
        # cancel_diff/wtb 不在 DDL，用 dark_money 和 pressure 字段计算
        return ['dark_money', 'buy_pressure', 'sell_pressure']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        df = ctx.money_flow_df
        if df is None or df.empty:
            return []
        if df is None or df.empty:
            return []
        required = ['dark_money', 'buy_pressure', 'sell_pressure']
        if 'code' not in df.columns or \
                not all(f in df.columns for f in required):
            return []

        # 每只股票最新一行
        if 'flow_time' in df.columns:
            df_l = df.sort_values('flow_time').groupby('code', as_index=False).last()
        else:
            df_l = df.groupby('code', as_index=False).last()

        for _, r in df_l.iterrows():
            # cancel_diff 用 dark_money 绝对值近似（暗资金撤单异常）
            cd = abs(_safe_float(r.get('dark_money')))
            bp = _safe_float(r.get('buy_pressure'))
            sp = _safe_float(r.get('sell_pressure'))
            # wtb = 委托买卖比 = 买盘压力 / 卖盘压力
            wtb = bp / sp if sp > 0 else 0.0
            if cd <= _CANCEL_DIFF_MIN or wtb <= _WTB_MIN:
                continue
            decisions.append(Decision(
                action='watch', code=r['code'], strategy=self.name,
                reason=f'暗资金异动: cancel_diff≈{cd:.0f} wtb={wtb:.2f}',
                score=min(100.0, 50.0 + cd * 0.5),
            ))
        return decisions
