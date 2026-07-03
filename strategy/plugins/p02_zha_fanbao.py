"""p02: 炸板反包

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p02_zha_fanbao.py
用途: 昨日炸板今日缩量回封的反包机会
依赖: 策略上下文 ctx (pricevol_df / more_info_df / snapshot_focus_df)
入库: qd_decisions (由 runner 写入)
条件:
  - 昨日炸板: 昨日涨幅触及涨停区 (ZAFYesterday >= 9%) 但未封住 (启发式, 无直接炸板字段)
  - 今日回封: Now >= ZTPrice
  - 缩量反包: 今日成交额 < 昨日成交额 (Amount < CJJEPre1)
  - 连板: fLianB >= 1
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_ZAF_YESTERDAY_MIN = 9.0   # 昨涨幅 >= 9% 视为昨日触及涨停
_ZT_TOL = 0.999


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class ZhaFanbaoStrategy(StrategyBase):
    name = 'zha_fanbao'
    version = '1.0'

    def required_fields(self):
        return ['Now', 'ZTPrice', 'fLianB', 'ZAFYesterday', 'CJJEPre1', 'Amount']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        pv = ctx.pricevol_df
        mi = ctx.more_info_df
        if pv is None or pv.empty or mi is None or mi.empty:
            return []

        if 'snapshot_time' in pv.columns:
            pv = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
        else:
            pv = pv.groupby('code', as_index=False).last()
        need = ['code', 'ZTPrice', 'fLianB', 'ZAFYesterday', 'CJJEPre1']
        if not all(c in mi.columns for c in need):
            return []
        mi_l = mi.groupby('code', as_index=False).last()[need]
        merged = pv.merge(mi_l, on='code', how='inner')

        # 今日成交额
        snap = ctx.snapshot_focus_df
        if snap is not None and not snap.empty and 'Amount' in snap.columns:
            snap_l = snap.groupby('code', as_index=False).last()[['code', 'Amount']]
            merged = merged.merge(snap_l, on='code', how='left')
        else:
            merged['Amount'] = 0.0

        for _, r in merged.iterrows():
            zt = _safe_float(r.get('ZTPrice'))
            now = _safe_float(r.get('Now'))
            if zt <= 0 or now < zt * _ZT_TOL:        # 今日回封
                continue
            lianb = _safe_float(r.get('fLianB'))
            if lianb < 1:                            # 连板
                continue
            zaf_y = _safe_float(r.get('ZAFYesterday'))
            if zaf_y < _ZAF_YESTERDAY_MIN:           # 昨日炸板 (启发式)
                continue
            amt = _safe_float(r.get('Amount'))
            cjj_pre1 = _safe_float(r.get('CJJEPre1'))
            if cjj_pre1 <= 0 or amt >= cjj_pre1:     # 缩量反包
                continue
            score = min(100.0, 70.0 + lianb * 5)
            decisions.append(Decision(
                action='buy', code=r['code'], strategy=self.name,
                reason=f'炸板反包: 昨涨{zaf_y:.1f}% 今日回封 缩量'
                       f'({amt / 1e8:.2f}亿<{cjj_pre1 / 1e8:.2f}亿) {lianb:.0f}连板',
                position_pct=8, stop_loss=5, stop_profit=10,
                price=now, score=score,
            ))
        return decisions
