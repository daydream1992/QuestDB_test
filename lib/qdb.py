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
"""

import os
from datetime import datetime, timedelta

import psycopg2
import pandas as pd
from dotenv import load_dotenv


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
    """返回 autocommit=True 的 psycopg2 连接

    QuestDB PG 协议:
      - 占位符用 %s (? 会报错)
      - autocommit=True 避免跨连接 read-after-write 延迟
    """
    con = psycopg2.connect(
        host=QDB_HOST,
        port=QDB_PORT,
        user=QDB_USER,
        password=QDB_PASSWORD,
        dbname=QDB_DBNAME,
    )
    con.autocommit = True
    return con


def executemany_batch(con, table, columns, rows, batch_size=500):
    """批量写入 QuestDB

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
    cols = ', '.join(columns)
    placeholders = ', '.join(['%s'] * len(columns))
    sql = 'INSERT INTO {table} ({cols}) VALUES ({ph})'.format(
        table=table, cols=cols, ph=placeholders)
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


def query_df(con, sql, params=None):
    """执行查询, 返回 pandas DataFrame

    Args:
        con: psycopg2 连接
        sql: SQL 语句 (占位符用 %s)
        params: 参数 (tuple/list/dict)
    """
    return pd.read_sql_query(sql, con, params=params)


def query_one(con, sql, params=None):
    """执行查询, 返回单行 (dict) 或 None

    Args:
        con: psycopg2 连接
        sql: SQL 语句 (占位符用 %s)
        params: 参数 (tuple/list/dict)

    Returns:
        dict: 列名 → 值; 无结果返回 None
    """
    cur = con.cursor()
    try:
        cur.execute(sql, params or ())
        row = cur.fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in cur.description]
        return dict(zip(cols, row))
    finally:
        cur.close()
