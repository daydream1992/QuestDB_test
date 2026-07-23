"""subscribe_pool: 订阅候选池 + 冷却 (本地 SQLite, 跨进程共享)

用途:
  - subscribe 进程订阅前读 ``top_candidates()`` 取高分配额
  - 卸载后 ``mark_cooldown()`` 写入冷却 (防漏斗再提名)
  - 心跳 JSON / 主调度 / 订阅进程皆可读: SQLite WAL 模式跨进程 OK

守 CLAUDE.md §三: lib/ 不 import 任何业务模块. 本文件只依赖
标准库 (sqlite3) 与 dataclass, 不接 tqcenter / QuestDB / pandas.
落库走本地 logs/subscribe_pool.sqlite, 零外部资源, 可随时关停.

用法:
    from lib.subscribe_pool import SubscribePool, Candidate

    pool = SubscribePool().init()
    pool.upsert_candidates([
        Candidate(code='002747.SZ', name='埃斯顿', score=82.5,
                  near_limit=0.72, vol_surge=0.55, zjl=0.30, gene=0.80),
        ...
    ])
    for c in pool.top_candidates(limit=10, exclude=already_subscribed):
        subscribe(c.code)
        ...
    # 触发即弃
    pool.mark_cooldown('002747.SZ', reason='limit_break', cooldown_sec=300)
    # 维护 (低频)
    pool.purge_stale_candidates(ttl_sec=1800)
    pool.expire_cooldowns()
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass

# ── 常量 ──

# schema: 两张表 candidate_pool 候选 / cooldown 冷却
# WITHOUT ROWID: code / expire_ts 是 PK, 整表聚簇存储, 按 code 读 O(1)
_SCHEMA = """
CREATE TABLE IF NOT EXISTS candidate_pool (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    score       REAL NOT NULL,
    near_limit  REAL NOT NULL DEFAULT 0.0,
    vol_surge   REAL NOT NULL DEFAULT 0.0,
    zjl         REAL NOT NULL DEFAULT 0.0,
    gene        REAL NOT NULL DEFAULT 0.0,
    updated_at  REAL NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS cooldown (
    code        TEXT PRIMARY KEY,
    reason      TEXT NOT NULL DEFAULT '',
    trigger_ts  REAL NOT NULL,
    expire_ts   REAL NOT NULL
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_cooldown_expire ON cooldown(expire_ts);
CREATE INDEX IF NOT EXISTS idx_candidate_updated ON candidate_pool(updated_at);
"""

_DEFAULT_DB_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'logs',
    'subscribe_pool.sqlite',
)

# 默认参数 (yaml 可覆盖, 此处是兜底)
_CANDIDATE_FRESH_SEC = 600      # 候选 10min 内刷新视为有效
_COOLDOWN_KEEP_SEC = 3600       # 过期 cooldown 物理保留 1h 后删


# ── 数据结构 (immutable, dataclass frozen) ──


@dataclass(frozen=True)
class Candidate:
    """订阅候选 (不可变)"""
    code: str
    name: str
    score: float
    near_limit: float = 0.0      # 距涨停归一化 0~1
    vol_surge: float = 0.0       # 量放大归一化
    zjl: float = 0.0             # 主力净额评分 0~1 (Stage-1=0, 订阅后精算)
    gene: float = 0.0            # 涨停基因评分 0~1 (订阅时缓存)


# ── 主类 ──


class SubscribePool:
    """订阅候选池 (本地 SQLite, 跨进程安全, 线程安全)

    线程模型:
      - 每线程独占 sqlite3.Connection (sqlite3 本身非线程安全)
      - 跨进程共享通过 WAL 模式 + busy_timeout

    进程模型:
      - 订阅进程 / 主调度 / 任何读取方可同时持有本类实例
      - 进程 kill 不影响文件: 下次启动 .init() 即恢复

    Args:
        db_path: SQLite 文件路径. 默认 logs/subscribe_pool.sqlite
    """

    def __init__(self, db_path: str = _DEFAULT_DB_PATH):
        self._db_path = db_path
        self._local = threading.local()  # 每线程独立 con

    # ── 连接管理 ──

    def _con(self) -> sqlite3.Connection:
        con = getattr(self._local, 'con', None)
        if con is None:
            con = sqlite3.connect(self._db_path, timeout=5, isolation_level=None)
            # WAL: 读写并发, 订阅进程写不阻塞主调度读
            con.execute('PRAGMA journal_mode=WAL')
            # NORMAL: 不 fsync, 断电丢最后 1 条可接受 (cooldown 重算即可)
            con.execute('PRAGMA synchronous=NORMAL')
            # 5s busy: 跨进程竞争时短暂等待
            con.execute('PRAGMA busy_timeout=5000')
            self._local.con = con
        return con

    def init(self) -> "SubscribePool":
        """建表 (幂等, 可重复调用). 返回 self 便于链式.

        注意: 失败时仅记 warn, 不抛异常 — 让订阅管线在 DB 不可用时
        仍能以冷启动 fallback (_DEFAULT_WATCH) 跑, 不影响主流程.
        """
        try:
            con = self._con()
            for stmt in _SCHEMA.strip().split(';'):
                s = stmt.strip()
                if s:
                    con.execute(s)
        except Exception as e:
            from loguru import logger
            logger.warning('subscribe_pool.init 失败 (将以降级模式跑): {}', e)
        return self

    def close(self):
        """关闭本线程的连接 (其他线程不影响)."""
        con = getattr(self._local, 'con', None)
        if con is not None:
            try:
                con.close()
            except Exception:
                pass
            self._local.con = None

    # ── 候选: 写入 ──

    def upsert_candidate(self, c: Candidate):
        """insert-or-replace 单条 (Stage-1 漏斗用, 低频写)"""
        con = self._con()
        con.execute(
            'INSERT OR REPLACE INTO candidate_pool'
            ' (code,name,score,near_limit,vol_surge,zjl,gene,updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (c.code, c.name, c.score, c.near_limit,
             c.vol_surge, c.zjl, c.gene, time.time()),
        )

    def upsert_candidates(self, cands: list[Candidate]):
        """批量 upsert (Stage-1 漏斗全市场跑完一次后批量写)"""
        if not cands:
            return
        con = self._con()
        now = time.time()
        rows = [
            (c.code, c.name, c.score, c.near_limit,
             c.vol_surge, c.zjl, c.gene, now)
            for c in cands
        ]
        try:
            con.execute('BEGIN')
            con.executemany(
                'INSERT OR REPLACE INTO candidate_pool'
                ' (code,name,score,near_limit,vol_surge,zjl,gene,updated_at)'
                ' VALUES (?,?,?,?,?,?,?,?)',
                rows,
            )
            con.execute('COMMIT')
        except Exception:
            try:
                con.execute('ROLLBACK')
            except Exception:
                pass
            raise

    # ── 候选: 读取 ──

    def top_candidates(
        self,
        limit: int = 20,
        exclude: set[str] | None = None,
        fresh_sec: int = _CANDIDATE_FRESH_SEC,
    ) -> list[Candidate]:
        """取 score 最高的 N 条候选, 已排除: exclude + cooldown 内 + stale (>=fresh_sec 未刷)

        Args:
            limit: 最多返回几条 (默认 20, 订阅池 cap 5%~30%)
            exclude: 调用方已知订阅中的 code (避免重复提名)
            fresh_sec: 候选"新鲜"窗口 (秒), 过期不返回. 默认 600 = 10 min.

        Returns:
            list[Candidate]: 按 score 降序
        """
        exclude = exclude or set()
        con = self._con()
        now = time.time()
        rows = con.execute(
            'SELECT code,name,score,near_limit,vol_surge,zjl,gene,updated_at'
            ' FROM candidate_pool WHERE updated_at > ?',
            (now - fresh_sec,),
        ).fetchall()
        cooldowns = {
            r[0] for r in con.execute(
                'SELECT code FROM cooldown WHERE expire_ts > ?', (now,)
            ).fetchall()
        }
        out: list[Candidate] = []
        for row in rows:
            code, name, score, near_limit, vol_surge, zjl, gene, _updated = row
            if code in exclude or code in cooldowns:
                continue
            out.append(Candidate(
                code=code, name=name, score=score,
                near_limit=near_limit, vol_surge=vol_surge,
                zjl=zjl, gene=gene,
            ))
        out.sort(key=lambda c: c.score, reverse=True)
        return out[:limit]

    def all_active_codes(self, fresh_sec: int = _CANDIDATE_FRESH_SEC) -> list[str]:
        """调试/对账用: 当前所有未过期候选 code"""
        con = self._con()
        rows = con.execute(
            'SELECT code FROM candidate_pool WHERE updated_at > ?',
            (time.time() - fresh_sec,),
        ).fetchall()
        return [r[0] for r in rows]

    # ── 冷却 ──

    def mark_cooldown(self, code: str, reason: str, cooldown_sec: int = 300):
        """写入/刷新 cooldown (订阅卸载时调用, 防漏斗立即再提名)

        Args:
            code: 标的代码
            reason: 触发原因 ('limit_break'/'break_down'/'vol_price_div'/
                    'cooldown_expire'/'manual'/'close_clear')
            cooldown_sec: 冷却时长 (秒). 默认 300.
        """
        con = self._con()
        now = time.time()
        con.execute(
            'INSERT OR REPLACE INTO cooldown'
            ' (code,reason,trigger_ts,expire_ts)'
            ' VALUES (?,?,?,?)',
            (code, reason, now, now + cooldown_sec),
        )

    def is_cooling(self, code: str) -> bool:
        """单条查: 给 evaluate_tick 用, 临界区评分时可参考"""
        con = self._con()
        row = con.execute(
            'SELECT 1 FROM cooldown WHERE code=? AND expire_ts > ? LIMIT 1',
            (code, time.time()),
        ).fetchone()
        return row is not None

    # ── 维护 ──

    def purge_stale_candidates(self, ttl_sec: int = 1800):
        """清理 ttl_sec 未刷新的候选 (Stage-1 漏斗持续喂, 老候选自然过期)"""
        con = self._con()
        con.execute(
            'DELETE FROM candidate_pool WHERE updated_at < ?',
            (time.time() - ttl_sec,),
        )

    def expire_cooldowns(self, keep_after_expire_sec: int = _COOLDOWN_KEEP_SEC):
        """物理清理已过期 cooldown (保留 keep_after_expire_sec 给回溯, 防刚过期就被提名)

        频率建议: 每 60s 由订阅进程调一次
        """
        con = self._con()
        cutoff_ts = time.time() - keep_after_expire_sec
        con.execute('DELETE FROM cooldown WHERE expire_ts < ?', (cutoff_ts,))

    # ── 心跳 / 调试 ──

    def stats(self) -> dict:
        """只读统计 (主调度拼心跳时调用, O(1) ~ O(10ms))

        Returns:
            dict: candidates_active / cooldowns_active / db_path
        """
        con = self._con()
        now = time.time()
        cands = con.execute(
            'SELECT COUNT(*) FROM candidate_pool WHERE updated_at > ?',
            (now - _CANDIDATE_FRESH_SEC,),
        ).fetchone()[0]
        coold = con.execute(
            'SELECT COUNT(*) FROM cooldown WHERE expire_ts > ?',
            (now,),
        ).fetchone()[0]
        return {
            'candidates_active': cands,
            'cooldowns_active': coold,
            'db_path': self._db_path,
        }


# ── 便捷函数 (短路调用) ──


_default_pool: SubscribePool | None = None
_default_lock = threading.Lock()


def get_default() -> SubscribePool:
    """进程内单例 (避免每处 new). 跨进程各自持有实例即可."""
    global _default_pool
    if _default_pool is None:
        with _default_lock:
            if _default_pool is None:
                _default_pool = SubscribePool().init()
    return _default_pool


if __name__ == '__main__':
    # CLI 自检: 可独立运行 (Phase 1 验收 / 调试)
    #   python -m lib.subscribe_pool        (PYTHONPATH=. 时)
    #   python lib/subscribe_pool.py
    import json
    from loguru import logger
    logger.add(lambda m: print(m, end=''), level='INFO')

    pool = SubscribePool().init()
    pool.upsert_candidate(Candidate(code='002747.SZ', name='埃斯顿',
                                     score=82.5, near_limit=0.72,
                                     vol_surge=0.55, zjl=0.30, gene=0.80))
    pool.upsert_candidate(Candidate(code='002008.SZ', name='大族激光',
                                     score=70.0, near_limit=0.55,
                                     vol_surge=0.50, zjl=0.20, gene=0.40))
    pool.mark_cooldown('002008.SZ', reason='limit_break', cooldown_sec=60)
    print('[stats]', json.dumps(pool.stats(), ensure_ascii=False))
    print('[top ]', pool.top_candidates(limit=5))
    pool.purge_stale_candidates()
    pool.expire_cooldowns()
    pool.close()
    print('[ok] self-test done')
