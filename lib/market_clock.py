"""交易时钟

脚本路径: K:\QuestDB_test\\lib\market_clock.py
用途: 判断交易日 / 盘中 / 竞价时段, 返回当前交易阶段
依赖: 系统本地时间 + tqcenter (H6, 交易日历) + .env FORCE_TRADE_DAY
数据源: 系统本地时间 (Asia/Shanghai); 交易日历来自 tqcenter.get_trading_dates
入库表: 无
说明:
  - H6: is_trading_day 优先查交易日历缓存, 缺失/失败回退 weekday, 不阻断 loop
  - 交易日历按年缓存到 %LOCALAPPDATA%\\tqcenter\\trading_dates\\{YYYY}.json
  - FORCE_TRADE_DAY=True (.env 或环境变量) 强制返回 True (节假日测试用)
  - 阶段划分遵循 A 股常规交易时段
"""

import os
import json
from datetime import datetime, time, timedelta

from loguru import logger

# 加载 config/.env 确保 FORCE_TRADE_DAY 生效
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'config', '.env')
if os.path.exists(_ENV_PATH):
    from dotenv import load_dotenv
    load_dotenv(_ENV_PATH)


# === H6 交易日历缓存 ===

def _cache_dir():
    """缓存目录: %LOCALAPPDATA%\\tqcenter\\trading_dates\\ (无则建)"""
    base = os.environ.get('LOCALAPPDATA') or os.path.expanduser('~')
    d = os.path.join(base, 'tqcenter', 'trading_dates')
    os.makedirs(d, exist_ok=True)
    return d


def _cache_path(year: int) -> str:
    return os.path.join(_cache_dir(), f'{year}.json')


def _force_trade_day() -> bool:
    """H6: FORCE_TRADE_DAY 开关 (.env 或环境变量, 默认 False)

    True 时强制 is_trading_day 返回 True (节假日跑回归/手动复盘用)
    """
    return os.environ.get('FORCE_TRADE_DAY', '').lower() in ('1', 'true', 'yes')


def get_trading_dates(year: int, count: int = -1, start: str = '', end: str = '') -> list:
    """H6: 拉某年的交易日列表 (按年缓存)

    优先读 %LOCALAPPDATA%\\tqcenter\\trading_dates\\{year}.json;
    缓存缺失/失败时实时调 tqcenter.get_trading_dates, 成功后写缓存。

    Args:
        year: 4 位年份 (如 2026)
        count: 返回最近的 count 个交易日 (-1 表示全部), 默认 -1
        start: 起始日期 YYYYMMDD, 默认 '' (整年)
        end: 结束日期 YYYYMMDD, 默认 '' (整年)

    Returns:
        list[str]: 交易日列表, YYYYMMDD 格式; 失败返回 []
    """
    cache_file = _cache_path(year)
    # 缓存命中
    if os.path.exists(cache_file):
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning('交易日历缓存读取失败 {}: {}', cache_file, e)

    # 实时调 tqcenter
    try:
        from lib.tq_client import safe_call
        from tqcenter import tq
        if not start:
            start = f'{year}0101'
        if not end:
            end = f'{year}1231'
        dates = safe_call(tq.get_trading_dates, market='SH',
                          start_time=start, end_time=end, count=count)
        # dates 可能是 list[str] (YYYYMMDD) 或其他; 统一转 str
        if dates is None:
            dates = []
        dates = [str(d) for d in dates]
        # 空结果不写缓存 (避免 tqcenter 未初始化时清空有效缓存)
        if dates:
            try:
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(dates, f, ensure_ascii=False, indent=2)
                logger.info('交易日历缓存已写入 {} ({} 个交易日)', cache_file, len(dates))
            except Exception as e:
                logger.warning('交易日历缓存写入失败 {}: {}', cache_file, e)
        return dates
    except Exception as e:
        logger.error('get_trading_dates 失败 (tqcenter 未初始化/999999 未下载?): {}', e)
        return []


def _date_yyyymmdd(dt: datetime) -> str:
    return dt.strftime('%Y%m%d')


def is_trading_day(now=None):
    """判断今天是否交易日 (H6: 优先查交易日历, 失败回退 weekday)

    Args:
        now: datetime, 默认 datetime.now()

    Returns:
        bool:
          - FORCE_TRADE_DAY=True → True
          - 本地日期在交易日历缓存里 → True
          - 缓存为空/调用失败 → weekday < 5 (兜底)
          - 缓存中存在但本地不在 → False (例如春节)
    """
    if _force_trade_day():
        return True
    now = now or datetime.now()
    ymd = _date_yyyymmdd(now)
    dates = get_trading_dates(now.year)
    if not dates:
        # 兜底: weekday 判
        return now.weekday() < 5
    return ymd in dates


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


# ══════════════════════════════════════════════════════════════
# 倒计时与提前启动函数 (监工/scheduler 用)
# ══════════════════════════════════════════════════════════════

# 关键时间节点 (时, 分, 秒)
_PHASE_TARGETS = {
    'auction_start':    (9, 15, 0),   # 竞价开始
    'daily_init':       (9, 25, 0),   # 盘前初始化
    'market_open':      (9, 30, 0),   # 开盘
    'afternoon_open':   (13, 0, 0),   # 下午开盘
    'pre_close_start':  (14, 57, 0),  # 收盘竞价
    'market_close':     (15, 0, 0),   # 收盘
}

_DEFAULT_LEAD_SECONDS = 480  # 8 分钟


def seconds_until(target_hour, target_min, target_sec=0):
    """距目标时间还有多少秒 (负数表示已过)"""
    now = datetime.now()
    target = now.replace(hour=target_hour, minute=target_min,
                         second=target_sec, microsecond=0)
    return (target - now).total_seconds()


def countdown_seconds(target_hour, target_min):
    """距目标还有多少秒 (倒计时, >=0)"""
    return max(0, seconds_until(target_hour, target_min))


def should_prestart(target_hour, target_min, lead_seconds=None):
    """判断是否应该提前启动进程

    检查当前时间是否在 [target-lead, target] 区间内。
    用于 scheduler 在主循环中决定是否提前拉起子进程。
    """
    secs = seconds_until(target_hour, target_min)
    lead = lead_seconds if lead_seconds is not None else _DEFAULT_LEAD_SECONDS
    return 0 <= secs <= lead


def format_countdown(target_hour, target_min):
    """返回倒计时文本 (供飞书推送用)"""
    secs = countdown_seconds(target_hour, target_min)
    m, s = divmod(int(secs), 60)
    if m > 0:
        return f'{m}分{s}秒'
    return f'{s}秒'


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