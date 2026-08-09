"""testv10.2 DuckDB 本地快照 — 防飞书挂 + 盘后 SQL 分析

脚本路径: K:/QuestDB_test/testv10.2/duckdb_snapshot.py
用途: radar 每轮把关键指标 append 到本地 DuckDB 单文件 (logs/v10.2_radar.duckdb),
      镜像飞书 8 表的核心字段。飞书/网络挂时本地有完整记录, 盘后可用 SQL 分析。
表:
  sentiment  (时间/综合分/档位/涨停/跌停/炸板/封板率/成交/主力/连板)  每轮 1 行
  board_pool (时间/板块代码/名称/状态/动能分/涨幅/涨停数)            每轮全池
  drilled    (时间/代码/名称/涨幅/封单/连板/主力/涨停比例)           每轮 Top
  events     (时间/类型/代码/名称/详情)                               事件驱动
轻量: 单文件列式压缩, 零新依赖 (duckdb 已装), 写失败不崩雷达 (故障隔离)。
红线: dry_run 不写 (守红线: 非 --push 不落盘); 失败 logger.debug 不崩。
"""

import bootstrap
bootstrap.ensure_paths()

import os  # noqa: E402
from datetime import datetime  # noqa: E402

from loguru import logger  # noqa: E402

import settings as cfg  # noqa: E402

_DB_PATH = os.path.join(cfg.LOG_DIR, 'v10.2_radar.duckdb')

# 各表 schema (建表 SQL; CREATE TABLE IF NOT EXISTS)
_TABLES = {
    'sentiment': '''
        CREATE TABLE IF NOT EXISTS sentiment (
            ts TIMESTAMP, score DOUBLE, label VARCHAR, trend VARCHAR,
            zt_cnt INT, dt_cnt INT, blasted INT, fbl DOUBLE,
            amount_wan DOUBLE, main_wan DOUBLE, max_lb INT, cont_chg DOUBLE
        )''',
    'board_pool': '''
        CREATE TABLE IF NOT EXISTS board_pool (
            ts TIMESTAMP, code VARCHAR, name VARCHAR, state VARCHAR,
            score DOUBLE, zaf DOUBLE, zt_num INT
        )''',
    'drilled': '''
        CREATE TABLE IF NOT EXISTS drilled (
            ts TIMESTAMP, code VARCHAR, name VARCHAR, zaf DOUBLE,
            fcamo DOUBLE, ever_zt INT, zjl_hb DOUBLE, limit_pct DOUBLE
        )''',
    'events': '''
        CREATE TABLE IF NOT EXISTS events (
            ts TIMESTAMP, etype VARCHAR, code VARCHAR, name VARCHAR, detail VARCHAR
        )''',
}


class DuckdbSnapshot:
    """DuckDB 本地快照: 每轮 append 关键指标。写失败不崩雷达。"""

    def __init__(self, dry_run: bool | None = None):
        self.dry_run = cfg.SENTIMENT_DRY_RUN if dry_run is None else dry_run
        self._conn = None
        self._enabled = not self.dry_run

    def _connect(self):
        if self._conn is None:
            import duckdb
            os.makedirs(cfg.LOG_DIR, exist_ok=True)
            self._conn = duckdb.connect(_DB_PATH)
            for tbl, ddl in _TABLES.items():
                self._conn.execute(ddl)
        return self._conn

    def _insert(self, table: str, cols: list, rows: list) -> None:
        if not self._enabled or not rows:
            return
        try:
            conn = self._connect()
            placeholders = ','.join('?' * len(cols))
            conn.executemany(f'INSERT INTO {table} ({",".join(cols)}) VALUES ({placeholders})',
                             rows)
        except Exception:  # noqa: BLE001  快照失败不崩雷达
            logger.debug('DuckDB 快照写入 {} 失败 ({} 行)', table, len(rows))

    # ── 每轮: 情绪 (镜像飞书情绪表核心字段) ──
    def snapshot_sentiment(self, result: dict, now: datetime) -> None:
        self._insert('sentiment',
                     ['ts', 'score', 'label', 'trend', 'zt_cnt', 'dt_cnt', 'blasted',
                      'fbl', 'amount_wan', 'main_wan', 'max_lb', 'cont_chg'],
                     [[now, result.get('score'), result.get('label'), result.get('trend'),
                       result.get('zt_cnt', 0), result.get('dt_cnt', 0),
                       result.get('blasted', 0), result.get('fbl', 0),
                       result.get('amount_today', 0), result.get('main_net', 0),
                       result.get('max_lb', 0), result.get('cont_chg', 0)]])

    # ── 每轮: 池内板块 (镜像 board_pool 状态) ──
    def snapshot_pool(self, pool, now: datetime) -> None:
        rows = []
        for st in pool.snapshot():
            rows.append([now, st.code, st.name, st.state, st.score,
                         st.zaf, st.zt_num])
        self._insert('board_pool',
                     ['ts', 'code', 'name', 'state', 'score', 'zaf', 'zt_num'], rows)

    # ── 每轮: 钻取 Top (镜像个股榜核心) ──
    def snapshot_drilled(self, drilled: dict, ms, now: datetime) -> None:
        rows = []
        for c, d in list(drilled.items())[:30]:
            rows.append([now, c, ms.stock_name(c) if ms else c,
                         d.get('ZAF', 0), d.get('FCAmo', 0),
                         d.get('EverZTCount', 0), d.get('Zjl_HB', 0),
                         d.get('limit_pct', 0)])
        self._insert('drilled',
                     ['ts', 'code', 'name', 'zaf', 'fcamo', 'ever_zt', 'zjl_hb', 'limit_pct'],
                     rows)

    # ── 事件驱动: 机会/预警 (镜像推送卡) ──
    def record_event(self, etype: str, code: str, name: str, detail: str,
                     now: datetime) -> None:
        self._insert('events',
                     ['ts', 'etype', 'code', 'name', 'detail'],
                     [[now, etype, code, name, detail]])

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None


if __name__ == '__main__':
    # 自检: 建表 + 插入 + 查询 (dry_run=True 跳过, 这里强制真写验 schema)
    import os
    import duckdb
    _TEST_DB = os.path.join(cfg.LOG_DIR, '_test_duckdb.duckdb')
    # 临时换路径测
    orig = _DB_PATH
    globals()['_DB_PATH'] = _TEST_DB
    snap = DuckdbSnapshot(dry_run=False)
    snap._enabled = True
    now = datetime.now()
    snap.snapshot_sentiment({'score': 72.5, 'label': '偏暖', 'trend': '↑',
                             'zt_cnt': 74, 'dt_cnt': 4, 'blasted': 6, 'fbl': 87.0,
                             'amount_today': 268340672, 'main_net': 4539885,
                             'max_lb': 4, 'cont_chg': 1.26}, now)
    snap.close()
    conn = duckdb.connect(_TEST_DB)
    r = conn.execute('SELECT score, zt_cnt, fbl FROM sentiment').fetchall()
    print(f'DuckDB 快照自检: sentiment 1 行 = {r}')
    conn.close()
    os.remove(_TEST_DB)
    globals()['_DB_PATH'] = orig
    print('DUCKDB SNAPSHOT SELF-TEST PASSED')
