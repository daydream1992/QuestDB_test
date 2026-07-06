"""策略基类

脚本路径: K:\QuestDB_test\\strategy\\base.py
用途: 所有策略插件必须继承的抽象基类, 定义 Decision 决策结构与 evaluate 接口
依赖: 标准库 abc / dataclasses / typing
说明:
  - Decision 为策略产出的统一决策结构, 贯穿信号→风控→仓位全流程
  - StrategyBase 用 ABC 强制子类实现 evaluate
  - required_fields 声明依赖字段, 供上下文校验数据完整性
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List


@dataclass
class Decision:
    """策略决策"""
    action: str           # buy / sell / hold / warn / watch
    code: str
    strategy: str         # 策略名
    reason: str           # 触发原因
    position_pct: float = 0  # 建议仓位 %
    stop_loss: float = 0     # 止损 %
    stop_profit: float = 0   # 止盈 %
    price: float = 0         # 触发价
    score: float = 0         # 信号评分 0-100


class StrategyBase(ABC):
    """策略基类"""
    name = 'base'
    version = '1.0'
    enabled = True

    @abstractmethod
    def evaluate(self, context) -> List[Decision]:
        """评估策略, 返回决策列表"""
        pass

    def required_fields(self) -> list:
        """声明依赖字段"""
        return []

    @staticmethod
    def safe_float(v, default=0.0) -> float:
        """安全转浮点, 失败返回 default"""
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def normalize_score(score, min_val=0.0, max_val=100.0) -> float:
        """将任意范围的 score 归一化到 0-100

        Args:
            score: 原始评分
            min_val: 该策略理论最低分
            max_val: 该策略理论最高分

        Returns:
            float: 0-100 归一化评分
        """
        if score is None:
            return 50.0
        if max_val <= min_val:
            return 50.0
        return max(0.0, min(100.0, (float(score) - min_val) / (max_val - min_val) * 100))
