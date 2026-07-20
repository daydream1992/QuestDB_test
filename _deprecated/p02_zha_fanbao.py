"""p02: 炸板反包

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p02_zha_fanbao.py
用途: 昨日炸板今日缩量回封的反包机会
依赖: ctx.snapshot_focus_df (Now/FCAmo/Amount, C8拆表后intraday已merge) + ctx.more_info_df (ZAFYesterday/CJJEPre1)
入库: qd_decisions (由 runner 写入)

判定 (C8拆表 + FCAmo权威判定后重写):
  - 今日真封板: FCAmo > 0 (权威, 替代旧 Now>=ZTPrice 后者会误判触价未封)
  - 昨日炸板: ZAFYesterday >= 9% (启发式, ⚠️昨涨9%不等于炸板, 可能昨封住; 待 GP14 开板次数接入后精确化)
  - 缩量反包: 今日成交额 < 昨日成交额 (Amount < CJJEPre1)

⚠️ 已移除: 旧版用 fLianB>=1 当"连板" — fLianB 实为量比(非连板), 误用。
   连板位置维度待 GP 接入后补。当前 p02 只判"昨炸今回封+缩量"。
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

_ZAF_YESTERDAY_MIN = 9.0   # 昨涨幅 >= 9% 视为昨日触及涨停 (启发式)


def _safe_float(v, default=0.0) -> float:
    try:
        r = float(v)
    except (TypeError, ValueError):
        return default
    if r != r:  # NaN
        return default
    return r


@StrategyRegistry.register
class ZhaFanbaoStrategy(StrategyBase):
    name = 'zha_fanbao'
    version = '1.0'

    def required_fields(self):
        return ['Now', 'FCAmo', 'Amount', 'ZAFYesterday', 'CJJEPre1']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        snap = ctx.snapshot_focus_df
        mi = ctx.more_info_df
        if snap is None or snap.empty or mi is None or mi.empty:
            return []
        need_snap = ['code', 'Now', 'FCAmo', 'Amount']
        if not all(c in snap.columns for c in need_snap):
            return []
        need_mi = ['code', 'ZAFYesterday', 'CJJEPre1']
        if not all(c in mi.columns for c in need_mi):
            return []
        # 每股最新一行
        if 'snapshot_time' in snap.columns:
            snap_l = snap.sort_values('snapshot_time').groupby('code', as_index=False).last()[need_snap]
        else:
            snap_l = snap.groupby('code', as_index=False).last()[need_snap]
        mi_l = mi.groupby('code', as_index=False).last()[need_mi]
        merged = snap_l.merge(mi_l, on='code', how='inner')

        for _, r in merged.iterrows():
            fcamo = _safe_float(r.get('FCAmo'))
            if fcamo <= 0:                              # 今日真封板 (FCAmo 权威)
                continue
            zaf_y = _safe_float(r.get('ZAFYesterday'))
            if zaf_y < _ZAF_YESTERDAY_MIN:              # 昨日触及涨停区 (启发式炸板)
                continue
            amt = _safe_float(r.get('Amount'))
            cjj_pre1 = _safe_float(r.get('CJJEPre1'))
            if cjj_pre1 <= 0 or amt >= cjj_pre1:        # 缩量反包
                continue
            score = min(100.0, 70.0 + zaf_y)
            decisions.append(Decision(
                action='buy', code=r['code'], strategy=self.name,
                reason=f'炸板反包: 昨涨{zaf_y:.1f}% 今日FCAmo封单{fcamo / 1e4:.0f}万 '
                       f'缩量({amt / 1e8:.2f}亿<{cjj_pre1 / 1e8:.2f}亿)',
                position_pct=8, stop_loss=5, stop_profit=10,
                price=_safe_float(r.get('Now')), score=score,
            ))
        return decisions