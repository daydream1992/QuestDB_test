"""Alpha 组合引擎

脚本路径: K:\QuestDB_test\\compute\\alpha_engine.py
用途: 多因子加权组合, 输出 alpha_score + 横截面排名 + 十分位
依赖: pandas / numpy / loguru / compute.factors
配置: config/strategies.yaml 的 factor_model 段
说明:
  - AlphaEngine.compute(ctx) 返回 (alpha_df, coverage)
  - alpha_df: DataFrame index=code, columns=[各因子, alpha_score, rank, decile, top_factors]
  - coverage: dict {因子名: 覆盖率}, 用于监控数据完整性
  - ICTracker 跟踪因子预测能力 (因子值 vs 未来收益的相关性), 用于动态调权
设计要点:
  - 因子缺失按 0 处理, 但记录覆盖率 (低覆盖率因子权重应折扣)
  - top_factors 列存贡献最大的 3 个因子名, 供策略解释决策原因
  - 冷启动 (warmup 不足) 时返回空 df, 调用方应跳过 alpha 阶段
"""

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class ICRecord:
    """单次 IC 记录"""
    ts: datetime
    factor_name: str
    ic: float            # Spearman rank IC
    forward_bars: int    # 向前看多少 bar


class ICTracker:
    """因子信息系数跟踪器

    IC = 因子值与未来 N 分钟收益的 Spearman 秩相关
    - IC > 0.03 且 IC_IR > 0.3 是有效因子经验阈值
    - IC 衰减: 不同 forward_bars 的 IC 序列, 帮助确定最优持有期
    """

    def __init__(self, max_history: int = 240):
        self.history: deque = deque(maxlen=max_history)

    def update(self, factor_name: str, factor_values: pd.Series,
               future_returns: pd.Series, forward_bars: int = 6):
        """记录一次 IC 测量"""
        if factor_values is None or future_returns is None:
            return
        if factor_values.empty or future_returns.empty:
            return
        aligned = pd.DataFrame({'f': factor_values, 'r': future_returns}).dropna()
        if len(aligned) < 10:
            return
        try:
            ic = aligned['f'].corr(aligned['r'], method='spearman')
            if np.isnan(ic):
                return
            self.history.append(ICRecord(
                ts=datetime.now(), factor_name=factor_name,
                ic=float(ic), forward_bars=forward_bars
            ))
        except Exception as e:
            logger.debug('IC 计算失败 {}: {}', factor_name, e)

    def summary(self) -> dict:
        """返回各因子的 IC 统计"""
        if not self.history:
            return {}
        df = pd.DataFrame([
            {'factor': r.factor_name, 'ic': r.ic, 'ts': r.ts}
            for r in self.history
        ])
        result = {}
        for factor, g in df.groupby('factor'):
            mean_ic = g['ic'].mean()
            std_ic = g['ic'].std()
            result[factor] = {
                'mean_ic': float(mean_ic),
                'std_ic': float(std_ic),
                'ic_ir': float(mean_ic / std_ic) if std_ic > 0 else 0.0,
                'sample_size': len(g),
                'last_ic': float(g['ic'].iloc[-1]),
            }
        return result


class AlphaEngine:
    """多因子组合引擎"""

    def __init__(self, weights: dict, ic_tracking: bool = True,
                 ic_decay_bars: int = 60):
        """
        Args:
            weights: {因子名: 权重}, e.g. {'ts_momentum_5m': 0.3, ...}
            ic_tracking: 是否启用 IC 跟踪
            ic_decay_bars: IC 衰减观察窗口 (bar 数)
        """
        self.weights = {k: float(v) for k, v in weights.items()}
        self.ic_tracker = ICTracker() if ic_tracking else None
        self.ic_decay_bars = ic_decay_bars
        self._last_alpha_df: Optional[pd.DataFrame] = None

    @classmethod
    def from_yaml(cls, yaml_path: str) -> 'AlphaEngine':
        """从 strategies.yaml 加载配置创建引擎"""
        import os
        import yaml
        if not os.path.exists(yaml_path):
            logger.warning('strategies.yaml 不存在: {}, 使用空权重', yaml_path)
            return cls(weights={})
        with open(yaml_path, 'r', encoding='utf-8') as f:
            cfg = yaml.safe_load(f) or {}
        fm = cfg.get('factor_model', {})
        if not fm.get('enabled', False):
            logger.info('factor_model 未启用')
            return cls(weights={})
        weights = fm.get('weights', {})
        return cls(
            weights=weights,
            ic_tracking=fm.get('ic_tracking', True),
            ic_decay_bars=fm.get('ic_decay', 60),
        )

    def compute(self, ctx) -> Tuple[pd.DataFrame, dict]:
        """计算 alpha

        Returns:
            alpha_df: DataFrame index=code
                columns: [各因子列..., alpha_score, rank, decile, top_factors]
            coverage: {因子名: 覆盖率 0-1}
        """
        from compute.factors.base import FactorRegistry

        if not self.weights:
            logger.debug('AlphaEngine 无权重配置, 跳过')
            return pd.DataFrame(), {}

        # 1. 计算所有启用因子
        factor_values = {}
        coverage = {}
        for name, factor_cls in FactorRegistry.get_all().items():
            if name not in self.weights:
                continue
            try:
                # 检查依赖的 ctx 属性是否就绪
                factor_inst = factor_cls()
                required = factor_inst.required_inputs()
                missing = [a for a in required if getattr(ctx, a, None) is None]
                if missing:
                    logger.debug('因子 {} 缺依赖: {}', name, missing)
                    coverage[name] = 0.0
                    continue
                values = factor_inst.compute(ctx)
                if values is None or values.empty:
                    coverage[name] = 0.0
                    continue
                factor_values[name] = values
                coverage[name] = float(values.notna().mean())
            except Exception as e:
                logger.warning('因子 {} 计算异常: {}', name, e)
                coverage[name] = 0.0

        if not factor_values:
            logger.warning('无有效因子, alpha 计算跳过')
            return pd.DataFrame(), coverage

        # 2. 对齐到同一 index (强制 code 转 str, 防止 int/str 混合)
        for name in factor_values:
            factor_values[name].index = factor_values[name].index.astype(str)

        df = pd.DataFrame(factor_values)
        # 缺失因子补 0 (但记录覆盖率, 调用方可降权)
        for name in self.weights:
            if name not in df.columns:
                df[name] = 0.0

        # 3. 加权组合
        alpha = pd.Series(0.0, index=df.index)
        contributions = {}
        for name, w in self.weights.items():
            if name in df.columns:
                contribution = df[name].fillna(0) * w
                alpha += contribution
                contributions[name] = contribution
        df['alpha_score'] = alpha

        # 4. 排名
        df['rank'] = alpha.rank(ascending=False, method='min').astype(int)
        try:
            df['decile'] = pd.qcut(alpha, 10, labels=False, duplicates='drop')
            df['decile'] = df['decile'].fillna(-1).astype(int)
        except Exception:
            df['decile'] = 0

        # 5. top_factors (每只票贡献最大的 3 个因子, 便于解释决策)
        top_factors_list = []
        if contributions:
            contrib_df = pd.DataFrame(contributions)
            for code in df.index:
                row = contrib_df.loc[code].abs().sort_values(ascending=False)
                top3 = row.head(3).index.tolist()
                top_factors_list.append(json.dumps(top3, ensure_ascii=False))
        else:
            top_factors_list = ['[]'] * len(df)
        df['top_factors'] = top_factors_list

        self._last_alpha_df = df
        logger.debug('alpha 计算完成: {} 只票, 覆盖率均值 {:.2f}',
                     len(df), np.mean(list(coverage.values())) if coverage else 0)
        return df, coverage

    def update_ic(self, ctx, forward_returns: pd.Series, forward_bars: int = 6):
        """更新 IC 跟踪 (需要未来收益, 通常在 T+N bar 调用)
        Args:
            ctx: 当前 ctx (用于取因子值)
            forward_returns: T+N 的收益 (index=code)
            forward_bars: 向前多少 bar
        """
        if self.ic_tracker is None or self._last_alpha_df is None:
            return
        for name in self.weights:
            if name in self._last_alpha_df.columns:
                self.ic_tracker.update(
                    name, self._last_alpha_df[name],
                    forward_returns, forward_bars
                )

    def ic_summary(self) -> dict:
        return self.ic_tracker.summary() if self.ic_tracker else {}
