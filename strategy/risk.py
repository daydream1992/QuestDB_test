"""风控

脚本路径: K:\QuestDB_test\\strategy\\risk.py
用途: 仓位上限校验 + 止损止盈出场判断 + 持仓 DB 持久化
依赖: loguru, lib.qdb
配置: config/strategies.yaml 的 risk 段
说明:
  - RiskManager 持仓同步到 qd_positions 表, 进程重启恢复
  - can_open 接受 code 参数, 查重仓
"""

from datetime import datetime
from loguru import logger
from lib.qdb import connect, executemany_batch, query_df, cutoff

_POSITION_COLS = ['update_time', 'code', 'direction', 'entry_price', 'current_price',
                  'quantity', 'pnl', 'pnl_pct', 'stop_loss_price', 'take_profit_price', 'status']


class RiskManager:
    """风控管理器"""

    def __init__(self, max_total_position=80, max_single_position=30,
                 positions=None, con=None):
        self.max_total_position = max_total_position
        self.max_single_position = max_single_position
        self.positions = positions if positions is not None else []
        if con is not None:
            self._load_positions(con)

    def _load_positions(self, con):
        """从 qd_positions 加载持仓"""
        try:
            df = query_df(con,
                "SELECT code, entry_price, stop_loss_price, take_profit_price, "
                "quantity, current_price, pnl, pnl_pct, status "
                "FROM qd_positions WHERE status = 'open'")
            if df is not None and not df.empty:
                self.positions = []
                for _, r in df.iterrows():
                    self.positions.append({
                        'code': r['code'],
                        'entry_price': float(r.get('entry_price', 0) or 0),
                        'stop_loss': float(r.get('stop_loss_price', 0) or 0),
                        'stop_profit': float(r.get('take_profit_price', 0) or 0),
                        'position_pct': 0,  # 盘后恢复无仓位%, 默认 0
                        'cost_price': float(r.get('entry_price', 0) or 0),
                    })
                logger.info('风控加载 {} 笔持仓', len(self.positions))
        except Exception as e:
            logger.warning('风控加载持仓失败: {}', e)

    def current_total_position(self) -> float:
        """当前总仓位 %"""
        return sum(float(p.get('position_pct', 0)) for p in self.positions)

    def can_open(self, code, position_pct) -> bool:
        """判断是否可开仓

        Args:
            code: 标的代码 (检查是否已持仓)
            position_pct: 拟开仓仓位 %

        Returns:
            bool: True 可开; False 超限/重复
        """
        if not code or position_pct <= 0:
            return False
        # 查重仓
        if any(p.get('code') == code for p in self.positions):
            logger.warning('风控拦截: {} 已持仓, 不可重复开仓', code)
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
            position: 持仓 dict {entry_price/cost_price, stop_loss/stop_loss_price, ...}
            current_price: 当前价

        Returns:
            tuple|None: (action, reason) action='sell'; 未触发返回 None
        """
        cost = float(position.get('entry_price') or position.get('cost_price', 0))
        if cost <= 0:
            return None
        try:
            current_price = float(current_price)
        except (TypeError, ValueError):
            return None

        change_pct = (current_price - cost) / cost * 100
        stop_loss = float(position.get('stop_loss') or position.get('stop_loss_price', 0) or 0)
        stop_profit = float(position.get('stop_profit') or position.get('take_profit_price', 0) or 0)

        if stop_loss > 0 and change_pct <= -stop_loss:
            reason = f'止损: 跌幅 {change_pct:.2f}% 达 -{stop_loss}%'
            logger.info('出场触发 {} {}', position.get('code'), reason)
            return ('sell', reason)
        if stop_profit > 0 and change_pct >= stop_profit:
            reason = f'止盈: 涨幅 {change_pct:.2f}% 达 {stop_profit}%'
            logger.info('出场触发 {} {}', position.get('code'), reason)
            return ('sell', reason)
        return None

    def add_position(self, position, con=None):
        """新增持仓 (内存 + 可选的 DB 持久化)

        Args:
            position: dict {code, entry_price, position_pct, stop_loss, stop_profit, ...}
            con: 可选 DB 连接, 有则写入 qd_positions
        """
        self.positions.append(position)
        if con is not None:
            try:
                now = datetime.now()
                row = (now, position['code'], 'long',
                       float(position.get('entry_price', 0) or 0),
                       float(position.get('entry_price', 0) or 0),
                       1, 0.0, 0.0,
                       float(position.get('stop_loss', 0) or 0),
                       float(position.get('stop_profit', 0) or 0),
                       'open')
                executemany_batch(con, 'qd_positions', _POSITION_COLS, [row])
            except Exception as e:
                logger.warning('写入持仓 {} 失败: {}', position.get('code'), e)

    def remove_position(self, code, con=None):
        """移除持仓 (按 code, 内存 + 可选的 DB 关闭)

        Args:
            code: 标的代码
            con: 可选 DB 连接, 有则更新 qd_positions 状态为 closed
        """
        self.positions = [p for p in self.positions if p.get('code') != code]
        if con is not None:
            try:
                cur = con.cursor()
                try:
                    cur.execute(
                        "UPDATE qd_positions SET status = 'closed', update_time = %s "
                        "WHERE code = %s AND status = 'open'",
                        (datetime.now(), code))
                finally:
                    cur.close()
            except Exception as e:
                logger.warning('关闭持仓 {} 失败: {}', code, e)
