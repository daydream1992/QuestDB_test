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
"""

import os
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
        return _exec_with_reconnect(con, lambda c: _do_executemany(c, sql, native_rows, batch_size))
    except Exception as e:
        logger.warning('executemany_batch 失败 {}.{}: {}', table, len(rows), e)
        raise


def _do_executemany(con, sql, rows, batch_size):
    """executemany_batch 实际执行 (分离出来便于 _ensure_alive 重试)"""
    cur = con.cursor()
    total = 0
    try:
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            cur.executemany(sql, batch)
            total += len(batch)
    finally:
        cur.close()
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
