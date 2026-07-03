"""p01: 涨停打板

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p01_zt_daban.py
用途: 检测封板且板块共振的打板机会, 多条件严格筛选
依赖: 策略上下文 ctx (pricevol_df / more_info_df / snapshot_focus_df / graph)
入库: qd_decisions (由 runner 写入)
条件:
  - 封板: Now >= ZTPrice (含 0.1% 容差, 抵触浮动)
  - 连板: fLianB >= 1
  - 板块内涨停 >= 3 (板块涨停潮, 用关系图谱统计)
  - 成交额 >= 50000 万 (5e8 元)
  - 换手 3%-20% (more_info.fHSL)
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

# 阈值
_AMOUNT_MIN = 5e8               # 成交额 >= 50000 万 (5e8 元)
_HSL_MIN, _HSL_MAX = 3.0, 20.0  # 换手 3%-20%
_ZT_TOL = 0.999                 # 封板容差 (Now >= ZTPrice * 0.999)
_SECTOR_ZT_MIN = 3              # 板块内涨停 >= 3


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class ZtDabanStrategy(StrategyBase):
    name = 'zt_daban'
    version = '1.0'

    def required_fields(self):
        return ['Now', 'LastClose', 'ZTPrice', 'fLianB', 'fHSL', 'Amount']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        pv = ctx.pricevol_df
        mi = ctx.more_info_df
        if pv is None or pv.empty or mi is None or mi.empty:
            return []

        # 取每只股票最新一行
        if 'snapshot_time' in pv.columns:
            pv = pv.sort_values('snapshot_time').groupby('code', as_index=False).last()
        else:
            pv = pv.groupby('code', as_index=False).last()
        mi_cols = [c for c in ('code', 'ZTPrice', 'fLianB', 'fHSL') if c in mi.columns]
        if len(mi_cols) < 4:
            return []
        mi_l = mi.groupby('code', as_index=False).last()[mi_cols]
        merged = pv.merge(mi_l, on='code', how='inner')

        # 成交额 (snapshot_focus_df.Amount)
        snap = ctx.snapshot_focus_df
        if snap is not None and not snap.empty and 'Amount' in snap.columns:
            snap_l = snap.groupby('code', as_index=False).last()[['code', 'Amount']]
            merged = merged.merge(snap_l, on='code', how='left')
        else:
            merged['Amount'] = 0.0

        # 涨停集合
        def _is_zt(r):
            zt = _safe_float(r.get('ZTPrice'))
            now = _safe_float(r.get('Now'))
            return zt > 0 and now >= zt * _ZT_TOL

        zt_mask = merged.apply(_is_zt, axis=1)
        zt_codes = set(merged[zt_mask]['code'].tolist())
        if not zt_codes:
            return []

        # 板块 -> 涨停数 (用关系图谱反向索引)
        from lib.relation_graph import get_stock_sectors
        sector_zt = {}
        for c in zt_codes:
            for s in get_stock_sectors(c):
                bc = s.get('block_code')
                if bc:
                    sector_zt[bc] = sector_zt.get(bc, 0) + 1

        for _, r in merged.iterrows():
            code = r['code']
            if code not in zt_codes:
                continue
            lianb = _safe_float(r.get('fLianB'))
            hsl = _safe_float(r.get('fHSL'))
            amt = _safe_float(r.get('Amount'))
            if lianb < 1 or amt < _AMOUNT_MIN:
                continue
            if not (_HSL_MIN <= hsl <= _HSL_MAX):
                continue
            # 板块涨停数 (取所属板块中最大值)
            cnt = 0
            for s in get_stock_sectors(code):
                cnt = max(cnt, sector_zt.get(s.get('block_code'), 0))
            if cnt < _SECTOR_ZT_MIN:
                continue
            score = min(100.0, 60.0 + lianb * 5 + cnt * 3)
            decisions.append(Decision(
                action='buy', code=code, strategy=self.name,
                reason=f'涨停打板: {lianb:.0f}连板 板块涨停{cnt}只 '
                       f'成交额{amt / 1e8:.2f}亿 换手{hsl:.1f}%',
                position_pct=10, stop_loss=5, stop_profit=10,
                price=_safe_float(r.get('Now')), score=score,
            ))
        return decisions
