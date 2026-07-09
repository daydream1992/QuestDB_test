"""QuestDB 连接封装

脚本路径: K:\QuestDB_test\\lib\\qdb.py
用途: 封装 psycopg2 连接 QuestDB, 提供批量写入 / 查询 DataFrame / 单行查询
依赖: psycopg2, pandas, python-dotenv
数据源: QuestDB (PG 协议, 端口 8812)
入库表: 通用 (由调用方指定表名)
说明:
  - QuestDB PG 协议占位符用 %s (? 会报错)
  - autocommit=True, 避免跨连接 read-after-write 延迟
  - QuestDB 9.4.3 不支持 DELETE FROM, 用 DEDUP UPSERT KEYS 幂等
  - H5: connect 加 libpq TCP keepalives (Linux/macOS 有效, Windows 仅 PG 客户端 ≥16 支持),
    提供 _ensure_alive(con) 工具在 query/写前 ping + OperationalError 重连一次
  - DuckDB 双写: executemany_batch 写入高频表时, 同步缓存并落盘 parquet
    到 D:\\dbshujubeifen, 不阻断主流程。详见 _BACKUP_* 常量。
"""

import os
import re
from collections import defaultdict
from datetime import datetime, timedelta
import numpy as np

import psycopg2
from psycopg2 import OperationalError, InterfaceError
import pandas as pd
from dotenv import load_dotenv
from loguru import logger



def cutoff(seconds=0, minutes=0, hours=0, days=0):
    """本地 now 倒推的 ISO 时间字符串 (供 SQL WHERE timestamp > '...' 用)

    QuestDB now() 返回 UTC, 与 Python 本地 (北京) 写入的 TIMESTAMP 字面值差 8h,
    用 SQL now()/dateadd(..., now()) 会错位 8h 导致 WHERE 命中数据范围与预期不符
    (偏多, 读到全部当天而非近 N 分钟)。统一用本函数替代。
    """
    delta = timedelta(seconds=seconds, minutes=minutes, hours=hours, days=days)
    return (datetime.now() - delta).strftime('%Y-%m-%dT%H:%M:%S')

# 加载 config/.env
_ENV_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'config', '.env')
load_dotenv(_ENV_PATH)

QDB_HOST = os.getenv('QDB_HOST', '127.0.0.1')
QDB_PORT = int(os.getenv('QDB_PORT', '8812'))
QDB_USER = os.getenv('QDB_USER', 'admin')
QDB_PASSWORD = os.getenv('QDB_PASSWORD', 'quest')
QDB_DBNAME = os.getenv('QDB_DBNAME', 'qdb')

# —— parquet 双写备份 (批次分片, O(1) 写入) ——
# 每批写独立小文件 (today/NNNNNN.parquet), 不读不拼不合并。
# 读取时用 pyarrow/duckdb 读目录下所有分片 (收盘后可通过 force_flush_backup 合并)。
# 每批 I/O = 写当前批次, O(1), 不随当日数据量增长。
#
# 熔断器: 连续 3 次写入延迟 > 500ms 或 5 次异常 → 自动禁用当天备份, 保主线不崩。
# 开关: .env 中 BACKUP_ENABLED=true/false 或直接改下面常量; 重启后重置。
_BACKUP_ENABLED = os.getenv('BACKUP_ENABLED', 'false').lower() in ('true', '1', 'yes')
_BACKUP_DIR = r'D:\dbshujubeifen'
_BACKUP_HIGH_FREQ = {
    'qd_pricevol', 'qd_kline_1m', 'qd_kline_5m',
    'qd_stock_snapshot', 'qd_money_flow', 'qd_big_order',
}
_BACKUP_SEQ: dict[str, int] = {}  # table → batch_seq

# — 熔断器状态 —
_BACKUP_CIRCUIT_BROKEN = False       # True 后当天不再备份
_BACKUP_SLOW_COUNT = 0               # 连续慢写入计数
_BACKUP_FAIL_COUNT = 0               # 连续失败计数
_BACKUP_MAX_SLOW = 3                 # 连续超过 _SLOW_THRESHOLD_MS 则熔断
_BACKUP_MAX_FAIL = 5                 # 连续失败 5 次则熔断
_BACKUP_SLOW_THRESHOLD_MS = 500     # 单次写入超过此值视为慢


def _write_parquet_backup(table, columns, rows):
    """每批直写 parquet (批次分片, O(1), 不读不拼不合并)

    内置熔断器: 连续慢/失败 → 自动禁用当天备份, 保主线不崩。

    Args:
        table: 表名
        columns: 列名列表
        rows: 行数据 (list of tuple)
    """
    global _BACKUP_CIRCUIT_BROKEN, _BACKUP_SLOW_COUNT, _BACKUP_FAIL_COUNT
    if _BACKUP_CIRCUIT_BROKEN or not _BACKUP_ENABLED:
        return
    if not rows or table not in _BACKUP_HIGH_FREQ:
        return
    import time as _time
    _t0 = _time.time()
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        table_dir = os.path.join(_BACKUP_DIR, table, today)
        os.makedirs(table_dir, exist_ok=True)
        # 批次序号 (每表每日自增, key 含日期防跨日溢出)
        seq_key = f'{table}:{today}'
        seq = _BACKUP_SEQ.setdefault(seq_key, 0) + 1
        _BACKUP_SEQ[seq_key] = seq
        dst = os.path.join(table_dir, f'{seq:06d}.parquet')
        df = pd.DataFrame(rows, columns=columns)
        df.to_parquet(dst, index=False)
        # —— 熔断检测: 写入超过阈值 ——
        _elapsed = (_time.time() - _t0) * 1000
        if _elapsed > _BACKUP_SLOW_THRESHOLD_MS:
            _BACKUP_SLOW_COUNT += 1
            logger.warning('parquet备份写入慢 {table}: {elapsed:.0f}ms (连续{slow}次)',
                           table=table, elapsed=_elapsed, slow=_BACKUP_SLOW_COUNT)
            if _BACKUP_SLOW_COUNT >= _BACKUP_MAX_SLOW:
                _BACKUP_CIRCUIT_BROKEN = True
                logger.error('parquet备份熔断: 连续 {slow} 次写入超 {th}ms, 当天禁用',
                              slow=_BACKUP_SLOW_COUNT, th=_BACKUP_SLOW_THRESHOLD_MS)
        else:
            _BACKUP_SLOW_COUNT = 0  # 写入正常 → 重置慢计数
        _BACKUP_FAIL_COUNT = 0      # 写入正常 → 重置失败计数
    except Exception as e:
        _BACKUP_FAIL_COUNT += 1
        logger.warning('parquet备份失败 {table} (连续{count}次): {e}',
                        table=table, count=_BACKUP_FAIL_COUNT, e=e)
        if _BACKUP_FAIL_COUNT >= _BACKUP_MAX_FAIL:
            _BACKUP_CIRCUIT_BROKEN = True
            logger.error('parquet备份熔断: 连续 {count} 次失败, 当天禁用',
                          count=_BACKUP_FAIL_COUNT)


def force_flush_backup():
    """收盘后合并当天所有分片为单个文件 (可选, 方便归档)

    遍历 _BACKUP_HIGH_FREQ 每张表下今天的日期目录,
    读所有分片, concat 后写一个合并文件并删除分片。
    非必调: 查询时可直接读目录下所有分片 (pyarrow/duckdb 原生支持)。
    """
    if not _BACKUP_ENABLED:
        return
    today = datetime.now().strftime('%Y-%m-%d')
    for table in _BACKUP_HIGH_FREQ:
        table_dir = os.path.join(_BACKUP_DIR, table, today)
        if not os.path.isdir(table_dir):
            continue
        try:
            parts = sorted([p for p in os.listdir(table_dir) if p.endswith('.parquet')])
            if not parts:
                continue
            if len(parts) == 1:
                # 唯一文件, 移到父目录
                src = os.path.join(table_dir, parts[0])
                dst = os.path.join(_BACKUP_DIR, table, f'{today}.parquet')
                os.rename(src, dst)
            else:
                dfs = [pd.read_parquet(os.path.join(table_dir, p)) for p in parts]
                merged = pd.concat(dfs, ignore_index=True)
                dst = os.path.join(_BACKUP_DIR, table, f'{today}.parquet')
                merged.to_parquet(dst, index=False)
                # 删除分片
                for p in parts:
                    os.remove(os.path.join(table_dir, p))
            os.rmdir(table_dir)
        except Exception as e:
            logger.warning('parquet 合并失败 {table}: {e}', table=table, e=e)


def connect():
    """返回 autocommit=True 的 psycopg2 连接 (H5: TCP keepalives 防静默断)

    QuestDB PG 协议:
      - 占位符用 %s (? 会报错)
      - autocommit=True 避免跨连接 read-after-write 延迟
      - keepalives_idle/interval/count: libpq 参数, 30s 空闲后发 keepalive,
        每 10s 重试, 共 3 次失败视为断 (≈60s 检测到断连)
        Linux/macOS 有效; Windows 仅当 PG 客户端 ≥16 才生效, 否则静默忽略

    Returns:
        psycopg2.connection: autocommit=True, 带 keepalives (平台支持时)
    """
    con = psycopg2.connect(
        host=QDB_HOST,
        port=QDB_PORT,
        user=QDB_USER,
        password=QDB_PASSWORD,
        dbname=QDB_DBNAME,
        # H5: TCP keepalive (libpq); Windows 上如驱动不支持会被 libpq 静默忽略
        keepalives=1,
        keepalives_idle=30,
        keepalives_interval=10,
        keepalives_count=3,
    )
    con.autocommit = True
    return con


def _ensure_alive(con, max_retry=1):
    """H5: ping 连接, OperationalError 时自动重连一次返回新连接

    用 SELECT 1 而非 con.closed (closed 只反映 close() 调用, 不断网);
    OperationalError 是 psycopg2 网络断的标准异常 (PG 客户端编码层)。

    Args:
        con: 旧连接
        max_retry: 重试次数, 默认 1 (再断就让上层抛)

    Returns:
        psycopg2.connection: 健康连接 (可能 == 原 con, 也可能 == 新 con)
    """
    if con is None:
        return connect()
    try:
        cur = con.cursor()
        try:
            cur.execute('SELECT 1')
            cur.fetchone()
        finally:
            cur.close()
        return con
    except (OperationalError, InterfaceError) as e:
        if max_retry <= 0:
            raise
        logger.warning('QuestDB 连接失效, 自动重连: {}', e)
        try:
            con.close()
        except Exception:
            pass
        return connect()


def executemany_batch(con, table, columns, rows, batch_size=500):
    """批量写入 QuestDB (H5: OperationalError 自动重连一次重试)

    用 psycopg2 executemany 分批写入, 每 batch_size 行一批。
    占位符用 %s (QuestDB PG 协议要求)。

    Args:
        con: psycopg2 连接 (建议 autocommit=True)
        table: 表名
        columns: 列名列表
        rows: 行数据列表 (每行是 tuple/list, 顺序与 columns 一致)
        batch_size: 每批行数, 默认 500

    Returns:
        int: 已写入的行数
    """
    if not rows:
        return 0
    # 表名校验 (防止 SQL 注入)
    if not re.match(r'^qd_[a-z0-9_]+$', table):
        logger.error('executemany_batch 非法表名: {}', table)
        return 0
    cols = ', '.join(columns)
    placeholders = ', '.join(['%s'] * len(columns))
    sql = 'INSERT INTO {table} ({cols}) VALUES ({ph})'.format(
        table=table, cols=cols, ph=placeholders)
    # 将 numpy 标量转为原生 Python (np.float64 → float, np.int64 → int)
    # QuestDB PG 协议不能直接推 np.float64 等 numpy 标量
    def _native(v):
        if isinstance(v, (np.floating, np.integer)):
            return v.item()
        if isinstance(v, (np.bool_,)):
            return bool(v)
        return v
    native_rows = [tuple(_native(v) for v in row) for row in rows]
    # H5: 重连后重试一次 (DEDUP UPSERT 幂等, 重写无副作用)
    try:
        n = _exec_with_reconnect(con, lambda c: _do_executemany(c, sql, native_rows, batch_size))
        # parquet 双写 (不阻断主流程, 每批直写)
        _write_parquet_backup(table, columns, native_rows)
        return n
    except Exception as e:
        logger.warning('executemany_batch 失败 {}.{}: {}', table, len(rows), e)
        raise


def _do_executemany(con, sql, rows, batch_size):
    """executemany_batch 实际执行, 每批故障时自动重连并重试

    解决场景: QuestDB 重启/断连导致 mid-batch 失败, 后续批次全部写不进。
    """
    cur = con.cursor()
    total = 0
    try:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                cur.executemany(sql, batch)
                total += len(batch)
            except (OperationalError, InterfaceError) as e:
                # 本批断连: 自动重连后创建新 cursor, 重试本批
                from loguru import logger
                logger.warning('executemany 批次连接断, 自动重连重试 (已写 {} 行, 失败在 {})', total, i)
                cur.close()
                import time
                time.sleep(0.5)
                con = _ensure_alive(con)
                cur = con.cursor()
                cur.executemany(sql, batch)
                total += len(batch)
    finally:
        try:
            cur.close()
        except Exception:
            pass
    return total


def _exec_with_reconnect(con, fn, max_retry=1):
    """H5: 调 fn(con), OperationalError 时 _ensure_alive 后重试一次"""
    try:
        return fn(con)
    except (OperationalError, InterfaceError):
        if max_retry <= 0:
            raise
        new_con = _ensure_alive(con, max_retry=max_retry - 1)
        return fn(new_con)


def query_df(con, sql, params=None):
    """执行查询, 返回 pandas DataFrame (H5: OperationalError 自动重连一次重试)

    Args:
        con: psycopg2 连接
        sql: SQL 语句 (占位符用 %s)
        params: 参数 (tuple/list/dict)
    """
    def _do(c):
        return pd.read_sql_query(sql, c, params=params)
    return _exec_with_reconnect(con, _do)


def query_one(con, sql, params=None):
    """执行查询, 返回单行 (dict) 或 None (H5: OperationalError 自动重连一次重试)

    Args:
        con: psycopg2 连接
        sql: SQL 语句 (占位符用 %s)
        params: 参数 (tuple/list/dict)

    Returns:
        dict: 列名 → 值; 无结果返回 None
    """
    def _do(c):
        cur = c.cursor()
        try:
            cur.execute(sql, params or ())
            row = cur.fetchone()
            if row is None:
                return None
            cols = [desc[0] for desc in cur.description]
            return dict(zip(cols, row))
        finally:
            cur.close()
    return _exec_with_reconnect(con, _do)
