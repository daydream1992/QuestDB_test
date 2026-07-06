"""组合管理 + 风控 (Portfolio v2)

脚本路径: K:\QuestDB_test\\strategy\\portfolio.py
用途: 替代 strategy/risk.py 的简单仓位上限, 提供组合层面风控 + 仓位管理
依赖: loguru / datetime / pandas / lib.qdb
设计:
  - Portfolio 持仓: dict[code, Position], 启动时从 qd_positions_v2 恢复
  - can_open: 总仓位/单股/行业/日亏熔断/持仓数 5 重检查
  - size_position: 波动率目标仓位 × alpha 分位调整
  - mark_to_market: 每轮按最新价更新 unrealized_pnl
  - 持久化: open/close 时写 qd_positions_v2 (DEDUP UPSERT 幂等)
说明:
  - 与 RiskManager 并存, 老策略继续用 RiskManager, 新策略用 Portfolio
  - 迁移完成后删 RiskManager
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Tuple, List

from loguru import logger


@dataclass
class Position:
    """单只持仓"""
    code: str
    sector: str
    shares: int                # 股数 (A 股 100 股一手)
    entry_price: float
    entry_time: datetime
    size_pct: float            # 占总资金仓位 %
    stop_loss_pct: float       # 止损 % (正数)
    stop_profit_pct: float     # 止盈 % (正数)
    entry_alpha: float = 0.0
    entry_rank: int = 0
    status: str = 'open'       # 'open' / 'closed'
    realized_pnl: float = 0.0
    close_price: float = 0.0
    close_time: Optional[datetime] = None
    close_reason: str = ''

    @property
    def market_value(self) -> float:
        return self.shares * self.entry_price

    def unrealized_pnl(self, current_price: float) -> float:
        return (current_price - self.entry_price) * self.shares

    def unrealized_pnl_pct(self, current_price: float) -> float:
        if self.entry_price <= 0:
            return 0.0
        return (current_price - self.entry_price) / self.entry_price


class Portfolio:
    """组合管理器"""

    # 风控参数 (可从 strategies.yaml 覆盖)
    MAX_TOTAL_POSITION = 0.80    # 总仓位上限 80%
    MAX_SINGLE_POSITION = 0.30   # 单只仓位上限 30%
    MAX_SECTOR_EXPOSURE = 0.30   # 单板块仓位上限 30%
    MAX_POSITIONS = 10           # 最大持仓数
    DAILY_LOSS_CIRCUIT_BREAKER = -0.03   # 日亏 3% 熔断
    TARGET_VOL_PER_POSITION = 0.02       # 单只目标波动 2%

    def __init__(self, capital: float = 1_000_000):
        self.capital = capital
        self.positions: dict[str, Position] = {}
        self.daily_pnl: float = 0.0
        self.realized_pnl_total: float = 0.0
        self.trade_log: list[dict] = []

    # ========== 加载/持久化 ==========

    @classmethod
    def load_from_db(cls, con, capital: float = 1_000_000) -> 'Portfolio':
        """从 qd_positions_v2 恢复 open 状态持仓"""
        pf = cls(capital=capital)
        try:
            from lib.qdb import query_df
            df = query_df(con, """
                SELECT * FROM qd_positions_v2
                WHERE status = 'open'
                  AND updated_time > dateadd('h', -24, now())
                LATEST ON updated_time PARTITION BY code
            """)
            if df is None or df.empty:
                return pf
            for _, r in df.iterrows():
                pf.positions[r['code']] = Position(
                    code=r['code'],
                    sector=r.get('sector', 'UNKNOWN'),
                    shares=int(r.get('shares', 0)),
                    entry_price=float(r.get('entry_price', 0)),
                    entry_time=r.get('entry_time', datetime.now()),
                    size_pct=float(r.get('size_pct', 0)),
                    stop_loss_pct=float(r.get('stop_loss_pct', 0)),
                    stop_profit_pct=float(r.get('stop_profit_pct', 0)),
                    entry_alpha=float(r.get('entry_alpha', 0)),
                    entry_rank=int(r.get('entry_rank', 0)),
                    status='open',
                )
            logger.info('Portfolio 从 DB 恢复 {} 个持仓', len(pf.positions))
        except Exception as e:
            logger.warning('Portfolio 从 DB 恢复失败 (新表?): {}', e)
        return pf

    def persist_open(self, con, pos: Position):
        """写持仓到 qd_positions_v2 (open 状态)"""
        try:
            from lib.qdb import executemany_batch
            cols = ['updated_time', 'code', 'direction', 'size_pct', 'shares',
                    'entry_price', 'entry_time', 'sector', 'stop_loss_pct',
                    'stop_profit_pct', 'entry_alpha', 'entry_rank', 'status',
                    'realized_pnl', 'close_price', 'close_time', 'close_reason']
            row = (datetime.now(), pos.code, 'long', pos.size_pct, pos.shares,
                   pos.entry_price, pos.entry_time, pos.sector,
                   pos.stop_loss_pct, pos.stop_profit_pct,
                   pos.entry_alpha, pos.entry_rank, pos.status,
                   pos.realized_pnl, pos.close_price,
                   pos.close_time or datetime.now(), pos.close_reason)
            executemany_batch(con, 'qd_positions_v2', cols, [row])
        except Exception as e:
            logger.warning('持仓写入失败 {} {}: {}', pos.code, pos.status, e)

    def persist_close(self, con, code: str, close_price: float,
                      reason: str):
        """平仓后写 closed 状态"""
        pos = self.positions.get(code)
        if pos is None:
            return
        pos.status = 'closed'
        pos.close_price = close_price
        pos.close_time = datetime.now()
        pos.close_reason = reason
        pos.realized_pnl = (close_price - pos.entry_price) * pos.shares
        self.realized_pnl_total += pos.realized_pnl
        self.daily_pnl += pos.realized_pnl
        self.persist_open(con, pos)  # 复用, status=closed
        self.trade_log.append({
            'time': datetime.now(), 'code': code, 'action': 'close',
            'price': close_price, 'pnl': pos.realized_pnl, 'reason': reason,
        })

    # ========== 风控检查 ==========

    def total_position_pct(self) -> float:
        """当前总仓位 % (0-1)"""
        return sum(p.size_pct for p in self.positions.values()) / 100.0

    def sector_exposure(self, sector: str) -> float:
        """单板块仓位占比"""
        sector_pct = sum(p.size_pct for p in self.positions.values()
                         if p.sector == sector)
        return sector_pct / 100.0

    def can_open(self, code: str, alpha_score: float,
                 sector: str) -> Tuple[bool, str]:
        """组合层面开仓检查

        Returns:
            (是否可开, 原因)
        """
        # 1. 已持仓不加仓 (A 股 T+1, 加仓逻辑可后续支持)
        if code in self.positions:
            return False, '已持仓'
        # 2. 持仓数上限
        if len(self.positions) >= self.MAX_POSITIONS:
            return False, f'持仓数超限 ({self.MAX_POSITIONS})'
        # 3. 总仓位
        if self.total_position_pct() >= self.MAX_TOTAL_POSITION:
            return False, f'总仓位超限 ({self.total_position_pct()*100:.0f}%>'
        # 4. 行业集中度
        if self.sector_exposure(sector) >= self.MAX_SECTOR_EXPOSURE:
            return False, f'行业集中 ({sector}: {self.sector_exposure(sector)*100:.0f}%>'
        # 5. 当日亏损熔断
        if self.daily_pnl <= self.DAILY_LOSS_CIRCUIT_BREAKER * self.capital:
            return False, f'日亏熔断 ({self.daily_pnl:.0f})'
        # 6. alpha 极低不开 (排末尾的票)
        if alpha_score < -1.0:
            return False, f'alpha 过低 ({alpha_score:.2f})'
        return True, 'OK'

    # ========== 仓位管理 ==========

    def size_position(self, alpha_score: float,
                      volatility: float) -> float:
        """波动率目标仓位 + alpha 调整

        Args:
            alpha_score: 归一化后的 alpha 分数 (z-score 量级)
            volatility: 标的近期波动率 (e.g. 5m 收益 std)

        Returns:
            仓位百分比 (0-30, e.g. 10.0 表示 10%)
        """
        if volatility <= 0:
            base_pct = 5.0  # 默认 5%
        else:
            # 目标波动 2% / 实际波动 = 杠杆倍数
            vol_mult = self.TARGET_VOL_PER_POSITION / volatility
            base_pct = vol_mult * 100.0
        # alpha 分位调整: z=0 → 1.0x; z=+2 → 1.5x; z=-2 → 0.5x
        alpha_mult = max(0.5, min(1.5, 1.0 + alpha_score * 0.25))
        size_pct = base_pct * alpha_mult
        # 单只上限
        return min(size_pct, self.MAX_SINGLE_POSITION * 100)

    def open(self, con, code: str, price: float, size_pct: float,
             sector: str, stop_loss_pct: float = 5.0,
             stop_profit_pct: float = 10.0,
             alpha_score: float = 0.0, rank: int = 0):
        """开仓 (A 股按 100 股取整)"""
        if size_pct <= 0:
            return False
        target_value = self.capital * size_pct / 100.0
        shares = int(target_value / price / 100) * 100  # 100 股一手
        if shares <= 0:
            logger.info('资金不足开 {} (price={}, size={}%)', code, price, size_pct)
            return False
        pos = Position(
            code=code, sector=sector, shares=shares,
            entry_price=price, entry_time=datetime.now(),
            size_pct=size_pct,
            stop_loss_pct=stop_loss_pct,
            stop_profit_pct=stop_profit_pct,
            entry_alpha=alpha_score, entry_rank=rank,
        )
        self.positions[code] = pos
        self.persist_open(con, pos)
        self.trade_log.append({
            'time': datetime.now(), 'code': code, 'action': 'open',
            'price': price, 'shares': shares, 'size_pct': size_pct,
        })
        logger.info('开仓 {} {}股@{} ({}%, alpha={}, rank={})',
                    code, shares, price, f'{size_pct:.1f}%',
                    f'{alpha_score:.2f}', rank)
        return True

    def close(self, con, code: str, price: float, reason: str):
        """平仓"""
        if code not in self.positions:
            return False
        self.persist_close(con, code, price, reason)
        del self.positions[code]
        logger.info('平仓 {} @{} ({})', code, price, reason)
        return True

    # ========== 估值 ==========

    def mark_to_market(self, prices: dict):
        """按最新价更新 unrealized_pnl (仅内存, 不落库)"""
        for code, pos in self.positions.items():
            if code in prices:
                pos._unrealized_pnl = pos.unrealized_pnl(prices[code])

    def total_unrealized_pnl(self) -> float:
        return sum(getattr(p, '_unrealized_pnl', 0)
                   for p in self.positions.values())

    def daily_reset(self):
        """每日开盘前重置日亏"""
        self.daily_pnl = 0.0

    def summary(self) -> dict:
        return {
            'positions': len(self.positions),
            'total_position_pct': f'{self.total_position_pct()*100:.1f}%',
            'realized_pnl_total': self.realized_pnl_total,
            'daily_pnl': self.daily_pnl,
            'unrealized_pnl': self.total_unrealized_pnl(),
            'sector_breakdown': self._sector_breakdown(),
        }

    def _sector_breakdown(self) -> dict:
        bk = {}
        for p in self.positions.values():
            bk[p.sector] = bk.get(p.sector, 0) + p.size_pct
        return bk
