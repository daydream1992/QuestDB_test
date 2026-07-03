"""c6: 龙虎榜采集

脚本路径: K:\QuestDB_test\\collect\\c6_lhb.py
用途: 盘后拉龙虎榜明细 + 识别知名营业部, 写 qd_lhb_detail + qd_lhb_broker
数据源: tqcenter get_lhb_data(date)
入库表:
  - qd_lhb_detail  (龙虎榜明细, 按 lhb_date+code+broker_name 粒度)
  - qd_lhb_broker  (营业部画像, 当日聚合)
频率: 盘后 15:30, 1 次/天
字段映射 (qd_lhb_detail):
  code          ← 上榜股票代码
  lhb_date      ← 龙虎榜日期 (date 参数)
  reason        ← 上榜原因
  rank          ← 营业部在买5/卖5中的排名 (1-5)
  buy_amount    ← 营业部买入额
  sell_amount   ← 营业部卖出额
  net_amount    ← buy_amount - sell_amount
  broker_name   ← 营业部名称
  broker_type   ← FAMOUS_BROKERS 识别 (hot_money_xz/institution/north_sh 等), 未识别为 ''
  broker_label  ← BROKER_LABELS 标签 (拉萨天团/机构 等), 未识别为 ''
字段映射 (qd_lhb_broker, 当日聚合):
  broker_name       ← 营业部名称
  update_time       ← datetime.now()
  broker_type       ← FAMOUS_BROKERS 识别
  broker_label      ← BROKER_LABELS
  total_buy_30d     ← 当日该营业部累计买入额 (注: 30 日聚合由 compute 模块完成, 这里写当日值)
  total_sell_30d    ← 当日累计卖出额
  appear_count_30d  ← 当日上榜次数
  hot_level         ← 热度评级 1-5 (按当日上榜次数)

说明:
  - 入库 code 用标准代码 ('000001.SZ')
  - 买5/卖5 营业部逐个展开, 同一 (code, broker_name) 在买卖都出现时聚合 buy/sell
  - 营业部识别用 config/broker_list.py 的 FAMOUS_BROKERS / BROKER_LABELS
  - qd_lhb_broker 当日画像: 30 日聚合理论上由 compute 模块做, c6 写当日值占位
  - tqcenter COM 单进程串行, 用 safe_call 包装
"""

import os
import sys
from datetime import datetime, date

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.tq_client import safe_call, init, close  # noqa: E402
from lib.qdb import connect, executemany_batch  # noqa: E402

from config.broker_list import FAMOUS_BROKERS, BROKER_LABELS  # noqa: E402
from tqcenter import tq  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c6_lhb_{time:YYYYMMDD}.log'),
           rotation='1 day', retention='30 days', encoding='utf-8')

# qd_lhb_detail 列顺序 (与 DDL 12_lhb.sql 严格一致)
LHB_DETAIL_COLS = [
    'code', 'lhb_date', 'reason', 'rank',
    'buy_amount', 'sell_amount', 'net_amount',
    'broker_name', 'broker_type', 'broker_label',
]

# qd_lhb_broker 列顺序
LHB_BROKER_COLS = [
    'broker_name', 'update_time', 'broker_type', 'broker_label',
    'total_buy_30d', 'total_sell_30d', 'appear_count_30d', 'hot_level',
]

# 字段名候选 (兼容中英文)
_CODE_KEYS = ['code', '股票代码', 'Code']
_REASON_KEYS = ['reason', '上榜原因', 'Reason']
_BUY5_KEYS = ['买5', 'buy5', 'Buy5', 'buy_list', 'BuyList']
_SELL5_KEYS = ['卖5', 'sell5', 'Sell5', 'sell_list', 'SellList']
_BROKER_NAME_KEYS = ['name', '营业部名称', 'operator_name', 'seat']
_BUY_AMT_KEYS = ['buy', '买入额', 'buy_amount', 'BuyMoney']
_SELL_AMT_KEYS = ['sell', '卖出额', 'sell_amount', 'SellMoney']


def _pick(d, keys, default=None):
    """从 dict 按候选 key 取值"""
    if not d:
        return default
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def _identify_broker(broker_name):
    """识别营业部身份

    Returns:
        (broker_type, broker_label)
    """
    if not broker_name:
        return ('', '')
    # 精确匹配
    btype = FAMOUS_BROKERS.get(broker_name)
    if btype:
        return (btype, BROKER_LABELS.get(btype, ''))
    # 包含匹配 (如 "东方财富证券拉萨..." 变体)
    for key, t in FAMOUS_BROKERS.items():
        if key and key in broker_name:
            return (t, BROKER_LABELS.get(t, ''))
    return ('', '')


def parse_lhb_detail(data, lhb_date):
    """解析龙虎榜明细 → (detail_rows, broker_agg)

    Args:
        data: get_lhb_data 返回 list[dict], 每个是一只股票
        lhb_date: 龙榜日期 (date → datetime)

    Returns:
        detail_rows: list[tuple] 与 LHB_DETAIL_COLS 对应
        broker_agg:  dict{broker_name: {'buy':, 'sell':, 'count':, 'type':, 'label':}}
    """
    detail_rows = []
    broker_agg = {}

    for item in (data or []):
        code = _pick(item, _CODE_KEYS, '')
        reason = _pick(item, _REASON_KEYS, '')
        if not code:
            continue

        # 买5/卖5 营业部列表
        buy5 = _pick(item, _BUY5_KEYS, []) or []
        sell5 = _pick(item, _SELL5_KEYS, []) or []

        # 按 (broker_name) 聚合, 买侧 rank=1..5, 卖侧 rank=1..5
        # 同一 broker 在买卖都出现时, 合并 buy/sell, rank 取首次出现位置
        seen = {}  # broker_name → rank

        for idx, b in enumerate(buy5, start=1):
            bname = _pick(b, _BROKER_NAME_KEYS, '')
            if not bname:
                continue
            buy_amt = _pick(b, _BUY_AMT_KEYS, 0) or 0
            sell_amt = _pick(b, _SELL_AMT_KEYS, 0) or 0
            if bname not in seen:
                seen[bname] = idx
                btype, blabel = _identify_broker(bname)
                detail_rows.append((
                    code, lhb_date, reason, idx,
                    buy_amt, sell_amt, buy_amt - sell_amt,
                    bname, btype, blabel,
                ))
            # 营业部聚合
            agg = broker_agg.setdefault(bname, {'buy': 0, 'sell': 0, 'count': 0})
            agg['buy'] += buy_amt
            agg['sell'] += sell_amt

        for idx, s in enumerate(sell5, start=1):
            bname = _pick(s, _BROKER_NAME_KEYS, '')
            if not bname:
                continue
            buy_amt = _pick(s, _BUY_AMT_KEYS, 0) or 0
            sell_amt = _pick(s, _SELL_AMT_KEYS, 0) or 0
            if bname not in seen:
                # 卖侧新出现的营业部
                seen[bname] = idx
                btype, blabel = _identify_broker(bname)
                detail_rows.append((
                    code, lhb_date, reason, idx,
                    buy_amt, sell_amt, buy_amt - sell_amt,
                    bname, btype, blabel,
                ))
            # 营业部聚合 (买5 已计入的不重复加, 但 sell 侧的 sell_amt 要补)
            # 简化: 买5和卖5的 buy/sell 分别累加 (避免重复)
            agg = broker_agg.setdefault(bname, {'buy': 0, 'sell': 0, 'count': 0})
            agg['sell'] += sell_amt
            if bname not in {x for x in seen if seen[x] != idx}:
                agg['buy'] += buy_amt

        # 上榜一次计数 +1
        for bname in seen:
            broker_agg.setdefault(bname, {'buy': 0, 'sell': 0, 'count': 0})
            broker_agg[bname]['count'] += 1

    return detail_rows, broker_agg


def _build_broker_rows(broker_agg, update_time):
    """构造 qd_lhb_broker 行

    hot_level: 按当日上榜次数评级 1-5
        count>=10 → 5, >=6 → 4, >=3 → 3, >=1 → 2, 否则 1
    """
    rows = []
    for bname, agg in broker_agg.items():
        btype, blabel = _identify_broker(bname)
        cnt = agg['count']
        if cnt >= 10:
            hot = 5
        elif cnt >= 6:
            hot = 4
        elif cnt >= 3:
            hot = 3
        elif cnt >= 1:
            hot = 2
        else:
            hot = 1
        rows.append((
            bname, update_time, btype, blabel,
            agg['buy'], agg['sell'], cnt, hot,
        ))
    return rows


def run(date=None, con=None):
    """龙虎榜采集主入口

    Args:
        date: 龙虎榜日期 (datetime.date), None 用今天
        con:  psycopg2 连接, None 则自建

    Returns:
        dict: {'qd_lhb_detail': n, 'qd_lhb_broker': n}
    """
    own_con = con is None
    if own_con:
        con = connect()

    if date is None:
        date = datetime.now().date()
    # lhb_date 用当日 00:00:00 的 datetime (QuestDB TIMESTAMP)
    lhb_date = datetime(date.year, date.month, date.day)

    try:
        logger.info('龙虎榜采集开始, date={}', date)

        # 1. 调用 get_lhb_data
        data = safe_call(tq.get_lhb_data, date=date)
        if not data:
            logger.warning('get_lhb_data 返回空, date={}', date)
            return {'qd_lhb_detail': 0, 'qd_lhb_broker': 0}

        logger.info('龙虎榜原始记录 {} 条', len(data) if hasattr(data, '__len__') else '?')

        # 2. 解析明细 + 营业部聚合
        detail_rows, broker_agg = parse_lhb_detail(data, lhb_date)
        logger.info('解析: 明细 {} 行, 营业部 {} 个', len(detail_rows), len(broker_agg))

        # 3. 写 qd_lhb_detail
        n_detail = executemany_batch(con, 'qd_lhb_detail', LHB_DETAIL_COLS, detail_rows)

        # 4. 写 qd_lhb_broker (当日画像)
        broker_rows = _build_broker_rows(broker_agg, datetime.now())
        n_broker = executemany_batch(con, 'qd_lhb_broker', LHB_BROKER_COLS, broker_rows)

        logger.info('龙虎榜写入完成: detail={}, broker={}', n_detail, n_broker)
        return {'qd_lhb_detail': n_detail, 'qd_lhb_broker': n_broker}
    finally:
        if own_con:
            con.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='c6 龙虎榜采集')
    parser.add_argument('--date', type=str, default=None,
                        help='龙虎榜日期 YYYY-MM-DD, 默认今天')
    args = parser.parse_args()

    d = None
    if args.date:
        d = datetime.strptime(args.date, '%Y-%m-%d').date()

    init()
    try:
        run(date=d)
    finally:
        close()


if __name__ == '__main__':
    main()
