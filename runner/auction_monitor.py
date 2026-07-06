"""竞价监控

脚本路径: K:\QuestDB_test\\runner\\auction_monitor.py
用途: 竞价时段拉快照 → 竞价分析 → 信号推送
执行时间: 09:15-09:30 / 14:57-15:00
频率: 3-5s/轮
流程:
  1. 判断竞价子阶段 (pre_open/pre_open2/open/pre_close)
  2. 拉重点 500 只 snapshot → qd_auction_snapshot
  3. 计算竞价缺口 + 量比
  4. 遍历竞价策略插件 → decisions
  5. 飞书推送
"""

import os
import sys
import time
from datetime import datetime, time as dtime

import pandas as pd

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df, executemany_batch, cutoff  # noqa: E402


def _write_heartbeat(name):
    """写入心跳文件 (logs/heartbeats/{name}.ts)"""
    import os as _os
    hb_dir = _os.path.join(_PROJ_ROOT, 'logs', 'heartbeats')
    _os.makedirs(hb_dir, exist_ok=True)
    try:
        fp = os.path.join(hb_dir, f'{name}.ts')
        with open(fp, 'w') as f:
            f.write(str(time.time()))
    except Exception:
        pass
from lib.tq_client import safe_call, init, close  # noqa: E402
import importlib as _il
_feishu = _il.import_module('feishu')  # noqa: E402
from lib.market_clock import is_auction_time, is_trading_day, get_auction_phase  # noqa: E402

from tqcenter import tq  # noqa: E402

from strategy.registry import StrategyRegistry  # noqa: E402
from strategy.context import StrategyContext  # noqa: E402

# 配置路径
_YAML_PATH = os.path.join(_PROJ_ROOT, 'config', 'strategies.yaml')
_PLUGINS_DIR = os.path.join(_PROJ_ROOT, 'strategy', 'plugins')

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'runner_auction_monitor_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# qd_auction_snapshot 列顺序 (与 DDL 10_auction.sql 一致)
_AUCTION_COLS = ['code', 'auction_time', 'auction_price', 'auction_volume',
                 'auction_amount', 'gap_pct', 'auction_type', 'prev_close']

# qd_decisions 列顺序
_DECISION_COLS = ['decision_time', 'code', 'strategy_name',
                  'action', 'position_size', 'price', 'reason']

# 重点标的上限
_FOCUS_LIMIT = 500


def _safe_float(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _get_all_stock_codes(con):
    """从注册表取所有股票代码"""
    try:
        df = query_df(con, "SELECT code FROM qd_code_registry WHERE code_type = 'stock'")
        if not df.empty:
            return df['code'].tolist()
    except Exception as e:
        logger.warning('查询注册表失败: {}', e)
    return []


def _auction_type_for_phase(phase):
    """竞价类型: 开盘竞价 → open, 收盘竞价 → close"""
    if phase in ('pre_open', 'pre_open2', 'open'):
        return 'open'
    if phase == 'pre_close':
        return 'close'
    return 'open'


def _collect_auction(con, codes, auction_type):
    """拉竞价快照 → 写 qd_auction_snapshot, 返回 DataFrame 供策略使用

    Args:
        con: psycopg2 连接
        codes: 待采集代码列表
        auction_type: 'open' / 'close'

    Returns:
        DataFrame: 列与 qd_auction_snapshot 一致
    """
    if not codes:
        return pd.DataFrame()
    now = datetime.now()
    rows = []
    err = 0
    for code in codes:
        try:
            data = safe_call(tq.get_market_snapshot, stock_code=code, field_list=[])
            if not data:
                err += 1
                continue
            now_price = _safe_float(data.get('Now'))
            last_close = _safe_float(data.get('LastClose'))
            volume = _safe_float(data.get('Volume'))
            amount = _safe_float(data.get('Amount'))
            gap = ((now_price - last_close) / last_close * 100
                   if last_close > 0 else 0.0)
            vol_int = int(volume) if volume else None
            rows.append((code, now, now_price, vol_int, amount,
                         gap, auction_type, last_close))
        except Exception as e:
            err += 1
            logger.warning('竞价快照失败 {}: {}', code, e)
    if rows:
        executemany_batch(con, 'qd_auction_snapshot', _AUCTION_COLS, rows)
        logger.info('写入 qd_auction_snapshot: {} 行 (type={}, err={})',
                    len(rows), auction_type, err)
    return pd.DataFrame(rows, columns=_AUCTION_COLS)


def _process_decisions(con, decisions):
    """写 qd_decisions + 飞书推送 (竞价无风控, 直接写)"""
    if not decisions:
        return
    now = datetime.now()
    rows = []
    feishu_signals = []
    for d in decisions:
        reason = d.reason
        if d.score:
            reason = '{} [评分{:.0f}]'.format(reason, d.score)
        rows.append((now, d.code, d.strategy, d.action,
                     d.position_pct, d.price, reason))
        if d.action in ('buy', 'sell', 'watch', 'warn'):
            feishu_signals.append({
                'decision_time': now,
                'code': d.code,
                'strategy_name': d.strategy,
                'action': d.action,
                'position_size': d.position_pct,
                'price': d.price,
                'reason': reason,
            })
    if feishu_signals:
        try:
            _feishu.log_signals(feishu_signals)
        except Exception as e:
            logger.warning('飞书写入失败: {}', e)
    if rows:
        n = executemany_batch(con, 'qd_decisions', _DECISION_COLS, rows)
        logger.info('写入 qd_decisions: {} 行', n)


def run(con=None):
    """竞价监控主循环

    Args:
        con: psycopg2 连接, None 则自建
    """
    logger.info('===== auction_monitor 启动 {} =====', datetime.now())
    own_con = con is None
    if own_con:
        con = connect()

    # 加载策略插件 + 配置
    StrategyRegistry.load_plugins(_PLUGINS_DIR)
    StrategyRegistry.load_config(_YAML_PATH)
    logger.info('策略加载完成: 启用 {} 个', len(StrategyRegistry.get_all()))

    all_stocks = _get_all_stock_codes(con)
    # 重点 500 只
    focus = all_stocks[:_FOCUS_LIMIT] if len(all_stocks) > _FOCUS_LIMIT else all_stocks
    logger.info('竞价监控股票: {} 只', len(focus))

    try:
        while True:
            now = datetime.now()
            # 退出条件: 非交易日 或 15:00 后
            if not is_trading_day(now):
                logger.info('非交易日, 退出')
                break
            if now.time() > dtime(15, 0):
                logger.info('15:00 后, 退出')
                break
            # 09:30-14:57 非竞价时段, 退出让位给 intraday_loop (避免争 COM)
            if dtime(9, 30) <= now.time() < dtime(14, 57):
                logger.info('竞价结束 (09:30-14:57), 退出让位给盘中主循环')
                break

            # 非竞价时段, 短暂等待
            if not is_auction_time(now):
                time.sleep(3)
                continue

            phase = get_auction_phase(now)
            if phase == 'none':
                time.sleep(3)
                continue

            auction_type = _auction_type_for_phase(phase)
            t0 = time.time()
            logger.info('--- 竞价 {} ({}) {} ---',
                        phase, auction_type, now.strftime('%H:%M:%S'))

            # 1. 拉竞价快照 → qd_auction_snapshot
            auction_df = _collect_auction(con, focus, auction_type)

            # 2. 构建上下文
            ctx = StrategyContext(timestamp=now, is_trading=False)
            ctx.auction_df = auction_df
            # more_info (量比需 CJJEPre1 昨成交额)
            try:
                ctx.more_info_df = query_df(
                    con, "SELECT * FROM qd_stock_daily "
                         f"WHERE date > '{cutoff(days=2)}'")
            except Exception:
                pass

            # 3. 遍历竞价策略 (仅 auction_* 开头)
            decisions = []
            for name, cls in StrategyRegistry.get_all().items():
                if not name.startswith('auction'):
                    continue
                try:
                    ds = cls().evaluate(ctx)
                    if ds:
                        decisions.extend(ds)
                except Exception as e:
                    logger.error('竞价策略 {} 失败: {}', name, e)
            if decisions:
                logger.info('竞价策略产出 {} 条决策', len(decisions))

            # 4. 写决策 + 飞书推送
            _process_decisions(con, decisions)

            # 5. 心跳: 每轮刷新时间戳
            _write_heartbeat('auction_monitor')

            # 6. 控频: 撮合阶段/收盘竞价 5s, 其余 3s
            sleep_s = 5 if phase in ('open', 'pre_close') else 3
            elapsed = time.time() - t0
            time.sleep(max(0.5, sleep_s - elapsed))
    except KeyboardInterrupt:
        logger.info('Ctrl+C 退出竞价监控')
    finally:
        logger.info('===== auction_monitor 退出 =====')
        if own_con:
            con.close()


def main():
    import signal

    def _graceful_exit(signum, frame):
        raise KeyboardInterrupt

    if os.name == 'nt':
        signal.signal(signal.SIGBREAK, _graceful_exit)
    signal.signal(signal.SIGTERM, _graceful_exit)

    init()
    try:
        run()
    finally:
        close()


if __name__ == '__main__':
    main()
