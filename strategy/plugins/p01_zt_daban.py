"""p01: 涨停打板

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p01_zt_daban.py
用途: 检测真封板 + 板块涨停潮 + 放量 + 换手充分的打板机会
依赖: ctx.snapshot_focus_df (C8拆表后含快照列 + merge 的 intraday 列 FCAmo/fHSL/fLianB/Amount)
入库: qd_decisions (由 runner 写入)

判定 (C8拆表 + FCAmo权威判定后重写, 修正历史臆测):
  - 真封板: FCAmo > 0  (权威涨跌停判定, 替代旧的 Now>=ZTPrice 后者会误选触价未封的假涨停)
  - 板块涨停潮: 所属板块内 FCAmo>0 的票 >= 3 (资金共识)
  - 成交额 >= 5亿 (流动性, T+1 要跑得掉)
  - 换手 fHSL 3%-20% (筹码交换充分, 防一字闷杀)
  - 量比 fLianB (放量活跃度参考; ⚠️非连板数)

⚠️ 已知缺失: 连板位置(首板/2板/3板加速/高位)是打板核心维度, 但库内无现成字段
   (fLianB=量比非连板)。待接入 GP40(近1年连板率)/自算跨日涨停连续性后补强。
   当前 p01 只筛"真封板+板块潮", 不区分连板位置。
"""

from typing import List

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry

# 阈值
_AMOUNT_MIN = 5e8               # 成交额 >= 5亿 (流动性)
_HSL_MIN, _HSL_MAX = 3.0, 20.0  # 换手 3%-20%
_SECTOR_ZT_MIN = 3              # 板块内涨停 >= 3 (涨停潮)
# GP 股性筛选 (B1+B2: 连板率+次日红盘率, 数据来自 qd_stock_gpjy 近1年统计)
_GP40_LB_RATE_MIN = 20.0        # 近1年连板率 >= 20% (p80分位, 筛能连板的活跃股)
_GP39_NEXT_RED_MIN = 70.0       # 近1年次日红盘率 >= 70% (p70, T+1命门: 选次日大概率溢价的)


def _safe_float(v, default=0.0) -> float:
    try:
        r = float(v)
    except (TypeError, ValueError):
        return default
    if r != r:  # NaN (pandas merge 产生, 不过滤会让筛选条件 nan<x 失效误通过)
        return default
    return r


@StrategyRegistry.register
class ZtDabanStrategy(StrategyBase):
    name = 'zt_daban'
    version = '1.0'

    def required_fields(self):
        # 均在 snapshot_focus_df (C8拆表后 intraday 列已 merge 进来)
        return ['Now', 'FCAmo', 'ZTPrice', 'fLianB', 'fHSL', 'Amount']

    def evaluate(self, ctx) -> List[Decision]:
        decisions: List[Decision] = []
        snap = ctx.snapshot_focus_df
        if snap is None or snap.empty:
            return []
        needed = ['code', 'Now', 'FCAmo', 'ZTPrice', 'fLianB', 'fHSL', 'Amount']
        if not all(c in snap.columns for c in needed):
            return []
        # 每只股票最新一行
        if 'snapshot_time' in snap.columns:
            df = snap.sort_values('snapshot_time').groupby('code', as_index=False).last()[needed]
        else:
            df = snap.groupby('code', as_index=False).last()[needed]

        # GP 股性 merge (B1+B2: 连板率/次日红盘率, 从 ctx.gp_df 每code最新)
        gp = ctx.gp_df
        if gp is not None and not gp.empty and 'code' in gp.columns:
            gp_need = [c for c in ('code', 'gp40_lb_rate', 'gp39_next_red_rate', 'gp38_zt_cnt') if c in gp.columns]
            if 'snapshot_time' in gp.columns:
                gp_l = gp.sort_values('date').groupby('code', as_index=False).last()[gp_need]
            else:
                gp_l = gp.groupby('code', as_index=False).last()[gp_need]
            df = df.merge(gp_l, on='code', how='left')
        else:
            df['gp40_lb_rate'] = None
            df['gp39_next_red_rate'] = None

        # 真封板: FCAmo > 0 (权威涨跌停判定, 有买单封单)
        zt_df = df[df['FCAmo'].apply(lambda v: _safe_float(v) > 0)]
        if zt_df.empty:
            return []
        zt_codes = set(zt_df['code'].tolist())

        # 板块涨停潮: 统计每板块内真封板数
        from lib.relation_graph import get_stock_sectors
        sector_zt = {}
        for c in zt_codes:
            for s in get_stock_sectors(c):
                bc = s.get('block_code')
                if bc:
                    sector_zt[bc] = sector_zt.get(bc, 0) + 1

        for _, r in zt_df.iterrows():
            code = r['code']
            lianb = _safe_float(r.get('fLianB'))   # 量比 (⚠️非连板, 放量活跃度)
            hsl = _safe_float(r.get('fHSL'))
            amt = _safe_float(r.get('Amount'))
            fcamo = _safe_float(r.get('FCAmo'))    # 封单额(万元), 越大封板越结实
            if amt < _AMOUNT_MIN:
                continue
            if not (_HSL_MIN <= hsl <= _HSL_MAX):
                continue
            cnt = 0
            for s in get_stock_sectors(code):
                cnt = max(cnt, sector_zt.get(s.get('block_code'), 0))
            if cnt < _SECTOR_ZT_MIN:
                continue
            # B1+B2 GP 股性筛选: 连板率 + 次日红盘率 (T+1打板核心)
            lb_rate = _safe_float(r.get('gp40_lb_rate'))
            red_rate = _safe_float(r.get('gp39_next_red_rate'))
            if lb_rate < _GP40_LB_RATE_MIN:
                continue
            if red_rate < _GP39_NEXT_RED_MIN:
                continue
            # 评分: 封单额 + 板块潮 + 量比 + 连板率 + 次日红盘率
            score = min(100.0, 50.0 + fcamo / 1e4 + cnt * 3 + lianb * 2
                        + lb_rate * 0.3 + (red_rate - 70) * 0.5)
            decisions.append(Decision(
                action='buy', code=code, strategy=self.name,
                reason=f'涨停打板: 封单{fcamo / 1e4:.0f}万 量比{lianb:.1f} '
                       f'板块涨停{cnt}只 成交额{amt / 1e8:.2f}亿 换手{hsl:.1f}% '
                       f'连板率{lb_rate:.0f}% 次日红盘{red_rate:.0f}%',
                position_pct=10, stop_loss=5, stop_profit=10,
                price=_safe_float(r.get('Now')), score=score,
            ))
        return decisions