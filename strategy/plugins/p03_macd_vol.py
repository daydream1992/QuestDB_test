"""p03: MACD金叉放量

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p03_macd_vol.py
用途: MACD 金叉 + 放量 + 突破 MA5 + 红柱的共振买点
依赖: 策略上下文 ctx (signals_df / indicators_df / snapshot_focus_df / more_info_df)
入库: qd_decisions (由 runner 写入)
条件:
  - golden_cross 信号 (signals_df.signal_type == 'golden_cross')
  - 量比 >= 1.5 (今日成交额 / 昨日成交额 CJJEPre1, pricevol 无均量字段用代理)
  - 突破 MA5: close > ma5 (indicators_df)
  - 红柱: macd_hist > 0
"""

from typing import List

import pandas as pd

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_VOL_RATIO_MIN = 1.5


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class MacdGoldenVolStrategy(StrategyBase):
    name = 'macd_golden_vol'
    version = '1.0'

    def required_fields(self):
        return ['signal_type', 'macd_hist', 'ma5', 'close', 'Amount', 'CJJEPre1']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        sig = ctx.signals_df
        ind = ctx.indicators_df
        if sig is None or sig.empty or ind is None or ind.empty:
            return []
        if 'signal_type' not in sig.columns or 'code' not in sig.columns:
            return []

        # 取每只股票最新一条 golden_cross 信号
        gc = sig[sig['signal_type'] == 'golden_cross']
        if gc.empty:
            return []
        if 'signal_time' in gc.columns:
            gc = gc.sort_values('signal_time').groupby('code', as_index=False).last()
        else:
            gc = gc.groupby('code', as_index=False).last()
        gc_codes = set(gc['code'].tolist())

        # 指标最新一行
        if 'calc_time' in ind.columns:
            ind_l = ind.sort_values('calc_time').groupby('code', as_index=False).last()
        else:
            ind_l = ind.groupby('code', as_index=False).last()

        # 成交额 + 昨成交额
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

        for _, r in ind_l.iterrows():
            code = r['code']
            if code not in gc_codes:
                continue
            hist = _safe_float(r.get('macd_hist'))
            close = _safe_float(r.get('close'))
            ma5 = _safe_float(r.get('ma5'))
            if hist <= 0 or ma5 <= 0 or close <= ma5:   # 红柱 + 突破MA5
                continue
            amt = amt_map.get(code, 0.0)
            cjj = cjj_map.get(code, 0.0)
            vol_ratio = amt / cjj if cjj > 0 else 0.0
            if vol_ratio < _VOL_RATIO_MIN:              # 量比
                continue
            score = min(100.0, 60.0 + vol_ratio * 10)
            decisions.append(Decision(
                action='buy', code=code, strategy=self.name,
                reason=f'MACD金叉放量: 量比{vol_ratio:.2f} 突破MA5 '
                       f'红柱{hist:.4f}',
                position_pct=8, stop_loss=5, stop_profit=10,
                price=close, score=score,
            ))
        return decisions
