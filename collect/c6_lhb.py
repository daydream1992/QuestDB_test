"""c6: 龙虎榜采集

脚本路径: K:\QuestDB_test\\collect\\c6_lhb.py
用途: 盘后拉龙虎榜明细 + 识别知名营业部, 写 qd_lhb_detail + qd_lhb_broker
数据源: K:\\QTM\\longhubang.db3 (sqlite)
入库表:
  - qd_lhb_detail  (龙虎榜明细, 按 lhb_date+code+broker_name 粒度)
  - qd_lhb_broker  (营业部画像, 当日聚合)
频率: 盘后 15:30, 1 次/天

字段映射 (qd_lhb_detail):
  code          ← stock_code
  lhb_date      ← trade_date
  reason        ← reason
  reason_id     ← reason_id
  direction     ← direction (B/S)
  rank          ← rank
  buy_amount    ← buyin_amount
  buyin_ratio   ← buyin_ratio
  sell_amount   ← sellout_amount
  sellout_ratio ← sellout_ratio
  net_amount    ← net_amount
  broker_name   ← business_department
  stock_name    ← stock_name (新增)
  close_price   ← close_price (新增)
  rise_and_fall ← rise_and_fall (新增)
  broker_type   ← FAMOUS_BROKERS 识别
  broker_label  ← BROKER_LABELS 标签

说明:
  - 数据源从 tqcenter 改为 K:\\QTM\\longhubang.db3 (sqlite)
  - 买5/卖5 营业部逐个展开, 同一 (code, broker_name) 在买卖都出现时聚合 buy/sell
  - 营业部识别用 config/broker_list.py 的 FAMOUS_BROKERS / BROKER_LABELS
"""

import os
import sqlite3
import sys
from datetime import datetime

import pandas as pd

_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from loguru import logger  # noqa: E402

from lib.qdb import connect, executemany_batch  # noqa: E402

from config.broker_list import FAMOUS_BROKERS, BROKER_LABELS  # noqa: E402

_LOG_DIR = os.path.join(_PROJ_ROOT, 'logs')
os.makedirs(_LOG_DIR, exist_ok=True)
logger.add(os.path.join(_LOG_DIR, 'c6_lhb_{time:YYYYMMDD}.log'),
           rotation='50 MB', retention='30 days', encoding='utf-8')

# QMT sqlite 路径
QMT_LHB_PATH = r'K:\QTM\longhubang.db3'

# qd_lhb_detail 列顺序 (与 DDL 12_lhb.sql 严格一致)
LHB_DETAIL_COLS = [
    'code', 'lhb_date', 'stock_name', 'close_price', 'rise_and_fall',
    'reason', 'reason_id', 'direction', 'rank',
    'buy_amount', 'buyin_ratio', 'sell_amount', 'sellout_ratio',
    'net_amount', 'broker_name', 'broker_type', 'broker_label',
]

# qd_lhb_broker 列顺序
LHB_BROKER_COLS = [
    'broker_name', 'update_time', 'broker_type', 'broker_label',
    'total_buy_30d', 'total_sell_30d', 'appear_count_30d', 'hot_level',
]


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


def _safe_float(v, default=0.0):
    """安全转 float"""
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _get_latest_date(con):
    """获取 QuestDB 最新龙虎榜日期"""
    try:
        cur = con.cursor()
        cur.execute("SELECT MAX(lhb_date) FROM qd_lhb_detail")
        result = cur.fetchone()[0]
        cur.close()
        if result:
            # result 是 datetime 或 string
            if hasattr(result, 'strftime'):
                return result.strftime('%Y-%m-%d')
            return str(result)[:10]
    except Exception as e:
        logger.warning('获取最新龙虎榜日期失败: {}', e)
        pass
    return None


def run(date=None, con=None, dry_run=False):
    """龙虎榜采集主入口

    Args:
        date: 龙虎榜日期 (datetime.date), None 用今天
        con:  psycopg2 连接, None 则自建
        dry_run: True 时只查询不入库

    Returns:
        dict: {'qd_lhb_detail': n, 'qd_lhb_broker': n}
    """
    own_con = con is None
    if own_con:
        con = connect()

    if date is None:
        date = datetime.now().date()
    date_str = date.strftime('%Y-%m-%d')
    # lhb_date 用当日 00:00:00 的 datetime (QuestDB TIMESTAMP)
    lhb_date = datetime(date.year, date.month, date.day)

    try:
        logger.info('龙虎榜采集开始, date={}', date_str)

        # 1. 连接 QMT sqlite
        conn = sqlite3.connect(QMT_LHB_PATH)
        conn.text_factory = lambda b: b.decode('utf-8', errors='ignore')

        # 2. 查询 longhubang 表
        df_lhb = pd.read_sql(
            "SELECT * FROM longhubang WHERE trade_date = ?", conn, params=(date_str,)
        )
        if df_lhb.empty:
            logger.warning('longhubang 无数据, date={}', date_str)
            conn.close()
            return {'qd_lhb_detail': 0, 'qd_lhb_broker': 0}

        logger.info('longhubang {} 条', len(df_lhb))

        # 3. 查询 trader_booth 表
        df_booth = pd.read_sql(
            "SELECT * FROM trader_booth WHERE trade_date = ?", conn, params=(date_str,)
        )
        conn.close()

        logger.info('trader_booth {} 条', len(df_booth))

        # dry_run 模式只查询不写入
        if dry_run:
            logger.info('dry_run 模式，跳过写入')
            return {'qd_lhb_detail': len(df_booth), 'qd_lhb_broker': 0}

        # 4. 解析明细行
        detail_rows = []
        broker_agg = {}  # broker_name → {buy, sell, count}

        for _, r in df_booth.iterrows():
            code = str(r.get('stock_code', ''))
            stock_name = str(r.get('stock_name', '')) if r.get('stock_name') else ''
            direction = str(r.get('direction', 'B'))
            rank = int(r.get('rank', 0)) if r.get('rank') else 0
            broker_name = str(r.get('business_department', ''))

            buy_amt = _safe_float(r.get('buyin_amount'))
            buy_ratio = _safe_float(r.get('buyin_ratio'))
            sell_amt = _safe_float(r.get('sellout_amount'))
            sell_ratio = _safe_float(r.get('sellout_ratio'))
            net_amt = _safe_float(r.get('net_amount'))

            # 回填 longhubang 信息
            lhb_match = df_lhb[df_lhb['stock_code'] == code]
            if not lhb_match.empty:
                lhb_row = lhb_match.iloc[0]
                reason = str(lhb_row.get('reason', ''))
                reason_id = int(lhb_row.get('reason_id', 0)) if lhb_row.get('reason_id') else None
                close_price = _safe_float(lhb_row.get('close_price'))
                rise_fall = _safe_float(lhb_row.get('rise_and_fall'))
                if not stock_name:
                    stock_name = str(lhb_row.get('stock_name', '')) if lhb_row.get('stock_name') else ''
            else:
                reason = ''
                reason_id = None
                close_price = None
                rise_fall = None

            # 营业部识别
            btype, blabel = _identify_broker(broker_name)

            detail_rows.append((
                code, lhb_date, stock_name, close_price, rise_fall,
                reason, reason_id, direction, rank,
                buy_amt, buy_ratio, sell_amt, sell_ratio,
                net_amt, broker_name, btype, blabel,
            ))

            # 营业部聚合
            agg = broker_agg.setdefault(broker_name, {'buy': 0.0, 'sell': 0.0, 'count': 0})
            agg['buy'] += buy_amt
            agg['sell'] += sell_amt
            agg['count'] += 1

        logger.info('解析: 明细 {} 行, 营业部 {} 个', len(detail_rows), len(broker_agg))

        # 5. 写 qd_lhb_detail
        n_detail = executemany_batch(con, 'qd_lhb_detail', LHB_DETAIL_COLS, detail_rows)

        # 6. 写 qd_lhb_broker (当日画像)
        broker_rows = []
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
            broker_rows.append((
                bname, datetime.now(), btype, blabel,
                agg['buy'], agg['sell'], cnt, hot,
            ))
        n_broker = executemany_batch(con, 'qd_lhb_broker', LHB_BROKER_COLS, broker_rows)

        logger.info('龙虎榜写入完成: detail={}, broker={}', n_detail, n_broker)
        return {'qd_lhb_detail': n_detail, 'qd_lhb_broker': n_broker}
    except Exception as e:
        logger.error('龙虎榜采集失败: {}', e)
        raise
    finally:
        if own_con:
            con.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description='c6 龙虎榜采集')
    parser.add_argument('--date', type=str, default=None,
                        help='龙虎榜日期 YYYY-MM-DD, 默认今天')
    parser.add_argument('--full', action='store_true',
                        help='全量同步所有历史数据')
    args = parser.parse_args()

    if args.full:
        # 全量同步
        con = connect()
        try:
            latest = _get_latest_date(con)
            if latest:
                logger.info('当前 QuestDB 最新日期: {}', latest)
            else:
                logger.info('QuestDB 无数据，将全量同步')

            # 连接 sqlite 获取所有日期
            conn = sqlite3.connect(QMT_LHB_PATH)
            conn.text_factory = lambda b: b.decode('utf-8', errors='ignore')
            df_dates = pd.read_sql(
                "SELECT DISTINCT trade_date FROM longhubang ORDER BY trade_date",
                conn
            )
            conn.close()

            dates = df_dates['trade_date'].tolist()
            logger.info('QMT 数据共 {} 个日期 {} ~ {}',
                       len(dates),
                       dates[0] if dates else 'N/A',
                       dates[-1] if dates else 'N/A')

            total_detail = 0
            total_broker = 0
            for i, d in enumerate(dates):
                logger.info('[{}/{}] 同步 {}', i+1, len(dates), d)
                try:
                    result = run(date=datetime.strptime(d, '%Y-%m-%d').date(), con=con)
                    total_detail += result.get('qd_lhb_detail', 0)
                    total_broker += result.get('qd_lhb_broker', 0)
                except Exception as e:
                    logger.error('日期 {} 失败: {}', d, e)
                    continue

            logger.info('全量同步完成: detail {} 行, broker {} 行',
                        total_detail, total_broker)
        finally:
            con.close()
    else:
        # 单日采集
        d = None
        if args.date:
            d = datetime.strptime(args.date, '%Y-%m-%d').date()
        run(date=d)


if __name__ == '__main__':
    main()
