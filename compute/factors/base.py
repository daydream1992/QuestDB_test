"""因子基类 + 注册表

脚本路径: K:\QuestDB_test\\compute\\factors\\base.py
用途: 定义因子层契约, 所有因子继承 FactorBase, 用 @FactorRegistry.register 注册
依赖: abc / dataclasses / pandas / loguru
设计要点:
  - FactorBase 模板方法: compute_raw (子类实现) → normalize (默认 winsorize+zscore) → 方向调整
  - direction: +1 越大越看多; -1 越大越看空 (反转类因子); 0 中性
  - warmup_bars: 因子需要多少根 bar 才有效 (用于冷启动过滤)
  - FactorRegistry 装饰器注册, get_all 返回已注册因子 (load_config 后按 enabled 过滤)
说明:
  - 因子只读 ctx 现有 DataFrame, 不主动取数 (保证可回测)
  - compute 失败时返回空 Series, 不抛异常 (单因子失败不阻断 alpha 计算)
"""

from abc import ABC, abstractmethod
from typing import List, Optional

import pandas as pd
from loguru import logger

from compute.factors._normalize import winsorize_zscore


class FactorBase(ABC):
    """因子抽象基类"""
    name: str = ''                  # 全局唯一标识, e.g. 'ts_momentum_5m'
    version: str = '1.0'
    timeframe: str = 'minute'       # 'tick'(10s) | 'minute'(60s) | 'daily'
    warmup_bars: int = 20           # 冷启动需要的 bar 数
    direction: int = 1              # +1 大=看多; -1 大=看空; 0 中性
    enabled: bool = True

    @abstractmethod
    def required_inputs(self) -> List[str]:
        """声明依赖的 ctx 属性名, e.g. ['snapshot_focus_df', 'indicators_df']
        供 AlphaEngine 在 ctx 缺数据时跳过本因子 (不报错)"""
        return []

    @abstractmethod
    def compute_raw(self, ctx) -> Optional[pd.Series]:
        """计算原始因子值
        Returns:
            pd.Series, index=code, value=原始未归一化值
            返回 None / 空 Series 表示本轮无效 (e.g. 数据不足)
        """
        return None

    def normalize(self, raw: pd.Series) -> pd.Series:
        """默认归一化: 1%/99% winsorize + z-score
        子类可重写为 rank_normalize (厚尾分布更稳健)"""
        return winsorize_zscore(raw)

    def compute(self, ctx) -> pd.Series:
        """模板方法: raw → normalize → direction 调整
        任何异常都吞掉, 返回空 Series (单因子失败不阻断组合)"""
        try:
            raw = self.compute_raw(ctx)
            if raw is None or raw.empty:
                return pd.Series(dtype='float64')
            norm = self.normalize(raw)
            if self.direction == -1:
                norm = -norm
            return norm
        except Exception as e:
            logger.warning('因子 {} 计算失败: {}', self.name, e)
            return pd.Series(dtype='float64')

    def describe(self) -> str:
        return (f'{self.name}(v{self.version}, {self.timeframe}, '
                f'dir={self.direction:+d}, warmup={self.warmup_bars})')


class FactorRegistry:
    """因子注册表 (与 StrategyRegistry 风格一致)"""
    _factors: dict = {}  # name → class

    @classmethod
    def register(cls, factor_class):
        if not factor_class.name:
            raise ValueError(f'因子类 {factor_class.__name__} 缺少 name 属性')
        cls._factors[factor_class.name] = factor_class
        logger.debug('注册因子: {}', factor_class().describe())
        return factor_class

    @classmethod
    def get_all(cls) -> dict:
        """返回所有 enabled 因子类"""
        return {n: c for n, c in cls._factors.items() if c.enabled}

    @classmethod
    def get(cls, name: str) -> Optional[type]:
        return cls._factors.get(name)

    @classmethod
    def enable(cls, name: str):
        if name in cls._factors:
            cls._factors[name].enabled = True

    @classmethod
    def disable(cls, name: str):
        if name in cls._factors:
            cls._factors[name].enabled = False

    @classmethod
    def load_factors(cls, factors_dir: str):
        """动态导入 factors/ 目录下所有非下划线开头的 .py"""
        import importlib
        import pathlib
        if not pathlib.Path(factors_dir).is_dir():
            logger.warning('因子目录不存在: {}', factors_dir)
            return
        for p in sorted(pathlib.Path(factors_dir).glob('*.py')):
            if p.name.startswith('_') or p.name == '__init__.py':
                continue
            mod_name = f'compute.factors.{p.stem}'
            try:
                importlib.import_module(mod_name)
                logger.debug('加载因子模块: {}', mod_name)
            except Exception as e:
                logger.error('加载因子模块失败 {}: {}', mod_name, e)

    @classmethod
    def load_config(cls, cfg: dict):
        """从 factor_model 配置加载 enabled 开关 + 权重
        cfg: strategies.yaml 的 factor_model 段"""
        for name, settings in (cfg or {}).get('weights', {}).items():
            if name in cls._factors:
                # weight == 0 视为 disabled
                if float(settings) <= 0:
                    cls.disable(name)
                else:
                    cls.enable(name)
            else:
                logger.warning('配置中因子 {} 未注册 (跳过)', name)
        logger.info('因子配置加载完成: 启用={}, 已注册={}',
                    len(cls.get_all()), len(cls._factors))
