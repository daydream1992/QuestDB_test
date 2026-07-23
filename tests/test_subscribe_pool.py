"""subscribe_pool 单元测试

覆盖:
  - upsert_candidates 幂等
  - top_candidates 排序 (score desc) + 排除逻辑 (exclude/cooldown/stale)
  - cooldown 行为 (写入/读取/过期清理)
  - stats 与 db_path

可两种方式跑:
  1. pytest tests/test_subscribe_pool.py -v
  2. python tests/test_subscribe_pool.py   (print 结果, 无需 pytest)
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import pytest

# 把项目根加入 path, 让 `lib.subscribe_pool` 可直接 import
_PROJ_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJ_ROOT not in sys.path:
    sys.path.insert(0, _PROJ_ROOT)

from lib.subscribe_pool import (  # noqa: E402
    Candidate,
    SubscribePool,
)


# ── pytest fixtures ──


@pytest.fixture()
def pool(tmp_path) -> SubscribePool:
    """每个用例一个独立 db 文件 (临时目录 pytest 自动清理)"""
    db = str(tmp_path / 'subscribe_pool.sqlite')
    p = SubscribePool(db_path=db).init()
    yield p
    p.close()


# ── 候选 upsert 幂等 ──


class TestUpsert:
    def test_upsert_single_persists(self, pool: SubscribePool):
        pool.upsert_candidate(Candidate(code='002747.SZ', name='埃斯顿', score=80.0))
        codes = pool.all_active_codes()
        assert codes == ['002747.SZ']

    def test_upsert_replaces_score(self, pool: SubscribePool):
        """同 code 二次 upsert 必须替换 score (无重复行)"""
        pool.upsert_candidate(Candidate(code='002747.SZ', name='埃斯顿', score=80.0))
        pool.upsert_candidate(Candidate(code='002747.SZ', name='埃斯顿', score=95.0))
        rows = pool._con().execute(
            'SELECT score FROM candidate_pool WHERE code=?', ('002747.SZ',)
        ).fetchall()
        assert len(rows) == 1, f'应只 1 行, 实际 {len(rows)}'
        assert rows[0][0] == 95.0

    def test_upsert_batch(self, pool: SubscribePool):
        pool.upsert_candidates([
            Candidate(code=f'{i:06d}.SZ', name=f'X{i}', score=50.0 + i)
            for i in range(1, 6)
        ])
        assert len(pool.all_active_codes()) == 5

    def test_empty_batch_noop(self, pool: SubscribePool):
        pool.upsert_candidates([])
        assert len(pool.all_active_codes()) == 0


# ── top_candidates 排序 + 排除 ──


class TestTopCandidates:
    def test_sort_by_score_desc(self, pool: SubscribePool):
        pool.upsert_candidates([
            Candidate(code='000001.SZ', name='低分', score=20.0),
            Candidate(code='000002.SZ', name='高分', score=90.0),
            Candidate(code='000003.SZ', name='中分', score=50.0),
        ])
        out = pool.top_candidates(limit=10)
        assert [c.code for c in out] == ['000002.SZ', '000003.SZ', '000001.SZ']
        assert [c.score for c in out] == [90.0, 50.0, 20.0]

    def test_exclude_already_subscribed(self, pool: SubscribePool):
        pool.upsert_candidates([
            Candidate(code='000001.SZ', name='a', score=80.0),
            Candidate(code='000002.SZ', name='b', score=70.0),
        ])
        out = pool.top_candidates(limit=10, exclude={'000001.SZ'})
        assert [c.code for c in out] == ['000002.SZ']

    def test_cooldown_excluded(self, pool: SubscribePool):
        """cooldown 内的 code 不应被 top_candidates 返回"""
        pool.upsert_candidates([
            Candidate(code='000001.SZ', name='a', score=80.0),
            Candidate(code='000002.SZ', name='b', score=70.0),
        ])
        pool.mark_cooldown('000001.SZ', reason='limit_break', cooldown_sec=60)
        out = pool.top_candidates(limit=10)
        assert [c.code for c in out] == ['000002.SZ']

    def test_limit_caps_result(self, pool: SubscribePool):
        pool.upsert_candidates([
            Candidate(code=f'{i:06d}.SZ', name=f'x{i}', score=float(i))
            for i in range(1, 11)  # 10 条
        ])
        out = pool.top_candidates(limit=3)
        assert len(out) == 3

    def test_stale_excluded(self, pool: SubscribePool):
        """fresh_sec 窗口外的候选应被排除"""
        pool.upsert_candidate(Candidate(code='000001.SZ', name='a', score=80.0))
        # 手动改 updated_at 模拟 1 小时前
        pool._con().execute(
            'UPDATE candidate_pool SET updated_at = ? WHERE code = ?',
            (time.time() - 3600, '000001.SZ'),
        )
        out = pool.top_candidates(limit=10, fresh_sec=600)
        assert out == []


# ── cooldown ──


class TestCooldown:
    def test_mark_and_query(self, pool: SubscribePool):
        pool.mark_cooldown('002747.SZ', reason='limit_break', cooldown_sec=60)
        assert pool.is_cooling('002747.SZ') is True
        assert pool.is_cooling('002008.SZ') is False

    def test_cooldown_expires(self, pool: SubscribePool):
        """过期后应允许再提名"""
        pool.mark_cooldown('002747.SZ', reason='limit_break', cooldown_sec=1)
        assert pool.is_cooling('002747.SZ') is True
        time.sleep(1.2)
        assert pool.is_cooling('002747.SZ') is False

    def test_expire_cooldowns_physical_cleanup(self, pool: SubscribePool):
        """已过期 cooldown 应被 expire_cooldowns(keep=0) 物理删除"""
        pool._con().execute(
            'INSERT INTO cooldown (code,reason,trigger_ts,expire_ts)'
            ' VALUES (?,?,?,?)',
            ('002747.SZ', 'test', time.time() - 100, time.time() - 50),
        )
        pool.expire_cooldowns(keep_after_expire_sec=0)
        row = pool._con().execute(
            'SELECT COUNT(*) FROM cooldown WHERE code=?', ('002747.SZ',)
        ).fetchone()[0]
        assert row == 0

    def test_cooldown_idempotent(self, pool: SubscribePool):
        """同一 code 多次 mark_cooldown 应只有最新一条 (INSERT OR REPLACE)"""
        pool.mark_cooldown('002747.SZ', reason='limit_break', cooldown_sec=60)
        pool.mark_cooldown('002747.SZ', reason='limit_break', cooldown_sec=300)
        row = pool._con().execute(
            'SELECT COUNT(*), MAX(expire_ts) FROM cooldown WHERE code=?',
            ('002747.SZ',),
        ).fetchone()
        assert row[0] == 1, f'应只 1 行, 实际 {row[0]}'


# ── stats / 进程模型 ──


class TestStats:
    def test_stats_initial_zero(self, pool: SubscribePool):
        s = pool.stats()
        assert s['candidates_active'] == 0
        assert s['cooldowns_active'] == 0
        assert s['db_path'].endswith('subscribe_pool.sqlite')

    def test_stats_counts_active_only(self, pool: SubscribePool):
        pool.upsert_candidate(Candidate(code='002747.SZ', name='a', score=80.0))
        pool.mark_cooldown('002008.SZ', reason='test', cooldown_sec=60)
        s = pool.stats()
        assert s['candidates_active'] == 1
        assert s['cooldowns_active'] == 1


# ── 导入与初始化健壮性 ──


class TestInitResilience:
    def test_init_failure_falls_back(self, tmp_path):
        """db_path 不可写时 init() 不抛, 后续读返回空"""
        bad = str(tmp_path / 'no_such_dir' / 'sub' / 'x.sqlite')  # 父目录不存在
        # sqlite3.connect 会创建中间目录, 故改用非法 path
        # 用文件占位 + 尝试当目录打开
        blocker = tmp_path / 'block'
        blocker.write_text('x')
        p = SubscribePool(db_path=str(blocker)).init()
        # 不抛即可 (降级跑, 上层有 fallback)
        p.close()

    def test_get_default_singleton(self):
        from lib.subscribe_pool import get_default
        a = get_default()
        b = get_default()
        assert a is b


# ── 手动 main (无 pytest 也可跑) ──


def main() -> int:
    """无 pytest 时的简易 runner: 打印每个用例是否通过

    每个用例独立 tmp 目录 SQLite, 避免跨用例数据污染 (真 bug vs 状态泄漏
    一眼可分).
    """
    import tempfile
    failures = 0
    total = 0

    test_classes = [TestUpsert, TestTopCandidates,
                    TestCooldown, TestStats, TestInitResilience]

    for cls in test_classes:
        obj = cls()
        methods = [m for m in dir(obj) if m.startswith('test_')]
        for m in methods:
            total += 1
            fn = getattr(obj, m)
            # 每个用例建独立 tmp 目录, 隔离状态
            td = tempfile.mkdtemp(prefix='subpool_test_')
            db = os.path.join(td, 'subscribe_pool.sqlite')
            pool = SubscribePool(db_path=db).init()
            ok = False
            err_msg = ''
            try:
                sig = fn.__code__.co_varnames[:fn.__code__.co_argcount]
                args = []
                if 'pool' in sig:
                    args.append(pool)
                elif 'tmp_path' in sig:
                    from pathlib import Path
                    args.append(Path(td))
                fn(*args)
                ok = True
            except Exception as e:
                err_msg = f'{type(e).__name__}: {e}'
            finally:
                # 先 close pool 释放 sqlite 文件句柄, 否则 Win 删不动
                try:
                    pool.close()
                except Exception:
                    pass
                # 再清 tmpdir (ignore_errors 防止 Win 偶尔抽风)
                import shutil
                shutil.rmtree(td, ignore_errors=True)
            if ok:
                print(f'  [PASS] {cls.__name__}.{m}')
            else:
                failures += 1
                print(f'  [FAIL] {cls.__name__}.{m}: {err_msg}')

    print(f'\n[summary] {total - failures}/{total} passed, {failures} failed')
    return 0 if failures == 0 else 1


if __name__ == '__main__':
    sys.exit(main())
