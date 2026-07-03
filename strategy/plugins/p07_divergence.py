"""p07: 背离预警

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p07_divergence.py
用途: 顶背离预警 (个股涨但所属板块资金净流出), 提示减仓风险
依赖: 策略上下文 ctx (pricevol_df / sector_flow_df / graph)
入库: qd_decisions (由 runner 写入)
条件:
  - 顶背离: 个股涨幅 > 1% 且 所属板块资金净流出 (net_flow < 0)
  - 输出 action='warn'
说明:
  - resonance_df 由 scan_market 产出, divergence 全为 None (不含资金流)
  - 故本插件用 pricevol + sector_flow_df + 关系图谱自算顶背离
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_STOCK_CHANGE_MIN = 1.0   # 个股涨幅阈值


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _change_pct(now, lastclose) -> float:
    now = _safe_float(now)
    lastclose = _safe_float(lastclose)
    if lastclose <= 0:
        return 0.0
    return (now - lastclose) / lastclose * 100


@StrategyRegistry.register
class DivergenceWarnStrategy(StrategyBase):
    name = 'divergence_warn'
    version = '1.0'

    def required_fields(self):
        return ['Now', 'LastClose', 'net_flow']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        pv = ctx.pricevol_df
        sf = ctx.sector_flow_df
        if pv is None or pv.empty or sf is None or sf.empty:
            return []
        if 'block_code' not in sf.columns or 'net_flow' not in sf.columns:
            return []

        # 板块净流出现金 (net_flow<0 的板块集合)
        outflow_blocks = {}
        for _, r in sf.iterrows():
            nf = _safe_float(r.get('net_flow'))
            if nf < 0:
                outflow_blocks[r.get('block_code')] = nf
        if not outflow_blocks:
            return []

        # 个股最新涨幅
        if 'snapshot_time' in pv.columns:
            pv_l = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
        else:
            pv_l = pv.groupby('code', as_index=False).last()

        from lib.relation_graph import get_stock_sectors
        for _, r in pv_l.iterrows():
            code = r['code']
            chg = _change_pct(r.get('Now'), r.get('LastClose'))
            if chg <= _STOCK_CHANGE_MIN:
                continue
            # 个股所属板块是否有资金净流出
            hit_block, hit_flow = None, 0.0
            for s in get_stock_sectors(code):
                bc = s.get('block_code')
                if bc in outflow_blocks:
                    hit_block, hit_flow = bc, outflow_blocks[bc]
                    break
            if not hit_block:
                continue
            decisions.append(Decision(
                action='warn', code=code, strategy=self.name,
                reason=f'顶背离: 个股涨{chg:.2f}% 但板块{hit_block}'
                       f'资金净流出{hit_flow:.0f}',
                price=_safe_float(r.get('Now')), score=60.0,
            ))
        return decisions
