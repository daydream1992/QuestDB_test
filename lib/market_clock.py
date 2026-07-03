"""交易时钟

脚本路径: K:\QuestDB_test\\lib\\market_clock.py
用途: 判断交易日 / 盘中 / 竞价时段, 返回当前交易阶段
依赖: 无 (仅标准库 datetime)
数据源: 系统本地时间 (Asia/Shanghai)
入库表: 无
说明:
  - is_trading_day 简单按周一~周五判断, 不查假日历
  - 阶段划分遵循 A 股常规交易时段
"""

from datetime import datetime, time


def is_trading_day(now=None):
    """判断今天是否交易日 (周一~周五, 不查假日历)

    Args:
        now: datetime, 默认 datetime.now()
    """
    now = now or datetime.now()
    return now.weekday() < 5  # 0=周一 ... 4=周五


def is_trading_time(now=None):
    """判断当前是否在盘中 (9:30-11:30 / 13:00-15:00)"""
    now = now or datetime.now()
    if not is_trading_day(now):
        return False
    t = now.time()
    return (time(9, 30) <= t <= time(11, 30)) or (time(13, 0) <= t <= time(15, 0))


def is_auction_time(now=None):
    """判断是否竞价时段 (9:15-9:25 / 14:57-15:00)"""
    now = now or datetime.now()
    if not is_trading_day(now):
        return False
    t = now.time()
    return (time(9, 15) <= t <= time(9, 25)) or (time(14, 57) <= t <= time(15, 0))


def get_phase(now=None):
    """返回当前阶段

    取值:
      - pre_market: 交易日 09:15 前 (含 09:25-09:30 撮合段)
      - auction:    09:15-09:25 集合竞价
      - morning:    09:30-11:30 上午连续竞价
      - lunch:      11:30-13:00 午间休市
      - afternoon:  13:00-14:57 下午连续竞价
      - pre_close:  14:57-15:00 收盘集合竞价
      - closed:     非交易日 / 15:00 后
    """
    now = now or datetime.now()
    if not is_trading_day(now):
        return 'closed'
    t = now.time()
    if t < time(9, 15):
        return 'pre_market'
    if t < time(9, 25):
        return 'auction'
    if t < time(9, 30):
        return 'pre_market'  # 09:25-09:30 撮合, 不可挂撤单
    if t < time(11, 30):
        return 'morning'
    if t < time(13, 0):
        return 'lunch'
    if t < time(14, 57):
        return 'afternoon'
    if t <= time(15, 0):
        return 'pre_close'
    return 'closed'


def get_auction_phase(now=None):
    """返回竞价子阶段

    取值:
      - pre_open:  09:15-09:20 开盘集合竞价 (可撤单)
      - pre_open2: 09:20-09:25 开盘集合竞价 (不可撤单)
      - open:      09:25-09:30 撮合阶段
      - pre_close: 14:57-15:00 收盘集合竞价
      - none:      非竞价时段
    """
    now = now or datetime.now()
    if not is_trading_day(now):
        return 'none'
    t = now.time()
    if time(9, 15) <= t < time(9, 20):
        return 'pre_open'
    if time(9, 20) <= t < time(9, 25):
        return 'pre_open2'
    if time(9, 25) <= t < time(9, 30):
        return 'open'
    if time(14, 57) <= t <= time(15, 0):
        return 'pre_close'
    return 'none'
