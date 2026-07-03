"""风控

脚本路径: K:\QuestDB_test\\strategy\\risk.py
用途: 仓位上限校验 + 止损止盈出场判断
依赖: loguru
配置: config/strategies.yaml 的 risk 段
参数:
  max_total_position   最大总仓位 % (默认 80)
  max_single_position  单只最大仓位 % (默认 30)
说明:
  - RiskManager 持仓列表 positions: list[dict]
      {code, position_pct, cost_price, stop_loss, stop_profit}
  - can_open 判断新增仓位: 单只不超上限 且 总仓位不超上限
  - check_exit 判断持仓是否触发止损/止盈, 返回 (action, reason) 或 None
"""

from loguru import logger


class RiskManager:
    """风控管理器"""

    def __init__(self, max_total_position=80, max_single_position=30,
                 positions=None):
        self.max_total_position = max_total_position
        self.max_single_position = max_single_position
        self.positions = positions if positions is not None else []

    def current_total_position(self) -> float:
        """当前总仓位 %"""
        return sum(float(p.get('position_pct', 0)) for p in self.positions)

    def can_open(self, position_pct) -> bool:
        """判断是否可开仓

        Args:
            position_pct: 拟开仓仓位 %

        Returns:
            bool: True 可开; False 超限
        """
        if position_pct <= 0:
            return False
        if position_pct > self.max_single_position:
            logger.warning('风控拦截: 单只仓位 {:.1f}% 超上限 {}%',
                           position_pct, self.max_single_position)
            return False
        total = self.current_total_position()
        if total + position_pct > self.max_total_position:
            logger.warning('风控拦截: 总仓位 {:.1f}%+{:.1f}% 超上限 {}%',
                           total, position_pct, self.max_total_position)
            return False
        return True

    def check_exit(self, position, current_price):
        """判断持仓是否触发止损/止盈

        Args:
            position: 持仓 dict {cost_price, stop_loss, stop_profit, ...}
                stop_loss / stop_profit 为百分比 (正数, 如 5 表示 5%)
            current_price: 当前价

        Returns:
            tuple|None: (action, reason) action='sell'; 未触发返回 None
        """
        cost = float(position.get('cost_price', 0))
        if cost <= 0:
            return None
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            return None

        change_pct = (current_price - cost) / cost * 100
        stop_loss = float(position.get('stop_loss', 0) or 0)
        stop_profit = float(position.get('stop_profit', 0) or 0)

        if stop_loss > 0 and change_pct <= -stop_loss:
            reason = f'止损: 跌幅 {change_pct:.2f}% 达 -{stop_loss}%'
            logger.info('出场触发 {} {}', position.get('code'), reason)
            return ('sell', reason)
        if stop_profit > 0 and change_pct >= stop_profit:
            reason = f'止盈: 涨幅 {change_pct:.2f}% 达 {stop_profit}%'
            logger.info('出场触发 {} {}', position.get('code'), reason)
            return ('sell', reason)
        return None

    def add_position(self, position):
        """新增持仓"""
        self.positions.append(position)

    def remove_position(self, code):
        """移除持仓 (按 code)"""
        self.positions = [p for p in self.positions if p.get('code') != code]
