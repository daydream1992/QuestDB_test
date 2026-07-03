"""c1: 全市场批量价量采集

脚本路径: K:\QuestDB_test\\collect\\c1_pricevol.py
用途: 1 次 get_pricevol 拿全场价量, 写 qd_pricevol
数据源: tqcenter get_pricevol(stock_list)
入库表: qd_pricevol (3 数据列)
频率: 10 秒/轮
字段映射:
  code          ← 标的代码 (标准格式 '000001.SZ', 与 qd_code_registry 一致)
  snapshot_time ← datetime.now()
  last_close    ← LastClose (前收盘价)
  now           ← Now (现价)
  volume        ← Volume (累计成交量)

说明:
  - get_pricevol 传入标准代码 stock_list, 返回 dict{标准code: {LastClose,Now,Volume}}
    (实测 tqcenter 接受标准代码, 返回 key 也是标准代码; 值为字符串需转 float/int)
  - 入库 code 用标准代码 ('000001.SZ'), 与 qd_code_registry / route_type / relation_graph 一致
  - 仅采集股票 + 指数 (有 tdx_code 的标的); 板块价量意义不大且无 tdx_code, 跳过
  - tqcenter COM 单进程串行, 用 lib.tq_client.safe_call 包装
"""

import os
import sys
from datetime import datetime

# 确保项目根在 sys.path
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call, init, close  # noqa: E402
from lib.tq_utils import fetch_all_codes, to_tdx  # noqa: E402
from lib.qdb import connect, executemany_batch  # noqa: E402

from tqcenter import tq  # noqa: E402

# 日志配置
_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c1_pricevol_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')


def _to_float(v):
    """转 float, 失败返回 None (tqcenter 返回字符串值)"""
    if v is None or v == '':
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v):
    """转 int, 失败返回 None"""
    if v is None or v == '':
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_pricevol(data, snapshot_time):
    """解析 get_pricevol 返回的 dict → rows

    Args:
        data: get_pricevol 返回 dict {标准code: {'LastClose':.., 'Now':.., 'Volume':..}}
               (实测 key 为标准代码, 值为字符串)
        snapshot_time: 采集时刻 datetime

    Returns:
        list[tuple]: (code, snapshot_time, last_close, now, volume)
    """
    rows = []
    skipped = 0
    for code, item in (data or {}).items():
        if not item:
            skipped += 1
            continue
        try:
            last_close = _to_float(item.get('LastClose'))
            now_price = _to_float(item.get('Now'))
            volume = _to_int(item.get('Volume'))
            rows.append((code, snapshot_time, last_close, now_price, volume))
        except Exception as e:
            skipped += 1
            logger.warning('解析价量失败 code={}, err={}', code, e)
            continue
    if skipped:
        logger.debug('价量解析跳过 {} 条', skipped)
    return rows


def run(con=None, limit=None):
    """采集全场价量并写入 qd_pricevol

    Args:
        con: psycopg2 连接, None 则自建
        limit: 限制采集数量 (测试用), None 表示全场

    Returns:
        int: 写入行数
    """
    own_con = con is None
    if own_con:
        con = connect()

    try:
        # 1. 拉全市场代码元数据 (fetch_all_codes 修复后返回标准代码 + tdx_code)
        codes_meta = fetch_all_codes()
        # 仅采集有 tdx_code 的 (股票 + 指数); 板块无 tdx_code 跳过
        valid = [c for c in codes_meta if c.get('tdx_code')]
        if limit:
            valid = valid[:limit]
            logger.info('测试模式: 仅取前 {} 只', limit)

        if not valid:
            logger.warning('无可用代码, 退出')
            return 0

        std_codes = [c['code'] for c in valid]
        logger.info('开始采集价量, 共 {} 只', len(std_codes))

        # 2. 调用 get_pricevol (1 次拿全场, 传标准代码)
        snapshot_time = datetime.now()
        data = safe_call(tq.get_pricevol, stock_list=std_codes)
        if not data:
            logger.warning('get_pricevol 返回空')
            return 0

        # 3. 解析 → rows
        rows = parse_pricevol(data, snapshot_time)
        logger.info('解析得到 {} 行价量数据', len(rows))

        # 4. 批量写入 qd_pricevol
        n = executemany_batch(
            con, 'qd_pricevol',
            ['code', 'snapshot_time', 'last_close', 'now', 'volume'],
            rows)
        logger.info('写入 qd_pricevol: {} 行 (snapshot_time={})', n, snapshot_time)
        return n
    finally:
        if own_con:
            con.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='c1 全市场价量采集')
    parser.add_argument('--limit', type=int, default=None, help='限制采集数量 (测试用)')
    args = parser.parse_args()

    init()
    try:
        run(limit=args.limit)
    finally:
        close()


if __name__ == '__main__':
    main()
