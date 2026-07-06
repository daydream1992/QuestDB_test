"""连板位置因子 (升级 p01 涨停打板核心维度)

脚本路径: K:\QuestDB_test\\compute\\factors\\ladder_position.py
用途: 识别连板梯队位置 (首板/2板/3板加速/高位), 解决 p01 缺失的连板维度
依赖: pandas / compute.factors
数据源: ctx.gp_df (qd_stock_gpjy, 含 ConZAFDateNum 连板数) +
        ctx.snapshot_focus_df (FCAmo 封单金额, 用于确认当日是否封板)
说明:
  - 连板位置是打板核心: 2进3 加速期胜率最高, 4板+ 高位分歧大
  - 本因子综合"当前是否封板" + "历史连板数" + "板块内连板排名"
  - direction=+1: 高位连板 = 资金共识强 = 看多 (但高位需配合 other 因子控制风险)
"""

from typing import List, Optional

import pandas as pd

from compute.factors.base import FactorBase, FactorRegistry
from compute.factors._normalize import winsorize_zscore


def _safe_float(v, default=0.0) -> float:
    try:
        r = float(v)
        return default if r != r else r
    except (TypeError, ValueError):
        return default


@FactorRegistry.register
class LadderPosition(FactorBase):
    """连板位置综合分

    综合: 当日封板状态 + 历史连板数 + 板块内连板排名
    """
    name = 'ladder_position'
    version = '1.0'
    timeframe = 'tick'
    warmup_bars = 1
    direction = +1

    def required_inputs(self) -> List[str]:
        return ['snapshot_focus_df', 'gp_df']

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        snap = ctx.snapshot_focus_df
        gp = ctx.gp_df
        if snap is None or snap is None or snap.empty:
            return None
        if 'code' not in snap.columns:
            return None

        # 取最新快照
        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in snap.columns:
                time_col = c
                break
        if time_col:
            snap_l = snap.sort_values(time_col).groupby('code', as_index=False).last()
        else:
            snap_l = snap.groupby('code', as_index=False).last()

        # 当日是否真封板 (FCAmo > 0)
        if 'FCAmo' not in snap_l.columns:
            return None
        snap_l = snap_l.copy()
        snap_l['sealed'] = snap_l['FCAmo'].apply(lambda v: _safe_float(v) > 0)

        # 连板数: 优先从 gp_df (qd_stock_gpjy ConZAFDateNum), 没有则用 0
        ladder_map = {}
        if gp is not None and not gp.empty and 'code' in gp.columns:
            gp_time = None
            for c in ('date', 'snapshot_time', 'timestamp'):
                if c in gp.columns:
                    gp_time = c
                    break
            if gp_time:
                gp_l = gp.sort_values(gp_time).groupby('code', as_index=False).last()
            else:
                gp_l = gp.groupby('code', as_index=False).last()
            for _, r in gp_l.iterrows():
                # ConZAFDateNum 是截至昨日的连板数; 今日再封板则 +1
                ladder_map[r['code']] = _safe_float(r.get('ConZAFDateNum'))

        result = {}
        for _, r in snap_l.iterrows():
            code = r['code']
            sealed = bool(r['sealed'])
            con_zaf = ladder_map.get(code, 0)
            # 今日封板则连板数 +1 (ConZAFDateNum 反映的是截至昨日)
            today_boards = int(con_zaf) + (1 if sealed else 0)
            if today_boards <= 0:
                continue
            # 综合分: 连板数 × 2 + 封板加成 × 3
            # 2板=4, 3板=6 (加速期); 4板=8, 5板+ 高位但共识强
            score = today_boards * 2.0 + (3.0 if sealed else 0.0)
            result[code] = score

        if len(result) < 5:
            return None
        return pd.Series(result, name=self.name)

    def normalize(self, raw: pd.Series) -> pd.Series:
        return winsorize_zscore(raw)


@FactorRegistry.register
class QualityGP(FactorBase):
    """GP 股性因子 (历史连板率 + 次日红盘率)

    升级 p01 的 GP 筛选逻辑为因子, 让 alpha 引擎统一加权
    """
    name = 'quality_gp'
    version = '1.0'
    timeframe = 'daily'
    warmup_bars = 1
    direction = +1

    _LB_RATE_WEIGHT = 0.5
    _NEXT_RED_WEIGHT = 0.5

    def required_inputs(self) -> List[str]:
        return ['gp_df']

    def compute_raw(self, ctx) -> Optional[pd.Series]:
        gp = ctx.gp_df
        if gp is None or gp.empty or 'code' not in gp.columns:
            return None
        gp_time = None
        for c in ('date', 'snapshot_time', 'timestamp'):
            if c in gp.columns:
                gp_time = c
                break
        if gp_time:
            gp_l = gp.sort_values(gp_time).groupby('code', as_index=False).last()
        else:
            gp_l = gp.groupby('code', as_index=False).last()

        result = {}
        for _, r in gp_l.iterrows():
            lb_rate = _safe_float(r.get('gp40_lb_rate'))
            next_red = _safe_float(r.get('gp39_next_red_rate'))
            if lb_rate <= 0 and next_red <= 0:
                continue
            score = (lb_rate * self._LB_RATE_WEIGHT
                     + next_red * self._NEXT_RED_WEIGHT)
            result[r['code']] = score
        if len(result) < 10:
            return None
        return pd.Series(result, name=self.name)

    def normalize(self, raw: pd.Series) -> pd.Series:
        return winsorize_zscore(raw)
