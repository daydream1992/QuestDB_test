"""盘后更新

脚本路径: K:\QuestDB_test\\runner\\daily_close.py
用途: 15:05 盘后执行, 更新日级数据 + 龙虎榜 + 策略评估
执行时间: 15:05 (交易日)
流程:
  1. c3_more_info: 全场 88 字段收盘数据 → qd_*_daily
  2. c6_lhb: 龙虎榜数据 → qd_lhb_detail + qd_lhb_broker
  3. 策略评估 → qd_strategy_eval
  4. 飞书汇报当日总结
"""

import os
import sys
from datetime import datetime

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, query_df, executemany_batch  # noqa: E402
from lib.tq_client import init, close  # noqa: E402
from lib.tq_utils import fetch_all_codes  # noqa: E402
from lib import lark  # noqa: E402

import collect.c3_more_info as c3  # noqa: E402
import collect.c6_lhb as c6  # noqa: E402

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'runner_daily_close_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# qd_strategy_eval 列顺序 (与 DDL 06_signals.sql 一致)
_EVAL_COLS = ['eval_time', 'strategy_name', 'total_signals', 'win_count',
              'loss_count', 'win_rate', 'total_pnl', 'profit_factor',
              'max_drawdown']


def _eval_strategies(con):
    """统计 qd_decisions 当日决策数 → qd_strategy_eval

    盘后阶段无成交回测, win/loss/pnl 留空, 仅统计 total_signals。
    """
    try:
        df = query_df(
            con,
            "SELECT strategy_name, COUNT(*) as cnt "
            "FROM qd_decisions "
            "WHERE decision_time > dateadd('d', -1, now()) "
            "GROUP BY strategy_name")
        if df.empty:
            logger.info('当日无决策数据')
            return 0
        now = datetime.now()
        rows = []
        for _, r in df.iterrows():
            rows.append((now, r['strategy_name'], int(r['cnt']),
                         0, 0, 0.0, 0.0, 0.0, 0.0))
        n = executemany_batch(con, 'qd_strategy_eval', _EVAL_COLS, rows)
        logger.info('写入 qd_strategy_eval: {} 行', n)
        return n
    except Exception as e:
        logger.error('策略评估失败: {}', e)
        return 0


def run(con=None):
    """盘后更新主流程

    Args:
        con: psycopg2 连接, None 则自建
    """
    logger.info('===== daily_close 开始 {} =====', datetime.now())
    own_con = con is None
    if own_con:
        con = connect()
    try:
        # 1. 全场日级数据 (收盘 88 字段)
        meta = fetch_all_codes()
        codes = [c['code'] for c in meta]
        n1 = c3.run(codes, mode='daily', con=con)
        logger.info('c3 daily 完成: {}', n1)

        # 2. 龙虎榜
        n2 = c6.run(date=datetime.now().date(), con=con)
        logger.info('c6 lhb 完成: {}', n2)

        # 3. 策略评估
        n3 = _eval_strategies(con)

        # 4. 飞书汇报当日总结
        msg = ('[daily_close] 当日总结\n'
               '  日级采集: {}\n'
               '  龙虎榜: {}\n'
               '  策略评估: {} 条\n'
               '  时间: {}').format(n1, n2, n3, datetime.now())
        lark.push_text(msg)
        logger.info('===== daily_close 完成 =====')
    finally:
        if own_con:
            con.close()


def main():
    init()
    try:
        run()
    finally:
        close()


if __name__ == '__main__':
    main()
