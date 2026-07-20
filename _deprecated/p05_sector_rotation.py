"""p05: 板块轮动

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p05_sector_rotation.py
用途: 根据 rotation_signal 切换板块, 兑现(流出加速)板块卖出, 切入(流入加速)板块买龙头
依赖: 策略上下文 ctx (rotation_signal / pricevol_df / sector_flow_df / graph / positions)
入库: qd_decisions (由 runner 写入)
条件:
  - context.rotation_signal 存在且有轮动信号 (inflow_accelerate / outflow_accelerate)
  - 兑现板块 (流出加速): 卖出持仓中属于该板块的个股
  - 切入板块 (流入加速): 买入该板块内涨幅最大的龙头
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


def _safe_float(v, default=0.0) -> float:
    return StrategyBase.safe_float(v, default)


def _change_pct(now, lastclose) -> float:
    now = _safe_float(now)
    lastclose = _safe_float(lastclose)
    if lastclose <= 0:
        return 0.0
    return (now - lastclose) / lastclose * 100


@StrategyRegistry.register
class SectorRotationStrategy(StrategyBase):
    name = 'sector_rotation'
    version = '1.0'

    def required_fields(self):
        return ['Now', 'LastClose']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        rot = ctx.rotation_signal
        if not rot or not isinstance(rot, dict):
            return []
        rtype = rot.get('type')
        block_code = rot.get('block_code')
        if not rtype or rtype == 'insufficient' or not block_code:
            return []

        from lib.relation_graph import get_stock_sectors, get_sector_stocks, get_stock_name
        from lib.relation_graph import _sector_meta as _sector_meta_local

        # 兑现板块: 流出加速 → 卖出该板块持仓
        if rtype == 'outflow_accelerate':
            sector_codes = {block_code}
            block_name = _sector_meta_local.get(block_code, {}).get('sector_name', block_code)
            for p in ctx.positions or []:
                pcode = p.get('code')
                if not pcode:
                    continue
                in_sector = any(s.get('block_code') in sector_codes
                                for s in get_stock_sectors(pcode))
                if in_sector:
                    decisions.append(Decision(
                        action='sell', code=pcode, strategy=self.name,
                        reason=f'板块轮动兑现: {block_name} 流出加速 '
                               f'{rot.get("reason", "")}',
                        price=_safe_float(p.get('cost_price')),
                        score=70.0,
                    ))
            return decisions

        # 切入板块: 流入加速 → 买龙头 (板块内涨幅最大)
        if rtype == 'inflow_accelerate':
            block_name = _sector_meta_local.get(block_code, {}).get('sector_name', block_code)
            pv = ctx.pricevol_df
            if pv is None or pv.empty:
                return decisions
            members = get_sector_stocks(block_code)
            if not members:
                return decisions
            member_codes = {m.get('code') for m in members if m.get('code')}
            if 'snapshot_time' in pv.columns:
                pv_l = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
            else:
                pv_l = pv.groupby('code', as_index=False).last()
            cand = pv_l[pv_l['code'].isin(member_codes)]
            if cand.empty:
                return decisions
            cand = cand.copy()
            cand['chg'] = cand.apply(
                lambda r: _change_pct(r.get('Now'), r.get('LastClose')), axis=1)
            leader = cand.sort_values('chg', ascending=False).iloc[0]
            chg = float(leader['chg'])
            if chg <= 0:
                return decisions
            decisions.append(Decision(
                action='buy', code=leader['code'], strategy=self.name,
                reason=f'板块轮动切入: {block_name} 流入加速 龙头'
                       f'{get_stock_name(leader["code"])} 涨{chg:.2f}%',
                position_pct=10, stop_loss=5, stop_profit=10,
                price=_safe_float(leader.get('Now')), score=75.0,
            ))
        return decisions
