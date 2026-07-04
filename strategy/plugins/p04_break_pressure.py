"""p04: 突破压力位

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p04_break_pressure.py
用途: 突破压力位 + 放量 + 涨幅确认的趋势启动买点
依赖: 策略上下文 ctx (signals_df / pricevol_df / snapshot_focus_df / more_info_df)
入库: qd_decisions (由 runner 写入)
条件:
  - break_pressure 信号 (signals_df.signal_type == 'break_pressure')
  - 量比 >= 2.0 (今日成交额 / 昨日成交额 CJJEPre1)
  - 涨幅 >= 3% ((Now - LastClose) / LastClose * 100)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_VOL_RATIO_MIN = 2.0
_CHANGE_MIN = 3.0


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
class BreakPressureStrategy(StrategyBase):
    name = 'break_pressure'
    version = '1.0'

    def required_fields(self):
        return ['signal_type', 'Now', 'LastClose', 'Amount', 'CJJEPre1']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        sig = ctx.signals_df
        pv = ctx.pricevol_df
        if sig is None or sig.empty or pv is None or pv.empty:
            return []
        if 'signal_type' not in sig.columns:
            return []

        bp = sig[sig['signal_type'] == 'break_pressure']
        if bp.empty:
            return []
        bp_codes = set(bp['code'].tolist())

        # pricevol 最新一行
        if 'snapshot_time' in pv.columns:
            pv_l = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
        else:
            pv_l = pv.groupby('code', as_index=False).last()

        # 成交额 / 昨成交额
        amt_map = {}
        snap = ctx.snapshot_focus_df
        if snap is not None and not snap.empty and 'Amount' in snap.columns:
            for c, g in snap.groupby('code'):
                amt_map[c] = _safe_float(g.iloc[-1]['Amount'])
        cjj_map = {}
        mi = ctx.more_info_df
        if mi is not None and not mi.empty and 'CJJEPre1' in mi.columns:
            for c, g in mi.groupby('code'):
                cjj_map[c] = _safe_float(g.iloc[-1]['CJJEPre1'])

        for _, r in pv_l.iterrows():
            code = r['code']
            if code not in bp_codes:
                continue
            chg = _change_pct(r.get('Now'), r.get('LastClose'))
            if chg < _CHANGE_MIN:
                continue
            amt = amt_map.get(code, 0.0)
            cjj = cjj_map.get(code, 0.0)
            vol_ratio = amt / cjj if cjj > 0 else 0.0
            if vol_ratio < _VOL_RATIO_MIN:
                continue
            score = min(100.0, 60.0 + chg * 5 + vol_ratio * 5)
            # A3 降权: buy → watch (突破是结果非因, T+1追突破常被埋; 改呈现不诱导)
            decisions.append(Decision(
                action='watch', code=code, strategy=self.name,
                reason=f'突破压力位(参考): 涨幅{chg:.2f}% 量比{vol_ratio:.2f}',
                price=_safe_float(r.get('Now')), score=score,
            ))
        return decisions
