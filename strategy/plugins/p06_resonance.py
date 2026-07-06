"""p06: 多层共振

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p06_resonance.py
用途: 共振总分高 + 个股涨幅强的买点
依赖: 策略上下文 ctx (resonance_df + pricevol_df)
入库: qd_decisions (由 runner 写入)
条件:
  - resonance_df.total_score >= 80 (共振总分, 由 _run_resonance 落库 qd_resonance)
  - 个股涨幅 (pricevol Now/LastClose) > 3%
说明:
  - qd_resonance 表存 total_score/signal_type (见 ddl/09_resonance.sql),
    scan_market 内存产出的 market_change/sector_change/stock_change 落库时已丢弃;
    故本插件用 total_score + pricevol 涨幅组合判断 (C7 修复: 旧版读已丢弃列导致永远空返)。
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_SCORE_MIN = 70.0          # 共振总分阈，从 80 降到 70
_STOCK_CHANGE_MIN = 2.0    # 个股涨幅阈值，从 3% 降到 2%


def _safe_float(v, default=0.0) -> float:
    return StrategyBase.safe_float(v, default)


def _change_pct(now, lastclose) -> float:
    now = _safe_float(now)
    lastclose = _safe_float(lastclose)
    if lastclose <= 0:
        return 0.0
    return (now - lastclose) / lastclose * 100


@StrategyRegistry.register
class ResonanceTripleStrategy(StrategyBase):
    name = 'resonance_triple'
    version = '1.0'

    def required_fields(self):
        return ['total_score']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        df = ctx.resonance_df
        if df is None or df.empty:
            return []
        if not all(c in df.columns for c in ['code', 'total_score']):
            return []

        # 个股涨幅从 pricevol (qd_resonance 不存个股涨跌幅)
        chg_map = {}
        pv = ctx.pricevol_df
        if pv is not None and not pv.empty:
            if 'snapshot_time' in pv.columns:
                pv_l = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
            else:
                pv_l = pv.groupby('code', as_index=False).last()
            for _, r in pv_l.iterrows():
                chg_map[r['code']] = _change_pct(r.get('Now'), r.get('LastClose'))

        for _, r in df.iterrows():
            score = _safe_float(r.get('total_score'))
            if score < _SCORE_MIN:
                continue
            code = r['code']
            chg = chg_map.get(code, 0.0)
            if chg <= _STOCK_CHANGE_MIN:
                continue
            decisions.append(Decision(
                action='buy', code=code, strategy=self.name,
                reason=f'共振总分{score:.0f} 个股涨{chg:.2f}%',
                position_pct=10, stop_loss=5, stop_profit=10,
                price=0, score=score,
            ))
        return decisions
