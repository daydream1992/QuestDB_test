"""盘前初始化

脚本路径: K:\QuestDB_test\\runner\\daily_init.py
用途: 9:25 盘前执行 1 次, 加载映射 + 刷新注册表 + 拉日级数据
执行时间: 09:25 (交易日)
流程:
  1. c5_mapping: 加载板块映射 JSON → 6 张关系图谱表
  2. tq_utils.refresh_registry: 刷新注册表 (新股发现)
  3. c3_more_info: 全场 88 字段 → qd_*_daily (3 张日级表)
  4. 飞书汇报
"""

import os
import sys
from datetime import datetime

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect  # noqa: E402
from lib.tq_client import init, close  # noqa: E402
from lib.tq_utils import refresh_registry, fetch_all_codes  # noqa: E402
import importlib as _il
_feishu = _il.import_module('feishu')  # noqa: E402

import collect.c5_mapping as c5  # noqa: E402
import collect.c3_more_info as c3  # noqa: E402
import collect.c4_kline as c4  # noqa: E402


def _c4_with_retry(codes, period, count, con, retries=3):
    """K 线预拉带重试"""
    import time
    for i in range(retries):
        try:
            return c4.run(codes, period=period, count=count, con=con)
        except Exception as e:
            logger.warning('c4 {} 第{}次失败: {}', period, i + 1, e)
            if i < retries - 1:
                time.sleep(2)
    return 0

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'runner_daily_init_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')


def run(con=None):
    """盘前初始化主流程

    Args:
        con: psycopg2 连接, None 则自建
    """
    logger.info('===== daily_init 开始 {} =====', datetime.now())
    own_con = con is None
    if own_con:
        con = connect()
    try:
        # 1. 加载板块映射 JSON → 6 张关系图谱表
        n1 = c5.run(con=con)
        logger.info('c5_mapping 完成: {}', n1)

        # 2. 刷新注册表 (新股发现)
        n2 = refresh_registry(con)
        logger.info('refresh_registry 完成: {} 条', n2)

        # 3. 拉全场 more_info 日级数据
        meta = fetch_all_codes()
        codes = [c['code'] for c in meta]
        n3 = c3.run(codes, mode='daily', con=con)
        logger.info('c3_more_info daily 完成: {}', n3)

        # 4. 预拉 K 线 (供盘中 k1 指标计算用, 必须 9:25 拉足量)
        n4_1m = _c4_with_retry(codes, '1m', 240, con)
        logger.info('c4_kline 1m 完成: {} 行', n4_1m)
        n4_5m = _c4_with_retry(codes, '5m', 240, con)
        logger.info('c4_kline 5m 完成: {} 行', n4_5m)

        # 5. 飞书汇报
        msg = ('[daily_init] 完成\n'
               '  映射: {}\n'
               '  注册表: {} 条\n'
               '  日级采集: {}\n'
               '  K线 1m: {} 行\n'
               '  K线 5m: {} 行\n'
               '  时间: {}').format(n1, n2, n3, n4_1m, n4_5m, datetime.now())
        _feishu.push_text(msg)
        logger.info('===== daily_init 完成 =====')
    finally:
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
