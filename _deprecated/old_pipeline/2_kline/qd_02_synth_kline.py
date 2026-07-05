"""qd_02: SAMPLE BY 合成 1m/5m K 线

数据源: qd_snapshots_realtime (由 get_market_snapshot 实时写入)
输出:   qd_kline_1m / qd_kline_5m (QuestDB 原生 SAMPLE BY 聚合, 不在 Python 算)
幂等:   按 [min_src_ts, max_src_ts] 窗口先 DELETE 再 INSERT

注意: QuestDB 9.x SAMPLE BY 会自动把 SELECT 中的 timestamp 字段 floor 到桶边界
"""
import os, sys
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import psycopg2
from loguru import logger

load_dotenv(Path(__file__).resolve().parent.parent / '.env')

QDB = dict(
    host=os.environ['QDB_HOST'],
    port=int(os.environ['QDB_PORT']),
    user=os.environ['QDB_USER'],
    password=os.environ['QDB_PASSWORD'],
    dbname=os.environ['QDB_DBNAME'],
)

LOG_DIR = Path(__file__).resolve().parent / 'logs'
LOG_DIR.mkdir(exist_ok=True)
logger.add(LOG_DIR / 'qd_02_{time:YYYYMMDD}.log', rotation='1 day', retention='7 days')

SRC = 'qd_snapshots_realtime'
DSTS = [
    ('qd_kline_1m', '1m'),
    ('qd_kline_5m', '5m'),
]


def connect():
    """QuestDB 9.4.3 PG 协议存在事务快照延迟, 用 autocommit=True 避免 read-after-write 问题"""
    con = psycopg2.connect(**QDB)
    con.autocommit = True
    return con


def synth_one(con, src, dst, minutes):
    """对一张目标表, 幂等合成 K 线 (依赖表 DEDUP UPSERT KEYS 幂等)"""
    cur = con.cursor()

    # 找源数据的时间窗口
    cur.execute(f"SELECT min(snapshot_time), max(snapshot_time), count(*) FROM {src}")
    row = cur.fetchone()
    min_ts, max_ts, n = row
    if not n or n == 0:
        logger.warning(f'{src} 空, 跳过 {dst}')
        return 0

    # QuestDB 9.4.3 不支持 DELETE FROM, 表有 DEDUP UPSERT KEYS 幂等
    # QuestDB PG 协议用 %s 占位符, WHERE 直接用字符串(避免参数解析问题)
    min_str = min_ts.strftime('%Y-%m-%d %H:%M:%S')
    max_str = max_ts.strftime('%Y-%m-%d %H:%M:%S')
    sql = f"""
    INSERT INTO {dst} (code, kline_time, open, high, low, close, sum_amount)
    SELECT
        code,
        snapshot_time as kline_time,
        first(open) as open,
        max(high)  as high,
        min(low)   as low,
        last(now)  as close,
        sum(amount) as sum_amount
    FROM {src}
    WHERE snapshot_time >= '{min_str}' AND snapshot_time <= '{max_str}'
    SAMPLE BY {minutes}
    """
    cur.execute(sql)
    # QuestDB 9.x 的 INSERT...SELECT 不返回 rowcount (= -1), 用窗口内 count 估行
    cur.execute(f"SELECT count(*) FROM {dst} WHERE kline_time >= '{min_str}' AND kline_time <= '{max_str}'")
    n_rows = cur.fetchone()[0]
    logger.info(f'{dst}: 窗口 [{min_ts} ~ {max_ts}] K线条数={n_rows} ({minutes})')
    return n_rows


def run(con=None):
    logger.info('▶ qd_02 K线合成开始')
    own = con is None
    if own:
        con = connect()
    try:
        for dst, minutes in DSTS:
            try:
                synth_one(con, SRC, dst, minutes)
            except Exception as e:
                logger.error(f'{dst} 合成失败: {e}')
                con.rollback()
    finally:
        if own:
            con.close()
    logger.info('✓ qd_02 完成')


if __name__ == '__main__':
    run()
