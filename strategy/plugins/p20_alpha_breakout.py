"""p20: 多因子 Alpha + 突破入场 (新世代策略示例)

脚本路径: K:\QuestDB_test\\strategy\\plugins\\p20_alpha_breakout.py
用途: 演示如何消费 alpha_df + top_candidates, 与老策略形成对比
依赖: strategy.base / compute.alpha_engine / strategy.portfolio
数据源: ctx.alpha_df (AlphaEngine 写入) + ctx.indicators_df (突破判定)
说明:
  - 与老策略区别: 不再用单因子阈值, 而是用 alpha_score (多因子组合) + 时机判断
  - 入场时机: alpha 排名 top-50 AND 价格突破近 5 根 5m K 线高点
  - 仓位: 由 Portfolio.size_position(alpha, volatility) 计算, 非固定 10%
  - 出场: 交给 p15_stop_loss / p16_stop_profit (修复字段名后) + alpha 衰减
  - 不再硬编码 score, 而是用 alpha_score 直接映射
"""

from typing import List

import pandas as pd
from loguru import logger

from strategy.base import StrategyBase, Decision
from strategy.registry import StrategyRegistry


_BREAKOUT_LOOKBACK = 5        # 突破近 5 根 5m K 线高点
_TOP_N_CANDIDATES = 50        # 只看 alpha 排名前 50
_MIN_ALPHA_SCORE = 0.5        # alpha_score 至少 0.5 (约前 30%)


def _safe_float(v, default=0.0) -> float:
    try:
        r = float(v)
        return default if r != r else r
    except (TypeError, ValueError):
        return default


@StrategyRegistry.register
class AlphaBreakoutStrategy(StrategyBase):
    """多因子 alpha + 突破入场"""
    name = 'alpha_breakout'
    version = '1.0'
    enabled = True

    def required_fields(self):
        # 新世代策略不用 required_fields (从 ctx.alpha_df 取)
        # 返回空列表避免 validate_required_fields 误报
        return []

    def evaluate(self, ctx) -> List[Decision]:
        alpha_df = getattr(ctx, 'alpha_df', None)
        if alpha_df is None or alpha_df.empty:
            return []

        # 1. 筛 alpha top-N 且 score > 阈值
        candidates = alpha_df[
            (alpha_df['rank'] <= _TOP_N_CANDIDATES) &
            (alpha_df['alpha_score'] >= _MIN_ALPHA_SCORE)
        ].copy()
        if candidates.empty:
            return []

        # 2. 取近 N 根 5m K 线, 判突破
        indicators = ctx.indicators_df
        breakout_map = self._detect_breakouts(indicators, candidates.index)

        decisions = []
        for code, row in candidates.iterrows():
            if code not in breakout_map:
                continue
            brk = breakout_map[code]
            if not brk['is_breakout']:
                continue
            # 3. 时机 + alpha 双重确认, 产出 buy
            # 用 alpha_score 直接映射到 score (50-100 区间)
            score = float(min(100.0, 50.0 + row['alpha_score'] * 12))
            decisions.append(Decision(
                action='buy',
                code=code,
                strategy=self.name,
                reason=(f'alpha={row["alpha_score"]:.2f} rank={int(row["rank"])} '
                        f'突破5m高点 {brk["high"]:.2f} top_factors={row.get("top_factors", "[]")}'),
                position_pct=0,        # 交给 Portfolio 计算
                stop_loss=0,           # 交给 Portfolio 动态算
                stop_profit=0,
                price=brk['current'],
                score=score,
            ))
        return decisions

    def _detect_breakouts(self, indicators_df: pd.DataFrame,
                          codes) -> dict:
        """检测每只票是否突破近 N 根 K 线高点"""
        result = {}
        if indicators_df is None or indicators_df.empty:
            return result
        if 'code' not in indicators_df.columns or 'high' not in indicators_df.columns:
            return result

        time_col = None
        for c in ('snapshot_time', 'kline_time', 'timestamp'):
            if c in indicators_df.columns:
                time_col = c
                break
        if time_col is None:
            return result

        df = indicators_df[indicators_df['code'].isin(codes)].sort_values(time_col)
        for code, g in df.groupby('code'):
            if len(g) < _BREAKOUT_LOOKBACK + 1:
                continue
            recent = g.tail(_BREAKOUT_LOOKBACK + 1)
            # 近 N 根 (不含当前) 的最高价
            prev_high = float(recent['high'].iloc[:-1].max())
            current_high = float(recent['high'].iloc[-1])
            current_close = float(recent['close'].iloc[-1]) if 'close' in recent.columns else current_high
            # 当前根突破前 N 根高点
            is_breakout = current_high > prev_high and current_close >= prev_high * 0.999
            result[code] = {
                'is_breakout': is_breakout,
                'high': prev_high,
                'current': current_close,
            }
        return result
