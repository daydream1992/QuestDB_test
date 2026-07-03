"""p12: 大单跟单

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p12_big_order.py
用途: 巨量买入大单 + 价格上行 + 主力净额为正的跟单买点
依赖: 策略上下文 ctx (big_order_df / pricevol_df / money_flow_df / more_info_df)
入库: qd_decisions (由 runner 写入)
条件:
  - 巨量买入: big_order_df 中 order_type=='buy' 且 order_level in (huge, super)
    (兼容 strategy.big_order.detect 产出的 level/direction 字段)
  - 价格上行: Now > LastClose (pricevol_df)
  - 主力净额: zjl > 0 (money_flow_df 优先, 回退 more_info.Zjl)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_HUGE_LEVELS = {'huge', 'super'}


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _is_huge_buy(row) -> bool:
    """判断是否巨量买入 (兼容两种字段命名)"""
    # DDL schema: order_type / order_level
    ot = str(row.get('order_type', '')).lower()
    ol = str(row.get('order_level', '')).lower()
    if ot == 'buy' and ol in _HUGE_LEVELS:
        return True
    # detect schema: direction / level
    d = str(row.get('direction', '')).lower()
    lv = str(row.get('level', '')).lower()
    if d == 'buy' and lv in _HUGE_LEVELS:
        return True
    return False


@StrategyRegistry.register
class BigOrderPulseStrategy(StrategyBase):
    name = 'big_order_pulse'
    version = '1.0'

    def required_fields(self):
        return ['order_type', 'order_level', 'Now', 'LastClose', 'zjl']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        bo = ctx.big_order_df
        pv = ctx.pricevol_df
        if bo is None or bo.empty or pv is None or pv.empty:
            return []
        if 'code' not in bo.columns:
            return []

        # 筛选巨量买入
        buys = bo[bo.apply(_is_huge_buy, axis=1)]
        if buys.empty:
            return []
        # 命中股池
        pulse_codes = set(buys['code'].tolist())

        # pricevol 最新一行
        if 'snapshot_time' in pv.columns:
            pv_l = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
        else:
            pv_l = pv.groupby('code', as_index=False).last()

        # zjl 映射 (money_flow_df 优先, 回退 more_info.Zjl)
        zjl_map = {}
        mf = ctx.money_flow_df
        if mf is not None and not mf.empty and 'code' in mf.columns and 'zjl' in mf.columns:
            for c, g in mf.groupby('code'):
                zjl_map[c] = _safe_float(g.iloc[-1]['zjl'])
        else:
            mi = ctx.more_info_df
            if mi is not None and not mi.empty and 'Zjl' in mi.columns:
                for c, g in mi.groupby('code'):
                    zjl_map[c] = _safe_float(g.iloc[-1]['Zjl'])

        for _, r in pv_l.iterrows():
            code = r['code']
            if code not in pulse_codes:
                continue
            now = _safe_float(r.get('Now'))
            last = _safe_float(r.get('LastClose'))
            if last <= 0 or now <= last:          # 价格上行
                continue
            zjl = zjl_map.get(code, 0.0)
            if zjl <= 0:                          # 主力净额为正
                continue
            chg = (now - last) / last * 100
            decisions.append(Decision(
                action='buy', code=code, strategy=self.name,
                reason=f'大单跟单: 巨量买入 价格上行{chg:.2f}% 主力净额{zjl:.0f}',
                position_pct=10, stop_loss=4, stop_profit=10,
                price=now, score=min(100.0, 60.0 + chg * 5),
            ))
        return decisions
